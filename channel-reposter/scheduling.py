from __future__ import annotations

import time

from database import Window


def due_windows(windows: list[Window], now: float | None = None) -> list[Window]:
    now = time.time() if now is None else now
    ready = [
        win
        for win in windows
        if win.enabled and not win.paused and win.source and win.dest and win.next_run_at <= now
    ]
    return sorted(ready, key=lambda item: (item.next_run_at, item.id))


def next_run(window: Window, now: float | None = None, immediate: bool = False) -> float:
    now = time.time() if now is None else now
    if immediate:
        return now
    return now + max(5, int(window.interval_seconds))


def any_running(windows: list[Window]) -> bool:
    return any(win.enabled and not win.paused for win in windows)
