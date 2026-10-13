from __future__ import annotations

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_dir: Path) -> Path:
    """bot.log — весь процесс, caption.log — только правки описаний."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT, DATEFMT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(type(handler) is logging.StreamHandler for handler in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)
    bot_path = log_dir / "bot.log"
    if not _has_file(root, bot_path):
        bot_file = RotatingFileHandler(bot_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        bot_file.setFormatter(formatter)
        root.addHandler(bot_file)

    caption = logging.getLogger("caption")
    caption.setLevel(logging.INFO)
    caption.propagate = False
    caption_path = log_dir / "caption.log"
    if not _has_file(caption, caption_path):
        caption_file = RotatingFileHandler(caption_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        caption_file.setFormatter(formatter)
        caption.addHandler(caption_file)
    return caption_path


def caption_event(dest: str, message_id: int, kind: str, detail: str = "") -> None:
    _safe(logging.getLogger("caption"), "%s %s/%s %s", kind.upper(), dest, message_id, detail)


def caption_note(message: str) -> None:
    _safe(logging.getLogger("caption"), "%s", message)


def caption_report(payload: dict) -> str:
    if not payload:
        return "последняя правка описаний: ещё не было"
    state = "остановлена" if payload.get("stopped") else "готова"
    lines = [
        f"последняя правка описаний: {state}",
        (
            f"поставлено {payload.get('updated', 0)}, уже было {payload.get('same', 0)}, "
            f"пустых {payload.get('skipped', 0)}, ошибок {payload.get('failed', 0)}, "
            f"просмотрено {payload.get('scanned', 0)}"
        ),
        f"диапазон {payload.get('start_id', '—')} → {payload.get('last_id', '—')}",
    ]
    errors = payload.get("errors") or []
    if errors:
        lines.append("ошибки: " + "; ".join(str(item) for item in errors[:3]))
    return "\n".join(lines)


def dump_report(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def load_report(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def flush_logs() -> None:
    for logger in (logging.getLogger(), logging.getLogger("caption")):
        for handler in list(logger.handlers):
            try:
                handler.flush()
            except Exception:
                pass


def _safe(logger: logging.Logger, template: str, *args: object) -> None:
    try:
        logger.info(template, *args)
    except Exception:
        pass


def _has_file(logger: logging.Logger, path: Path) -> bool:
    target = os.path.abspath(path)
    return any(getattr(handler, "baseFilename", "") == target for handler in logger.handlers)
