"""Dealer-managed purchases using published catalogue, not static plan prices."""
import asyncio
import logging
from collections import defaultdict
from html import escape as h
from secrets import token_hex

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.handlers.states import DealerManagedSubscription
from app.keyboards.builders import dealer_created_name_kb, raw_btn
from app.middlewares.i18n import I18nMiddleware
from app.repositories import commercial_catalog_repo as catalog, db_repo
from app.services.catalog_wholesale import wholesale_usd
from app.services.monthly_traffic import grant_monthly, parse_time
from app.services.vpn_provider import get_vpn_provider, provider_name, subscription_link
from app.utils.menu import send_with_logo
from app.utils.notifications import notify_admins, notify_dealer_activity
from app.utils.tariffs import format_toman

log = logging.getLogger(__name__)
router = Router()
router.callback_query.middleware(I18nMiddleware())
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


class ManualCatalog(StatesGroup):
    buying = State()
    renewing = State()


def _allowed(user):
    return bool(settings.dynamic_catalog_enabled and user.role == "dealer" and not user.is_blocked)


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_offers(callback, t, lang, user, *, sub_id=None, page=0):
    if not _allowed(user):
        await send_with_logo(callback, t("dcat_unavailable"))
        return
    offers = await catalog.list_published()
    rows = []
    for item in offers:
        debit = wholesale_usd(item.period_days, item.devices, item.traffic_gb)
        key = (f"dcat:buy:{item.id}" if sub_id is None
               else f"dcat:renew:{sub_id}:{item.id}:{page}")
        label = f"📦 {catalog.title(item, lang)[:24]} · {format_toman(item.retail_toman)} · USD {debit:.3f}"
        rows.append([raw_btn(label, key)])
    back = "back:dealer" if sub_id is None else f"dealer:sub:{sub_id}:{page}"
    rows.append([raw_btn(t("btn_back"), back)])
    await send_with_logo(callback, t("dcat_offer_intro") if offers else t("cshop_empty"),
                         reply_markup=_kb(rows))


def _review(t, item, dealer_balance, *, renewal=None):
    debit = wholesale_usd(item.period_days, item.devices, item.traffic_gb)
    title = (t("dcat_buy_review") if renewal is None
             else t("dcat_renew_review", id=renewal))
    return title + "\n\n" + t(
        "catalog_card", name=h(item.name_fa), sku=h(item.sku),
        status=t("catalog_status_published"), days=item.period_days,
        devices=item.devices, traffic=item.traffic_gb,
        price=format_toman(item.retail_toman), revision=item.revision,
    ) + "\n\n" + t(
        "dcat_debit", amount=f"USD {debit:.3f}",
        balance=f"USD {dealer_balance:.3f}",
    ) + "\n" + t("dcat_retail_not_received")


@router.callback_query(F.data.startswith("dcat:buy:"))
async def show_purchase(callback: types.CallbackQuery, state: FSMContext, t, lang, db_user):
    if not _allowed(db_user):
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    try:
        item_id = int(callback.data.split(":")[2])
    except (ValueError, TypeError):
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    item = await catalog.get_published(item_id)
    if item is None:
        await callback.answer(t("cshop_changed"), show_alert=True)
        return
    token = token_hex(8)
    await state.set_state(ManualCatalog.buying)
    await state.set_data({"dcat_id": item.id, "dcat_rev": item.revision, "dcat_token": token})
    await callback.answer()
    await send_with_logo(callback, _review(t, item, db_user.dealer_balance), reply_markup=_kb([
        [raw_btn(t("dealer_confirm_purchase"), f"dcat:buyok:{item.id}:{item.revision}:{token}")],
        [raw_btn(t("btn_back"), "dealer:buy_sub")],
    ]))


