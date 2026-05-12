import json
import os
import re
import sqlite3
import subprocess
import threading
import traceback
import shutil
import time
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"
IMAGE_ROOT = Path(os.getenv("IMAGE_DASHBOARD_ROOT", "/home/publico/imagens"))
DB_PATH = Path(os.getenv("IMAGE_DASHBOARD_DB", str(BASE_DIR / "dashboard_imagens.db")))
HOST = os.getenv("IMAGE_DASHBOARD_HOST", "0.0.0.0")
PORT = int(os.getenv("IMAGE_DASHBOARD_PORT", "8081"))
AUTO_SCAN_ON_START = os.getenv("IMAGE_DASHBOARD_AUTO_SCAN", "1") == "1"
ENABLE_VIDEO_DURATION = os.getenv("IMAGE_DASHBOARD_ENABLE_DURATION", "1") == "1"
SCAN_INTERVAL_SECONDS = int(os.getenv("IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS", "300"))
DURATION_INTERVAL_SECONDS = int(os.getenv("IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS", "300"))
INDEX_FILE_BATCH_SIZE = int(os.getenv("IMAGE_DASHBOARD_INDEX_BATCH_SIZE", "5000"))
INDEX_CAMERA_BATCH_SIZE = int(os.getenv("IMAGE_DASHBOARD_CAMERA_BATCH_SIZE", "1000"))

DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_FILE_PATTERN = re.compile(r"^\d{14}(?:\..+)?$")
DAY_LEVELS = (
    (0, "none"),
    (1, "low"),
    (25, "medium"),
    (100, "high"),
)
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".mpeg", ".mpg", ".m4v"}

SCAN_LOCK = threading.Lock()
SCAN_STATE = {
    "running": False,
    "scan_id": None,
    "mode": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "last_result": None,
    "vehicles_scanned": 0,
    "cameras_scanned": 0,
    "files_scanned": 0,
    "current_vehicle": None,
    "current_camera": None,
}
DURATION_LOCK = threading.Lock()
DURATION_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "processed_files": 0,
    "updated_files": 0,
    "pending_files": 0,
    "current_file": None,
}
FFPROBE_AVAILABLE: Optional[bool] = None


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def parse_month_year(query: Dict[str, List[str]]) -> Tuple[int, int]:
    now = datetime.now()
    year = parse_int(query.get("year", [str(now.year)])[0], now.year)
    month = parse_int(query.get("month", [str(now.month)])[0], now.month)
    if month < 1 or month > 12:
        month = now.month
    return month, year


def parse_multi_filter(query: Dict[str, List[str]], key: str) -> List[str]:
    values: List[str] = []
    for raw_value in query.get(key, []):
        values.extend(part.strip() for part in raw_value.split(","))
    return [value for value in values if value]


def get_level(count: int) -> str:
    chosen = "none"
    for threshold, level in DAY_LEVELS:
        if count >= threshold:
            chosen = level
    return chosen


def vehicle_sort_key(value: str) -> Tuple[int, object]:
    cleaned = value.strip()
    if cleaned.isdigit():
        return (0, int(cleaned))
    return (1, cleaned.lower())


def open_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-64000")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def ensure_database() -> None:
    with open_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS indexed_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle TEXT NOT NULL,
                camera TEXT NOT NULL,
                capture_date TEXT NOT NULL,
                file_name TEXT NOT NULL,
                extension TEXT NOT NULL,
                relative_dir TEXT NOT NULL,
                relative_file_path TEXT NOT NULL UNIQUE,
                source_path TEXT NOT NULL,
                real_path TEXT NOT NULL,
                size_bytes INTEGER,
                modified_at TEXT,
                indexed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_indexed_files_month
                ON indexed_files (capture_date, vehicle, camera);

            CREATE INDEX IF NOT EXISTS idx_indexed_files_vehicle_camera
                ON indexed_files (vehicle, camera);

            CREATE INDEX IF NOT EXISTS idx_indexed_files_capture_extension
                ON indexed_files (capture_date, extension);

            CREATE INDEX IF NOT EXISTS idx_indexed_files_vehicle_capture
                ON indexed_files (vehicle, capture_date);

            CREATE TABLE IF NOT EXISTS camera_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle TEXT NOT NULL,
                camera TEXT NOT NULL,
                source_dir TEXT NOT NULL UNIQUE,
                real_dir TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_camera_inventory_vehicle_camera
                ON camera_inventory (vehicle, camera);

            CREATE INDEX IF NOT EXISTS idx_camera_inventory_camera_vehicle
                ON camera_inventory (camera, vehicle);

            CREATE TABLE IF NOT EXISTS app_metadata (
                meta_key TEXT PRIMARY KEY,
                meta_value TEXT NOT NULL
            );
            """
        )
        ensure_column(connection, "indexed_files", "last_seen_scan_id", "TEXT")
        ensure_column(connection, "indexed_files", "duration_seconds", "REAL")
        ensure_column(connection, "camera_inventory", "last_seen_scan_id", "TEXT")
        connection.commit()


def set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO app_metadata (meta_key, meta_value)
        VALUES (?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value
        """,
        (key, value),
    )


