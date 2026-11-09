"""Self-service customer cabinet. Never accesses dealer_subscriptions."""
import asyncio
import logging
import time
from collections import defaultdict
from html import escape as h

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.database.models import utcnow
from app.handlers.states import Purchase
from app.keyboards.builders import back_kb, payment_kb, plans_kb, raw_btn
from app.middlewares.i18n import I18nMiddleware
from app.repositories import customer_subscriptions_repo as customer_repo, db_repo
from app.services.subscription import can_buy_traffic
from app.services.vpn_provider import subscription_link, reset_subscription_link
from app.utils.menu import send_with_logo
from app.utils.tariffs import TRAFFIC_TOPUP, get_plan_intro, get_price_display

log = logging.getLogger(__name__)
router = Router()
router.callback_query.middleware(I18nMiddleware())
router.message.middleware(I18nMiddleware())
_reset_locks = defaultdict(asyncio.Lock)
_reset_recent: dict[tuple[int, int], float] = {}


class RenameSubscription(StatesGroup):
    name = State()


def _label(t, sub) -> str:
    if sub.label:
        return sub.label
    return t("customer_primary") if sub.is_primary else t("customer_unnamed", id=sub.id)


def _status(t, sub) -> str:
    if sub.status == "pending":
        return t("customer_pending")
    return t("customer_active") if sub.expire_at > utcnow() else t("customer_expired")


