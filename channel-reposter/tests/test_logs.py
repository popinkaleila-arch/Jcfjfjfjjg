import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

from database import Window
from logs import caption_event, caption_note, caption_report, dump_report, flush_logs, load_report, setup_logging
from poster import Poster


def test_caption_report_empty() -> None:
    assert "ещё не было" in caption_report({})


def test_caption_report_roundtrip() -> None:
    payload = {
        "updated": 4,
        "same": 1,
        "skipped": 2,
        "failed": 0,
        "scanned": 20,
        "start_id": 100,
        "last_id": 3,
        "stopped": False,
        "errors": ["9: flood"],
    }
    text = caption_report(payload)
    assert "поставлено 4" in text
    assert "просмотрено 20" in text
    assert "100 → 3" in text
    assert "9: flood" in text
    assert "готова" in text
    assert load_report(dump_report(payload))["updated"] == 4
    assert load_report("не json") == {}
    assert load_report("[1]") == {}


def test_caption_report_stopped() -> None:
    text = caption_report({"updated": 1, "stopped": True, "start_id": 8, "last_id": 4, "scanned": 3})
    assert "остановлена" in text


def test_setup_logging_writes_caption_file(tmp_path: Path) -> None:
    path = setup_logging(tmp_path)
    again = setup_logging(tmp_path)
    assert again == path
    caption_event("@ch", 9, "ok", "подпись поставлена")
    caption_note("старт проверки")
    flush_logs()
    body = path.read_text(encoding="utf-8")
    assert "OK @ch/9" in body
    assert "старт проверки" in body
    assert (tmp_path / "bot.log").exists()
    caption = logging.getLogger("caption")
    matches = [handler for handler in caption.handlers if getattr(handler, "baseFilename", "").endswith("caption.log") and str(tmp_path) in getattr(handler, "baseFilename", "")]
    assert len(matches) == 1
    assert json.loads(dump_report({"a": 1}))["a"] == 1


def test_backfill_writes_caption_log(tmp_path: Path) -> None:
    path = setup_logging(tmp_path)

    class Editor:
        def __init__(self) -> None:
            self.edited: list[int] = []

        async def check_rights(self, chat: str):
            return SimpleNamespace(ok=True, detail="ok")

        async def edit_caption(self, chat: str, message_id: int, html: str, media_only: bool = False) -> str:
            if message_id != 2:
                raise RuntimeError("пост не найден")
            self.edited.append(message_id)
            return "описание обновлено ботом"

    class Db:
        async def record_post(self, **kwargs) -> None:
            return None

    window = Window(
        id=1,
        title="",
        source="@src",
        dest="@dest",
        last_msg_id=0,
        enabled=False,
        paused=True,
        interval_seconds=60,
        posts_per_cycle=5,
        clean_filter=True,
        caption="promo",
        next_run_at=0,
        queued=0,
        posted_total=0,
        bot_edit=True,
    )
    poster = Poster(Db(), SimpleNamespace(), Editor())
    poster.backfill_delay = 0
    result = asyncio.run(poster.backfill_via_bot(window, start_id=2, html="<b>promo</b>", limit=0))
    flush_logs()
    body = path.read_text(encoding="utf-8")
    assert result.updated == 1
    assert "старт @dest с id 2" in body
    assert "OK @dest/2" in body
    assert "MISS @dest/1" in body
    assert "последняя правка описаний: готова" in body
    assert "просмотрено" in body
