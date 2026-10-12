import asyncio
from types import SimpleNamespace

from database import Window
from poster import Poster, _iter_ids


def make_window() -> Window:
    return Window(
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
        caption="<b>promo</b>",
        next_run_at=0,
        queued=0,
        posted_total=0,
        bot_edit=True,
    )


class MemoryDb:
    def __init__(self, ids=None) -> None:
        self.ids = list(ids or [])
        self.posts = []

    async def list_dest_message_ids(self, dest: str) -> list[int]:
        return list(self.ids)

    async def record_post(self, **kwargs) -> None:
        self.posts.append(kwargs)


class FakeEditor:
    def __init__(self, existing=None, skip=None) -> None:
        self.existing = set(existing or [])
        self.skip = set(skip or [])
        self.edited: list[int] = []

    async def check_rights(self, chat: str):
        return SimpleNamespace(ok=True, detail="ok")

    async def edit_caption(self, chat: str, message_id: int, html: str) -> str:
        if message_id in self.skip:
            raise RuntimeError("у этого куска альбома нет поля подписи")
        if message_id not in self.existing:
            raise RuntimeError("пост не найден")
        self.edited.append(message_id)
        return "описание обновлено ботом"


class StoppingEditor(FakeEditor):
    def __init__(self, poster_slot: list, after: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.poster_slot = poster_slot
        self.after = after

    async def edit_caption(self, chat: str, message_id: int, html: str) -> str:
        if len(self.edited) >= self.after:
            self.poster_slot[0].stop_backfill.set()
        return await super().edit_caption(chat, message_id, html)


def test_iter_ids_down() -> None:
    assert list(_iter_ids(4, -1)) == [4, 3, 2, 1]


def test_iter_ids_up_stops_by_caller() -> None:
    values = []
    for item in _iter_ids(5, 1):
        values.append(item)
        if item >= 8:
            break
    assert values == [5, 6, 7, 8]


def test_backfill_all_posts_no_cap() -> None:
    asyncio.run(_backfill_all_posts_no_cap())


async def _backfill_all_posts_no_cap() -> None:
    existing = {2, 5, 10, 40, 41}
    editor = FakeEditor(existing=existing)
    poster = Poster(MemoryDb(), SimpleNamespace(), editor)
    poster.backfill_delay = 0
    result = await poster.backfill_via_bot(make_window(), start_id=41, html="<b>x</b>", limit=0)
    assert result.stopped is False
    assert set(editor.edited) == existing
    assert result.updated == len(existing)
    assert poster.backfill_running is False


def test_backfill_known_ids_and_album_skip() -> None:
    asyncio.run(_backfill_known_ids_and_album_skip())


async def _backfill_known_ids_and_album_skip() -> None:
    existing = {10, 11, 12, 50}
    editor = FakeEditor(existing=existing, skip={11})
    db = MemoryDb(ids=[50, 12])
    poster = Poster(db, SimpleNamespace(), editor)
    poster.backfill_delay = 0
    result = await poster.backfill_via_bot(make_window(), start_id=50, html="cap")
    assert 11 not in editor.edited
    assert set(editor.edited) == {10, 12, 50}
    assert result.updated == 3
    assert result.skipped >= 1
    assert result.failed == 0


def test_stop_backfill_halts_walk() -> None:
    asyncio.run(_stop_backfill_halts_walk())


async def _stop_backfill_halts_walk() -> None:
    existing = set(range(1, 80))
    slot: list = [None]
    editor = StoppingEditor(slot, after=6, existing=existing)
    poster = Poster(MemoryDb(), SimpleNamespace(), editor)
    poster.backfill_delay = 0
    slot[0] = poster
    result = await poster.backfill_via_bot(make_window(), start_id=79, html="cap")
    assert result.stopped is True
    assert 6 <= result.updated < 30
    assert poster.request_stop_backfill() is False


def test_idle_stop_is_false() -> None:
    poster = Poster(MemoryDb(), SimpleNamespace(), FakeEditor())
    assert poster.request_stop_backfill() is False
