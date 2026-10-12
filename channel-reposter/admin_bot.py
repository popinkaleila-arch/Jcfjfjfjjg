from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from access import is_admin
from bot_editor import BotEditor
from bridge import UserbotBridge
from database import Database
from formatting import message_to_html
from links import (
    format_chat,
    format_duration,
    format_post_link,
    forwarded_point,
    normalize_chat,
    parse_duration,
    parse_start_link,
    preview_caption,
)
from poster import Poster
from scheduling import any_running
from userbot_auth import AuthError, UserbotAuth

log = logging.getLogger("admin_bot")
router = Router()


class Form(StatesGroup):
    api_id = State()
    api_hash = State()
    phone = State()
    code = State()
    password = State()
    source = State()
    dest = State()
    caption = State()
    interval = State()
    limit = State()
    start_link = State()
    edit_pick = State()
    edit_text = State()
    backfill = State()


def keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔐 Вход юзербота"), KeyboardButton(text="📊 Статус")],
            [KeyboardButton(text="📥 Источник"), KeyboardButton(text="📤 Назначение"), KeyboardButton(text="⭐ Избранное")],
            [KeyboardButton(text="🔗 Старт-ссылка"), KeyboardButton(text="📜 С начала"), KeyboardButton(text="✏️ Описание")],
            [KeyboardButton(text="🖊 Правка поста"), KeyboardButton(text="🖊 Существующие"), KeyboardButton(text="⏹ Стоп правки")],
            [KeyboardButton(text="⏱ Интервал"), KeyboardButton(text="📦 Лимит"), KeyboardButton(text="🧹 Чистый перелив")],
            [KeyboardButton(text="🤖 Бот правит подписи")],
            [KeyboardButton(text="▶️ Старт"), KeyboardButton(text="⏸ Пауза"), KeyboardButton(text="⏹ Стоп")],
            [KeyboardButton(text="🪟 Окна"), KeyboardButton(text="➕ Добавить"), KeyboardButton(text="📋 Клон"), KeyboardButton(text="▶️ Старт все")],
        ],
        resize_keyboard=True,
    )


def _ctx(message: Message) -> tuple[Database, UserbotBridge, UserbotAuth, Poster, BotEditor | None]:
    data = message.bot.workflow  # type: ignore[attr-defined]
    return data["db"], data["bridge"], data["auth"], data["poster"], data.get("editor")


async def _guard(message: Message) -> bool:
    db = message.bot.workflow["db"]  # type: ignore[attr-defined]
    if not await is_admin(db, message):
        await message.answer("нет доступа")
        return False
    return True


def parse_backfill_limit(text: str) -> int | None:
    raw = (text or "").strip().lower()
    if raw in {"все", "all", "всё"}:
        return 0
    if raw.isdigit():
        return max(1, int(raw))
    return None


async def status_text(
    db: Database,
    bridge: UserbotBridge,
    editor: BotEditor | None = None,
    poster: Poster | None = None,
) -> str:
    windows = await db.list_windows()
    current = await db.current_window()
    running = any_running(windows)
    enabled = sum(1 for win in windows if win.enabled and not win.paused)
    queued = sum(win.queued for win in windows)
    posted = sum(win.posted_total for win in windows)
    autopost = "идёт" if running else "пауза"
    pair = f"{format_chat(current.source)} → {format_chat(current.dest)}"
    lines = [
        f"панель @{(await _bot_username(db, bridge))}",
        f"юзербот: {bridge.status_text()}",
        f"автопост: {autopost}",
        f"цикл: {bridge.cycle}",
        f"окна: {enabled}/{len(windows)}",
        f"текущее: #{current.id} {pair}",
        f"интервал: {format_duration(current.interval_seconds)}",
        f"лимит: {current.posts_per_cycle}",
        f"фильтр: {'чистый' if current.clean_filter else 'все посты'}",
        f"бот правит подписи: {'вкл' if current.bot_edit else 'выкл'}",
        f"правка описаний: {'идёт — ⏹ Стоп правки остановит' if poster and poster.caption_edit_active() else 'нет'}",
        f"очередь≈{queued}  всего={posted}",
        f"источник: {format_chat(current.source)}",
        f"назначение: {format_chat(current.dest)}",
    ]
    if current.bot_edit and current.dest and editor:
        rights = await editor.check_rights(current.dest)
        lines.append(f"права бота: {'ок' if rights.ok else 'нет'} — {rights.detail}")
    if windows:
        lines.append("")
        lines.append("окна:")
        for win in windows:
            flag = "▶" if win.enabled and not win.paused else ("⏸" if win.paused else "·")
            lines.append(
                f"{flag} #{win.id} {format_chat(win.source)} → {format_chat(win.dest)}  last={win.last_msg_id}"
            )
    return "\n".join(lines)