@router.callback_query(F.data.startswith("dcat:buyok:"))
async def confirm_purchase(callback: types.CallbackQuery, state: FSMContext, t, lang, db_user):
    if not _allowed(db_user) or provider_name() != "remnawave":
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    try:
        _, _, item_str, rev_str, token = callback.data.split(":")
        item_id, revision = int(item_str), int(rev_str)
    except (ValueError, TypeError):
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    async with _locks[db_user.id]:
        state_data = await state.get_data()
        if (await state.get_state() != ManualCatalog.buying.state
                or state_data.get("dcat_id") != item_id
                or state_data.get("dcat_rev") != revision
                or state_data.get("dcat_token") != token):
            await callback.answer(t("dcat_already"), show_alert=True)
            return
        await state.clear()
        item = await catalog.get_published(item_id)
        if item is None or item.revision != revision:
            await callback.answer(t("cshop_changed"), show_alert=True)
            return
        status, sub = await db_repo.reserve_dealer_subscription_purchase(
            db_user.id, catalog.provision_plan(item), 0.01,
            catalog_tariff_id=item_id, catalog_expected_revision=revision,
            request_key=f"cat:{db_user.id}:{token}",
        )
        if status != "reserved" or sub is None:
            await callback.answer(t("dealer_balance_low" if status == "insufficient"
                                    else "cshop_changed"), show_alert=True)
            return
        await callback.answer("⏳")
        provider = get_vpn_provider()
        confirmed = False
        try:
            snap = sub.catalog_snapshot
            expiry = await grant_monthly(
                provider, sub.xui_email, int(snap["period_days"]),
                limit_ip=int(snap["devices"]), traffic_gb=int(snap["traffic_gb"]),
                squad_uuid=snap["internal_squad_uuid"], custom_terms=True,
            )
            confirmed = True
            if not await db_repo.complete_dealer_subscription_purchase(
                sub.id, db_user.id, expiry, int(snap["traffic_gb"]),
            ):
                raise RuntimeError("Paid subscription was issued but DB completion failed")
        except Exception as exc:
            try:
                remote = await provider.get_client(sub.xui_email)
            except Exception:
                remote = None
            if confirmed or remote:
                log.exception("Potentially issued subscription needs admin reconciliation")
                await notify_admins(f"🚨 Дилерская подписка #{sub.id} возможно выдана; проверь панель и учёт. Баланс удержан.")
                await callback.message.answer(t("dcat_manual_reconcile"))
            else:
                await db_repo.rollback_dealer_subscription_purchase(sub.id, db_user.id, str(exc))
                log.exception("Dealer catalogue purchase rolled back")
                await callback.message.answer(t("dealer_buy_failed"))
            return
    link = await subscription_link(sub.xui_email)
    await notify_dealer_activity(db_user, "✅ Ручная подписка по каталогу",
        f"#{sub.id} · {snap['title_fa']} · себестоимость USD {snap['wholesale_usd']:.3f}; "
        f"предложено {snap['base_retail_toman']} томан.")
    await state.set_state(DealerManagedSubscription.waiting_name)
    await state.update_data(sub_id=sub.id, page=0)
    await send_with_logo(callback, t("dealer_subscription_created", link=h(link)),
                         reply_markup=dealer_created_name_kb(sub.id, link, t=t))


@router.callback_query(F.data.startswith("dcat:renew:"))
async def show_renewal(callback: types.CallbackQuery, state: FSMContext, t, lang, db_user):
    if not _allowed(db_user):
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    try:
        _, _, sub_str, item_str, page_str = callback.data.split(":")
        sub_id, item_id, page = int(sub_str), int(item_str), int(page_str)
    except (ValueError, TypeError):
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    sub = await db_repo.get_dealer_subscription(db_user.id, sub_id)
    item = await catalog.get_published(item_id)
    if sub is None or item is None:
        await callback.answer(t("cshop_changed"), show_alert=True)
        return
    token = token_hex(8)
    await state.set_state(ManualCatalog.renewing)
    await state.set_data({"dcat_sub": sub_id, "dcat_id": item.id, "dcat_rev": item.revision,
                          "dcat_token": token, "dcat_page": page})
    await callback.answer()
    await send_with_logo(callback, _review(t, item, db_user.dealer_balance, renewal=sub_id),
                         reply_markup=_kb([
        [raw_btn(t("dealer_confirm_purchase"),
                 f"dcat:renewok:{sub_id}:{item.id}:{item.revision}:{token}:{page}")],
        [raw_btn(t("btn_back"), f"dealer:renew:{sub_id}:{page}")],
    ]))


