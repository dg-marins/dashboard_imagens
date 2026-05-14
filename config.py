import json
import os
from pathlib import Path
from typing import List, Tuple


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = Path(os.getenv("IMAGE_DASHBOARD_CONFIG_FILE", str(BASE_DIR / "dashboard_config.json")))


def load_config_overrides() -> dict:
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


CONFIG_OVERRIDES = load_config_overrides()


def setting(name: str, default: str) -> str:
    if name in CONFIG_OVERRIDES:
        return str(CONFIG_OVERRIDES[name])
    return os.getenv(name, default)


def env_bool(name: str, default: str = "0") -> bool:
    return setting(name, default).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: str) -> int:
    try:
        return int(setting(name, default))
    except (TypeError, ValueError):
        return int(default)


def env_list(name: str, default: str) -> List[str]:
    raw = setting(name, default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def env_day_levels(name: str, default: str) -> Tuple[Tuple[int, str], ...]:
    levels = []
    raw = setting(name, default)
    for item in raw.split(","):
        if ":" not in item:
            continue
        threshold, level = item.split(":", 1)
        try:
            levels.append((int(threshold.strip()), level.strip()))
        except ValueError:
            continue
    return tuple(sorted(levels)) or tuple()


STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"

IMAGE_ROOT = Path(setting("IMAGE_DASHBOARD_ROOT", "/home/publico/imagens"))
LOCAL_GARAGE = setting("IMAGE_DASHBOARD_GARAGE", "G1").strip() or "G1"
DB_PATH = Path(setting("IMAGE_DASHBOARD_DB", str(BASE_DIR / "dashboard_imagens.db")))

HOST = setting("IMAGE_DASHBOARD_HOST", "0.0.0.0")
PORT = env_int("IMAGE_DASHBOARD_PORT", "8081")

AUTO_SCAN_ON_START = env_bool("IMAGE_DASHBOARD_AUTO_SCAN", "1")
ENABLE_VIDEO_DURATION = env_bool("IMAGE_DASHBOARD_ENABLE_DURATION", "1")

SCAN_INTERVAL_SECONDS = env_int("IMAGE_DASHBOARD_SCAN_INTERVAL_SECONDS", "300")
DURATION_INTERVAL_SECONDS = env_int("IMAGE_DASHBOARD_DURATION_INTERVAL_SECONDS", "300")

REMOTE_GARAGES = setting("IMAGE_DASHBOARD_REMOTE_GARAGES", "").strip()
REMOTE_HEALTH_INTERVAL_SECONDS = env_int("IMAGE_DASHBOARD_REMOTE_HEALTH_INTERVAL_SECONDS", "120")
REMOTE_SYNC_INTERVAL_SECONDS = env_int("IMAGE_DASHBOARD_REMOTE_SYNC_INTERVAL_SECONDS", "180")
REMOTE_SYNC_DAYS = env_int("IMAGE_DASHBOARD_REMOTE_SYNC_DAYS", "30")
REMOTE_FULL_SYNC_INTERVAL_SECONDS = env_int("IMAGE_DASHBOARD_REMOTE_FULL_SYNC_INTERVAL_SECONDS", "3600")
REMOTE_FULL_SYNC_DAYS = env_int("IMAGE_DASHBOARD_REMOTE_FULL_SYNC_DAYS", "0")
REMOTE_REQUEST_TIMEOUT_SECONDS = env_int("IMAGE_DASHBOARD_REMOTE_TIMEOUT_SECONDS", "60")
REMOTE_EXPORT_BATCH_SIZE = env_int("IMAGE_DASHBOARD_REMOTE_EXPORT_BATCH_SIZE", "1000")

INDEX_FILE_BATCH_SIZE = env_int("IMAGE_DASHBOARD_INDEX_BATCH_SIZE", "5000")
INDEX_CAMERA_BATCH_SIZE = env_int("IMAGE_DASHBOARD_CAMERA_BATCH_SIZE", "1000")
SCAN_PROGRESS_EVERY_FILES = env_int("IMAGE_DASHBOARD_SCAN_PROGRESS_EVERY_FILES", "250")
DURATION_UPDATE_BATCH_SIZE = env_int("IMAGE_DASHBOARD_DURATION_UPDATE_BATCH_SIZE", "200")

DATE_DIR_REGEX = setting("IMAGE_DASHBOARD_DATE_DIR_REGEX", r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_FILE_REGEX = setting("IMAGE_DASHBOARD_TIMESTAMP_FILE_REGEX", r"^\d{14}(?:\..+)?$")
VIDEO_EXTENSIONS = set(env_list("IMAGE_DASHBOARD_VIDEO_EXTENSIONS", ".mp4,.avi,.mkv,.mov,.wmv,.flv,.webm,.mpeg,.mpg,.m4v"))
DAY_LEVELS = env_day_levels("IMAGE_DASHBOARD_DAY_LEVELS", "0:none,1:low,25:medium,100:high")
ALERT_DAYS_WITHOUT_FILES = env_int("IMAGE_DASHBOARD_ALERT_DAYS_WITHOUT_FILES", "3")
TOP_ROWS_LIMIT = env_int("IMAGE_DASHBOARD_TOP_ROWS_LIMIT", "8")

SQLITE_TIMEOUT_SECONDS = env_int("IMAGE_DASHBOARD_SQLITE_TIMEOUT_SECONDS", "30")
SQLITE_JOURNAL_MODE = setting("IMAGE_DASHBOARD_SQLITE_JOURNAL_MODE", "WAL")
SQLITE_SYNCHRONOUS = setting("IMAGE_DASHBOARD_SQLITE_SYNCHRONOUS", "NORMAL")
SQLITE_TEMP_STORE = setting("IMAGE_DASHBOARD_SQLITE_TEMP_STORE", "MEMORY")
SQLITE_CACHE_SIZE = env_int("IMAGE_DASHBOARD_SQLITE_CACHE_SIZE", "-64000")
SQLITE_BUSY_TIMEOUT_MS = env_int("IMAGE_DASHBOARD_SQLITE_BUSY_TIMEOUT_MS", str(SQLITE_TIMEOUT_SECONDS * 1000))
DB_STATUS_TIMEOUT_SECONDS = env_int("IMAGE_DASHBOARD_DB_STATUS_TIMEOUT_SECONDS", "1")

FFPROBE_BINARY = setting("IMAGE_DASHBOARD_FFPROBE_BINARY", "ffprobe")
FFPROBE_TIMEOUT_SECONDS = env_int("IMAGE_DASHBOARD_FFPROBE_TIMEOUT_SECONDS", "1")