def get_metadata() -> Dict[str, str]:
    with open_db() as connection:
        rows = connection.execute("SELECT meta_key, meta_value FROM app_metadata").fetchall()
    return {row["meta_key"]: row["meta_value"] for row in rows}


def safe_scandir(path: Path) -> List[os.DirEntry]:
    try:
        with os.scandir(path) as entries:
            return sorted(entries, key=lambda entry: entry.name)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return []


def has_ffprobe() -> bool:
    global FFPROBE_AVAILABLE
    if FFPROBE_AVAILABLE is None:
        FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None
    return FFPROBE_AVAILABLE


def probe_duration_seconds(file_path: Path) -> Optional[float]:
    if not ENABLE_VIDEO_DURATION:
        return None
    if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    if not has_ffprobe():
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    output = (result.stdout or "").strip()
    if not output:
        return None

    try:
        return round(float(output), 3)
    except ValueError:
        return None


def get_scan_status() -> dict:
    with SCAN_LOCK:
        return dict(SCAN_STATE)


def get_duration_status() -> dict:
    with DURATION_LOCK:
        return dict(DURATION_STATE)


def update_scan_state(**kwargs: object) -> None:
    with SCAN_LOCK:
        for key, value in kwargs.items():
            SCAN_STATE[key] = value


def update_duration_state(**kwargs: object) -> None:
    with DURATION_LOCK:
        for key, value in kwargs.items():
            DURATION_STATE[key] = value


def flush_file_batch(connection: sqlite3.Connection, batch: List[tuple]) -> None:
    if not batch:
        return
    connection.executemany(
        """
        INSERT INTO indexed_files (
            vehicle,
            camera,
            capture_date,
            file_name,
            extension,
            relative_dir,
            relative_file_path,
            source_path,
            real_path,
            size_bytes,
            duration_seconds,
            modified_at,
            indexed_at,
            last_seen_scan_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(relative_file_path) DO UPDATE SET
            vehicle = excluded.vehicle,
            camera = excluded.camera,
            capture_date = excluded.capture_date,
            file_name = excluded.file_name,
            extension = excluded.extension,
            relative_dir = excluded.relative_dir,
            source_path = excluded.source_path,
            real_path = excluded.real_path,
            size_bytes = excluded.size_bytes,
            duration_seconds = CASE
                WHEN indexed_files.size_bytes IS excluded.size_bytes
                 AND indexed_files.modified_at IS excluded.modified_at
                THEN indexed_files.duration_seconds
                ELSE excluded.duration_seconds
            END,
            modified_at = excluded.modified_at,
            indexed_at = excluded.indexed_at,
            last_seen_scan_id = excluded.last_seen_scan_id
        """,
        batch,
    )
    connection.commit()
    batch.clear()


def flush_camera_batch(connection: sqlite3.Connection, batch: List[tuple]) -> None:
    if not batch:
        return
    connection.executemany(
        """
        INSERT INTO camera_inventory (
            vehicle,
            camera,
            source_dir,
            real_dir,
            indexed_at,
            last_seen_scan_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_dir) DO UPDATE SET
            vehicle = excluded.vehicle,
            camera = excluded.camera,
            real_dir = excluded.real_dir,
            indexed_at = excluded.indexed_at,
            last_seen_scan_id = excluded.last_seen_scan_id
        """,
        batch,
    )
    connection.commit()
    batch.clear()


