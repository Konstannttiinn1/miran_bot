import logging
from datetime import timedelta
from html import escape as h

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from app.bot import bot
from app.config import settings
from app.database.models import utcnow
from app.handlers.states import DealerManagedSubscription
from app.keyboards.builders import (
    back_kb,
    dealer_buy_confirm_kb,
    dealer_buy_plans_kb,
    dealer_created_name_kb,
    dealer_menu_kb,
    dealer_renew_confirm_kb,
    dealer_renew_plans_kb,
    dealer_search_results_kb,
    dealer_subscription_card_kb,
    dealer_subscriptions_kb,
)
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services.subscription import grant_vpn
from app.services.vpn_provider import (
    create_test_client,
    get_vpn_provider,
    subscription_link,
)
from app.utils.emojis import strip_custom_emoji_tags
from app.utils.menu import send_with_logo
from app.utils.tariffs import PLANS, get_dealer_debit_usd
from app.utils.notifications import notify_admins

log = logging.getLogger(__name__)

router = Router()
router.callback_query.middleware(I18nMiddleware())
router.message.middleware(I18nMiddleware())


async def _dealer_only(callback: types.CallbackQuery, db_user, t) -> bool:
    if db_user.role == "dealer":
        return True
    await callback.answer(strip_custom_emoji_tags(t("dealer_access_only")), show_alert=True)
    return False


def _dealer_log_label(t, action: str) -> str:
    key = "dealer_log_" + action
    label = t(key)
    return label if label != key else t("dealer_log_other")


def _balance_text(value: float) -> str:
    return f"${float(value):,.3f}".rstrip("0").rstrip(".")


