import json
import os
import re
import sqlite3
import subprocess
import threading
import traceback
import shutil
import time
import calendar
import zipfile
import logging
from decimal import Decimal
from io import BytesIO
from datetime import date, datetime, timedelta
from html import escape as xml_escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

import config
from config import (
    ALERT_DAYS_WITHOUT_FILES,
    AUTO_SCAN_ON_START,
    CONFIG_FILE,
    DB_ENGINE,
    DB_PATH,
    DB_STATUS_TIMEOUT_SECONDS,
    DATE_DIR_REGEX,
    DASHBOARD_CACHE_SECONDS,
    DAY_LEVELS,
    DURATION_INTERVAL_SECONDS,
    DURATION_MAX_FILES_PER_RUN,
    DURATION_UPDATE_BATCH_SIZE,
    ENABLE_VIDEO_DURATION,
    EXPORT_XLSX_ENABLED,
    FFPROBE_BINARY,
    FFPROBE_TIMEOUT_SECONDS,
    HOST,
    IMAGE_ROOT,
    INDEX_CAMERA_BATCH_SIZE,
    INDEX_FILE_BATCH_SIZE,
    LOCAL_GARAGE,
    MYSQL_DB,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_PORT,
    MYSQL_USER,
    PORT,
    POSTGRES_DB,
    POSTGRES_DSN,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    REMOTE_EXPORT_BATCH_SIZE,
    REMOTE_FULL_SYNC_DAYS,
    REMOTE_FULL_SYNC_INTERVAL_SECONDS,
    REMOTE_REBUILD_SUMMARY_EACH_PAGE,
    REMOTE_GARAGES,
    REMOTE_HEALTH_INTERVAL_SECONDS,
    REMOTE_REQUEST_TIMEOUT_SECONDS,
    REMOTE_SYNC_DAYS,
    REMOTE_SYNC_INTERVAL_SECONDS,
    SCAN_INTERVAL_SECONDS,
    SCAN_PROGRESS_EVERY_FILES,
    SCAN_SORT_ENTRIES,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_CACHE_SIZE,
    SQLITE_JOURNAL_MODE,
    SQLITE_SYNCHRONOUS,
    SQLITE_TEMP_STORE,
    SQLITE_TIMEOUT_SECONDS,
    STATIC_DIR,
    TEMPLATE_DIR,
    TIMESTAMP_FILE_REGEX,
    TOP_ROWS_LIMIT,
    VIDEO_EXTENSIONS,
)

DATE_DIR_PATTERN = re.compile(DATE_DIR_REGEX)
TIMESTAMP_FILE_PATTERN = re.compile(TIMESTAMP_FILE_REGEX)
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "app.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
LOGGER = logging.getLogger("dashboard_imagens")

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
REMOTE_LOCK = threading.Lock()
REMOTE_GARAGE_STATE: Dict[str, dict] = {}
REMOTE_SYNC_LOCK = threading.Lock()
REMOTE_SYNC_STATE = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "current_garage": None,
    "current_step": None,
    "mode": None,
    "sync_days": None,
    "pages": 0,
    "imported_files": 0,
    "imported_cameras": 0,
    "error": None,
    "results": [],
}
FFPROBE_AVAILABLE: Optional[bool] = None
SCHEDULER_LOCK = threading.Lock()
SCHEDULERS_STARTED = set()
DASHBOARD_CACHE_LOCK = threading.Lock()
DASHBOARD_BUILD_LOCK = threading.Lock()
DASHBOARD_CACHE: Dict[str, Tuple[float, dict]] = {}

