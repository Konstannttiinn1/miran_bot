import asyncio
from secrets import token_hex
from weakref import WeakValueDictionary

from aiogram import Bot, F, Router, types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey

from app.config import settings
from app.handlers.states import Broadcast
from app.keyboards.builders import admin_menu_kb, dealer_menu_kb, raw_btn
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services.broadcast import BroadcastContent, deliver
from app.utils.emojis import strip_custom_emoji_tags
from app.utils.notifications import notify_dealer_activity

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
router.message.middleware(I18nMiddleware())
router.callback_query.middleware(I18nMiddleware())

_AUDIENCES = {"all": "broadcast_all", "active": "broadcast_active", "inactive": "broadcast_inactive"}
# The dispatcher uses memory FSM storage without event isolation.
_draft_locks: WeakValueDictionary[StorageKey, asyncio.Lock] = WeakValueDictionary()
_campaign_lock = asyncio.Lock()


def _lock_for(state: FSMContext) -> asyncio.Lock:
    lock = _draft_locks.get(state.key)
    if lock is None:
        lock = _draft_locks[state.key] = asyncio.Lock()
    return lock


async def clear_broadcast_draft(state: FSMContext) -> None:
    async with _lock_for(state):
        if await state.get_state() in Broadcast.__all_states_names__:
            await state.clear()


def _allowed(user) -> bool:
    return user.telegram_id in settings.admin_list or (user.role == "dealer" and not user.is_blocked)


async def _notice(callback: types.CallbackQuery, t, key: str) -> None:
    await callback.answer(strip_custom_emoji_tags(t(key)), show_alert=True)


def _controls(t, nonce: str, *, send: bool = False) -> types.InlineKeyboardMarkup:
    rows = [[raw_btn(t("broadcast_send"), f"bc:send:{nonce}")]] if send else []
    rows.append([raw_btn(t("broadcast_cancel"), f"bc:cancel:{nonce}")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "bc:open")
async def open_broadcast(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if not _allowed(db_user):
        return await _notice(callback, t, "broadcast_denied")
    async with _lock_for(state):
        await callback.answer()
        counts = await db_repo.count_broadcast_audiences()
        nonce = token_hex(8)
        rows = [
            [raw_btn(f"{t(key)} · {counts[audience]}", f"bc:aud:{nonce}:{audience}")]
            for audience, key in _AUDIENCES.items()
        ]
        rows.extend(_controls(t, nonce).inline_keyboard)
        await callback.message.answer(
            t("broadcast_choose"), reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows)
        )
        await state.set_data({"nonce": nonce})
        await state.set_state(Broadcast.choosing)


@router.callback_query(F.data.startswith("bc:aud:"))
async def choose_audience(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if not _allowed(db_user):
        return await _notice(callback, t, "broadcast_denied")
    async with _lock_for(state):
        draft = await state.get_data()
        nonce = draft.get("nonce")
        audience = callback.data.rsplit(":", 1)[-1]
        if (
            not nonce or audience not in _AUDIENCES
            or callback.data != f"bc:aud:{nonce}:{audience}"
            or await state.get_state() != Broadcast.choosing.state
        ):
            return await _notice(callback, t, "broadcast_stale")
        await callback.answer()
        await callback.message.answer(t("broadcast_compose"), reply_markup=_controls(t, nonce))
        await state.update_data(audience=audience)
        await state.set_state(Broadcast.composing)


@router.message(Broadcast.composing)
async def preview_broadcast(message: types.Message, state: FSMContext, t, db_user, bot: Bot):
    if not _allowed(db_user):
        return
    async with _lock_for(state):
        if await state.get_state() != Broadcast.composing.state:
            return
        draft = await state.get_data()
        if message.media_group_id or not (message.text or message.photo):
            await message.answer(t("broadcast_invalid"))
            return

        if message.photo:
            content = BroadcastContent(
                text=message.caption or "",
                photo=message.photo[-1].file_id,
                entities=message.caption_entities or [],
                has_spoiler=message.has_media_spoiler,
                show_caption_above_media=message.show_caption_above_media,
            )
        else:
            content = BroadcastContent(
                message.text, entities=message.entities or [], link_preview_options=message.link_preview_options,
            )
        recipients = await db_repo.list_broadcast_recipients(draft["audience"])
        if not recipients:
            await message.answer(t("broadcast_empty"), reply_markup=_controls(t, draft["nonce"]))
            return
        try:
            await content.send(bot, message.chat.id)
        except TelegramAPIError:
            await message.answer(t("broadcast_preview_failed"))
            return
        await message.answer(
            t("broadcast_preview", audience=t(_AUDIENCES[draft["audience"]]), count=len(recipients)),
            reply_markup=_controls(t, draft["nonce"], send=True),
        )
        await state.update_data(content=content, recipients=recipients)
        await state.set_state(Broadcast.preview)


@router.callback_query(F.data.startswith("bc:send:"))
async def confirm_broadcast(callback: types.CallbackQuery, state: FSMContext, t, db_user, bot: Bot):
    if not _allowed(db_user):
        return await _notice(callback, t, "broadcast_denied")
    async with _lock_for(state):
        draft = await state.get_data()
        nonce = draft.get("nonce")
        if not nonce or callback.data != f"bc:send:{nonce}" or await state.get_state() != Broadcast.preview.state:
            return await _notice(callback, t, "broadcast_stale")
        await callback.answer(strip_custom_emoji_tags(t("broadcast_started")))
        await state.clear()

    async with _campaign_lock:
        # Wait for any prior campaign, then refresh eligibility without adding new recipients.
        current = set(await db_repo.list_broadcast_recipients(draft["audience"]))
        recipients = [id_ for id_ in draft["recipients"] if id_ in current]
        sent, failed = await deliver(bot, recipients, draft["content"])
    stats = {"sent": sent, "failed": failed, "skipped": len(draft["recipients"]) - len(recipients)}
    if db_user.role == "dealer" and db_user.telegram_id not in settings.admin_list:
        audience = get_text("ru", _AUDIENCES[draft["audience"]])
        await notify_dealer_activity(
            db_user, "📣 Завершил рассылку",
            f"{audience}\n{get_text('ru', 'broadcast_result', **stats)}",
        )
    await callback.message.answer(t("broadcast_result", **stats))


@router.callback_query(F.data.startswith("bc:cancel:"))
async def cancel_broadcast(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if not _allowed(db_user):
        return await _notice(callback, t, "broadcast_denied")
    async with _lock_for(state):
        draft = await state.get_data()
        nonce = draft.get("nonce")
        if not nonce or callback.data != f"bc:cancel:{nonce}" or await state.get_state() not in Broadcast.__all_states_names__:
            return await _notice(callback, t, "broadcast_stale")
        await state.clear()
        await callback.answer()
        menu = admin_menu_kb() if db_user.telegram_id in settings.admin_list else dealer_menu_kb(t)
        await callback.message.answer(t("broadcast_canceled"), reply_markup=menu)
