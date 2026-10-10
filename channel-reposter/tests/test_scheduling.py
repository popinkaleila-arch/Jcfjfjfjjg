from scheduling import due_windows, next_run
from database import Window


def _win(**kwargs) -> Window:
    data = dict(
        id=1,
        title="w",
        source="@a",
        dest="@b",
        last_msg_id=0,
        enabled=True,
        paused=False,
        interval_seconds=60,
        posts_per_cycle=1,
        clean_filter=True,
        caption="",
        next_run_at=0,
        queued=0,
        posted_total=0,
        bot_edit=True,
    )
    data.update(kwargs)
    return Window(**data)


def test_due_ready() -> None:
    assert due_windows([_win(next_run_at=0)], now=100)


def test_due_skips_paused() -> None:
    assert due_windows([_win(paused=True)], now=100) == []


def test_next_run() -> None:
    assert next_run(_win(interval_seconds=30), now=1000) == 1030
