from __future__ import annotations

from aiogram.types import TelegramObject, User

import config
from database import Database


async def is_admin(db: Database, event: TelegramObject | User | int) -> bool:
    if isinstance(event, int):
        user_id = event
    elif isinstance(event, User):
        user_id = event.id
    else:
        user = getattr(event, "from_user", None)
        user_id = user.id if user else None
    if not user_id:
        return False
    configured = set(config.ADMIN_IDS)
    stored = set(await db.get_admin_ids())
    allowed = configured | stored
    if not allowed:
        await db.add_admin(user_id)
        return True
    return user_id in allowed