def refresh_index(scan_id: str, full_refresh: bool = False) -> dict:
    ensure_database()
    started_at = iso_now()
    indexed_at = iso_now()
    total_files = 0
    total_cameras = 0

    with open_db() as connection:
        set_metadata(connection, "last_scan_started_at", started_at)
        set_metadata(connection, "last_scan_finished_at", "")
        set_metadata(connection, "last_scan_error", "")
        set_metadata(connection, "last_scan_mode", "full" if full_refresh else "incremental")
        set_metadata(connection, "last_scan_id", scan_id)
        set_metadata(connection, "image_root", str(IMAGE_ROOT))
        connection.commit()

        if full_refresh:
            connection.execute("DELETE FROM indexed_files")
            connection.execute("DELETE FROM camera_inventory")
            connection.commit()

        file_batch: List[tuple] = []
        camera_batch: List[tuple] = []
        seen_vehicle_targets = set()
        seen_camera_targets = set()
        seen_day_targets = set()

        for vehicle_entry in safe_scandir(IMAGE_ROOT):
            if not vehicle_entry.is_dir(follow_symlinks=True):
                continue

            vehicle_path = Path(vehicle_entry.path)
            vehicle_real = str(vehicle_path.resolve(strict=False))
            if vehicle_real in seen_vehicle_targets:
                continue
            seen_vehicle_targets.add(vehicle_real)

            vehicle_name = vehicle_entry.name
            update_scan_state(current_vehicle=vehicle_name, current_camera=None)

            for camera_entry in safe_scandir(vehicle_path):
                if not camera_entry.is_dir(follow_symlinks=True):
                    continue

                camera_path = Path(camera_entry.path)
                camera_real = str(camera_path.resolve(strict=False))
                if camera_real in seen_camera_targets:
                    continue
                seen_camera_targets.add(camera_real)

                camera_name = camera_entry.name
                total_cameras += 1
                camera_batch.append(
                    (
                        vehicle_name,
                        camera_name,
                        str(camera_path),
                        camera_real,
                        indexed_at,
                        scan_id,
                    )
                )
                update_scan_state(
                    cameras_scanned=total_cameras,
                    current_vehicle=vehicle_name,
                    current_camera=camera_name,
                )
                if len(camera_batch) >= INDEX_CAMERA_BATCH_SIZE:
                    flush_camera_batch(connection, camera_batch)

                for day_entry in safe_scandir(camera_path):
                    if not day_entry.is_dir(follow_symlinks=True):
                        continue
                    if not DATE_DIR_PATTERN.match(day_entry.name):
                        continue

                    day_path = Path(day_entry.path)
                    day_real = str(day_path.resolve(strict=False))
                    if day_real in seen_day_targets:
                        continue
                    seen_day_targets.add(day_real)

                    relative_dir = f"{vehicle_name}/{camera_name}/{day_entry.name}"

                    for file_entry in safe_scandir(day_path):
                        if not file_entry.is_file(follow_symlinks=True):
                            continue
                        if not TIMESTAMP_FILE_PATTERN.match(file_entry.name):
                            continue

                        stat_info = None
                        try:
                            stat_info = file_entry.stat(follow_symlinks=True)
                        except (FileNotFoundError, PermissionError, OSError):
                            stat_info = None

                        file_source_path = file_entry.path
                        file_name = file_entry.name
                        total_files += 1
                        file_batch.append(
                            (
                                vehicle_name,
                                camera_name,
                                day_entry.name,
                                file_name,
                                os.path.splitext(file_name)[1].lower() or "[sem_ext]",
                                relative_dir,
                                f"{relative_dir}/{file_name}",
                                file_source_path,
                                str(Path(day_real) / file_name),
                                stat_info.st_size if stat_info else None,
                                None,
                                (
                                    datetime.fromtimestamp(stat_info.st_mtime).isoformat(timespec="seconds")
                                    if stat_info
                                    else None
                                ),
                                indexed_at,
                                scan_id,
                            )
                        )
                        if total_files % 250 == 0:
                            update_scan_state(
                                files_scanned=total_files,
                                cameras_scanned=total_cameras,
                                current_vehicle=vehicle_name,
                                current_camera=camera_name,
                            )
                        if len(file_batch) >= INDEX_FILE_BATCH_SIZE:
                            flush_file_batch(connection, file_batch)

            update_scan_state(vehicles_scanned=len(seen_vehicle_targets))

        flush_camera_batch(connection, camera_batch)
        flush_file_batch(connection, file_batch)

        deleted_files = connection.execute(
            "DELETE FROM indexed_files WHERE COALESCE(last_seen_scan_id, '') != ?",
            (scan_id,),
        ).rowcount
        deleted_cameras = connection.execute(
            "DELETE FROM camera_inventory WHERE COALESCE(last_seen_scan_id, '') != ?",
            (scan_id,),
        ).rowcount

        set_metadata(connection, "last_scan_started_at", started_at)
        set_metadata(connection, "last_scan_finished_at", iso_now())
        set_metadata(connection, "last_scan_total_files", str(total_files))
        set_metadata(connection, "last_scan_total_cameras", str(total_cameras))
        set_metadata(connection, "last_scan_deleted_files", str(deleted_files))
        set_metadata(connection, "last_scan_deleted_cameras", str(deleted_cameras))
        set_metadata(connection, "last_scan_mode", "full" if full_refresh else "incremental")
        set_metadata(connection, "last_scan_id", scan_id)
        set_metadata(connection, "image_root", str(IMAGE_ROOT))
        set_metadata(connection, "last_scan_error", "")
        connection.commit()

    return {
        "status": "ok",
        "scan_id": scan_id,
        "mode": "full" if full_refresh else "incremental",
        "started_at": started_at,
        "finished_at": iso_now(),
        "total_files": total_files,
        "total_cameras": total_cameras,
        "deleted_files": deleted_files,
        "deleted_cameras": deleted_cameras,
        "root": str(IMAGE_ROOT),
        "database": str(DB_PATH),
    }


