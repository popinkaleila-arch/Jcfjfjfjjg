from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    source TEXT,
    dest TEXT,
    last_msg_id INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 1,
    interval_seconds INTEGER NOT NULL,
    posts_per_cycle INTEGER NOT NULL,
    clean_filter INTEGER NOT NULL DEFAULT 1,
    caption TEXT NOT NULL DEFAULT '',
    next_run_at REAL NOT NULL DEFAULT 0,
    queued INTEGER NOT NULL DEFAULT 0,
    posted_total INTEGER NOT NULL DEFAULT 0,
    bot_edit INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id INTEGER NOT NULL,
    dest TEXT NOT NULL,
    dest_msg_id INTEGER NOT NULL,
    source TEXT,
    source_msg_id INTEGER,
    is_album INTEGER NOT NULL DEFAULT 0,
    caption TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE(dest, dest_msg_id)
);
"""


@dataclass
class Window:
    id: int
    title: str
    source: str | None
    dest: str | None
    last_msg_id: int
    enabled: bool
    paused: bool
    interval_seconds: int
    posts_per_cycle: int
    clean_filter: bool
    caption: str
    next_run_at: float
    queued: int
    posted_total: int
    bot_edit: bool


@dataclass
class PublishedPost:
    id: int
    window_id: int
    dest: str
    dest_msg_id: int
    source: str | None
    source_msg_id: int | None
    is_album: bool
    caption: str
    created_at: float


@dataclass
class AuthCreds:
    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    phone_code_hash: str | None = None
    flood_until: float = 0


@dataclass
class AppState:
    autostart: bool = False
    cycle: str = "idle"
    current_window_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._migrate()
        if not await self.list_windows():
            await self.add_window()

    async def _migrate(self) -> None:
        cur = await self.db.execute("PRAGMA table_info(windows)")
        columns = {row[1] for row in await cur.fetchall()}
        if "bot_edit" not in columns:
            await self.db.execute("ALTER TABLE windows ADD COLUMN bot_edit INTEGER NOT NULL DEFAULT 1")
            await self.db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None
        return self._db

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        cur = await self.db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.db.commit()

    async def get_admin_ids(self) -> list[int]:
        raw = await self.get_setting("admin_ids", "")
        ids = []
        for part in (raw or "").split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return ids

    async def add_admin(self, user_id: int) -> None:
        ids = await self.get_admin_ids()
        if user_id not in ids:
            ids.append(user_id)
            await self.set_setting("admin_ids", ",".join(str(i) for i in ids))

    async def auth(self) -> AuthCreds:
        raw = await self.get_setting("auth_json", "{}")
        data = json.loads(raw or "{}")
        return AuthCreds(
            api_id=data.get("api_id"),
            api_hash=data.get("api_hash"),
            phone=data.get("phone"),
            phone_code_hash=data.get("phone_code_hash"),
            flood_until=float(data.get("flood_until") or 0),
        )

    async def save_auth(self, creds: AuthCreds) -> None:
        await self.set_setting(
            "auth_json",
            json.dumps(
                {
                    "api_id": creds.api_id,
                    "api_hash": creds.api_hash,
                    "phone": creds.phone,
                    "phone_code_hash": creds.phone_code_hash,
                    "flood_until": creds.flood_until,
                },
                ensure_ascii=False,
            ),
        )

    def _row_to_window(self, row: aiosqlite.Row) -> Window:
        return Window(
            id=row["id"],
            title=row["title"] or f"окно {row['id']}",
            source=row["source"],
            dest=row["dest"],
            last_msg_id=row["last_msg_id"],
            enabled=bool(row["enabled"]),
            paused=bool(row["paused"]),
            interval_seconds=row["interval_seconds"],
            posts_per_cycle=row["posts_per_cycle"],
            clean_filter=bool(row["clean_filter"]),
            caption=row["caption"] or "",
            next_run_at=row["next_run_at"] or 0,
            queued=row["queued"] or 0,
            posted_total=row["posted_total"] or 0,
            bot_edit=bool(row["bot_edit"]) if "bot_edit" in row.keys() else True,
        )

    async def list_windows(self) -> list[Window]:
        cur = await self.db.execute("SELECT * FROM windows ORDER BY id")
        rows = await cur.fetchall()
        return [self._row_to_window(r) for r in rows]

    async def get_window(self, window_id: int) -> Window | None:
        cur = await self.db.execute("SELECT * FROM windows WHERE id = ?", (window_id,))
        row = await cur.fetchone()
        return self._row_to_window(row) if row else None

    async def current_window(self) -> Window:
        raw = await self.get_setting("current_window_id")
        if raw and raw.isdigit():
            win = await self.get_window(int(raw))
            if win:
                return win
        windows = await self.list_windows()
        return windows[0]

    async def set_current_window(self, window_id: int) -> None:
        await self.set_setting("current_window_id", str(window_id))

    async def add_window(self, clone: Window | None = None) -> Window:
        from config import DEFAULT_INTERVAL_SECONDS, DEFAULT_POSTS_PER_CYCLE, MAX_WINDOWS

        existing = await self.list_windows()
        if len(existing) >= MAX_WINDOWS:
            raise RuntimeError(f"лимит окон: {MAX_WINDOWS}")
        if clone:
            source, dest = clone.source, None
            interval, limit = clone.interval_seconds, clone.posts_per_cycle
            clean, caption = int(clone.clean_filter), clone.caption
        else:
            source = dest = None
            interval, limit = DEFAULT_INTERVAL_SECONDS, DEFAULT_POSTS_PER_CYCLE
            clean, caption = 1, ""
        cur = await self.db.execute(
            """
            INSERT INTO windows(title, source, dest, interval_seconds, posts_per_cycle, clean_filter, caption, paused, bot_edit)
            VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            ("", source, dest, interval, limit, clean, caption, int(clone.bot_edit) if clone else 1),
        )
        await self.db.commit()
        win = await self.get_window(cur.lastrowid)
        assert win
        await self.set_current_window(win.id)
        return win

    async def update_window(self, window_id: int, **fields: Any) -> Window:
        if not fields:
            win = await self.get_window(window_id)
            assert win
            return win
        mapping = {
            "title": "title",
            "source": "source",
            "dest": "dest",
            "last_msg_id": "last_msg_id",
            "enabled": "enabled",
            "paused": "paused",
            "interval_seconds": "interval_seconds",
            "posts_per_cycle": "posts_per_cycle",
            "clean_filter": "clean_filter",
            "caption": "caption",
            "next_run_at": "next_run_at",
            "queued": "queued",
            "posted_total": "posted_total",
            "bot_edit": "bot_edit",
        }
        sets = []
        values: list[Any] = []
        for key, column in mapping.items():
            if key in fields:
                value = fields[key]
                if key in {"enabled", "paused", "clean_filter", "bot_edit"}:
                    value = 1 if value else 0
                sets.append(f"{column} = ?")
                values.append(value)
        values.append(window_id)
        await self.db.execute(f"UPDATE windows SET {', '.join(sets)} WHERE id = ?", values)
        await self.db.commit()
        win = await self.get_window(window_id)
        assert win
        return win

    async def bump_posted(self, window_id: int, count: int) -> None:
        await self.db.execute(
            "UPDATE windows SET posted_total = posted_total + ? WHERE id = ?",
            (count, window_id),
        )
        await self.db.commit()

    async def global_pause(self, paused: bool, stop_enabled: bool = False) -> None:
        if stop_enabled:
            await self.db.execute(
                "UPDATE windows SET paused = ?, enabled = 0",
                (1 if paused else 0,),
            )
        else:
            await self.db.execute("UPDATE windows SET paused = ?", (1 if paused else 0,))
        await self.db.commit()

    async def start_all(self) -> None:
        now = time.time()
        await self.db.execute(
            "UPDATE windows SET paused = 0, enabled = 1, next_run_at = ? WHERE source IS NOT NULL AND dest IS NOT NULL",
            (now,),
        )
        await self.db.commit()

    def _row_to_post(self, row: aiosqlite.Row) -> PublishedPost:
        return PublishedPost(
            id=row["id"],
            window_id=row["window_id"],
            dest=row["dest"],
            dest_msg_id=row["dest_msg_id"],
            source=row["source"],
            source_msg_id=row["source_msg_id"],
            is_album=bool(row["is_album"]),
            caption=row["caption"] or "",
            created_at=row["created_at"] or 0,
        )

    async def record_post(
        self,
        window_id: int,
        dest: str,
        dest_msg_id: int,
        source: str | None = None,
        source_msg_id: int | None = None,
        is_album: bool = False,
        caption: str = "",
    ) -> PublishedPost:
        now = time.time()
        await self.db.execute(
            """
            INSERT INTO posts(window_id, dest, dest_msg_id, source, source_msg_id, is_album, caption, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dest, dest_msg_id) DO UPDATE SET
                window_id = excluded.window_id,
                source = excluded.source,
                source_msg_id = excluded.source_msg_id,
                is_album = excluded.is_album,
                caption = excluded.caption,
                created_at = excluded.created_at
            """,
            (window_id, dest, dest_msg_id, source, source_msg_id, 1 if is_album else 0, caption or "", now),
        )
        await self.db.commit()
        cur = await self.db.execute(
            "SELECT * FROM posts WHERE dest = ? AND dest_msg_id = ?",
            (dest, dest_msg_id),
        )
        row = await cur.fetchone()
        assert row
        return self._row_to_post(row)

    async def list_dest_message_ids(self, dest: str) -> list[int]:
        cur = await self.db.execute(
            "SELECT dest_msg_id FROM posts WHERE dest = ? ORDER BY dest_msg_id DESC",
            (dest,),
        )
        rows = await cur.fetchall()
        return [int(row[0]) for row in rows]

    async def list_recent_posts(self, window_id: int | None = None, limit: int = 10) -> list[PublishedPost]:
        if window_id is None:
            cur = await self.db.execute(
                "SELECT * FROM posts ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        else:
            cur = await self.db.execute(
                "SELECT * FROM posts WHERE window_id = ? ORDER BY id DESC LIMIT ?",
                (window_id, limit),
            )
        rows = await cur.fetchall()
        return [self._row_to_post(row) for row in rows]

    async def get_post(self, post_id: int) -> PublishedPost | None:
        cur = await self.db.execute("SELECT * FROM posts WHERE id = ?", (post_id,))
        row = await cur.fetchone()
        return self._row_to_post(row) if row else None

    async def find_post(self, dest: str, dest_msg_id: int) -> PublishedPost | None:
        cur = await self.db.execute(
            "SELECT * FROM posts WHERE dest = ? AND dest_msg_id = ?",
            (dest, dest_msg_id),
        )
        row = await cur.fetchone()
        return self._row_to_post(row) if row else None

    async def update_post_caption(self, dest: str, dest_msg_id: int, caption: str) -> None:
        await self.db.execute(
            "UPDATE posts SET caption = ? WHERE dest = ? AND dest_msg_id = ?",
            (caption, dest, dest_msg_id),
        )
        await self.db.commit()
