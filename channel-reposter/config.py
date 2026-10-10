from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = [
    int(part)
    for part in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",")
    if part.isdigit()
]
SESSION_NAME = os.getenv("SESSION_NAME", "reposter_userbot").strip() or "reposter_userbot"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT.parent / "data" / "reposter.sqlite3")))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = (ROOT / DATABASE_PATH).resolve()

DEFAULT_INTERVAL_SECONDS = _int("DEFAULT_INTERVAL_SECONDS", 86400)
DEFAULT_POSTS_PER_CYCLE = _int("DEFAULT_POSTS_PER_CYCLE", 5)
DEFAULT_CATCHUP_SECONDS = _int("DEFAULT_CATCHUP_SECONDS", 5)
POST_DELAY_MIN = _float("POST_DELAY_MIN", 2)
POST_DELAY_MAX = _float("POST_DELAY_MAX", 6)
PASS_PUBLISH_LIMIT = _int("PASS_PUBLISH_LIMIT", 20)
WINDOW_TIMEOUT_SECONDS = _int("WINDOW_TIMEOUT_SECONDS", 180)
PASS_TIMEOUT_SECONDS = _int("PASS_TIMEOUT_SECONDS", 900)
SCHEDULER_TICK_SECONDS = _float("SCHEDULER_TICK_SECONDS", 5)
MAX_WINDOWS = _int("MAX_WINDOWS", 16)

SESSION_PATH = ROOT / f"{SESSION_NAME}.session"
LOCK_PATH = ROOT.parent / "data" / "reposter.lock"