@router.callback_query(F.data == "back:dealer")
async def back_dealer(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if not await _dealer_only(callback, db_user, t):
        return
    await state.clear()
    await callback.answer()
    await send_with_logo(
        callback,
        t("dealer_menu_text"),
        reply_markup=dealer_menu_kb(t),
    )


def _dealer_price(plan: str) -> float:
    return get_dealer_debit_usd(
        plan,
        settings.toman_per_usd,
        settings.dealer_discount,
    )


def _sub_status_icon(sub) -> str:
    now = utcnow()
    if sub.expire_at is None:
        return "🟡"
    if sub.expire_at <= now:
        return "🔴"
    if sub.expire_at <= now + timedelta(days=3):
        return "🟡"
    return "🟢"


async def _subscription_link(sub) -> str | None:
    try:
        return await subscription_link(sub.xui_email)
    except Exception:
        return None


async def _show_subscription_card(
    target,
    t,
    dealer_id: int,
    sub_id: int,
    page: int = 0,
) -> None:
    sub = await db_repo.get_dealer_subscription(dealer_id, sub_id)
    if sub is None:
        if isinstance(target, types.CallbackQuery):
            await target.answer(strip_custom_emoji_tags(t("subscription_not_found")), show_alert=True)
        return

    link = await _subscription_link(sub)
    status = _sub_status_icon(sub)
    expire = sub.expire_at.strftime("%d.%m.%Y") if sub.expire_at else "—"
    text = t(
        "dealer_subscription_details", id=sub.id, name=h(sub.client_name),
        traffic=sub.traffic_limit_gb, expire=expire, status=status,
    )
    if link:
        text += f"\n🔗 <code>{h(link)}</code>"
    else:
        text += t("dealer_link_failed")

    if isinstance(target, types.CallbackQuery):
        await target.answer()
    await send_with_logo(
        target,
        text,
        reply_markup=dealer_subscription_card_kb(sub.id, page, link, t=t),
    )


@router.callback_query(F.data == "dealer:balance")
async def dealer_balance(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return
    await callback.answer()
    await send_with_logo(
        callback,
        t("dealer_balance_msg", balance=_balance_text(db_user.dealer_balance)),
        reply_markup=back_kb(t, "back:dealer"),
    )


@router.callback_query(F.data == "dealer:history")
async def dealer_history(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return
    logs = await db_repo.list_dealer_logs(db_user.id)
    await callback.answer()
    if not logs:
        await send_with_logo(
            callback,
            t("dealer_history_msg", logs="—"),
            reply_markup=back_kb(t, "back:dealer"),
        )
        return
    lines = [
        f"• {lg.created_at:%d.%m %H:%M} — {_dealer_log_label(t, lg.action)}"
        + (f" — #{lg.order_id}" if lg.order_id else "")
        for lg in logs
    ]
    await send_with_logo(
        callback,
        t("dealer_history_msg", logs="\n".join(lines)),
        reply_markup=back_kb(t, "back:dealer"),
    )





@router.callback_query(F.data == "dealer:buy_sub")
async def dealer_buy_subscription(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if not await _dealer_only(callback, db_user, t):
        return
    await state.clear()
    await callback.answer()
    await send_with_logo(
        callback,
        t("dealer_buy_intro"),
        reply_markup=dealer_buy_plans_kb(t=t),
    )


@router.callback_query(F.data.startswith("dealer:buyplan:"))
async def dealer_buy_plan(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return
    plan = callback.data.split(":")[2]
    if plan not in PLANS or plan == "test":
        await callback.answer(strip_custom_emoji_tags(t("invalid_plan")), show_alert=True)
        return
    price = _dealer_price(plan)
    traffic = int(PLANS[plan]["traffic_gb"])
    await callback.answer()
    await send_with_logo(
        callback,
        t(
            "dealer_buy_review", traffic=traffic, days=PLANS[plan]["days"],
            price="$" + format(price, ".3f"),
            balance=_balance_text(db_user.dealer_balance),
        ),
        reply_markup=dealer_buy_confirm_kb(plan, t=t),
    )


@router.callback_query(F.data.startswith("dealer:buyconfirm:"))
async def dealer_buy_confirm(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if not await _dealer_only(callback, db_user, t):
        return
    plan = callback.data.split(":")[2]
    if plan not in PLANS or plan == "test":
        await callback.answer(strip_custom_emoji_tags(t("invalid_plan")), show_alert=True)
        return

    price = _dealer_price(plan)
    status, sub = await db_repo.reserve_dealer_subscription_purchase(
        db_user.id, plan, price
    )
    if status == "insufficient":
        await callback.answer(strip_custom_emoji_tags(t("dealer_balance_low")), show_alert=True)
        return
    if status != "reserved" or sub is None:
        await callback.answer(strip_custom_emoji_tags(t("dealer_buy_unavailable")), show_alert=True)
        return

    await callback.answer("⏳")
    tariff = PLANS[plan]
    try:
        await get_vpn_provider().add_client(
            email=sub.xui_email,
            days=int(tariff["days"]),
            limit_ip=1,
            traffic_gb=int(tariff["traffic_gb"]),
        )
        expire_at = utcnow() + timedelta(days=int(tariff["days"]))
        completed = await db_repo.complete_dealer_subscription_purchase(
            sub.id,
            db_user.id,
            expire_at,
            int(tariff["traffic_gb"]),
        )
        if completed is None:
            raise RuntimeError("database completion failed")
    except Exception as exc:
        await db_repo.rollback_dealer_subscription_purchase(
            sub.id, db_user.id, str(exc)
        )
        log.exception("Dealer managed subscription purchase failed")
        await callback.message.answer(
            t("dealer_buy_failed")
        )
        await notify_admins(
            f"🚨 Ошибка покупки управляемой подписки дилером "
            f"{db_user.username or db_user.telegram_id}; баланс возвращён."
        )
        return

    link = await subscription_link(sub.xui_email)
    await state.set_state(DealerManagedSubscription.waiting_name)
    await state.update_data(sub_id=sub.id, page=0)
    await send_with_logo(
        callback,
        t("dealer_subscription_created", link=h(link)),
        reply_markup=dealer_created_name_kb(sub.id, link, t=t),
    )


@router.callback_query(F.data == "dealer:subs")
async def dealer_subscriptions_first(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    await _dealer_subscriptions_page(callback, t, db_user, state, 0)


@router.callback_query(F.data.startswith("dealer:subs:"))
async def dealer_subscriptions_page(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    try:
        page = max(0, int(callback.data.split(":")[2]))
    except (ValueError, IndexError):
        page = 0
    await _dealer_subscriptions_page(callback, t, db_user, state, page)


async def _dealer_subscriptions_page(callback, t, db_user, state: FSMContext, page: int):
    if not await _dealer_only(callback, db_user, t):
        return
    await state.clear()
    per_page = 5
    items, total = await db_repo.list_dealer_subscriptions(
        db_user.id, page=page, per_page=per_page
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page >= total_pages:
        page = total_pages - 1
        items, total = await db_repo.list_dealer_subscriptions(
            db_user.id, page=page, per_page=per_page
        )

    rows = [
        (sub.id, sub.client_name, _sub_status_icon(sub))
        for sub in items
    ]
    text = t("dealer_subscriptions_title", total=total, page=page + 1, pages=total_pages)
    if not items:
        text += t("dealer_subscriptions_empty")

    await callback.answer()
    await send_with_logo(
        callback,
        text,
        reply_markup=dealer_subscriptions_kb(rows, page, total_pages, t=t),
    )


@router.callback_query(F.data.startswith("dealer:sub:"))
async def dealer_subscription_card(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if not await _dealer_only(callback, db_user, t):
        return
    await state.clear()
    parts = callback.data.split(":")
    try:
        sub_id = int(parts[2])
        page = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        await callback.answer(strip_custom_emoji_tags(t("invalid_id")), show_alert=True)
        return
    await _show_subscription_card(callback, t, db_user.id, sub_id, page)


@router.callback_query(F.data.startswith("dealer:rename:"))
async def dealer_subscription_rename(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if not await _dealer_only(callback, db_user, t):
        return
    parts = callback.data.split(":")
    sub_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    sub = await db_repo.get_dealer_subscription(db_user.id, sub_id)
    if sub is None:
        await callback.answer(strip_custom_emoji_tags(t("subscription_not_found")), show_alert=True)
        return
    await state.set_state(DealerManagedSubscription.waiting_name)
    await state.update_data(sub_id=sub_id, page=page)
    await callback.answer()
    await send_with_logo(
        callback,
        t("dealer_rename_prompt", id=sub_id),
        reply_markup=back_kb(t, f"dealer:sub:{sub_id}:{page}"),
    )


@router.callback_query(F.data.startswith("dealer:name_skip:"))
async def dealer_subscription_skip_name(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if not await _dealer_only(callback, db_user, t):
        return
    sub_id = int(callback.data.split(":")[2])
    await state.clear()
    await _show_subscription_card(callback, t, db_user.id, sub_id, 0)


@router.message(DealerManagedSubscription.waiting_name)
async def dealer_subscription_name(message: types.Message, t, lang, db_user, state: FSMContext):
    if db_user.role != "dealer":
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name or name.startswith("/"):
        await message.answer(t("dealer_name_required"))
        return
    if len(name) > 64:
        await message.answer(t("dealer_name_too_long"))
        return

    data = await state.get_data()
    sub_id = int(data.get("sub_id", 0))
    page = int(data.get("page", 0))
    if not sub_id or not await db_repo.rename_dealer_subscription(
        db_user.id, sub_id, name
    ):
        await state.clear()
        await message.answer(t("subscription_not_found"))
        return

    await state.clear()
    sub = await db_repo.get_dealer_subscription(db_user.id, sub_id)
    if sub is None:
        await message.answer(t("subscription_not_found"))
        return
    link = await _subscription_link(sub)
    expire = sub.expire_at.strftime("%d.%m.%Y") if sub.expire_at else "—"
    text = t("dealer_name_saved", id=sub.id, name=h(sub.client_name), expire=expire)
    if link:
        text += f"\n\n🔗 <code>{h(link)}</code>"
    await message.answer(
        text,
        reply_markup=dealer_subscription_card_kb(sub.id, page, link, t=t),
    )


@router.callback_query(F.data == "dealer:search")
async def dealer_subscription_search(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    if not await _dealer_only(callback, db_user, t):
        return
    await state.set_state(DealerManagedSubscription.searching)
    await callback.answer()
    await send_with_logo(
        callback,
        t("dealer_search_prompt"),
        reply_markup=back_kb(t, "dealer:subs"),
    )


@router.message(DealerManagedSubscription.searching)
async def dealer_subscription_search_message(message: types.Message, t, lang, db_user, state: FSMContext):
    if db_user.role != "dealer":
        await state.clear()
        return
    query = (message.text or "").strip()
    if not query:
        await message.answer(t("dealer_search_required"))
        return
    results = await db_repo.search_dealer_subscriptions(db_user.id, query)
    await state.clear()
    rows = [
        (sub.id, sub.client_name, _sub_status_icon(sub))
        for sub in results
    ]
    text = t("dealer_search_results")
    if not results:
        text += t("dealer_search_empty")
    await message.answer(
        text,
        reply_markup=dealer_search_results_kb(rows, t=t),
    )


@router.callback_query(F.data.startswith("dealer:renew:"))
async def dealer_subscription_renew(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return
    parts = callback.data.split(":")
    sub_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    sub = await db_repo.get_dealer_subscription(db_user.id, sub_id)
    if sub is None:
        await callback.answer(strip_custom_emoji_tags(t("subscription_not_found")), show_alert=True)
        return
    await callback.answer()
    await send_with_logo(
        callback,
        t("dealer_renew_intro", id=sub_id),
        reply_markup=dealer_renew_plans_kb(sub_id, page, t=t),
    )


@router.callback_query(F.data.startswith("dealer:renewplan:"))
async def dealer_subscription_renew_plan(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return
    parts = callback.data.split(":")
    sub_id = int(parts[2])
    plan = parts[3]
    page = int(parts[4]) if len(parts) > 4 else 0
    sub = await db_repo.get_dealer_subscription(db_user.id, sub_id)
    if sub is None or plan not in PLANS or plan == "test":
        await callback.answer(strip_custom_emoji_tags(t("invalid_data")), show_alert=True)
        return
    price = _dealer_price(plan)
    traffic = int(PLANS[plan]["traffic_gb"])
    await callback.answer()
    await send_with_logo(
        callback,
        t(
            "dealer_renew_review", id=sub_id, name=h(sub.client_name),
            traffic=traffic, days=PLANS[plan]["days"],
            price="$" + format(price, ".3f"),
        ),
        reply_markup=dealer_renew_confirm_kb(sub_id, plan, page, t=t),
    )


@router.callback_query(F.data.startswith("dealer:renewconfirm:"))
async def dealer_subscription_renew_confirm(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return
    parts = callback.data.split(":")
    sub_id = int(parts[2])
    plan = parts[3]
    page = int(parts[4]) if len(parts) > 4 else 0
    if plan not in PLANS or plan == "test":
        await callback.answer(strip_custom_emoji_tags(t("invalid_plan")), show_alert=True)
        return

    price = _dealer_price(plan)
    status, sub, log_id = await db_repo.reserve_dealer_subscription_renewal(
        db_user.id, sub_id, plan, price
    )
    if status == "insufficient":
        await callback.answer(strip_custom_emoji_tags(t("dealer_balance_low")), show_alert=True)
        return
    if status == "processing":
        await callback.answer(strip_custom_emoji_tags(t("dealer_subscription_processing")), show_alert=True)
        return
    if status != "reserved" or sub is None or log_id is None:
        await callback.answer(strip_custom_emoji_tags(t("dealer_renew_unavailable")), show_alert=True)
        return

    await callback.answer("⏳")
    tariff = PLANS[plan]
    try:
        provider = get_vpn_provider()
        current = await provider.get_client(sub.xui_email)
        if current is None:
            raise RuntimeError("managed client not found in VPN provider")
        await provider.add_client(
            email=sub.xui_email,
            days=int(tariff["days"]),
            limit_ip=1,
            traffic_gb=int(tariff["traffic_gb"]),
        )
        base = sub.expire_at if sub.expire_at and sub.expire_at > utcnow() else utcnow()
        expire_at = base + timedelta(days=int(tariff["days"]))
        ok = await db_repo.complete_dealer_subscription_renewal(
            db_user.id,
            sub.id,
            log_id,
            plan,
            expire_at,
            int(tariff["traffic_gb"]),
        )
        if not ok:
            raise RuntimeError("database renewal completion failed")
    except Exception as exc:
        await db_repo.rollback_dealer_subscription_renewal(
            db_user.id, sub.id, log_id, str(exc)
        )
        log.exception("Dealer managed subscription renewal failed")
        await callback.message.answer(
            t("dealer_renew_failed")
        )
        await notify_admins(
            f"🚨 Ошибка продления управляемой подписки #{sub.id}; "
            f"баланс дилера возвращён."
        )
        return

    await _show_subscription_card(callback, t, db_user.id, sub.id, page)


@router.callback_query(F.data == "dealer:test_link")
async def dealer_test_link(callback: types.CallbackQuery, t, lang, db_user):
    """Выдаёт дилеру отдельную тестовую подписку: 10 дней / 5 ГБ по умолчанию."""
    if not await _dealer_only(callback, db_user, t):
        return

    status, slot_id, used = await db_repo.reserve_dealer_test_slot(
        dealer_id=db_user.id,
        daily_limit=settings.dealer_test_daily_limit,
        tz_name=settings.dealer_test_timezone,
        days=settings.dealer_test_days,
        traffic_gb=settings.dealer_test_traffic_gb,
    )

    if status == "limit":
        await callback.answer(
            strip_custom_emoji_tags(t("dealer_test_daily_limit", limit=settings.dealer_test_daily_limit)),
            show_alert=True,
        )
        return
    if status != "reserved" or slot_id is None:
        await callback.answer(strip_custom_emoji_tags(t("access_denied")), show_alert=True)
        return

    await callback.answer("⏳")

    try:
        xui_email, link = await create_test_client(
            dealer_id=db_user.id,
            days=settings.dealer_test_days,
            traffic_gb=settings.dealer_test_traffic_gb,
        )
    except Exception as exc:
        await db_repo.fail_dealer_test_slot(slot_id, str(exc))
        log.exception("Dealer test link creation failed for dealer %s", db_user.telegram_id)
        await callback.message.answer(
            t("dealer_test_link_failed")
        )
        await notify_admins(
            f"🚨 Не удалось создать дилерскую тест-ссылку для "
            f"{db_user.username or db_user.telegram_id}: {type(exc).__name__}"
        )
        return

    await db_repo.complete_dealer_test_slot(slot_id, xui_email)
    remaining = max(0, settings.dealer_test_daily_limit - used)

    await callback.message.answer(
        t(
            "dealer_test_link_ready", days=settings.dealer_test_days,
            traffic=settings.dealer_test_traffic_gb, link=h(link),
            remaining=remaining, limit=settings.dealer_test_daily_limit,
        )
    )
    log.info(
        "Dealer %s issued test link; used=%s/%s",
        db_user.telegram_id,
        used,
        settings.dealer_test_daily_limit,
    )

@router.callback_query(F.data.startswith("dealer_ok:"))
async def dealer_approve(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return

    order_id = int(callback.data.split(":")[1])
    status, order = await db_repo.claim_dealer_order(order_id, db_user.id)

    if status == "processed" or order is None:
        await callback.answer(
            strip_custom_emoji_tags(t("dealer_already_processed")),
            show_alert=True,
        )
        return
    if status == "insufficient":
        await callback.answer(
            strip_custom_emoji_tags(t("dealer_insufficient")),
            show_alert=True,
        )
        return
    if status != "claimed":
        await callback.answer(strip_custom_emoji_tags(t("access_denied")), show_alert=True)
        return

    await callback.answer("⏳")
    user = await db_repo.get_user_by_id(order.user_id)
    if user is None:
        await db_repo.rollback_dealer_order(order_id, db_user.id)
        await callback.message.answer(t("dealer_user_missing_refund"))
        return

    try:
        link, expire_at = await grant_vpn(user.id, user.telegram_id, order.plan)
    except Exception:
        await db_repo.rollback_dealer_order(order_id, db_user.id)
        log.exception("3x-UI failed at dealer confirm, balance rollback done")
        await callback.message.answer(t("dealer_activation_failed_refund"))
        await notify_admins(
            f"🚨 Дилер подтвердил заказ #{order_id}, но 3x-UI не выдал VPN. "
            f"Баланс дилера возвращён."
        )
        return

    await db_repo.complete_dealer_order(order_id, db_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await bot.send_message(
        user.telegram_id,
        get_text(
            user.lang,
            "payment_success",
            link=h(link),
            expire_date=expire_at.strftime("%d.%m.%Y"),
        ),
    )
    await callback.message.answer(t("dealer_order_confirmed"))
    await notify_admins(
        f"💰 Дилер {db_user.username or db_user.telegram_id} подтвердил заказ "
        f"#{order_id} ({order.plan}); списано ${float(order.amount):.3f}."
    )
    log.info("Dealer %s confirmed order %s", db_user.telegram_id, order_id)


@router.callback_query(F.data.startswith("dealer_no:"))
async def dealer_reject(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user, t):
        return

    order_id = int(callback.data.split(":")[1])
    order = await db_repo.reject_dealer_order(order_id, db_user.id)
    if order is None:
        await callback.answer(
            strip_custom_emoji_tags(t("dealer_already_processed")),
            show_alert=True,
        )
        return

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    user = await db_repo.get_user_by_id(order.user_id)
    await callback.answer("❌")
    if user is not None:
        await bot.send_message(
            user.telegram_id,
            get_text(user.lang, "dealer_rejected_msg", contact=_contact()),
        )
    await notify_admins(
        f"❌ Дилер {db_user.username or db_user.telegram_id} отклонил заказ #{order_id}"
    )


def _contact() -> str:
    from app.config import settings
    return settings.dealer_contact