async def show_cabinet(callback, t, db_user) -> None:
    subs = await customer_repo.list_subscriptions(db_user.id)
    total_extra = sum(not sub.is_primary for sub in subs)
    max_extra = max(0, min(settings.max_additional_subscriptions, 100))
    lines = [t("customer_cabinet_intro", count=len(subs), extra=total_extra, limit=max_extra)]
    rows = []
    for sub in subs:
        tag = "🟡" if sub.status == "pending" else (
            "🟢" if sub.expire_at > utcnow() else "🔴"
        )
        label = _label(t, sub)[:34]
        rows.append([raw_btn(f"{tag} {label}", f"cust:card:{sub.id}")])
    if not subs:
        lines.append(t("customer_cabinet_empty"))
    rows.append([raw_btn(t("customer_add"), "cust:add")])
    rows.append([raw_btn(t("btn_back"), "back:main")])
    await send_with_logo(
        callback, "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def _get_owned(callback, t, db_user, sub_id: int):
    sub = await customer_repo.get_subscription(db_user.id, sub_id)
    if sub is None:
        await callback.answer(t("subscription_not_found"), show_alert=True)
    return sub


async def show_card(callback, t, db_user, sub_id: int) -> None:
    sub = await _get_owned(callback, t, db_user, sub_id)
    if sub is None:
        return
    rows = []
    if sub.status == "active" and sub.expire_at > utcnow():
        rows.append([raw_btn(t("btn_get_link"), f"cust:link:{sub.id}")])
        rows.append([raw_btn(t("btn_reset_link"), f"cust:reset:{sub.id}")])
        rows.append([raw_btn(t("customer_topup"), f"cust:topup:{sub.id}")])
    if sub.status == "active":
        rows.append([raw_btn(t("customer_renew"), f"cust:renew:{sub.id}")])
    rows.append([raw_btn(t("customer_rename"), f"cust:rename:{sub.id}")])
    rows.append([raw_btn(t("btn_back"), "cust:subs")])
    description = t(
        "customer_card", name=h(_label(t, sub)), status=_status(t, sub),
        expire=sub.expire_at.strftime("%d.%m.%Y") if sub.status != "pending" else "—",
        traffic=sub.traffic_limit_gb, id=sub.id,
    )
    await send_with_logo(
        callback, description,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "cust:subs")
async def cabinet(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    await state.clear()
    await callback.answer()
    await show_cabinet(callback, t, db_user)


@router.callback_query(F.data.startswith("cust:card:"))
async def card(callback: types.CallbackQuery, t, db_user):
    await callback.answer()
    await show_card(callback, t, db_user, int(callback.data.rsplit(":", 1)[-1]))


@router.callback_query(F.data == "cust:add")
async def add(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if db_user.is_blocked:
        await callback.answer(t("access_denied"), show_alert=True)
        return
    primary = await db_repo.get_subscription(db_user.id)
    if primary is not None:
        count = await customer_repo.count_additional(db_user.id)
        if count >= max(0, min(settings.max_additional_subscriptions, 100)):
            await callback.answer(t("customer_limit"), show_alert=True)
            return
    await state.clear()
    await state.set_state(Purchase.choosing_plan)
    await state.update_data(
        purchase_mode="new" if primary else "primary",
        subscription_id=None, plan=None, order_id=None, checkout_promo_code=None,
    )
    with_test = primary is None and await db_repo.can_claim_test(db_user.id)
    await callback.answer()
    await send_with_logo(
        callback, get_plan_intro(t, lang),
        reply_markup=plans_kb(t, with_test=with_test, lang=lang),
    )


@router.callback_query(F.data.startswith("cust:renew:"))
async def renew(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    sub_id = int(callback.data.rsplit(":", 1)[-1])
    sub = await _get_owned(callback, t, db_user, sub_id)
    if sub is None:
        return
    if sub.status != "active" or db_user.is_blocked:
        await callback.answer(t("customer_unavailable"), show_alert=True)
        return
    await state.clear()
    await state.set_state(Purchase.choosing_plan)
    await state.update_data(
        purchase_mode="renew", subscription_id=sub_id, plan=None,
        order_id=None, checkout_promo_code=None,
    )
    await callback.answer()
    await send_with_logo(
        callback, t("customer_renew_intro", name=h(_label(t, sub))) + "\n\n" + get_plan_intro(t, lang),
        reply_markup=plans_kb(t, with_test=False, lang=lang),
    )


@router.callback_query(F.data.startswith("cust:topup:"))
async def topup(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    sub_id = int(callback.data.rsplit(":", 1)[-1])
    sub = await _get_owned(callback, t, db_user, sub_id)
    if sub is None:
        return
    if not await can_buy_traffic(db_user.id, sub_id):
        await callback.answer(t("traffic_requires_subscription"), show_alert=True)
        return
    await state.clear()
    await state.set_state(Purchase.choosing_payment)
    await state.update_data(
        purchase_mode="topup", subscription_id=sub_id, plan=TRAFFIC_TOPUP,
        order_id=None, checkout_promo_code=None,
    )
    await callback.answer()
    await send_with_logo(
        callback, t("customer_selected", name=h(_label(t, sub))) + "\n\n"
        + t("traffic_topup_intro", price=get_price_display(TRAFFIC_TOPUP, lang))
        + "\n\n" + t("select_payment"),
        reply_markup=payment_kb(t, f"cust:card:{sub_id}", allow_promo=False),
    )


@router.callback_query(F.data.startswith("cust:link:"))
async def link(callback: types.CallbackQuery, t, db_user):
    sub_id = int(callback.data.rsplit(":", 1)[-1])
    sub = await _get_owned(callback, t, db_user, sub_id)
    if sub is None:
        return
    if sub.status != "active" or sub.expire_at <= utcnow() or db_user.is_blocked:
        await callback.answer(t("customer_unavailable"), show_alert=True)
        return
    try:
        url = await subscription_link(sub.xui_email)
    except Exception:
        log.exception("Could not fetch customer subscription #%s", sub.id)
        await callback.answer(t("customer_unavailable"), show_alert=True)
        return
    await callback.answer()
    await send_with_logo(
        callback, t("connection_link", link=h(url)),
        reply_markup=back_kb(t, f"cust:card:{sub.id}"),
    )


@router.callback_query(F.data.regexp(r"^cust:reset:[0-9]+$"))
async def reset_prompt(callback: types.CallbackQuery, t, db_user):
    sub_id = int(callback.data.rsplit(":", 1)[-1])
    sub = await _get_owned(callback, t, db_user, sub_id)
    if sub is None:
        return
    if sub.status != "active" or sub.expire_at <= utcnow() or db_user.is_blocked:
        await callback.answer(t("reset_link_unavailable"), show_alert=True)
        return
    await callback.answer()
    await send_with_logo(
        callback, t("reset_link_warning"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [raw_btn(t("btn_confirm_reset_link"), f"cust:reset:confirm:{sub.id}")],
            [raw_btn(t("btn_back"), f"cust:card:{sub.id}")],
        ]),
    )


@router.callback_query(F.data.startswith("cust:reset:confirm:"))
async def reset_confirm(callback: types.CallbackQuery, t, db_user):
    sub_id = int(callback.data.rsplit(":", 1)[-1])
    sub = await _get_owned(callback, t, db_user, sub_id)
    if sub is None:
        return
    if (sub.status != "active" or sub.expire_at <= utcnow()
            or db_user.is_blocked or callback.from_user.id != db_user.telegram_id):
        await callback.answer(t("reset_link_unavailable"), show_alert=True)
        return
    key = (db_user.id, sub.id)
    async with _reset_locks[key]:
        recent = _reset_recent.get(key)
        if recent is not None and time.monotonic() - recent < 60:
            await callback.answer(t("reset_link_recent"), show_alert=True)
            return
        try:
            url = await reset_subscription_link(sub.xui_email)
        except Exception:
            log.exception("Customer subscription reset failed sub=%s", sub.id)
            await callback.answer(t("reset_link_failed"), show_alert=True)
            return
        _reset_recent[key] = time.monotonic()
        if len(_reset_recent) > 1000:
            cutoff = time.monotonic() - 120
            for old_key, stamp in list(_reset_recent.items()):
                if stamp < cutoff:
                    _reset_recent.pop(old_key, None)
    await callback.answer()
    await send_with_logo(
        callback, t("reset_link_success", link=h(url)),
        reply_markup=back_kb(t, f"cust:card:{sub.id}"),
    )


@router.callback_query(F.data.startswith("cust:rename:"))
async def rename_start(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    sub_id = int(callback.data.rsplit(":", 1)[-1])
    sub = await _get_owned(callback, t, db_user, sub_id)
    if sub is None:
        return
    await state.clear()
    await state.set_state(RenameSubscription.name)
    await state.update_data(rename_subscription_id=sub_id)
    await callback.answer()
    await send_with_logo(
        callback, t("customer_rename_ask"),
        reply_markup=back_kb(t, f"cust:card:{sub_id}"),
    )


@router.message(RenameSubscription.name)
async def rename_save(message: types.Message, t, db_user, state: FSMContext):
    value = (message.text or "").strip()
    if not value or len(value) > 64 or value.startswith("/"):
        await message.answer(t("customer_rename_invalid"))
        return
    data = await state.get_data()
    sub_id = data.get("rename_subscription_id")
    if not sub_id or not await customer_repo.rename_subscription(db_user.id, sub_id, value):
        await state.clear()
        await message.answer(t("subscription_not_found"))
        return
    await state.clear()
    await message.answer(
        t("customer_renamed", name=h(value)),
        reply_markup=back_kb(t, f"cust:card:{sub_id}"),
    )