def hydrate_missing_durations() -> dict:
    ensure_database()
    started_at = iso_now()

    if not ENABLE_VIDEO_DURATION:
        return {
            "status": "skipped",
            "message": "Coleta de duração desativada por configuração.",
            "started_at": started_at,
            "finished_at": iso_now(),
            "processed_files": 0,
            "updated_files": 0,
            "pending_files": 0,
        }

    if not has_ffprobe():
        return {
            "status": "skipped",
            "message": "ffprobe não encontrado no servidor.",
            "started_at": started_at,
            "finished_at": iso_now(),
            "processed_files": 0,
            "updated_files": 0,
            "pending_files": 0,
        }

    extension_placeholders = ", ".join("?" for _ in VIDEO_EXTENSIONS)
    processed_files = 0
    updated_files = 0

    with open_db() as connection:
        set_metadata(connection, "last_duration_started_at", started_at)
        set_metadata(connection, "last_duration_finished_at", "")
        set_metadata(connection, "last_duration_error", "")
        set_metadata(connection, "last_duration_status", "running")
        connection.commit()

        pending_rows = connection.execute(
            f"""
            SELECT id, source_path, file_name
            FROM indexed_files
            WHERE duration_seconds IS NULL
              AND LOWER(extension) IN ({extension_placeholders})
            ORDER BY id
            """,
            tuple(sorted(VIDEO_EXTENSIONS)),
        ).fetchall()

        update_duration_state(pending_files=len(pending_rows))
        update_batch: List[tuple] = []

        for row in pending_rows:
            if get_scan_status().get("running"):
                break

            processed_files += 1
            update_duration_state(
                processed_files=processed_files,
                current_file=row["file_name"],
            )

            duration_seconds = probe_duration_seconds(Path(row["source_path"]))
            if duration_seconds is not None:
                updated_files += 1
                update_batch.append((duration_seconds, row["id"]))

            if len(update_batch) >= 200:
                if get_scan_status().get("running"):
                    update_batch.clear()
                    break
                connection.executemany(
                    "UPDATE indexed_files SET duration_seconds = ? WHERE id = ?",
                    update_batch,
                )
                connection.commit()
                update_batch.clear()
                update_duration_state(updated_files=updated_files)

        if update_batch and not get_scan_status().get("running"):
            connection.executemany(
                "UPDATE indexed_files SET duration_seconds = ? WHERE id = ?",
                update_batch,
            )
            connection.commit()

        finished_at = iso_now()
        set_metadata(connection, "last_duration_started_at", started_at)
        set_metadata(connection, "last_duration_finished_at", finished_at)
        set_metadata(connection, "last_duration_error", "")
        set_metadata(connection, "last_duration_processed_files", str(processed_files))
        set_metadata(connection, "last_duration_updated_files", str(updated_files))
        set_metadata(connection, "last_duration_pending_files", str(len(pending_rows)))
        set_metadata(connection, "last_duration_status", "ok")
        connection.commit()

    return {
        "status": "ok",
        "started_at": started_at,
        "finished_at": iso_now(),
        "processed_files": processed_files,
        "updated_files": updated_files,
        "pending_files": processed_files,
    }


def run_duration_job() -> None:
    try:
        result = hydrate_missing_durations()
        with DURATION_LOCK:
            DURATION_STATE["running"] = False
            DURATION_STATE["finished_at"] = iso_now()
            DURATION_STATE["error"] = None if result["status"] != "error" else result.get("message")
            DURATION_STATE["processed_files"] = result.get("processed_files", 0)
            DURATION_STATE["updated_files"] = result.get("updated_files", 0)
            DURATION_STATE["pending_files"] = 0
            DURATION_STATE["current_file"] = None
    except Exception as exc:
        traceback.print_exc()
        try:
            with open_db() as connection:
                set_metadata(connection, "last_duration_finished_at", iso_now())
                set_metadata(connection, "last_duration_error", str(exc))
                set_metadata(connection, "last_duration_status", "error")
                connection.commit()
        except Exception:
            traceback.print_exc()
        with DURATION_LOCK:
            DURATION_STATE["running"] = False
            DURATION_STATE["finished_at"] = iso_now()
            DURATION_STATE["error"] = str(exc)
            DURATION_STATE["current_file"] = None