CONFIG_FIELDS = [
    {"name": "IMAGE_DASHBOARD_ROOT", "global": "IMAGE_ROOT", "type": "path", "group": "Arquivos", "label": "Diretorio de imagens", "live": True},
    {"name": "IMAGE_DASHBOARD_GARAGE", "global": "LOCAL_GARAGE", "type": "str", "group": "Arquivos", "label": "Garagem local", "live": True},
    {
        "name": "IMAGE_DASHBOARD_DB_ENGINE",
        "global": "DB_ENGINE",
        "type": "select",
        "group": "Banco",
        "label": "Banco de dados",
        "live": False,
        "options": [
            {"value": "sqlite", "label": "SQLite"},
            {"value": "postgres", "label": "PostgreSQL"},
            {"value": "mysql", "label": "MySQL (a implementar)"},
        ],
    },
    {"name": "IMAGE_DASHBOARD_DB", "global": "DB_PATH", "type": "path", "group": "Banco", "label": "Arquivo SQLite", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "sqlite"}},
    {"name": "IMAGE_DASHBOARD_POSTGRES_HOST", "global": "POSTGRES_HOST", "type": "str", "group": "Banco", "label": "PostgreSQL IP/host", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "postgres"}},
    {"name": "IMAGE_DASHBOARD_POSTGRES_PORT", "global": "POSTGRES_PORT", "type": "int", "group": "Banco", "label": "PostgreSQL porta", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "postgres"}},
    {"name": "IMAGE_DASHBOARD_POSTGRES_DB", "global": "POSTGRES_DB", "type": "str", "group": "Banco", "label": "PostgreSQL banco", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "postgres"}},
    {"name": "IMAGE_DASHBOARD_POSTGRES_USER", "global": "POSTGRES_USER", "type": "str", "group": "Banco", "label": "PostgreSQL usuario", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "postgres"}},
    {"name": "IMAGE_DASHBOARD_POSTGRES_PASSWORD", "global": "POSTGRES_PASSWORD", "type": "password", "group": "Banco", "label": "PostgreSQL senha", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "postgres"}},
    {"name": "IMAGE_DASHBOARD_MYSQL_HOST", "global": "MYSQL_HOST", "type": "str", "group": "Banco", "label": "MySQL IP/host", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "mysql"}},
    {"name": "IMAGE_DASHBOARD_MYSQL_PORT", "global": "MYSQL_PORT", "type": "int", "group": "Banco", "label": "MySQL porta", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "mysql"}},
    {"name": "IMAGE_DASHBOARD_MYSQL_DB", "global": "MYSQL_DB", "type": "str", "group": "Banco", "label": "MySQL banco", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "mysql"}},
    {"name": "IMAGE_DASHBOARD_MYSQL_USER", "global": "MYSQL_USER", "type": "str", "group": "Banco", "label": "MySQL usuario", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "mysql"}},
    {"name": "IMAGE_DASHBOARD_MYSQL_PASSWORD", "global": "MYSQL_PASSWORD", "type": "password", "group": "Banco", "label": "MySQL senha", "live": False, "depends_on": {"IMAGE_DASHBOARD_DB_ENGINE": "mysql"}},
    {"name": "IMAGE_DASHBOARD_HOST", "global": "HOST", "type": "str", "group": "Servidor", "label": "Host", "live": False},
    {"name": "IMAGE_DASHBOARD_PORT", "global": "PORT", "type": "int", "group": "Servidor", "label": "Porta", "live": False},
    {"name": "IMAGE_DASHBOARD_AUTO_SCAN", "global": "AUTO_SCAN_ON_START", "type": "bool", "group": "Indexacao", "label": "Indexar ao iniciar", "live": True},
    {"name": "IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS", "global": "SCAN_INTERVAL_SECONDS", "type": "int", "group": "Indexacao", "label": "Intervalo de indexacao (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_SCAN_SORT_ENTRIES", "global": "SCAN_SORT_ENTRIES", "type": "bool", "group": "Indexacao", "label": "Ordenar arquivos na varredura", "live": True},
    {"name": "IMAGE_DASHBOARD_INDEX_BATCH_SIZE", "global": "INDEX_FILE_BATCH_SIZE", "type": "int", "group": "Indexacao", "label": "Lote de arquivos", "live": True},
    {"name": "IMAGE_DASHBOARD_CAMERA_BATCH_SIZE", "global": "INDEX_CAMERA_BATCH_SIZE", "type": "int", "group": "Indexacao", "label": "Lote de cameras", "live": True},
    {"name": "IMAGE_DASHBOARD_SCAN_PROGRESS_EVERY_FILES", "global": "SCAN_PROGRESS_EVERY_FILES", "type": "int", "group": "Indexacao", "label": "Atualizar progresso a cada N arquivos", "live": True},
    {"name": "IMAGE_DASHBOARD_ENABLE_DURATION", "global": "ENABLE_VIDEO_DURATION", "type": "bool", "group": "Videos", "label": "Coletar duracao", "live": True},
    {"name": "IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS", "global": "DURATION_INTERVAL_SECONDS", "type": "int", "group": "Videos", "label": "Intervalo da fila de duracao (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_DURATION_UPDATE_BATCH_SIZE", "global": "DURATION_UPDATE_BATCH_SIZE", "type": "int", "group": "Videos", "label": "Lote de duracoes", "live": True},
    {"name": "IMAGE_DASHBOARD_DURATION_MAX_FILES_PER_RUN", "global": "DURATION_MAX_FILES_PER_RUN", "type": "int", "group": "Videos", "label": "Maximo de videos por rodada", "live": True},
    {"name": "IMAGE_DASHBOARD_VIDEO_EXTENSIONS", "global": "VIDEO_EXTENSIONS", "type": "csv_set", "group": "Videos", "label": "Extensoes de video", "live": True},
    {"name": "IMAGE_DASHBOARD_FFPROBE_BINARY", "global": "FFPROBE_BINARY", "type": "str", "group": "Videos", "label": "Binario ffprobe", "live": True},
    {"name": "IMAGE_DASHBOARD_FFPROBE_TIMEOUT_SECONDS", "global": "FFPROBE_TIMEOUT_SECONDS", "type": "int", "group": "Videos", "label": "Timeout ffprobe (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_GARAGES", "global": "REMOTE_GARAGES", "type": "str", "group": "Garagens remotas", "label": "Garagens remotas", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_HEALTH_INTERVAL_SECONDS", "global": "REMOTE_HEALTH_INTERVAL_SECONDS", "type": "int", "group": "Garagens remotas", "label": "Intervalo healthcheck (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_SYNC_INTERVAL_SECONDS", "global": "REMOTE_SYNC_INTERVAL_SECONDS", "type": "int", "group": "Garagens remotas", "label": "Intervalo sync recente (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_SYNC_DAYS", "global": "REMOTE_SYNC_DAYS", "type": "int", "group": "Garagens remotas", "label": "Dias da sync recente", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_FULL_SYNC_INTERVAL_SECONDS", "global": "REMOTE_FULL_SYNC_INTERVAL_SECONDS", "type": "int", "group": "Garagens remotas", "label": "Intervalo sync historico (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_FULL_SYNC_DAYS", "global": "REMOTE_FULL_SYNC_DAYS", "type": "int", "group": "Garagens remotas", "label": "Dias da sync historica", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_TIMEOUT_SECONDS", "global": "REMOTE_REQUEST_TIMEOUT_SECONDS", "type": "int", "group": "Garagens remotas", "label": "Timeout remoto (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_EXPORT_BATCH_SIZE", "global": "REMOTE_EXPORT_BATCH_SIZE", "type": "int", "group": "Garagens remotas", "label": "Lote export remoto", "live": True},
    {"name": "IMAGE_DASHBOARD_REMOTE_REBUILD_SUMMARY_EACH_PAGE", "global": "REMOTE_REBUILD_SUMMARY_EACH_PAGE", "type": "bool", "group": "Garagens remotas", "label": "Atualizar agregados a cada pagina", "live": True},
    {"name": "IMAGE_DASHBOARD_DATE_DIR_REGEX", "global": "DATE_DIR_REGEX", "type": "str", "group": "Padroes", "label": "Regex pasta de data", "live": True},
    {"name": "IMAGE_DASHBOARD_TIMESTAMP_FILE_REGEX", "global": "TIMESTAMP_FILE_REGEX", "type": "str", "group": "Padroes", "label": "Regex arquivo", "live": True},
    {"name": "IMAGE_DASHBOARD_DAY_LEVELS", "global": "DAY_LEVELS", "type": "day_levels", "group": "Relatorio", "label": "Niveis da matriz", "live": True},
    {"name": "IMAGE_DASHBOARD_ALERT_DAYS_WITHOUT_FILES", "global": "ALERT_DAYS_WITHOUT_FILES", "type": "int", "group": "Relatorio", "label": "Dias para alerta", "live": True},
    {"name": "IMAGE_DASHBOARD_TOP_ROWS_LIMIT", "global": "TOP_ROWS_LIMIT", "type": "int", "group": "Relatorio", "label": "Limite de destaques", "live": True},
    {"name": "IMAGE_DASHBOARD_CACHE_SECONDS", "global": "DASHBOARD_CACHE_SECONDS", "type": "int", "group": "Relatorio", "label": "Cache do dashboard (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_EXPORT_XLSX_ENABLED", "global": "EXPORT_XLSX_ENABLED", "type": "bool", "group": "Relatorio", "label": "Habilitar exportacao XLSX", "live": True},
    {"name": "IMAGE_DASHBOARD_SQLITE_TIMEOUT_SECONDS", "global": "SQLITE_TIMEOUT_SECONDS", "type": "int", "group": "SQLite", "label": "Timeout SQLite (s)", "live": True},
    {"name": "IMAGE_DASHBOARD_SQLITE_JOURNAL_MODE", "global": "SQLITE_JOURNAL_MODE", "type": "str", "group": "SQLite", "label": "Journal mode", "live": True},
    {"name": "IMAGE_DASHBOARD_SQLITE_SYNCHRONOUS", "global": "SQLITE_SYNCHRONOUS", "type": "str", "group": "SQLite", "label": "Synchronous", "live": True},
    {"name": "IMAGE_DASHBOARD_SQLITE_TEMP_STORE", "global": "SQLITE_TEMP_STORE", "type": "str", "group": "SQLite", "label": "Temp store", "live": True},
    {"name": "IMAGE_DASHBOARD_SQLITE_CACHE_SIZE", "global": "SQLITE_CACHE_SIZE", "type": "int", "group": "SQLite", "label": "Cache size", "live": True},
    {"name": "IMAGE_DASHBOARD_SQLITE_BUSY_TIMEOUT_MS", "global": "SQLITE_BUSY_TIMEOUT_MS", "type": "int", "group": "SQLite", "label": "Busy timeout (ms)", "live": True},
    {"name": "IMAGE_DASHBOARD_DB_STATUS_TIMEOUT_SECONDS", "global": "DB_STATUS_TIMEOUT_SECONDS", "type": "int", "group": "SQLite", "label": "Timeout db-status (s)", "live": True},
]


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def json_default(value: object) -> object:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def config_value_to_text(value: object, value_type: str) -> str:
    if value_type == "bool":
        return "1" if value else "0"
    if value_type == "csv_set":
        return ",".join(sorted(value)) if isinstance(value, set) else str(value)
    if value_type == "day_levels":
        return ",".join(f"{threshold}:{level}" for threshold, level in value)
    return str(value)


def parse_config_value(raw_value: object, value_type: str) -> object:
    text = str(raw_value).strip()
    if value_type == "bool":
        return text.lower() in {"1", "true", "yes", "on"}
    if value_type == "int":
        return int(text)
    if value_type == "path":
        return Path(text)
    if value_type == "csv_set":
        return {item.strip().lower() for item in text.split(",") if item.strip()}
    if value_type == "day_levels":
        levels = []
        for item in text.split(","):
            if ":" not in item:
                continue
            threshold, level = item.split(":", 1)
            levels.append((int(threshold.strip()), level.strip()))
        return tuple(sorted(levels))
    return text


def config_text_value(name: str, fallback: str = "") -> str:
    if name in config.CONFIG_OVERRIDES:
        return str(config.CONFIG_OVERRIDES[name]).strip()
    return os.getenv(name, fallback).strip()


def build_config_payload() -> dict:
    fields = []
    for field in CONFIG_FIELDS:
        value = globals()[field["global"]]
        display_value = config.CONFIG_OVERRIDES.get(field["name"])
        if display_value is None:
            display_value = config_value_to_text(value, field["type"])
        if field["name"] == "IMAGE_DASHBOARD_DB_ENGINE" and str(display_value).lower() == "postgresql":
            display_value = "postgres"
        fields.append(
            {
                "name": field["name"],
                "label": field["label"],
                "group": field["group"],
                "type": field["type"],
                "live": field["live"],
                "value": str(display_value),
                "options": field.get("options", []),
                "depends_on": field.get("depends_on", {}),
            }
        )
    return {
        "status": "ok",
        "generated_at": iso_now(),
        "config_file": str(CONFIG_FILE),
        "fields": fields,
    }


def save_config_overrides(values: Dict[str, str]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    config.CONFIG_OVERRIDES.clear()
    config.CONFIG_OVERRIDES.update(values)


def update_config_payload(payload: dict) -> dict:
    incoming = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(incoming, dict):
        raise ValueError("Payload invalido. Envie {'values': {...}}.")

    requested_engine = str(
        incoming.get(
            "IMAGE_DASHBOARD_DB_ENGINE",
            config_text_value("IMAGE_DASHBOARD_DB_ENGINE", DB_ENGINE),
        )
    ).strip().lower()
    if requested_engine == "postgresql":
        requested_engine = "postgres"
    if requested_engine not in {"sqlite", "postgres", "mysql"}:
        raise ValueError("Banco de dados invalido. Use sqlite, postgres ou mysql.")
    if requested_engine == "mysql":
        raise ValueError("MySQL ainda nao esta implementado nesta versao. Use SQLite ou PostgreSQL.")
    if requested_engine == "postgres":
        postgres_dsn = config_text_value("IMAGE_DASHBOARD_POSTGRES_DSN", POSTGRES_DSN)
        required_postgres = {
            "IP/host": incoming.get("IMAGE_DASHBOARD_POSTGRES_HOST", config_text_value("IMAGE_DASHBOARD_POSTGRES_HOST", POSTGRES_HOST)),
            "Porta": incoming.get("IMAGE_DASHBOARD_POSTGRES_PORT", config_text_value("IMAGE_DASHBOARD_POSTGRES_PORT", str(POSTGRES_PORT))),
            "Nome do banco": incoming.get("IMAGE_DASHBOARD_POSTGRES_DB", config_text_value("IMAGE_DASHBOARD_POSTGRES_DB", POSTGRES_DB)),
            "Usuario": incoming.get("IMAGE_DASHBOARD_POSTGRES_USER", config_text_value("IMAGE_DASHBOARD_POSTGRES_USER", POSTGRES_USER)),
        }
        missing = [label for label, value in required_postgres.items() if not str(value or "").strip()]
        if missing and not postgres_dsn:
            raise ValueError(f"Para usar PostgreSQL, preencha: {', '.join(missing)}.")

    current_overrides = dict(config.CONFIG_OVERRIDES)
    applied = []
    restart_required = []

    for field in CONFIG_FIELDS:
        name = field["name"]
        if name not in incoming:
            continue
        raw_value = str(incoming[name]).strip()
        parsed_value = parse_config_value(raw_value, field["type"])
        current_overrides[name] = raw_value

        if field["live"]:
            globals()[field["global"]] = parsed_value
            applied.append(name)
        else:
            restart_required.append(name)

    global DATE_DIR_PATTERN, TIMESTAMP_FILE_PATTERN, FFPROBE_AVAILABLE
    DATE_DIR_PATTERN = re.compile(DATE_DIR_REGEX)
    TIMESTAMP_FILE_PATTERN = re.compile(TIMESTAMP_FILE_REGEX)
    FFPROBE_AVAILABLE = None

    save_config_overrides(current_overrides)
    invalidate_dashboard_cache()
    start_background_schedulers()
    return {
        "status": "ok",
        "generated_at": iso_now(),
        "config_file": str(CONFIG_FILE),
        "applied": applied,
        "restart_required": restart_required,
    }


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


def parse_remote_garages(value: Optional[str] = None) -> Dict[str, str]:
    if value is None:
        value = REMOTE_GARAGES
    garages: Dict[str, str] = {}
    for raw_item in value.split(";"):
        item = raw_item.strip()
        if not item or ":" not in item:
            continue
        name, base_url = item.split(":", 1)
        name = name.strip()
        base_url = base_url.strip().rstrip("/")
        if name and base_url:
            garages[name] = base_url
    return garages


def build_month_dates(month: int, year: int) -> List[str]:
    today = date.today()
    last_day = calendar.monthrange(year, month)[1]
    end_day = last_day
    if year == today.year and month == today.month:
        end_day = today.day

    return [
        date(year, month, day).isoformat()
        for day in range(end_day, 0, -1)
    ]


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


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def rowcount(self) -> int:
        return self.cursor.rowcount

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.connection.rollback()
        self.connection.close()

    def execute(self, sql: str, params=()):
        cursor = self.connection.cursor()
        cursor.execute(sql.replace("?", "%s"), params or ())
        return PostgresCursor(cursor)

    def executemany(self, sql: str, params):
        cursor = self.connection.cursor()
        cursor.executemany(sql.replace("?", "%s"), params)
        return PostgresCursor(cursor)

    def executescript(self, sql: str) -> None:
        cursor = self.connection.cursor()
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                cursor.execute(statement)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()


def is_postgres() -> bool:
    return DB_ENGINE in {"postgres", "postgresql"}


def is_mysql() -> bool:
    return DB_ENGINE in {"mysql", "mariadb"}


def open_db(timeout_seconds: int = SQLITE_TIMEOUT_SECONDS):
    if is_mysql():
        raise RuntimeError("MySQL ainda nao esta implementado nesta versao. Configure SQLite ou PostgreSQL.")
    if is_postgres():
        if psycopg2 is None:
            raise RuntimeError("Instale psycopg2-binary para usar PostgreSQL: pip install psycopg2-binary")
        if not POSTGRES_DSN:
            raise RuntimeError("Configure host, porta, banco e usuario do PostgreSQL na pagina /config.")
        connection = psycopg2.connect(POSTGRES_DSN, connect_timeout=max(1, int(timeout_seconds)), cursor_factory=RealDictCursor)
        return PostgresConnection(connection)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=timeout_seconds)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
    connection.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}")
    connection.execute(f"PRAGMA temp_store={SQLITE_TEMP_STORE}")
    connection.execute(f"PRAGMA cache_size={SQLITE_CACHE_SIZE}")
    connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    return connection


def ensure_column(connection, table_name: str, column_name: str, column_sql: str) -> None:
    if is_postgres():
        columns = {
            row["column_name"]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                """,
                (table_name,),
            ).fetchall()
        }
    else:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def ensure_database() -> None:
    with open_db() as connection:
        if is_postgres():
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS indexed_files (
                    id BIGSERIAL PRIMARY KEY,
                    garage TEXT NOT NULL DEFAULT 'G1',
                    vehicle TEXT NOT NULL,
                    camera TEXT NOT NULL,
                    capture_date TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    relative_dir TEXT NOT NULL,
                    relative_file_path TEXT NOT NULL UNIQUE,
                    source_path TEXT NOT NULL,
                    real_path TEXT NOT NULL,
                    size_bytes BIGINT,
                    duration_seconds DOUBLE PRECISION,
                    modified_at TEXT,
                    indexed_at TEXT NOT NULL,
                    last_seen_scan_id TEXT
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
                    id BIGSERIAL PRIMARY KEY,
                    garage TEXT NOT NULL DEFAULT 'G1',
                    vehicle TEXT NOT NULL,
                    camera TEXT NOT NULL,
                    source_dir TEXT NOT NULL UNIQUE,
                    real_dir TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    last_seen_scan_id TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_camera_inventory_vehicle_camera
                    ON camera_inventory (vehicle, camera);

                CREATE INDEX IF NOT EXISTS idx_camera_inventory_camera_vehicle
                    ON camera_inventory (camera, vehicle);

                CREATE TABLE IF NOT EXISTS app_metadata (
                    meta_key TEXT PRIMARY KEY,
                    meta_value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vehicle_camera_day_summary (
                    garage TEXT NOT NULL,
                    vehicle TEXT NOT NULL,
                    camera TEXT NOT NULL,
                    capture_date TEXT NOT NULL,
                    total_files BIGINT NOT NULL DEFAULT 0,
                    total_size_bytes BIGINT NOT NULL DEFAULT 0,
                    latest_file TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (garage, vehicle, camera, capture_date)
                );
                """
            )
        else:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS indexed_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    garage TEXT NOT NULL DEFAULT 'G1',
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
                    garage TEXT NOT NULL DEFAULT 'G1',
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

                CREATE TABLE IF NOT EXISTS vehicle_camera_day_summary (
                    garage TEXT NOT NULL,
                    vehicle TEXT NOT NULL,
                    camera TEXT NOT NULL,
                    capture_date TEXT NOT NULL,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    total_size_bytes INTEGER NOT NULL DEFAULT 0,
                    latest_file TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (garage, vehicle, camera, capture_date)
                );
                """
            )
        ensure_column(connection, "indexed_files", "garage", "TEXT NOT NULL DEFAULT 'G1'")
        ensure_column(connection, "indexed_files", "last_seen_scan_id", "TEXT")
        ensure_column(connection, "indexed_files", "duration_seconds", "DOUBLE PRECISION" if is_postgres() else "REAL")
        ensure_column(connection, "camera_inventory", "garage", "TEXT NOT NULL DEFAULT 'G1'")
        ensure_column(connection, "camera_inventory", "last_seen_scan_id", "TEXT")
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_vehicle_camera_day_summary_month
                ON vehicle_camera_day_summary (capture_date, vehicle, camera);

            CREATE INDEX IF NOT EXISTS idx_vehicle_camera_day_summary_garage_month
                ON vehicle_camera_day_summary (garage, capture_date, vehicle, camera);

            CREATE INDEX IF NOT EXISTS idx_vehicle_camera_day_summary_vehicle_capture
                ON vehicle_camera_day_summary (vehicle, capture_date);

            CREATE INDEX IF NOT EXISTS idx_indexed_files_garage_month
                ON indexed_files (garage, capture_date, vehicle, camera);

            CREATE INDEX IF NOT EXISTS idx_indexed_files_garage_vehicle_capture
                ON indexed_files (garage, vehicle, capture_date);

            CREATE INDEX IF NOT EXISTS idx_indexed_files_garage_capture_id
                ON indexed_files (garage, capture_date, id);

            CREATE INDEX IF NOT EXISTS idx_indexed_files_duration_queue
                ON indexed_files (duration_seconds, extension, id);

            CREATE INDEX IF NOT EXISTS idx_camera_inventory_garage_vehicle_camera
                ON camera_inventory (garage, vehicle, camera);
            """
        )
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


def invalidate_dashboard_cache() -> None:
    with DASHBOARD_CACHE_LOCK:
        DASHBOARD_CACHE.clear()


def dashboard_cache_key(query: Dict[str, List[str]]) -> str:
    normalized = [
        (key, tuple(sorted(str(value) for value in values)))
        for key, values in sorted(query.items())
        if not key.startswith("_")
    ]
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def summary_has_rows(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT 1 FROM vehicle_camera_day_summary LIMIT 1").fetchone()
    return row is not None


def rebuild_summary_for_dates(connection: sqlite3.Connection, garage: str, dates: set) -> None:
    if not dates:
        return
    updated_at = iso_now()
    for capture_date in sorted(dates):
        connection.execute(
            """
            DELETE FROM vehicle_camera_day_summary
            WHERE garage = ?
              AND capture_date = ?
            """,
            (garage, capture_date),
        )
        connection.execute(
            """
            INSERT INTO vehicle_camera_day_summary (
                garage,
                vehicle,
                camera,
                capture_date,
                total_files,
                total_size_bytes,
                latest_file,
                updated_at
            )
            SELECT
                garage,
                vehicle,
                camera,
                capture_date,
                COUNT(*) AS total_files,
                COALESCE(SUM(size_bytes), 0) AS total_size_bytes,
                MAX(file_name) AS latest_file,
                ? AS updated_at
            FROM indexed_files
            WHERE garage = ?
              AND capture_date = ?
            GROUP BY garage, vehicle, camera, capture_date
            """,
            (updated_at, garage, capture_date),
        )


def rebuild_summary_for_range(connection: sqlite3.Connection, garage: str, start_date: str, end_date: str) -> None:
    updated_at = iso_now()
    connection.execute(
        """
        DELETE FROM vehicle_camera_day_summary
        WHERE garage = ?
          AND capture_date >= ?
          AND capture_date < ?
        """,
        (garage, start_date, end_date),
    )
    connection.execute(
        """
        INSERT INTO vehicle_camera_day_summary (
            garage,
            vehicle,
            camera,
            capture_date,
            total_files,
            total_size_bytes,
            latest_file,
            updated_at
        )
        SELECT
            garage,
            vehicle,
            camera,
            capture_date,
            COUNT(*) AS total_files,
            COALESCE(SUM(size_bytes), 0) AS total_size_bytes,
            MAX(file_name) AS latest_file,
            ? AS updated_at
        FROM indexed_files
        WHERE garage = ?
          AND capture_date >= ?
          AND capture_date < ?
        GROUP BY garage, vehicle, camera, capture_date
        """,
        (updated_at, garage, start_date, end_date),
    )


def bootstrap_summary_if_needed() -> None:
    try:
        with open_db() as connection:
            metadata = {
                row["meta_key"]: row["meta_value"]
                for row in connection.execute("SELECT meta_key, meta_value FROM app_metadata").fetchall()
            }
            if metadata.get("summary_bootstrapped") == "1" and summary_has_rows(connection):
                return
            print("Montando agregados iniciais do dashboard...")
            updated_at = iso_now()
            connection.execute("DELETE FROM vehicle_camera_day_summary")
            connection.execute(
                """
                INSERT INTO vehicle_camera_day_summary (
                    garage,
                    vehicle,
                    camera,
                    capture_date,
                    total_files,
                    total_size_bytes,
                    latest_file,
                    updated_at
                )
                SELECT
                    garage,
                    vehicle,
                    camera,
                    capture_date,
                    COUNT(*) AS total_files,
                    COALESCE(SUM(size_bytes), 0) AS total_size_bytes,
                    MAX(file_name) AS latest_file,
                    ? AS updated_at
                FROM indexed_files
                GROUP BY garage, vehicle, camera, capture_date
                """,
                (updated_at,),
            )
            set_metadata(connection, "summary_bootstrapped", "1")
            connection.commit()
            invalidate_dashboard_cache()
            print("Agregados iniciais do dashboard prontos.")
    except Exception as exc:
        LOGGER.exception("Falha ao montar agregados iniciais")
        print(f"[ERRO] Falha ao montar agregados iniciais: {exc}")


def safe_scandir(path: Path) -> List[os.DirEntry]:
    try:
        with os.scandir(path) as entries:
            items = list(entries)
            if SCAN_SORT_ENTRIES:
                items.sort(key=lambda entry: entry.name)
            return items
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return []


def has_ffprobe() -> bool:
    global FFPROBE_AVAILABLE
    if FFPROBE_AVAILABLE is None:
        FFPROBE_AVAILABLE = shutil.which(FFPROBE_BINARY) is not None
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
                FFPROBE_BINARY,
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
            timeout=FFPROBE_TIMEOUT_SECONDS,
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
    same_file_condition = (
        "indexed_files.size_bytes IS NOT DISTINCT FROM excluded.size_bytes "
        "AND indexed_files.modified_at IS NOT DISTINCT FROM excluded.modified_at"
        if is_postgres()
        else "indexed_files.size_bytes IS excluded.size_bytes "
        "AND indexed_files.modified_at IS excluded.modified_at"
    )
    connection.executemany(
        f"""
        INSERT INTO indexed_files (
            garage,
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(relative_file_path) DO UPDATE SET
            garage = excluded.garage,
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
                WHEN {same_file_condition}
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
            garage,
            vehicle,
            camera,
            source_dir,
            real_dir,
            indexed_at,
            last_seen_scan_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_dir) DO UPDATE SET
            garage = excluded.garage,
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
            connection.execute("DELETE FROM indexed_files WHERE garage = ?", (LOCAL_GARAGE,))
            connection.execute("DELETE FROM camera_inventory WHERE garage = ?", (LOCAL_GARAGE,))
            connection.execute("DELETE FROM vehicle_camera_day_summary WHERE garage = ?", (LOCAL_GARAGE,))
            connection.commit()

        file_batch: List[tuple] = []
        camera_batch: List[tuple] = []
        seen_vehicle_targets = set()
        seen_camera_targets = set()
        seen_day_targets = set()
        seen_capture_dates = set()

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
                        LOCAL_GARAGE,
                        vehicle_name,
                        camera_name,
                        f"{LOCAL_GARAGE}:{camera_path}",
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
                    seen_capture_dates.add(day_entry.name)

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
                                LOCAL_GARAGE,
                                vehicle_name,
                                camera_name,
                                day_entry.name,
                                file_name,
                                os.path.splitext(file_name)[1].lower() or "[sem_ext]",
                                relative_dir,
                                    f"{LOCAL_GARAGE}/{relative_dir}/{file_name}",
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
                        if SCAN_PROGRESS_EVERY_FILES > 0 and total_files % SCAN_PROGRESS_EVERY_FILES == 0:
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
            "DELETE FROM indexed_files WHERE garage = ? AND COALESCE(last_seen_scan_id, '') != ?",
            (LOCAL_GARAGE, scan_id),
        ).rowcount
        deleted_cameras = connection.execute(
            "DELETE FROM camera_inventory WHERE garage = ? AND COALESCE(last_seen_scan_id, '') != ?",
            (LOCAL_GARAGE, scan_id),
        ).rowcount
        if seen_capture_dates:
            placeholders = ", ".join("?" for _ in seen_capture_dates)
            connection.execute(
                f"""
                DELETE FROM vehicle_camera_day_summary
                WHERE garage = ?
                  AND capture_date NOT IN ({placeholders})
                """,
                [LOCAL_GARAGE, *sorted(seen_capture_dates)],
            )
            rebuild_summary_for_dates(connection, LOCAL_GARAGE, seen_capture_dates)
        else:
            connection.execute(
                "DELETE FROM vehicle_camera_day_summary WHERE garage = ?",
                (LOCAL_GARAGE,),
            )

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
        invalidate_dashboard_cache()

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
            LIMIT ?
            """,
            (*tuple(sorted(VIDEO_EXTENSIONS)), max(1, DURATION_MAX_FILES_PER_RUN)),
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

            if DURATION_UPDATE_BATCH_SIZE > 0 and len(update_batch) >= DURATION_UPDATE_BATCH_SIZE:
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
        LOGGER.exception("Falha no job de duracao")
        traceback.print_exc()
        try:
            with open_db() as connection:
                set_metadata(connection, "last_duration_finished_at", iso_now())
                set_metadata(connection, "last_duration_error", str(exc))
                set_metadata(connection, "last_duration_status", "error")
                connection.commit()
        except Exception:
            LOGGER.exception("Falha ao registrar erro de duracao")
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
        LOGGER.exception("Falha no job de indexacao")
        traceback.print_exc()
        try:
            with open_db() as connection:
                set_metadata(connection, "last_scan_started_at", SCAN_STATE.get("started_at") or iso_now())
                set_metadata(connection, "last_scan_finished_at", iso_now())
                set_metadata(connection, "last_scan_error", str(exc))
                connection.commit()
        except Exception:
            LOGGER.exception("Falha ao registrar erro de indexacao")
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
        if SCAN_INTERVAL_SECONDS <= 0:
            time.sleep(1)
            continue
        time.sleep(max(1, SCAN_INTERVAL_SECONDS))
        if SCAN_INTERVAL_SECONDS > 0:
            start_scan(full_refresh=False)


def run_periodic_duration_scheduler() -> None:
    while True:
        if DURATION_INTERVAL_SECONDS <= 0:
            time.sleep(1)
            continue
        time.sleep(max(1, DURATION_INTERVAL_SECONDS))
        if ENABLE_VIDEO_DURATION and not get_scan_status().get("running"):
            start_duration_job()


def start_background_schedulers() -> None:
    with SCHEDULER_LOCK:
        if "summary_bootstrap" not in SCHEDULERS_STARTED:
            threading.Thread(target=bootstrap_summary_if_needed, daemon=True).start()
            SCHEDULERS_STARTED.add("summary_bootstrap")
        if "scan" not in SCHEDULERS_STARTED:
            threading.Thread(target=run_periodic_scan_scheduler, daemon=True).start()
            SCHEDULERS_STARTED.add("scan")
        if "duration" not in SCHEDULERS_STARTED:
            threading.Thread(target=run_periodic_duration_scheduler, daemon=True).start()
            SCHEDULERS_STARTED.add("duration")
        if parse_remote_garages() and "remote_health" not in SCHEDULERS_STARTED:
            threading.Thread(target=run_remote_health_scheduler, daemon=True).start()
            SCHEDULERS_STARTED.add("remote_health")
        if parse_remote_garages() and "remote_sync" not in SCHEDULERS_STARTED:
            threading.Thread(target=run_remote_sync_scheduler, daemon=True).start()
            SCHEDULERS_STARTED.add("remote_sync")
        if parse_remote_garages() and "remote_full_sync" not in SCHEDULERS_STARTED:
            threading.Thread(target=run_remote_full_sync_scheduler, daemon=True).start()
            SCHEDULERS_STARTED.add("remote_full_sync")


def parse_export_range(query: Dict[str, List[str]]) -> Tuple[str, str]:
    today = date.today()
    default_from = (today - timedelta(days=REMOTE_SYNC_DAYS)).isoformat()
    default_to = (today + timedelta(days=1)).isoformat()
    start_date = query.get("from", [default_from])[0]
    end_date = query.get("to", [default_to])[0]
    if not DATE_DIR_PATTERN.match(start_date):
        start_date = default_from
    if not DATE_DIR_PATTERN.match(end_date):
        end_date = default_to
    return start_date, end_date


def build_export_payload(query: Dict[str, List[str]]) -> dict:
    ensure_database()
    start_date, end_date = parse_export_range(query)
    limit = max(0, parse_int(query.get("limit", ["0"])[0], 0))
    offset = max(0, parse_int(query.get("offset", ["0"])[0], 0))
    after_date = query.get("after_date", [""])[0]
    after_id = max(0, parse_int(query.get("after_id", ["0"])[0], 0))
    include_cameras = query.get("include_cameras", ["1"])[0] != "0"
    file_sql = """
        SELECT
            id,
            garage,
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
        FROM indexed_files
        WHERE garage = ?
          AND capture_date >= ?
          AND capture_date < ?
    """
    file_params: List[object] = [LOCAL_GARAGE, start_date, end_date]
    if DATE_DIR_PATTERN.match(after_date) and after_id > 0:
        file_sql += " AND (capture_date > ? OR (capture_date = ? AND id > ?))"
        file_params.extend([after_date, after_date, after_id])
        offset = 0
    file_sql += " ORDER BY capture_date, id"
    if limit:
        file_sql += " LIMIT ? OFFSET ?"
        file_params.extend([limit + 1, offset])

    with open_db() as connection:
        file_rows = connection.execute(file_sql, file_params).fetchall()
        camera_rows = []
        if include_cameras:
            camera_rows = connection.execute(
                """
                SELECT
                    garage,
                    vehicle,
                    camera,
                    source_dir,
                    real_dir,
                    indexed_at,
                    last_seen_scan_id
                FROM camera_inventory
                WHERE garage = ?
                ORDER BY vehicle, camera
                """,
                (LOCAL_GARAGE,),
            ).fetchall()

    has_more = bool(limit and len(file_rows) > limit)
    if has_more:
        file_rows = file_rows[:limit]
    next_row = file_rows[-1] if has_more and file_rows else None

    return {
        "status": "ok",
        "garage": LOCAL_GARAGE,
        "generated_at": iso_now(),
        "from": start_date,
        "to": end_date,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(file_rows) if has_more else None,
        "next_after_date": next_row["capture_date"] if next_row else None,
        "next_after_id": next_row["id"] if next_row else None,
        "has_more": has_more,
        "files": [dict(row) for row in file_rows],
        "cameras": [dict(row) for row in camera_rows],
    }


def parse_report_date(query: Dict[str, List[str]]) -> str:
    selected_date = query.get("date", [date.today().isoformat()])[0]
    if not DATE_DIR_PATTERN.match(selected_date):
        raise ValueError("Data invalida para exportacao.")
    return selected_date


def report_status_for_count(total_files: int) -> str:
    if total_files == 0:
        return "inoperante"
    if total_files <= 400:
        return "descarregamento parcial"
    return "funcionando"


def build_daily_export_rows(selected_date: str) -> List[List[object]]:
    ensure_database()
    with open_db() as connection:
        inventory_rows = connection.execute(
            """
            SELECT vehicle, garage
            FROM camera_inventory
            ORDER BY vehicle, garage
            """
        ).fetchall()
        day_rows = connection.execute(
            """
            SELECT vehicle, garage, SUM(total_files) AS total_files
            FROM vehicle_camera_day_summary
            WHERE capture_date = ?
            GROUP BY vehicle, garage
            ORDER BY vehicle, garage
            """,
            (selected_date,),
        ).fetchall()
        if not day_rows:
            day_rows = connection.execute(
                """
                SELECT vehicle, garage, COUNT(*) AS total_files
                FROM indexed_files
                WHERE capture_date = ?
                GROUP BY vehicle, garage
                ORDER BY vehicle, garage
                """,
                (selected_date,),
            ).fetchall()

    vehicle_garages: Dict[str, set] = {}
    for row in inventory_rows:
        vehicle_garages.setdefault(row["vehicle"], set()).add(row["garage"])

    vehicle_day_totals: Dict[str, int] = {}
    vehicle_day_garages: Dict[str, set] = {}
    for row in day_rows:
        vehicle = row["vehicle"]
        total_files = int(row["total_files"] or 0)
        vehicle_day_totals[vehicle] = vehicle_day_totals.get(vehicle, 0) + total_files
        if total_files > 0:
            vehicle_day_garages.setdefault(vehicle, set()).add(row["garage"])
        vehicle_garages.setdefault(vehicle, set()).add(row["garage"])

    parsed_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
    display_date = f"{parsed_date.day}/{parsed_date.month}/{parsed_date.year}"
    vehicles = sorted(vehicle_garages.keys(), key=vehicle_sort_key)
    rows: List[List[object]] = []
    for vehicle in vehicles:
        total_files = vehicle_day_totals.get(vehicle, 0)
        garages = vehicle_day_garages.get(vehicle) or vehicle_garages.get(vehicle, set())
        rows.append(
            [
                vehicle,
                report_status_for_count(total_files),
                display_date,
                parsed_date.month,
                parsed_date.year,
                parsed_date.day,
                "",
                "/".join(sorted(garages, key=vehicle_sort_key)),
            ]
        )
    return rows


def xlsx_col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def xlsx_style_for_cell(row_index: int, col_index: int) -> int:
    if row_index == 1:
        return 1
    if 4 <= col_index <= 6:
        return 2
    return 3


def xlsx_cell(value: object, row_index: int, col_index: int) -> str:
    cell_ref = f"{xlsx_col_name(col_index)}{row_index}"
    style_id = xlsx_style_for_cell(row_index, col_index)
    if isinstance(value, int):
        return f'<c r="{cell_ref}" s="{style_id}"><v>{value}</v></c>'
    text = xml_escape(str(value or ""))
    return f'<c r="{cell_ref}" s="{style_id}" t="inlineStr"><is><t>{text}</t></is></c>'


def build_xlsx_workbook(headers: List[str], rows: List[List[object]], sheet_name: str = "Relatorio") -> bytes:
    all_rows = [headers, *rows]
    sheet_rows = []
    for row_index, row_values in enumerate(all_rows, start=1):
        cells = "".join(
            xlsx_cell(value, row_index, col_index)
            for col_index, value in enumerate(row_values, start=1)
        )
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')

    worksheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:H{len(all_rows)}"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="15"/>
  <cols>
    <col min="1" max="1" width="13" customWidth="1"/>
    <col min="2" max="2" width="28" customWidth="1"/>
    <col min="3" max="3" width="14" customWidth="1"/>
    <col min="4" max="6" width="9" customWidth="1"/>
    <col min="7" max="7" width="12" customWidth="1"/>
    <col min="8" max="8" width="13" customWidth="1"/>
  </cols>
  <sheetData>{''.join(sheet_rows)}</sheetData>
</worksheet>"""
    workbook = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="{xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFB6D7A8"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border>
      <left style="thin"><color rgb="FF000000"/></left>
      <right style="thin"><color rgb="FF000000"/></right>
      <top style="thin"><color rgb="FF000000"/></top>
      <bottom style="thin"><color rgb="FF000000"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="2" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""

    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()


def build_daily_xlsx_export(query: Dict[str, List[str]]) -> Tuple[str, bytes]:
    if not EXPORT_XLSX_ENABLED:
        raise PermissionError("Exportacao XLSX desabilitada.")
    selected_date = parse_report_date(query)
    headers = ["Carro", "Status", "Data", "Mes", "Ano", "Dia", "", "Garagem"]
    rows = build_daily_export_rows(selected_date)
    filename = f"relatorio_cores_{selected_date}.xlsx"
    return filename, build_xlsx_workbook(headers, rows)


def fetch_remote_json(base_url: str, path: str, query: Optional[Dict[str, str]] = None) -> dict:
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    try:
        with urlopen(url, timeout=REMOTE_REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)
    except TimeoutError as exc:
        raise TimeoutError(f"Timeout ao chamar {url}") from exc
    except OSError as exc:
        raise OSError(f"Falha ao chamar {url}: {exc}") from exc


def update_remote_garage_state(
    garage: str,
    status: str,
    error: Optional[str] = None,
    health: Optional[dict] = None,
) -> None:
    with REMOTE_LOCK:
        previous_state = REMOTE_GARAGE_STATE.get(garage, {})
        checked_at = iso_now()
        last_online_at = checked_at if status == "online" else previous_state.get("last_online_at")
        last_scan_finished_at = previous_state.get("last_scan_finished_at")
        if status == "online" and health:
            last_scan_finished_at = health.get("last_scan_finished_at") or last_scan_finished_at
        REMOTE_GARAGE_STATE[garage] = {
            "status": status,
            "checked_at": checked_at,
            "last_online_at": last_online_at,
            "last_scan_finished_at": last_scan_finished_at,
            "error": error,
        }


def update_remote_sync_state(**kwargs: object) -> None:
    with REMOTE_LOCK:
        for key, value in kwargs.items():
            REMOTE_SYNC_STATE[key] = value


def get_remote_sync_state() -> dict:
    with REMOTE_LOCK:
        return dict(REMOTE_SYNC_STATE)


def upsert_remote_export(
    payload: dict,
    expected_garage: str,
    sync_id: str,
    prune: bool = True,
    rebuild_summary: bool = True,
    invalidate_cache: bool = True,
) -> dict:
    exported_garage = payload.get("garage") or expected_garage
    files = payload.get("files") or []
    cameras = payload.get("cameras") or []
    start_date = payload.get("from")
    end_date = payload.get("to")
    imported_files = 0
    imported_cameras = 0
    affected_dates = {
        str(row.get("capture_date"))
        for row in files
        if row.get("capture_date")
    }

    with open_db() as connection:
        camera_batch = []
        for row in cameras:
            garage = row.get("garage") or exported_garage
            if garage != expected_garage:
                garage = expected_garage
            camera_batch.append(
                (
                    garage,
                    row.get("vehicle"),
                    row.get("camera"),
                    row.get("source_dir") or f"{garage}:{row.get('vehicle')}/{row.get('camera')}",
                    row.get("real_dir") or "",
                    row.get("indexed_at") or iso_now(),
                    sync_id,
                )
            )
            imported_cameras += 1
            if len(camera_batch) >= INDEX_CAMERA_BATCH_SIZE:
                flush_camera_batch(connection, camera_batch)
        flush_camera_batch(connection, camera_batch)

        file_batch = []
        for row in files:
            garage = row.get("garage") or exported_garage
            if garage != expected_garage:
                garage = expected_garage
            file_batch.append(
                (
                    garage,
                    row.get("vehicle"),
                    row.get("camera"),
                    row.get("capture_date"),
                    row.get("file_name"),
                    row.get("extension") or "[sem_ext]",
                    row.get("relative_dir") or "",
                    row.get("relative_file_path") or f"{garage}/{row.get('vehicle')}/{row.get('camera')}/{row.get('capture_date')}/{row.get('file_name')}",
                    row.get("source_path") or "",
                    row.get("real_path") or row.get("source_path") or "",
                    row.get("size_bytes"),
                    row.get("duration_seconds"),
                    row.get("modified_at"),
                    row.get("indexed_at") or iso_now(),
                    sync_id,
                )
            )
            imported_files += 1
            if len(file_batch) >= INDEX_FILE_BATCH_SIZE:
                flush_file_batch(connection, file_batch)
        flush_file_batch(connection, file_batch)

        deleted_files = 0
        deleted_cameras = 0
        if prune and start_date and end_date:
            deleted_files = connection.execute(
                """
                DELETE FROM indexed_files
                WHERE garage = ?
                  AND capture_date >= ?
                  AND capture_date < ?
                  AND COALESCE(last_seen_scan_id, '') != ?
                """,
                (expected_garage, start_date, end_date, sync_id),
            ).rowcount
        if prune:
            deleted_cameras = connection.execute(
                """
                DELETE FROM camera_inventory
                WHERE garage = ?
                  AND COALESCE(last_seen_scan_id, '') != ?
                """,
                (expected_garage, sync_id),
            ).rowcount
        if rebuild_summary:
            if prune and start_date and end_date:
                rebuild_summary_for_range(connection, expected_garage, start_date, end_date)
            else:
                rebuild_summary_for_dates(connection, expected_garage, affected_dates)
        connection.commit()
        if invalidate_cache:
            invalidate_dashboard_cache()

    return {
        "garage": expected_garage,
        "imported_files": imported_files,
        "imported_cameras": imported_cameras,
        "deleted_files": deleted_files,
        "deleted_cameras": deleted_cameras,
    }


def prune_remote_sync(garage: str, sync_id: str, start_date: str, end_date: str) -> dict:
    with open_db() as connection:
        deleted_files = connection.execute(
            """
            DELETE FROM indexed_files
            WHERE garage = ?
              AND capture_date >= ?
              AND capture_date < ?
              AND COALESCE(last_seen_scan_id, '') != ?
            """,
            (garage, start_date, end_date, sync_id),
        ).rowcount
        deleted_cameras = connection.execute(
            """
            DELETE FROM camera_inventory
            WHERE garage = ?
              AND COALESCE(last_seen_scan_id, '') != ?
            """,
            (garage, sync_id),
        ).rowcount
        rebuild_summary_for_range(connection, garage, start_date, end_date)
        connection.commit()
        invalidate_dashboard_cache()
    return {"deleted_files": deleted_files, "deleted_cameras": deleted_cameras}


def get_remote_sync_range(sync_days: int) -> Tuple[str, str]:
    today = date.today()
    end_date = (today + timedelta(days=1)).isoformat()
    if sync_days <= 0:
        return "0001-01-01", end_date
    return (today - timedelta(days=sync_days)).isoformat(), end_date


def sync_remote_garage(garage: str, base_url: str, sync_days: int, mode: str) -> dict:
    sync_id = f"remote-{garage}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    start_date, end_date = get_remote_sync_range(sync_days)
    imported_files = 0
    imported_cameras = 0
    pages = 0
    try:
        update_remote_sync_state(current_garage=garage, current_step="healthcheck", mode=mode, sync_days=sync_days)
        health = fetch_remote_json(base_url, "/api/garage-health")
        if health.get("status") != "ok":
            raise RuntimeError(f"Health remoto inválido: {health}")
        update_remote_garage_state(garage, "online", health=health)
        offset = 0
        after_date = ""
        after_id = 0
        while True:
            update_remote_sync_state(current_garage=garage, current_step="export", pages=pages, mode=mode, sync_days=sync_days)
            export_query = {
                "from": start_date,
                "to": end_date,
                "limit": str(REMOTE_EXPORT_BATCH_SIZE),
                "include_cameras": "1" if not after_id and offset == 0 else "0",
            }
            if after_date and after_id:
                export_query["after_date"] = after_date
                export_query["after_id"] = str(after_id)
            else:
                export_query["offset"] = str(offset)
            payload = fetch_remote_json(base_url, "/api/export", export_query)
            update_remote_sync_state(current_garage=garage, current_step="import", mode=mode, sync_days=sync_days)
            result = upsert_remote_export(
                payload,
                garage,
                sync_id,
                prune=False,
                rebuild_summary=REMOTE_REBUILD_SUMMARY_EACH_PAGE,
                invalidate_cache=REMOTE_REBUILD_SUMMARY_EACH_PAGE,
            )
            imported_files += result["imported_files"]
            imported_cameras += result["imported_cameras"]
            pages += 1
            state = get_remote_sync_state()
            update_remote_sync_state(
                pages=pages,
                imported_files=int(state.get("imported_files") or 0) + result["imported_files"],
                imported_cameras=int(state.get("imported_cameras") or 0) + result["imported_cameras"],
            )
            if not payload.get("has_more"):
                break
            after_date = payload.get("next_after_date") or ""
            after_id = int(payload.get("next_after_id") or 0)
            if not after_date or not after_id:
                offset = int(payload.get("next_offset") or (offset + REMOTE_EXPORT_BATCH_SIZE))
        update_remote_sync_state(current_garage=garage, current_step="prune", mode=mode, sync_days=sync_days)
        prune_result = prune_remote_sync(garage, sync_id, start_date, end_date)
        update_remote_garage_state(garage, "online", health=health)
        return {
            "status": "ok",
            "garage": garage,
            "mode": mode,
            "sync_days": sync_days,
            "from": start_date,
            "to": end_date,
            "imported_files": imported_files,
            "imported_cameras": imported_cameras,
            "pages": pages,
            **prune_result,
        }
    except Exception as exc:
        LOGGER.exception("Falha ao sincronizar garagem remota %s", garage)
        update_remote_garage_state(garage, "offline", str(exc))
        return {"status": "error", "garage": garage, "mode": mode, "sync_days": sync_days, "error": str(exc)}


def sync_remote_garages_once(sync_days: int = REMOTE_SYNC_DAYS, mode: str = "recent") -> List[dict]:
    return [
        sync_remote_garage(garage, base_url, sync_days, mode)
        for garage, base_url in parse_remote_garages().items()
    ]


def check_remote_garage_health(garage: str, base_url: str) -> dict:
    try:
        health = fetch_remote_json(base_url, "/api/garage-health")
        if health.get("status") != "ok":
            raise RuntimeError(f"Health remoto invalido: {health}")
        update_remote_garage_state(garage, "online", health=health)
        return {"status": "online", "garage": garage}
    except Exception as exc:
        update_remote_garage_state(garage, "offline", str(exc))
        return {"status": "offline", "garage": garage, "error": str(exc)}


def check_remote_garages_health_once() -> List[dict]:
    return [
        check_remote_garage_health(garage, base_url)
        for garage, base_url in parse_remote_garages().items()
    ]


def run_remote_sync_job(sync_days: int = REMOTE_SYNC_DAYS, mode: str = "recent") -> dict:
    if not REMOTE_SYNC_LOCK.acquire(blocking=False):
        return {"status": "busy", "message": "Sincronizacao remota ja esta em andamento.", "sync": get_remote_sync_state()}

    started_at = iso_now()
    update_remote_sync_state(
        running=True,
        started_at=started_at,
        finished_at=None,
        current_garage=None,
        current_step="starting",
        mode=mode,
        sync_days=sync_days,
        pages=0,
        imported_files=0,
        imported_cameras=0,
        error=None,
        results=[],
    )
    try:
        results = sync_remote_garages_once(sync_days=sync_days, mode=mode)
        error = next((result.get("error") for result in results if result.get("status") == "error"), None)
        update_remote_sync_state(
            running=False,
            finished_at=iso_now(),
            current_garage=None,
            current_step="finished",
            error=error,
            results=results,
        )
        return {"status": "ok", "mode": mode, "sync_days": sync_days, "started_at": started_at, "finished_at": get_remote_sync_state().get("finished_at"), "results": results}
    except Exception as exc:
        update_remote_sync_state(
            running=False,
            finished_at=iso_now(),
            current_step="error",
            error=str(exc),
        )
        raise
    finally:
        REMOTE_SYNC_LOCK.release()


def start_remote_sync_job(sync_days: int = REMOTE_SYNC_DAYS, mode: str = "recent") -> dict:
    if get_remote_sync_state().get("running"):
        return {"status": "busy", "message": "Sincronizacao remota ja esta em andamento.", "sync": get_remote_sync_state()}
    thread = threading.Thread(target=run_remote_sync_job, kwargs={"sync_days": sync_days, "mode": mode}, daemon=True)
    thread.start()
    return {"status": "started", "message": "Sincronizacao remota iniciada em background.", "sync": get_remote_sync_state()}


def run_remote_sync_scheduler() -> None:
    while True:
        if REMOTE_SYNC_INTERVAL_SECONDS <= 0 or not parse_remote_garages():
            time.sleep(1)
            continue
        run_remote_sync_job(sync_days=REMOTE_SYNC_DAYS, mode="recent")
        time.sleep(max(1, REMOTE_SYNC_INTERVAL_SECONDS))


def run_remote_full_sync_scheduler() -> None:
    time.sleep(max(1, REMOTE_FULL_SYNC_INTERVAL_SECONDS))
    while True:
        if REMOTE_FULL_SYNC_INTERVAL_SECONDS <= 0 or not parse_remote_garages():
            time.sleep(1)
            continue
        run_remote_sync_job(sync_days=REMOTE_FULL_SYNC_DAYS, mode="historico")
        time.sleep(max(1, REMOTE_FULL_SYNC_INTERVAL_SECONDS))


def run_remote_health_scheduler() -> None:
    while True:
        if REMOTE_HEALTH_INTERVAL_SECONDS <= 0 or not parse_remote_garages():
            time.sleep(1)
            continue
        check_remote_garages_health_once()
        time.sleep(max(1, REMOTE_HEALTH_INTERVAL_SECONDS))


def get_garage_statuses(garages: List[str], local_last_scan_finished_at: Optional[str] = None) -> List[dict]:
    remote_config = parse_remote_garages()
    with REMOTE_LOCK:
        remote_state = dict(REMOTE_GARAGE_STATE)
        sync_state = dict(REMOTE_SYNC_STATE)

    statuses = []
    for garage in garages:
        if garage == LOCAL_GARAGE:
            statuses.append(
                {
                    "name": garage,
                    "status": "online",
                    "last_scan_finished_at": local_last_scan_finished_at,
                }
            )
            continue
        if sync_state.get("running") and sync_state.get("current_garage") == garage:
            statuses.append(
                {
                    "name": garage,
                    "status": "online",
                    "syncing": True,
                    "checked_at": sync_state.get("started_at"),
                    "last_online_at": remote_state.get(garage, {}).get("last_online_at"),
                    "last_scan_finished_at": remote_state.get(garage, {}).get("last_scan_finished_at"),
                    "step": sync_state.get("current_step"),
                    "mode": sync_state.get("mode"),
                    "pages": sync_state.get("pages"),
                    "imported_files": sync_state.get("imported_files"),
                }
            )
            continue
        state = remote_state.get(garage)
        if state:
            statuses.append(
                {
                    "name": garage,
                    "status": state.get("status", "offline"),
                    "checked_at": state.get("checked_at"),
                    "last_online_at": state.get("last_online_at"),
                    "last_scan_finished_at": state.get("last_scan_finished_at"),
                }
            )
        elif garage in remote_config:
            statuses.append({"name": garage, "status": "offline", "last_online_at": None})
        else:
            statuses.append({"name": garage, "status": "offline", "last_online_at": None})
    return statuses


def build_remote_status() -> dict:
    configured = parse_remote_garages()
    garage_names = sorted({LOCAL_GARAGE, *configured.keys()}, key=vehicle_sort_key)
    metadata = get_metadata()
    return {
        "status": "ok",
        "local_garage": LOCAL_GARAGE,
        "configured_remotes": configured,
        "remote_health_interval_seconds": REMOTE_HEALTH_INTERVAL_SECONDS,
        "remote_sync_interval_seconds": REMOTE_SYNC_INTERVAL_SECONDS,
        "remote_sync_days": REMOTE_SYNC_DAYS,
        "remote_full_sync_interval_seconds": REMOTE_FULL_SYNC_INTERVAL_SECONDS,
        "remote_full_sync_days": REMOTE_FULL_SYNC_DAYS,
        "remote_timeout_seconds": REMOTE_REQUEST_TIMEOUT_SECONDS,
        "remote_export_batch_size": REMOTE_EXPORT_BATCH_SIZE,
        "sync": get_remote_sync_state(),
        "garages": get_garage_statuses(garage_names, metadata.get("last_scan_finished_at")),
    }


def build_db_status() -> dict:
    started = time.perf_counter()
    with open_db(timeout_seconds=DB_STATUS_TIMEOUT_SECONDS) as connection:
        if is_postgres():
            database_row = connection.execute("SELECT current_database() AS database, current_schema() AS schema").fetchone()
            journal_mode = "postgres"
            busy_timeout = None
            database_list = [dict(database_row)] if database_row else []
        else:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            database_list = [dict(row) for row in connection.execute("PRAGMA database_list").fetchall()]
        file_counts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT garage, COUNT(*) AS files
                FROM indexed_files
                GROUP BY garage
                ORDER BY garage
                """
            ).fetchall()
        ]
        camera_counts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT garage, COUNT(*) AS cameras
                FROM camera_inventory
                GROUP BY garage
                ORDER BY garage
                """
            ).fetchall()
        ]
    return {
        "status": "ok",
        "generated_at": iso_now(),
        "engine": "postgres" if is_postgres() else "sqlite",
        "database": "postgres" if is_postgres() else str(DB_PATH),
        "postgres_dsn_configured": bool(POSTGRES_DSN) if is_postgres() else False,
        "journal_mode": journal_mode,
        "busy_timeout_ms": busy_timeout,
        "database_list": database_list,
        "file_counts": file_counts,
        "camera_counts": camera_counts,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def build_dashboard_payload(query: Dict[str, List[str]]) -> dict:
    ensure_database()
    month, year = parse_month_year(query)
    month_start = f"{year:04d}-{month:02d}-01"
    next_month = month + 1
    next_month_year = year
    if next_month == 13:
        next_month = 1
        next_month_year += 1
    next_month_start = f"{next_month_year:04d}-{next_month:02d}-01"
    garage_filters = parse_multi_filter(query, "garage")
    vehicle_filters = parse_multi_filter(query, "vehicle")
    camera_filters = parse_multi_filter(query, "camera")

    clauses = ["capture_date >= ?", "capture_date < ?"]
    params: List[str] = [month_start, next_month_start]

    if garage_filters:
        clauses.append(f"garage IN ({', '.join('?' for _ in garage_filters)})")
        params.extend(garage_filters)
    if vehicle_filters:
        clauses.append(f"vehicle IN ({', '.join('?' for _ in vehicle_filters)})")
        params.extend(vehicle_filters)
    if camera_filters:
        clauses.append(f"camera IN ({', '.join('?' for _ in camera_filters)})")
        params.extend(camera_filters)

    where_sql = " AND ".join(clauses)
    metadata = get_metadata()

    with open_db() as connection:
        use_summary = summary_has_rows(connection)
        rows = []
        if use_summary:
            rows = connection.execute(
                f"""
                SELECT
                    garage,
                    vehicle,
                    camera,
                    capture_date,
                    total_files AS total,
                    total_size_bytes,
                    latest_file
                FROM vehicle_camera_day_summary
                WHERE {where_sql}
                ORDER BY vehicle, camera, capture_date, garage
                """,
                params,
            ).fetchall()

        if not rows:
            use_summary = False
            rows = connection.execute(
                f"""
                SELECT
                    garage,
                    vehicle,
                    camera,
                    capture_date,
                    COUNT(*) AS total,
                    COALESCE(SUM(size_bytes), 0) AS total_size_bytes,
                    MAX(file_name) AS latest_file
                FROM indexed_files
                WHERE {where_sql}
                GROUP BY garage, vehicle, camera, capture_date
                ORDER BY vehicle, camera, capture_date, garage
                """,
                params,
            ).fetchall()

        inventory_clauses = ["1 = 1"]
        inventory_params: List[str] = []
        if garage_filters:
            inventory_clauses.append(f"garage IN ({', '.join('?' for _ in garage_filters)})")
            inventory_params.extend(garage_filters)
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
        if not inventory_rows:
            inventory_rows = connection.execute(
                f"""
                SELECT DISTINCT vehicle, camera
                FROM {'vehicle_camera_day_summary' if use_summary else 'indexed_files'}
                WHERE {where_sql}
                ORDER BY vehicle, camera
                """,
                params,
            ).fetchall()

        last_capture_clauses = ["1 = 1"]
        last_capture_params: List[str] = []
        if garage_filters:
            last_capture_clauses.append(f"garage IN ({', '.join('?' for _ in garage_filters)})")
            last_capture_params.extend(garage_filters)
        if vehicle_filters:
            last_capture_clauses.append(f"vehicle IN ({', '.join('?' for _ in vehicle_filters)})")
            last_capture_params.extend(vehicle_filters)
        if camera_filters:
            last_capture_clauses.append(f"camera IN ({', '.join('?' for _ in camera_filters)})")
            last_capture_params.extend(camera_filters)
        last_capture_rows = connection.execute(
            f"""
            SELECT vehicle, MAX(capture_date) AS last_capture_date
            FROM {'vehicle_camera_day_summary' if use_summary else 'indexed_files'}
            WHERE {" AND ".join(last_capture_clauses)}
            GROUP BY vehicle
            """,
            last_capture_params,
        ).fetchall()

        fleet_row = connection.execute(
            f"""
            SELECT COUNT(DISTINCT vehicle) AS total_fleet
            FROM camera_inventory
            WHERE {" AND ".join(["1 = 1"] + (
                [f"garage IN ({', '.join('?' for _ in garage_filters)})"] if garage_filters else []
            ))}
            """,
            garage_filters,
        ).fetchone()
        fleet_total = fleet_row["total_fleet"] if fleet_row else 0
        if fleet_total == 0:
            fleet_row = connection.execute(
                f"""
                SELECT COUNT(DISTINCT vehicle) AS total_fleet
                FROM {'vehicle_camera_day_summary' if use_summary else 'indexed_files'}
                WHERE {where_sql}
                """,
                params,
            ).fetchone()
            fleet_total = fleet_row["total_fleet"] if fleet_row else 0

        extension_rows = []

        vehicles = connection.execute(
            f"""
            SELECT DISTINCT vehicle
            FROM camera_inventory
            WHERE {" AND ".join(["1 = 1"] + (
                [f"garage IN ({', '.join('?' for _ in garage_filters)})"] if garage_filters else []
            ))}
            ORDER BY vehicle
            """,
            garage_filters,
        ).fetchall()
        if not vehicles:
            vehicles = connection.execute(
                f"""
                SELECT DISTINCT vehicle
                FROM {'vehicle_camera_day_summary' if use_summary else 'indexed_files'}
                WHERE {" AND ".join(["1 = 1"] + (
                    [f"garage IN ({', '.join('?' for _ in garage_filters)})"] if garage_filters else []
                ))}
                ORDER BY vehicle
                """,
                garage_filters,
            ).fetchall()

        available_camera_clauses = ["1 = 1"]
        available_camera_params: List[str] = []
        if garage_filters:
            available_camera_clauses.append(f"garage IN ({', '.join('?' for _ in garage_filters)})")
            available_camera_params.extend(garage_filters)
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
        if not cameras:
            cameras = connection.execute(
                f"""
                SELECT DISTINCT camera
                FROM {'vehicle_camera_day_summary' if use_summary else 'indexed_files'}
                WHERE {" AND ".join(available_camera_clauses)}
                ORDER BY camera
                """,
                available_camera_params,
            ).fetchall()

        garages = connection.execute(
            "SELECT DISTINCT garage FROM camera_inventory ORDER BY garage"
        ).fetchall()
        if not garages:
            garages = connection.execute(
                "SELECT DISTINCT garage FROM indexed_files ORDER BY garage"
            ).fetchall()

    date_totals: Dict[str, int] = {}
    date_sizes: Dict[str, int] = {}
    date_vehicles: Dict[str, set] = {}
    matrix: Dict[str, dict] = {}
    top_by_camera: Dict[Tuple[str, str], dict] = {}
    active_vehicles = set()
    active_pairs = set()
    active_days = set()
    total_files = 0
    latest_capture = None
    alert_threshold_date = (date.today() - timedelta(days=ALERT_DAYS_WITHOUT_FILES)).isoformat()
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
                "camera_totals": {},
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
        date_sizes[capture_date] = date_sizes.get(capture_date, 0) + (row["total_size_bytes"] or 0)
        date_vehicles.setdefault(capture_date, set()).add(row["vehicle"])

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
                "camera_totals": {},
                "total": 0,
                "active_days": set(),
                "days": {},
                "latest_file": None,
            }

        matrix[matrix_key]["total"] += count
        matrix[matrix_key]["active_days"].add(capture_date)
        matrix[matrix_key]["cameras"].add(row["camera"])
        camera_total = matrix[matrix_key]["camera_totals"].setdefault(
            row["camera"],
            {"name": row["camera"], "count": 0, "size_bytes": 0},
        )
        camera_total["count"] += count
        camera_total["size_bytes"] += row["total_size_bytes"] or 0
        if capture_date not in matrix[matrix_key]["days"]:
            matrix[matrix_key]["days"][capture_date] = {
                "count": 0,
                "level": "none",
                "cameras": {},
                "garages": {},
            }
        matrix[matrix_key]["days"][capture_date]["count"] += count
        matrix[matrix_key]["days"][capture_date]["level"] = get_level(
            matrix[matrix_key]["days"][capture_date]["count"]
        )
        camera_day = matrix[matrix_key]["days"][capture_date]["cameras"].setdefault(
            row["camera"],
            {"name": row["camera"], "count": 0},
        )
        camera_day["count"] += count
        garage_day = matrix[matrix_key]["days"][capture_date]["garages"].setdefault(
            row["garage"],
            {"name": row["garage"], "count": 0, "cameras": []},
        )
        garage_day["count"] += count
        garage_day["cameras"].append({"name": row["camera"], "count": count})
        matrix[matrix_key]["latest_file"] = max(
            matrix[matrix_key]["latest_file"], row["latest_file"]
        ) if matrix[matrix_key]["latest_file"] else row["latest_file"]

    dates = build_month_dates(month, year)
    for capture_date in dates:
        date_totals.setdefault(capture_date, 0)
        date_sizes.setdefault(capture_date, 0)
        date_vehicles.setdefault(capture_date, set())

    matrix_rows = []
    for row in matrix.values():
        matrix_rows.append(
            {
                "vehicle": row["vehicle"],
                "cameras": sorted(row["cameras"], key=vehicle_sort_key),
                "camera_totals": sorted(
                    row["camera_totals"].values(),
                    key=lambda item: vehicle_sort_key(item["name"]),
                ),
                "camera_count": len(row["cameras"]),
                "total": row["total"],
                "active_days": len(row["active_days"]),
                "days": {
                    capture_date: {
                        "count": day_data["count"],
                        "level": get_level(day_data["count"]),
                        "cameras": sorted(
                            day_data["cameras"].values(),
                            key=lambda item: vehicle_sort_key(item["name"]),
                        ),
                        "garage_names": sorted(day_data["garages"].keys(), key=vehicle_sort_key),
                        "garages": [
                            {
                                "name": garage_data["name"],
                                "count": garage_data["count"],
                                "cameras": sorted(
                                    garage_data["cameras"],
                                    key=lambda item: vehicle_sort_key(item["name"]),
                                ),
                            }
                            for garage_data in sorted(
                                day_data["garages"].values(),
                                key=lambda item: vehicle_sort_key(item["name"]),
                            )
                        ],
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
    )[:TOP_ROWS_LIMIT]
    available_garage_names = sorted(
        {LOCAL_GARAGE, *parse_remote_garages().keys(), *[row["garage"] for row in garages]},
        key=vehicle_sort_key,
    )

    return {
        "generated_at": iso_now(),
        "root": str(IMAGE_ROOT),
        "database": str(DB_PATH),
        "filters": {
            "month": month,
            "year": year,
            "garages": garage_filters,
            "vehicles": vehicle_filters,
            "cameras": camera_filters,
        },
        "summary": {
            "vehicles": inventory_vehicles,
            "active_vehicles": len(active_vehicles),
            "alert_vehicles": alert_vehicles,
            "alert_vehicle_count": len(alert_vehicles),
            "alert_threshold_days": ALERT_DAYS_WITHOUT_FILES,
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
                "vehicles": len(date_vehicles[date]),
                "total_size_bytes": date_sizes[date],
            }
            for date in dates
        ],
        "rows": matrix_rows,
        "top_rows": top_rows,
        "extensions": {row["extension"]: row["total"] for row in extension_rows},
        "available_filters": {
            "garages": available_garage_names,
            "vehicles": [row["vehicle"] for row in vehicles],
            "cameras": [row["camera"] for row in cameras],
        },
        "features": {
            "export_xlsx_enabled": EXPORT_XLSX_ENABLED,
        },
        "garage_status": get_garage_statuses(available_garage_names, metadata.get("last_scan_finished_at")),
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


def build_dashboard(query: Dict[str, List[str]]) -> dict:
    if query.get("_live", ["0"])[0] == "1":
        return build_dashboard_payload(query)
    if DASHBOARD_CACHE_SECONDS <= 0:
        return build_dashboard_payload(query)

    cache_key = dashboard_cache_key(query)
    now = time.time()
    with DASHBOARD_CACHE_LOCK:
        cached = DASHBOARD_CACHE.get(cache_key)
        if cached and now - cached[0] < DASHBOARD_CACHE_SECONDS:
            return cached[1]

    with DASHBOARD_BUILD_LOCK:
        now = time.time()
        with DASHBOARD_CACHE_LOCK:
            cached = DASHBOARD_CACHE.get(cache_key)
            if cached and now - cached[0] < DASHBOARD_CACHE_SECONDS:
                return cached[1]
        payload = build_dashboard_payload(query)
        with DASHBOARD_CACHE_LOCK:
            DASHBOARD_CACHE[cache_key] = (time.time(), payload)
    return payload


def dashboard_section(query: Dict[str, List[str]], section: str) -> dict:
    dashboard = build_dashboard(query)
    payload = {
        "status": "ok",
        "generated_at": dashboard["generated_at"],
        "filters": dashboard["filters"],
    }
    if section == "summary":
        payload["summary"] = dashboard["summary"]
        payload["garage_status"] = dashboard["garage_status"]
        payload["scan_info"] = dashboard["scan_info"]
    elif section == "daily-overview":
        payload["dates"] = dashboard["dates"]
        payload["daily_overview"] = dashboard["daily_overview"]
    elif section == "top-cameras":
        payload["top_rows"] = dashboard["top_rows"]
    elif section == "matrix":
        payload["dates"] = dashboard["dates"]
        payload["rows"] = dashboard["rows"]
        payload["fleet_total"] = dashboard["summary"]["fleet_total"]
    elif section == "filters":
        payload["available_filters"] = dashboard["available_filters"]
    elif section == "garage-status":
        payload = {
            "status": "ok",
            "generated_at": iso_now(),
            "garage_status": build_remote_status()["garages"],
        }
    else:
        raise ValueError(f"Secao desconhecida: {section}")
    return payload


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)

            if parsed.path == "/":
                return self.serve_file(TEMPLATE_DIR / "index.html", "text/html; charset=utf-8")
            if parsed.path in {"/config", "/config/"}:
                return self.serve_file(TEMPLATE_DIR / "config.html", "text/html; charset=utf-8")
            if parsed.path == "/api/config":
                return self.serve_json(build_config_payload())
            if parsed.path == "/api/dashboard":
                query = parse_qs(parsed.query)
                return self.serve_json(build_dashboard(query))
            if parsed.path in {
                "/api/summary",
                "/api/daily-overview",
                "/api/top-cameras",
                "/api/matrix",
                "/api/filters",
                "/api/garage-status",
            }:
                query = parse_qs(parsed.query)
                section = parsed.path.removeprefix("/api/")
                return self.serve_json(dashboard_section(query, section))
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
            if parsed.path == "/api/garage-health":
                metadata = get_metadata()
                return self.serve_json(
                    {
                        "status": "ok",
                        "garage": LOCAL_GARAGE,
                        "last_scan_started_at": metadata.get("last_scan_started_at"),
                        "last_scan_finished_at": metadata.get("last_scan_finished_at"),
                        "generated_at": iso_now(),
                    }
                )
            if parsed.path == "/api/export":
                query = parse_qs(parsed.query)
                return self.serve_json(build_export_payload(query))
            if parsed.path == "/api/export-xlsx":
                query = parse_qs(parsed.query)
                try:
                    filename, body = build_daily_xlsx_export(query)
                except PermissionError as exc:
                    return self.serve_json({"status": "error", "message": str(exc)}, status_code=HTTPStatus.FORBIDDEN)
                except ValueError as exc:
                    return self.serve_json({"status": "error", "message": str(exc)}, status_code=HTTPStatus.BAD_REQUEST)
                return self.serve_bytes(
                    body,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename=filename,
                )
            if parsed.path == "/api/remote-sync":
                query = parse_qs(parsed.query)
                full_sync = query.get("full", ["0"])[0] == "1" or query.get("mode", ["recent"])[0] in {"full", "historico"}
                sync_days = REMOTE_FULL_SYNC_DAYS if full_sync else REMOTE_SYNC_DAYS
                sync_mode = "historico" if full_sync else "recent"
                if query.get("wait", ["0"])[0] == "1":
                    return self.serve_json(run_remote_sync_job(sync_days=sync_days, mode=sync_mode))
                payload = start_remote_sync_job(sync_days=sync_days, mode=sync_mode)
                status_code = HTTPStatus.ACCEPTED if payload["status"] == "started" else HTTPStatus.CONFLICT
                return self.serve_json(
                    {**payload, "generated_at": iso_now()},
                    status_code=status_code,
                )
            if parsed.path == "/api/remote-status":
                return self.serve_json(build_remote_status())
            if parsed.path == "/api/remote-health":
                return self.serve_json(
                    {
                        "status": "ok",
                        "generated_at": iso_now(),
                        "results": check_remote_garages_health_once(),
                    }
                )
            if parsed.path == "/api/db-status":
                return self.serve_json(build_db_status())
            if parsed.path == "/health":
                return self.serve_json(
                    {
                        "status": "ok",
                        "garage": LOCAL_GARAGE,
                        "generated_at": iso_now(),
                        "root": str(IMAGE_ROOT),
                        "root_exists": IMAGE_ROOT.exists(),
                        "database_engine": "postgres" if is_postgres() else "sqlite",
                        "database": "postgres" if is_postgres() else str(DB_PATH),
                        "database_exists": True if is_postgres() else DB_PATH.exists(),
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
            LOGGER.exception("Falha ao atender %s", self.path)
            print(f"[ERRO] Falha ao atender {self.path}: {exc}")
            try:
                self.serve_json(
                    {
                        "status": "error",
                        "message": "Falha ao carregar banco de dados.",
                        "path": self.path,
                        "generated_at": iso_now(),
                    },
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/api/config":
                payload = self.read_json_body()
                return self.serve_json(update_config_payload(payload))
            self.send_error(HTTPStatus.NOT_FOUND, "Rota nÃ£o encontrada")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            LOGGER.exception("Falha ao atender %s", self.path)
            print(f"[ERRO] Falha ao atender {self.path}: {exc}")
            try:
                self.serve_json(
                    {
                        "status": "error",
                        "message": "Falha ao carregar banco de dados.",
                        "path": self.path,
                        "generated_at": iso_now(),
                    },
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            except (BrokenPipeError, ConnectionResetError):
                return

    def log_message(self, format: str, *args) -> None:
        return

    def read_json_body(self) -> dict:
        content_length = parse_int(self.headers.get("Content-Length", "0"), 0)
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        return json.loads(raw_body.decode("utf-8") or "{}")

    def serve_json(self, payload: dict, status_code: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
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
        self.serve_bytes(body, content_type)

    def serve_bytes(self, body: bytes, content_type: str, filename: Optional[str] = None) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
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
    if is_postgres():
        print("Banco PostgreSQL configurado.")
    else:
        print(f"Banco SQLite em: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