async def _bot_username(db: Database, bridge: UserbotBridge) -> str:
    return "ZzzLV_bot"


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.clear()
    db, bridge, _auth, poster, editor = _ctx(message)
    await message.answer(
        "Channel Reposter — панель управления.\n\n"
        "1) 🔐 Вход юзербота (api_id, api_hash, телефон, код)\n"
        "2) 📥 Источник и 📤 Назначение\n"
        "3) выдайте @ZzzLV_bot админку назначения с правом менять сообщения\n"
        "4) ▶️ Старт — бот сам проставит описание с форматированием\n"
        "🖊 Существующие — описание на все уже залитые посты, ⏹ Стоп правки — остановить\n\n"
        + await status_text(db, bridge, editor, poster),
        reply_markup=keyboard(),
    )


@router.message(Command("status"))
@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message) -> None:
    if not await _guard(message):
        return
    db, bridge, _auth, poster, editor = _ctx(message)
    await message.answer(await status_text(db, bridge, editor, poster), reply_markup=keyboard())


@router.message(F.text == "🔐 Вход юзербота")
async def login_start(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.set_state(Form.api_id)
    await message.answer(
        "api_id с https://my.telegram.org/apps — отправьте число.\n"
        "/cancel чтобы отменить."
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    _db, _bridge, _auth, poster, _editor = _ctx(message)
    await state.clear()
    if poster.request_stop_backfill():
        await message.answer("правка описаний останавливается", reply_markup=keyboard())
        return
    await message.answer("отменено", reply_markup=keyboard())


@router.message(Form.api_id)
async def got_api_id(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.update_data(api_id=(message.text or "").strip())
    await state.set_state(Form.api_hash)
    await message.answer("теперь api_hash")


@router.message(Form.api_hash)
async def got_api_hash(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, _bridge, auth, _poster, _editor = _ctx(message)
    data = await state.get_data()
    try:
        await auth.save_api(data.get("api_id", ""), (message.text or "").strip())
    except AuthError as exc:
        await message.answer(str(exc))
        return
    await state.set_state(Form.phone)
    await message.answer("номер телефона юзербота, например +79001234567")


@router.message(Form.phone)
async def got_phone(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    _db, _bridge, auth, _poster, _editor = _ctx(message)
    try:
        info = await auth.send_code(message.text or "")
    except AuthError as exc:
        await message.answer(str(exc), reply_markup=keyboard())
        await state.clear()
        return
    await state.set_state(Form.code)
    await message.answer(info)


@router.message(Form.code)
async def got_code(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    _db, _bridge, auth, _poster, _editor = _ctx(message)
    try:
        info = await auth.sign_in(message.text or "")
    except AuthError as exc:
        await message.answer(str(exc), reply_markup=keyboard())
        return
    if "2FA" in info:
        await state.set_state(Form.password)
        await message.answer(info)
        return
    await state.clear()
    await message.answer(info, reply_markup=keyboard())


@router.message(Form.password)
async def got_password(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    _db, _bridge, auth, _poster, _editor = _ctx(message)
    try:
        info = await auth.check_password(message.text or "")
    except AuthError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(info, reply_markup=keyboard())


@router.message(F.text == "📥 Источник")
async def ask_source(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.set_state(Form.source)
    await message.answer("источник: @channel, id, t.me/c/… или «избранное»")


@router.message(Form.source)
async def set_source(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    try:
        chat = normalize_chat(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    win = await db.current_window()
    await db.update_window(win.id, source=chat)
    await state.clear()
    await message.answer(f"источник: {format_chat(chat)}", reply_markup=keyboard())


@router.message(F.text == "📤 Назначение")
async def ask_dest(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.set_state(Form.dest)
    await message.answer("назначение: @channel, id, t.me/c/… или «избранное»")


@router.message(Form.dest)
async def set_dest(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    try:
        chat = normalize_chat(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    win = await db.current_window()
    await db.update_window(win.id, dest=chat)
    await state.clear()
    await message.answer(f"назначение: {format_chat(chat)}", reply_markup=keyboard())


@router.message(F.text == "⭐ Избранное")
async def saved(message: Message) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    win = await db.current_window()
    if not win.dest:
        await db.update_window(win.id, dest="me")
        await message.answer("назначение: ⭐ Избранное", reply_markup=keyboard())
        return
    await db.update_window(win.id, dest="me")
    await message.answer("назначение: ⭐ Избранное", reply_markup=keyboard())


@router.message(F.text == "🔗 Старт-ссылка")
async def ask_link(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.set_state(Form.start_link)
    await message.answer("ссылка на пост, со следующего начнём: t.me/channel/123")


@router.message(Form.start_link)
async def set_link(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    try:
        point = parse_start_link(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    win = await db.current_window()
    fields = {"last_msg_id": point.message_id}
    if point.chat and not win.source:
        fields["source"] = point.chat
    await db.update_window(win.id, **fields)
    await state.clear()
    await message.answer(
        f"старт после сообщения {point.message_id} в {format_chat(point.chat or win.source)}",
        reply_markup=keyboard(),
    )


@router.message(F.text == "📜 С начала")
async def from_start(message: Message) -> None:
    if not await _guard(message):
        return
    db, _bridge, _auth, poster, _editor = _ctx(message)
    win = await db.current_window()
    if not win.source:
        await message.answer("сначала укажите источник")
        return
    await message.answer("сканирую историю источника…")
    try:
        scanned = await poster.rewind_to_start(win)
    except Exception as exc:
        await message.answer(f"не вышло: {exc}")
        return
    await message.answer(f"история сброшена, в очереди ≈{scanned} постов", reply_markup=keyboard())


@router.message(F.text == "✏️ Описание")
async def ask_caption(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.set_state(Form.caption)
    await message.answer("шаблон подписи (HTML сохранится). «-» чтобы очистить")


@router.message(Form.caption)
async def set_caption(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    text = "-" if (message.text or "").strip() == "-" else message_to_html(message)
    if text == "-":
        text = ""
    win = await db.current_window()
    await db.update_window(win.id, caption=text)
    await state.clear()
    await message.answer("описание сохранено" if text else "описание снято", reply_markup=keyboard())


def _recent_lines(posts) -> str:
    if not posts:
        return "в журнале ещё нет залитых постов — можно прислать ссылку или переслать пост из назначения"
    lines = []
    for index, post in enumerate(posts, start=1):
        lines.append(
            f"{index}. {format_post_link(post.dest, post.dest_msg_id)} — {preview_caption(post.caption)}"
        )
    return "\n".join(lines)


@router.message(Command("edit"))
@router.message(F.text == "🖊 Правка поста")
async def ask_edit_post(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, bridge, *_ = _ctx(message)
    if not bridge.logged_in():
        await message.answer("сначала 🔐 Вход юзербота")
        return
    win = await db.current_window()
    posts = await db.list_recent_posts(win.id, limit=8)
    await state.set_state(Form.edit_pick)
    await state.update_data(recent_ids=[post.id for post in posts])
    await message.answer(
        "правлю описание уже залитого поста.\n\n"
        "пришлите ссылку t.me/…/123, перешлите пост из назначения, "
        "напишите «последний» или номер из списка.\n"
        "/cancel — отмена.\n\n" + _recent_lines(posts)
    )


@router.message(Form.edit_pick)
async def pick_edit_post(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    point = forwarded_point(message)
    text = (message.text or message.caption or "").strip()
    dest = None
    dest_msg_id = None
    if point:
        dest, dest_msg_id = point.chat, point.message_id
    elif text.lower() in {"последний", "last", "последнее"}:
        win = await db.current_window()
        posts = await db.list_recent_posts(win.id, limit=1)
        if not posts:
            await message.answer("для этого окна ещё нет залитых постов")
            return
        dest, dest_msg_id = posts[0].dest, posts[0].dest_msg_id
    elif text.isdigit():
        data = await state.get_data()
        recent_ids = data.get("recent_ids") or []
        index = int(text)
        if 1 <= index <= len(recent_ids):
            post = await db.get_post(int(recent_ids[index - 1]))
            if post:
                dest, dest_msg_id = post.dest, post.dest_msg_id
        if dest is None:
            await message.answer("нет такого номера в списке")
            return
    elif text:
        try:
            parsed = parse_start_link(text)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        dest, dest_msg_id = parsed.chat, parsed.message_id
    else:
        await message.answer("нужна ссылка, пересланный пост, «последний» или номер")
        return
    known = await db.find_post(dest, dest_msg_id)
    current = preview_caption(known.caption, 200) if known else "не знаю текущую подпись"
    await state.set_state(Form.edit_text)
    await state.update_data(edit_dest=dest, edit_msg_id=dest_msg_id)
    await message.answer(
        f"пост {format_post_link(dest, dest_msg_id)}\n"
        f"сейчас: {current}\n\n"
        "пришлите новое описание — жирный, ссылки и остальное форматирование Telegram сохранится.\n"
        "«-» снимет подпись. Сначала правит бот-админ, если не выйдет — юзербот."
    )


@router.message(Form.edit_text)
async def apply_edit_post(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    _db, _bridge, _auth, poster, _editor = _ctx(message)
    data = await state.get_data()
    dest = data.get("edit_dest")
    dest_msg_id = data.get("edit_msg_id")
    if not dest or not dest_msg_id:
        await state.clear()
        await message.answer("цель потерялась, нажмите 🖊 Правка поста ещё раз", reply_markup=keyboard())
        return
    raw = (message.text or "").strip()
    caption = "" if raw == "-" else message_to_html(message)
    try:
        result = await poster.edit_caption(str(dest), int(dest_msg_id), caption)
    except Exception as exc:
        await message.answer(f"{exc}")
        return
    await state.clear()
    await message.answer(f"{result}\n{format_post_link(str(dest), int(dest_msg_id))}", reply_markup=keyboard())


def _backfill_text(result) -> str:
    title = "правка остановлена" if getattr(result, "stopped", False) else "готово"
    lines = [
        f"{title}: просмотрено {result.scanned}",
        f"поставлено описаний {result.updated}",
        f"уже было {getattr(result, 'same', 0)}",
        f"пропущено {result.skipped}",
        f"ошибок {result.failed}",
        "подпись видно при открытии поста, в сетке медиа Telegram её не показывает.",
    ]
    if result.errors:
        lines.append("первые ошибки:")
        lines.extend(result.errors[:5])
    return "\n".join(lines)


def _is_stop_edit(text: str) -> bool:
    raw = (text or "").strip().lower()
    return raw in {"⏹ стоп правки", "стоп правки"} or (text or "").strip() == "⏹ Стоп правки"


def _is_stop_all(text: str) -> bool:
    return (text or "").strip() in {"⏹ Стоп"} or (text or "").strip().lower() == "стоп"


async def _stop_caption_edit(message: Message, also_autopost: bool = False) -> None:
    db, bridge, _auth, poster, _editor = _ctx(message)
    stopped_edit = poster.request_stop_backfill()
    if also_autopost:
        bridge.stop_cycle.set()
        await db.global_pause(True, stop_enabled=True)
        if stopped_edit:
            await message.answer("стоп: правка описаний останавливается, автопост выключен", reply_markup=keyboard())
        else:
            await message.answer("стоп: цикл оборван, автопост выключен", reply_markup=keyboard())
        return
    if stopped_edit:
        await message.answer("правка описаний останавливается. уже проставленные подписи остаются", reply_markup=keyboard())
        return
    await message.answer("правка описаний сейчас не идёт", reply_markup=keyboard())


@router.message(Command("backfill"))
@router.message(F.text == "🖊 Существующие")
async def ask_backfill(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, _bridge, _auth, poster, editor = _ctx(message)
    win = await db.current_window()
    if poster.caption_edit_active():
        await message.answer("правка существующих уже идёт. ⏹ Стоп правки — остановить")
        return
    if win.dest and editor:
        rights = await editor.check_rights(win.dest)
        if not rights.ok:
            await message.answer(
                "бот ещё не может править этот канал — юзербот не нужен, нужна только админка бота.\n"
                f"{rights.detail}"
            )
            return
    await state.set_state(Form.backfill)
    await state.update_data(bf_caption="", bf_start=0, bf_limit=0)
    hint = "есть шаблон ✏️ Описание — можно сразу прислать пост" if win.caption else "шаблона нет — сначала пришлите новое описание"
    await message.answer(
        "правлю уже залитые посты только через этого бота, без юзербота.\n"
        "по умолчанию — ВСЕ посты, пока они находятся.\n"
        "⏹ Стоп правки — остановить в любой момент.\n"
        f"{hint}\n\n"
        "1) пришлите новое описание (жирный, ссылки и т.д. сохранятся) или напишите «шаблон»\n"
        "2) перешлите любой пост из канала-назначения или ссылку t.me/…/123\n"
        "по желанию число — сколько постов максимум, «все» — без лимита\n"
        "/cancel — отмена"
    )


@router.message(Form.backfill)
async def collect_backfill(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, _bridge, _auth, poster, editor = _ctx(message)
    text = (message.text or "").strip()
    if _is_stop_edit(text):
        await state.clear()
        await _stop_caption_edit(message)
        return
    if _is_stop_all(text):
        await state.clear()
        await _stop_caption_edit(message, also_autopost=True)
        return
    win = await db.current_window()
    data = await state.get_data()
    caption = data.get("bf_caption") or ""
    start_id = int(data.get("bf_start") or 0)
    limit = int(data.get("bf_limit") or 0)
    point = forwarded_point(message)
    parsed_limit = parse_backfill_limit(text)

    if point or (text and "t.me/" in text.lower()):
        if not point:
            parsed = parse_start_link(text)
            dest, start_id = parsed.chat, parsed.message_id
        else:
            dest, start_id = point.chat, point.message_id
        if dest and dest != win.dest:
            await db.update_window(win.id, dest=dest)
            win = await db.current_window()
        await state.update_data(bf_start=start_id)
        if editor and win.dest:
            rights = await editor.check_rights(win.dest)
            if not rights.ok:
                await message.answer(f"бот не админ этого канала.\n{rights.detail}")
                return
    elif text.lower() in {"шаблон", "описание"}:
        if not win.caption:
            await message.answer("шаблон пустой — пришлите текст описания сообщением")
            return
        caption = win.caption
        await state.update_data(bf_caption=caption)
    elif parsed_limit is not None:
        limit = parsed_limit
        await state.update_data(bf_limit=limit)
        await message.answer("без лимита — все посты" if limit == 0 else f"лимит правки: {limit} постов")
        return
    elif text or message.caption:
        caption = message_to_html(message)
        await state.update_data(bf_caption=caption)
    else:
        await message.answer("нужны описание и ссылка/пересланный пост из назначения")
        return

    if not caption:
        caption = win.caption
    if not start_id:
        await message.answer(
            "описание есть. теперь перешлите любой пост из канала или пришлите ссылку на него."
            if caption
            else "пришлите описание (с форматированием) или «шаблон», затем ссылку на пост."
        )
        return
    if not caption:
        await message.answer("есть пост. теперь пришлите новое описание или «шаблон».")
        return

    await state.clear()
    if poster.caption_edit_active():
        await message.answer("правка существующих уже идёт. ⏹ Стоп правки — остановить", reply_markup=keyboard())
        return

    status = await message.answer(
        "ставлю описания под фото/видео.\n"
        "в сетке медиа подписи нет — смотрите открытый пост.\n"
        "⏹ Стоп правки — остановить."
        if limit == 0
        else f"правлю до {limit} постов в фоне. ⏹ Стоп правки — остановить."
    )

    async def progress(result) -> None:
        try:
            extra = " (остановка…)" if poster.stop_backfill.is_set() else ""
            wait = f" жду Telegram {result.waiting}с." if getattr(result, "waiting", 0) else ""
            await status.edit_text(
                f"иду{extra}: поставлено {result.updated}, уже было {getattr(result, 'same', 0)}, "
                f"просмотрено {result.scanned}, пропущено {result.skipped}, ошибок {result.failed}.{wait}\n"
                "подпись видно при открытии поста.\n"
                "⏹ Стоп правки — остановить"
            )
        except Exception:
            pass

    async def runner() -> None:
        try:
            result = await poster.backfill_via_bot(win, start_id, caption, limit=limit, progress=progress)
            await message.answer(_backfill_text(result), reply_markup=keyboard())
        except Exception as exc:
            await message.answer(f"не вышло: {exc}", reply_markup=keyboard())

    poster.backfill_task = asyncio.create_task(runner())


@router.message(F.text == "⏱ Интервал")
async def ask_interval(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.set_state(Form.interval)
    await message.answer("интервал: 30м, 2ч, 1д или секунды")


@router.message(Form.interval)
async def set_interval(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    try:
        seconds = parse_duration(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    win = await db.current_window()
    await db.update_window(win.id, interval_seconds=seconds)
    await state.clear()
    await message.answer(f"интервал: {format_duration(seconds)}", reply_markup=keyboard())


@router.message(F.text == "📦 Лимит")
async def ask_limit(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    await state.set_state(Form.limit)
    await message.answer("сколько постов за один цикл (1–50)")


@router.message(Form.limit)
async def set_limit(message: Message, state: FSMContext) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    try:
        value = int((message.text or "").strip())
        if not 1 <= value <= 50:
            raise ValueError
    except ValueError:
        await message.answer("нужно число от 1 до 50")
        return
    win = await db.current_window()
    await db.update_window(win.id, posts_per_cycle=value)
    await state.clear()
    await message.answer(f"лимит: {value}", reply_markup=keyboard())


@router.message(Command("filter"))
@router.message(F.text == "🧹 Чистый перелив")
async def toggle_filter(message: Message) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    win = await db.current_window()
    await db.update_window(win.id, clean_filter=not win.clean_filter)
    win = await db.get_window(win.id)
    assert win
    await message.answer(
        "чистый перелив: только фото/видео, без подписи" if win.clean_filter else "фильтр выключен — уходят все посты",
        reply_markup=keyboard(),
    )


@router.message(Command("botedit"))
@router.message(F.text == "🤖 Бот правит подписи")
async def toggle_bot_edit(message: Message) -> None:
    if not await _guard(message):
        return
    db, _bridge, _auth, _poster, editor = _ctx(message)
    win = await db.current_window()
    await db.update_window(win.id, bot_edit=not win.bot_edit)
    win = await db.get_window(win.id)
    assert win
    if not win.bot_edit:
        await message.answer("бот больше не трогает подписи после залива", reply_markup=keyboard())
        return
    extra = ""
    if win.dest and editor:
        rights = await editor.check_rights(win.dest)
        extra = "\n" + ("права ок: " + rights.detail if rights.ok else "⚠ " + rights.detail)
    await message.answer(
        "бот сам проставит описание после залива, со всем HTML-форматированием."
        "\nвыдайте @ZzzLV_bot админку назначения с правом «Изменение сообщений»."
        + extra,
        reply_markup=keyboard(),
    )


@router.message(F.text == "▶️ Старт")
async def start_one(message: Message) -> None:
    if not await _guard(message):
        return
    db, bridge, _auth, _poster, editor = _ctx(message)
    if not bridge.logged_in():
        await message.answer("сначала 🔐 Вход юзербота")
        return
    win = await db.current_window()
    if not win.source or not win.dest:
        await message.answer("нужны источник и назначение")
        return
    warn = ""
    if win.bot_edit and editor:
        rights = await editor.check_rights(win.dest)
        if not rights.ok:
            warn = f"\n⚠ бот не сможет править подписи: {rights.detail}"
    await db.update_window(win.id, enabled=True, paused=False, next_run_at=time.time())
    bridge.stop_cycle.clear()
    await message.answer(
        f"окно #{win.id} запущено. первый цикл в ближайшие секунды.{warn}",
        reply_markup=keyboard(),
    )


@router.message(F.text == "⏸ Пауза")
async def pause(message: Message) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    await db.global_pause(True)
    await message.answer("пауза: текущий цикл доработает, новые не стартуют", reply_markup=keyboard())


@router.message(F.text == "⏹ Стоп правки")
async def stop_edits(message: Message) -> None:
    if not await _guard(message):
        return
    await _stop_caption_edit(message)


@router.message(F.text == "⏹ Стоп")
async def stop(message: Message) -> None:
    if not await _guard(message):
        return
    await _stop_caption_edit(message, also_autopost=True)


@router.message(F.text == "🪟 Окна")
async def list_windows(message: Message) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    windows = await db.list_windows()
    lines = ["выберите окно номером, например 1"]
    for win in windows:
        flag = "▶" if win.enabled and not win.paused else "⏸"
        lines.append(f"{flag} {win.id}. {format_chat(win.source)} → {format_chat(win.dest)}")
    await message.answer("\n".join(lines), reply_markup=keyboard())


@router.message(F.text.regexp(r"^\d{1,2}$"))
async def select_window(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    win = await db.get_window(int(message.text or "0"))
    if not win:
        return
    await db.set_current_window(win.id)
    await message.answer(
        f"текущее окно #{win.id}: {format_chat(win.source)} → {format_chat(win.dest)}",
        reply_markup=keyboard(),
    )


@router.message(F.text == "➕ Добавить")
async def add_window(message: Message) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    try:
        win = await db.add_window()
    except RuntimeError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"добавлено окно #{win.id}", reply_markup=keyboard())


@router.message(F.text == "📋 Клон")
async def clone_window(message: Message) -> None:
    if not await _guard(message):
        return
    db, *_ = _ctx(message)
    current = await db.current_window()
    try:
        win = await db.add_window(clone=current)
    except RuntimeError as exc:
        await message.answer(str(exc))
        return
    await message.answer(f"клон #{win.id} источника {format_chat(current.source)}", reply_markup=keyboard())


@router.message(F.text == "▶️ Старт все")
async def start_all(message: Message) -> None:
    if not await _guard(message):
        return
    db, bridge, *_ = _ctx(message)
    if not bridge.logged_in():
        await message.answer("сначала 🔐 Вход юзербота")
        return
    await db.start_all()
    bridge.stop_cycle.clear()
    await message.answer("все заполненные окна запущены", reply_markup=keyboard())


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


async def start_polling(bot: Bot, dp: Dispatcher) -> None:
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=["message"])
