"""Opt-in customer storefront for editable, published Orion plans.

The dealer-owned catalogue is the source of truth for display. The order
repository verifies the published revision and freezes the paid quote again.
"""
from html import escape as h

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.handlers.states import Purchase
from app.keyboards.builders import payment_kb, raw_btn
from app.middlewares.i18n import I18nMiddleware
from app.repositories import commercial_catalog_repo as catalog, db_repo
from app.services.catalog_pricing import retail_amount
from app.utils.promo_pricing import discounted_amount, discounted_toman
from app.utils.menu import send_with_logo
from app.utils.tariffs import format_toman

router = Router()
router.callback_query.middleware(I18nMiddleware())


def offer_price(item, lang: str, percent: int = 0) -> str:
    if lang == "fa":
        value = item.retail_toman
        if percent:
            value = discounted_toman(value, percent)
        return format_toman(value)
    value = retail_amount(
        item.retail_toman, "usdt",
        rate=settings.toman_per_usd, stars_reward=settings.stars_reward_usd,
        dealer_discount=settings.dealer_discount,
    )
    if percent:
        value = discounted_amount(value, percent, "usdt")
    return "$" + f"{value:.2f}"


async def show_offers(target, t, lang: str, user_id: int) -> None:
    offers = await catalog.list_published()
    if not offers:
        await send_with_logo(
            target, t("cshop_empty"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [raw_btn(t("btn_back"), "back:main")]
            ]),
        )
        return
    promo = await db_repo.get_active_purchase_promo(user_id)
    discount = promo.discount_percent if promo else 0
    rows = [
        [raw_btn(
            f"📦 {catalog.title(item, lang)[:45]} · {offer_price(item, lang, discount)}",
            f"cshop:choose:{item.id}",
        )] for item in offers
    ]
    rows.append([raw_btn(t("btn_back"), "back:main")])
    await send_with_logo(
        target,
        t("cshop_intro", count=len(offers)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "cshop:list")
async def reopen_offers(callback: types.CallbackQuery, state: FSMContext, t, lang, db_user):
    if db_user.is_blocked or not settings.dynamic_catalog_enabled:
        await callback.answer(t("cshop_unavailable"), show_alert=True)
        return
    data = await state.get_data()
    if (await state.get_state() not in (
        Purchase.choosing_plan.state, Purchase.choosing_payment.state,
        Purchase.entering_promo.state,
    ) or data.get("purchase_mode") not in {"primary", "new", "renew"}):
        await callback.answer(t("cshop_unavailable"), show_alert=True)
        return
    await state.update_data(plan=None, catalog_tariff_id=None, catalog_revision=None, checkout_promo_code=None)
    await state.set_state(Purchase.choosing_plan)
    await callback.answer()
    await show_offers(callback, t, lang, db_user.id)


@router.callback_query(F.data.startswith("cshop:choose:"))
async def choose_offer(callback: types.CallbackQuery, state: FSMContext, t, lang, db_user):
    if db_user.is_blocked or not settings.dynamic_catalog_enabled:
        await callback.answer(t("cshop_unavailable"), show_alert=True)
        return
    data = await state.get_data()
    if (await state.get_state() != Purchase.choosing_plan.state
        or data.get("purchase_mode") not in {"primary", "new", "renew"}):
        await callback.answer(t("cshop_unavailable"), show_alert=True)
        return
    try:
        offer_id = int(callback.data.rsplit(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer(t("cshop_unavailable"), show_alert=True)
        return
    item = await catalog.get_published(offer_id)
    if item is None:
        await callback.answer(t("cshop_changed"), show_alert=True)
        return
    plan = catalog.provision_plan(item)
    from app.handlers.client import _checkout_promo, _target_caption
    promo = await _checkout_promo(db_user.id, data)
    discount = promo.discount_percent if promo else 0
    await state.update_data(plan=plan, catalog_tariff_id=item.id,
                            catalog_revision=item.revision, order_id=None)
    await state.set_state(Purchase.choosing_payment)
    await callback.answer()
    label = catalog.title(item, lang)
    text = (
        await _target_caption(t, db_user.id, data) + "\n\n"
        + t("cshop_summary", name=h(label),
            days=item.period_days, devices=item.devices, traffic=item.traffic_gb,
            price=offer_price(item, lang, discount))
        + "\n\n" + t("select_payment")
    )
    await send_with_logo(
        callback, text,
        reply_markup=payment_kb(t, "cshop:list", allow_promo=True),
    )