def start_duration_job() -> dict:
    if get_scan_status().get("running"):
        return {
            "status": "busy",
            "message": "Indexação em andamento; fila de duração aguardará a próxima janela.",
            "duration": get_duration_status(),
        }

    with DURATION_LOCK:
        if DURATION_STATE["running"]:
            return {
                "status": "busy",
                "message": "A fila de duração já está em andamento.",
                "duration": dict(DURATION_STATE),
            }

        DURATION_STATE["running"] = True
        DURATION_STATE["started_at"] = iso_now()
        DURATION_STATE["finished_at"] = None
        DURATION_STATE["error"] = None
        DURATION_STATE["processed_files"] = 0
        DURATION_STATE["updated_files"] = 0
        DURATION_STATE["pending_files"] = 0
        DURATION_STATE["current_file"] = None

    worker = threading.Thread(target=run_duration_job, daemon=True)
    worker.start()

    return {
        "status": "started",
        "message": "Fila de duração iniciada em background.",
        "duration": get_duration_status(),
    }


def run_scan_job(scan_id: str, full_refresh: bool) -> None:
    try:
        result = refresh_index(scan_id=scan_id, full_refresh=full_refresh)
        with SCAN_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished_at"] = iso_now()
            SCAN_STATE["error"] = None
            SCAN_STATE["last_result"] = result
        if ENABLE_VIDEO_DURATION:
            start_duration_job()
    except Exception as exc:
        traceback.print_exc()
        try:
            with open_db() as connection:
                set_metadata(connection, "last_scan_started_at", SCAN_STATE.get("started_at") or iso_now())
                set_metadata(connection, "last_scan_finished_at", iso_now())
                set_metadata(connection, "last_scan_error", str(exc))
                connection.commit()
        except Exception:
            traceback.print_exc()
        with SCAN_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished_at"] = iso_now()
            SCAN_STATE["error"] = str(exc)
            SCAN_STATE["last_result"] = None


def start_scan(full_refresh: bool = False) -> dict:
    with SCAN_LOCK:
        if SCAN_STATE["running"]:
            return {
                "status": "busy",
                "message": "Já existe uma indexação em andamento.",
                "scan": dict(SCAN_STATE),
            }


        scan_id = f"scan-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        SCAN_STATE["running"] = True
        SCAN_STATE["scan_id"] = scan_id
        SCAN_STATE["mode"] = "full" if full_refresh else "incremental"
        SCAN_STATE["started_at"] = iso_now()
        SCAN_STATE["finished_at"] = None
        SCAN_STATE["error"] = None
        SCAN_STATE["last_result"] = None
        SCAN_STATE["vehicles_scanned"] = 0
        SCAN_STATE["cameras_scanned"] = 0
        SCAN_STATE["files_scanned"] = 0
        SCAN_STATE["current_vehicle"] = None
        SCAN_STATE["current_camera"] = None

    worker = threading.Thread(
        target=run_scan_job,
        args=(scan_id, full_refresh),
        daemon=True,
    )
    worker.start()

    return {
        "status": "started",
        "message": "Indexação iniciada em background.",
        "scan": get_scan_status(),
    }


def run_periodic_scan_scheduler() -> None:
    while True:
        time.sleep(max(1, SCAN_INTERVAL_SECONDS))
        start_scan(full_refresh=False)


def run_periodic_duration_scheduler() -> None:
    while True:
        time.sleep(max(1, DURATION_INTERVAL_SECONDS))
        if ENABLE_VIDEO_DURATION and not get_scan_status().get("running"):
            start_duration_job()


def start_background_schedulers() -> None:
    if SCAN_INTERVAL_SECONDS > 0:
        threading.Thread(target=run_periodic_scan_scheduler, daemon=True).start()
    if ENABLE_VIDEO_DURATION and DURATION_INTERVAL_SECONDS > 0:
        threading.Thread(target=run_periodic_duration_scheduler, daemon=True).start()