@router.callback_query(F.data.startswith("dcat:renewok:"))
async def confirm_renewal(callback: types.CallbackQuery, state: FSMContext, t, lang, db_user):
    if not _allowed(db_user) or provider_name() != "remnawave":
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    try:
        _, _, sub_str, item_str, rev_str, token, page_str = callback.data.split(":")
        sub_id, item_id, revision, page = map(int, (sub_str, item_str, rev_str, page_str))
    except (ValueError, TypeError):
        await callback.answer(t("dcat_unavailable"), show_alert=True)
        return
    async with _locks[db_user.id]:
        state_data = await state.get_data()
        if (await state.get_state() != ManualCatalog.renewing.state
            or state_data.get("dcat_sub") != sub_id or state_data.get("dcat_id") != item_id
            or state_data.get("dcat_rev") != revision or state_data.get("dcat_token") != token):
            await callback.answer(t("dcat_already"), show_alert=True)
            return
        await state.clear()
        item = await catalog.get_published(item_id)
        if item is None or item.revision != revision:
            await callback.answer(t("cshop_changed"), show_alert=True)
            return
        status, sub, log_id = await db_repo.reserve_dealer_subscription_renewal(
            db_user.id, sub_id, catalog.provision_plan(item), 0.01,
            catalog_tariff_id=item_id, catalog_expected_revision=revision,
        )
        if status != "reserved" or sub is None or log_id is None:
            await callback.answer(t("dealer_balance_low" if status == "insufficient"
                                    else "dealer_renew_unavailable"), show_alert=True)
            return
        await callback.answer("⏳")
        provider = get_vpn_provider()
        prior = None
        confirmed = False
        try:
            current = await provider.get_client(sub.xui_email)
            if current is None:
                raise RuntimeError("Managed user missing from provider")
            prior = parse_time(current.get("expireAt"))
            expiry = await grant_monthly(
                provider, sub.xui_email, item.period_days, limit_ip=item.devices,
                traffic_gb=item.traffic_gb, squad_uuid=item.internal_squad_uuid,
                custom_terms=True,
            )
            confirmed = True
            if not await db_repo.complete_dealer_subscription_renewal(
                db_user.id, sub.id, log_id, catalog.provision_plan(item), expiry, None,
            ):
                raise RuntimeError("Paid renewal issued but DB completion failed")
        except Exception as exc:
            try:
                remote = await provider.get_client(sub.xui_email)
                after = parse_time((remote or {}).get("expireAt"))
            except Exception:
                after = None
            if confirmed or (prior and after and after > prior):
                log.exception("Renewal needs reconciliation")
                await notify_admins(f"🚨 Дилерская подписка #{sub.id} возможно продлена; проверь панель. Баланс удержан.")
                await callback.message.answer(t("dcat_manual_reconcile"))
            else:
                await db_repo.rollback_dealer_subscription_renewal(db_user.id, sub.id, log_id, str(exc))
                log.exception("Dealer catalogue renewal rolled back")
                await callback.message.answer(t("dealer_renew_failed"))
            return
    await notify_dealer_activity(db_user, "📅 Продление по каталогу",
        f"#{sub.id} · {item.name_fa} · закупка USD {wholesale_usd(item.period_days,item.devices,item.traffic_gb):.3f}")
    from app.handlers.dealer import _show_subscription_card
    await _show_subscription_card(callback, t, db_user.id, sub.id, page)