def build_dashboard(query: Dict[str, List[str]]) -> dict:
    ensure_database()
    month, year = parse_month_year(query)
    month_start = f"{year:04d}-{month:02d}-01"
    next_month = month + 1
    next_month_year = year
    if next_month == 13:
        next_month = 1
        next_month_year += 1
    next_month_start = f"{next_month_year:04d}-{next_month:02d}-01"
    vehicle_filters = parse_multi_filter(query, "vehicle")
    camera_filters = parse_multi_filter(query, "camera")

    clauses = ["capture_date >= ?", "capture_date < ?"]
    params: List[str] = [month_start, next_month_start]

    if vehicle_filters:
        clauses.append(f"vehicle IN ({', '.join('?' for _ in vehicle_filters)})")
        params.extend(vehicle_filters)
    if camera_filters:
        clauses.append(f"camera IN ({', '.join('?' for _ in camera_filters)})")
        params.extend(camera_filters)

    where_sql = " AND ".join(clauses)
    metadata = get_metadata()

    with open_db() as connection:
        rows = connection.execute(
            f"""
            SELECT
                vehicle,
                camera,
                capture_date,
                COUNT(*) AS total,
                MAX(file_name) AS latest_file
            FROM indexed_files
            WHERE {where_sql}
            GROUP BY vehicle, camera, capture_date
            ORDER BY vehicle, camera, capture_date
            """,
            params,
        ).fetchall()

        timeline_rows = connection.execute(
            """
            SELECT DISTINCT capture_date
            FROM indexed_files
            WHERE capture_date >= ? AND capture_date < ?
            ORDER BY capture_date
            """,
            (month_start, next_month_start),
        ).fetchall()

        inventory_clauses = ["1 = 1"]
        inventory_params: List[str] = []
        if vehicle_filters:
            inventory_clauses.append(f"vehicle IN ({', '.join('?' for _ in vehicle_filters)})")
            inventory_params.extend(vehicle_filters)
        if camera_filters:
            inventory_clauses.append(f"camera IN ({', '.join('?' for _ in camera_filters)})")
            inventory_params.extend(camera_filters)

        inventory_row = connection.execute(
            f"""
            SELECT
                COUNT(DISTINCT vehicle || '|' || camera) AS total_inventory_pairs,
                COUNT(DISTINCT vehicle) AS total_inventory_vehicles
            FROM camera_inventory
            WHERE {" AND ".join(inventory_clauses)}
            """,
            inventory_params,
        ).fetchone()

        inventory_rows = connection.execute(
            f"""
            SELECT vehicle, camera
            FROM camera_inventory
            WHERE {" AND ".join(inventory_clauses)}
            ORDER BY vehicle, camera
            """,
            inventory_params,
        ).fetchall()

        last_capture_clauses = ["1 = 1"]
        last_capture_params: List[str] = []
        if vehicle_filters:
            last_capture_clauses.append(f"vehicle IN ({', '.join('?' for _ in vehicle_filters)})")
            last_capture_params.extend(vehicle_filters)
        if camera_filters:
            last_capture_clauses.append(f"camera IN ({', '.join('?' for _ in camera_filters)})")
            last_capture_params.extend(camera_filters)
        last_capture_rows = connection.execute(
            f"""
            SELECT vehicle, MAX(capture_date) AS last_capture_date
            FROM indexed_files
            WHERE {" AND ".join(last_capture_clauses)}
            GROUP BY vehicle
            """,
            last_capture_params,
        ).fetchall()

        fleet_row = connection.execute(
            "SELECT COUNT(DISTINCT vehicle) AS total_fleet FROM camera_inventory"
        ).fetchone()

        extension_rows = connection.execute(
            f"""
            SELECT extension, COUNT(*) AS total
            FROM indexed_files
            WHERE {where_sql}
            GROUP BY extension
            ORDER BY total DESC, extension ASC
            """,
            params,
        ).fetchall()

        vehicles = connection.execute(
            "SELECT DISTINCT vehicle FROM camera_inventory ORDER BY vehicle"
        ).fetchall()

        available_camera_clauses = ["1 = 1"]
        available_camera_params: List[str] = []
        if vehicle_filters:
            available_camera_clauses.append(f"vehicle IN ({', '.join('?' for _ in vehicle_filters)})")
            available_camera_params.extend(vehicle_filters)
        cameras = connection.execute(
            f"""
            SELECT DISTINCT camera
            FROM camera_inventory
            WHERE {" AND ".join(available_camera_clauses)}
            ORDER BY camera
            """,
            available_camera_params,
        ).fetchall()

    date_totals: Dict[str, int] = {}
    matrix: Dict[str, dict] = {}
    top_by_camera: Dict[Tuple[str, str], dict] = {}
    active_vehicles = set()
    active_pairs = set()
    active_days = set()
    total_files = 0
    latest_capture = None
    alert_threshold_date = (date.today() - timedelta(days=3)).isoformat()
    inventory_vehicles_set = set()
    last_capture_by_vehicle = {
        row["vehicle"]: row["last_capture_date"]
        for row in last_capture_rows
    }

    for row in inventory_rows:
        inventory_vehicles_set.add(row["vehicle"])
        matrix_key = row["vehicle"]
        if matrix_key not in matrix:
            matrix[matrix_key] = {
                "vehicle": row["vehicle"],
                "cameras": set(),
                "total": 0,
                "active_days": set(),
                "days": {},
                "latest_file": None,
            }
        matrix[matrix_key]["cameras"].add(row["camera"])

    for row in rows:
        capture_date = row["capture_date"]
        count = row["total"]
        total_files += count
        active_vehicles.add(row["vehicle"])
        active_pairs.add((row["vehicle"], row["camera"]))
        active_days.add(capture_date)
        latest_capture = max(latest_capture, row["latest_file"]) if latest_capture else row["latest_file"]
        date_totals[capture_date] = date_totals.get(capture_date, 0) + count

        top_key = (row["vehicle"], row["camera"])
        if top_key not in top_by_camera:
            top_by_camera[top_key] = {
                "vehicle": row["vehicle"],
                "camera": row["camera"],
                "total": 0,
                "active_days": set(),
                "latest_file": None,
            }
        top_by_camera[top_key]["total"] += count
        top_by_camera[top_key]["active_days"].add(capture_date)
        top_by_camera[top_key]["latest_file"] = (
            max(top_by_camera[top_key]["latest_file"], row["latest_file"])
            if top_by_camera[top_key]["latest_file"]
            else row["latest_file"]
        )

        matrix_key = row["vehicle"]
        if matrix_key not in matrix:
            matrix[matrix_key] = {
                "vehicle": row["vehicle"],
                "cameras": set(),
                "total": 0,
                "active_days": set(),
                "days": {},
                "latest_file": None,
            }

        matrix[matrix_key]["total"] += count
        matrix[matrix_key]["active_days"].add(capture_date)
        matrix[matrix_key]["cameras"].add(row["camera"])
        if capture_date not in matrix[matrix_key]["days"]:
            matrix[matrix_key]["days"][capture_date] = {
                "count": 0,
                "level": "none",
                "cameras": [],
            }
        matrix[matrix_key]["days"][capture_date]["count"] += count
        matrix[matrix_key]["days"][capture_date]["level"] = get_level(
            matrix[matrix_key]["days"][capture_date]["count"]
        )
        matrix[matrix_key]["days"][capture_date]["cameras"].append(
            {"name": row["camera"], "count": count}
        )
        matrix[matrix_key]["latest_file"] = max(
            matrix[matrix_key]["latest_file"], row["latest_file"]
        ) if matrix[matrix_key]["latest_file"] else row["latest_file"]

    today = date.today()
    for row in timeline_rows:
        date_totals.setdefault(row["capture_date"], 0)

    if year == today.year and month == today.month:
        date_totals.setdefault(today.isoformat(), 0)

    dates = sorted(date_totals.keys(), reverse=True)
    matrix_rows = []
    for row in matrix.values():
        matrix_rows.append(
            {
                "vehicle": row["vehicle"],
                "cameras": sorted(row["cameras"], key=vehicle_sort_key),
                "camera_count": len(row["cameras"]),
                "total": row["total"],
                "active_days": len(row["active_days"]),
                "days": {
                    capture_date: {
                        "count": day_data["count"],
                        "level": get_level(day_data["count"]),
                        "cameras": sorted(day_data["cameras"], key=lambda item: vehicle_sort_key(item["name"])),
                    }
                    for capture_date, day_data in row["days"].items()
                },
                "latest_file": row["latest_file"],
            }
        )

    matrix_rows = sorted(matrix_rows, key=lambda row: vehicle_sort_key(row["vehicle"]))
    alert_vehicles = sorted(
        [
            vehicle
            for vehicle in inventory_vehicles_set
            if not last_capture_by_vehicle.get(vehicle)
            or last_capture_by_vehicle[vehicle] <= alert_threshold_date
        ],
        key=vehicle_sort_key,
    )
    inventory_pairs = inventory_row["total_inventory_pairs"] if inventory_row else 0
    inventory_vehicles = inventory_row["total_inventory_vehicles"] if inventory_row else 0
    fleet_total = fleet_row["total_fleet"] if fleet_row else 0
    top_rows = sorted(
        (
            {
                "vehicle": row["vehicle"],
                "camera": row["camera"],
                "total": row["total"],
                "active_days": len(row["active_days"]),
                "latest_file": row["latest_file"],
            }
            for row in top_by_camera.values()
        ),
        key=lambda row: (-row["total"], vehicle_sort_key(row["vehicle"]), vehicle_sort_key(row["camera"])),
    )[:8]

    return {
        "generated_at": iso_now(),
        "root": str(IMAGE_ROOT),
        "database": str(DB_PATH),
        "filters": {
            "month": month,
            "year": year,
            "vehicles": vehicle_filters,
            "cameras": camera_filters,
        },
        "summary": {
            "vehicles": inventory_vehicles,
            "active_vehicles": len(active_vehicles),
            "alert_vehicles": alert_vehicles,
            "alert_vehicle_count": len(alert_vehicles),
            "alert_threshold_days": 3,
            "fleet_total": fleet_total,
            "cameras": len(active_pairs),
            "total_files": total_files,
            "days_with_files": len(active_days),
            "cameras_without_files": max(0, inventory_pairs - len(active_pairs)),
            "latest_capture": latest_capture,
        },
        "dates": dates,
        "daily_overview": [
            {
                "date": date,
                "total": date_totals[date],
                "active_cameras": sum(len(row["days"][date]["cameras"]) for row in matrix_rows if date in row["days"]),
            }
            for date in dates
        ],
        "rows": matrix_rows,
        "top_rows": top_rows,
        "extensions": {row["extension"]: row["total"] for row in extension_rows},
        "available_filters": {
            "vehicles": [row["vehicle"] for row in vehicles],
            "cameras": [row["camera"] for row in cameras],
        },
        "scan_info": {
            "last_scan_started_at": metadata.get("last_scan_started_at"),
            "last_scan_finished_at": metadata.get("last_scan_finished_at"),
            "last_scan_total_files": metadata.get("last_scan_total_files", "0"),
            "last_scan_total_cameras": metadata.get("last_scan_total_cameras", "0"),
            "last_scan_deleted_files": metadata.get("last_scan_deleted_files", "0"),
            "last_scan_deleted_cameras": metadata.get("last_scan_deleted_cameras", "0"),
            "last_scan_mode": metadata.get("last_scan_mode"),
            "last_scan_id": metadata.get("last_scan_id"),
            "last_scan_error": metadata.get("last_scan_error"),
            "current_status": get_scan_status(),
            "duration_status": {
                "last_duration_started_at": metadata.get("last_duration_started_at"),
                "last_duration_finished_at": metadata.get("last_duration_finished_at"),
                "last_duration_error": metadata.get("last_duration_error"),
                "last_duration_processed_files": metadata.get("last_duration_processed_files", "0"),
                "last_duration_updated_files": metadata.get("last_duration_updated_files", "0"),
                "last_duration_pending_files": metadata.get("last_duration_pending_files", "0"),
                "last_duration_status": metadata.get("last_duration_status"),
                "current_status": get_duration_status(),
            },
        },
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)

            if parsed.path == "/":
                return self.serve_file(TEMPLATE_DIR / "index.html", "text/html; charset=utf-8")
            if parsed.path == "/api/dashboard":
                query = parse_qs(parsed.query)
                return self.serve_json(build_dashboard(query))
            if parsed.path == "/api/rescan":
                query = parse_qs(parsed.query)
                full_refresh = query.get("full", ["0"])[0] == "1"
                status_code = HTTPStatus.ACCEPTED
                payload = start_scan(full_refresh=full_refresh)
                if payload["status"] == "busy":
                    status_code = HTTPStatus.CONFLICT
                return self.serve_json(payload, status_code=status_code)
            if parsed.path == "/api/scan-status":
                return self.serve_json(get_scan_status())
            if parsed.path == "/api/duration-status":
                return self.serve_json(get_duration_status())
            if parsed.path == "/health":
                return self.serve_json(
                    {
                        "status": "ok",
                        "generated_at": iso_now(),
                        "root": str(IMAGE_ROOT),
                        "root_exists": IMAGE_ROOT.exists(),
                        "database": str(DB_PATH),
                        "database_exists": DB_PATH.exists(),
                        "scan": get_scan_status(),
                        "duration": get_duration_status(),
                    }
                )
            if parsed.path.startswith("/static/"):
                relative_path = parsed.path[len("/static/"):]
                target = STATIC_DIR / relative_path
                return self.serve_static(target)

            self.send_error(HTTPStatus.NOT_FOUND, "Rota não encontrada")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f"[ERRO] Falha ao atender {self.path}: {exc}")
            traceback.print_exc()
            try:
                self.serve_json(
                    {
                        "status": "error",
                        "message": str(exc),
                        "path": self.path,
                        "generated_at": iso_now(),
                    },
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            except (BrokenPipeError, ConnectionResetError):
                return

    def log_message(self, format: str, *args) -> None:
        return

    def serve_json(self, payload: dict, status_code: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, file_path: Path, content_type: str) -> None:
        if not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Arquivo não encontrado")
            return

        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, file_path: Path) -> None:
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Arquivo estático não encontrado")
            return

        content_type = "text/plain; charset=utf-8"
        if file_path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"

        self.serve_file(file_path, content_type)


def main() -> None:
    ensure_database()
    start_background_schedulers()
    if AUTO_SCAN_ON_START:
        scan_payload = start_scan(full_refresh=False)
        print(scan_payload.get("message", "Indexação automática solicitada."))

    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"Dashboard disponível em http://{HOST}:{PORT}")
    print(f"Lendo imagens em: {IMAGE_ROOT}")
    print(f"Banco SQLite em: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
