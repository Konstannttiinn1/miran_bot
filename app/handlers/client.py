import logging
from html import escape as h

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, LabeledPrice

from app.bot import bot
from app.config import settings
from app.handlers.states import Purchase
from app.keyboards.builders import back_kb, dealer_confirm_kb, devices_kb, payment_kb, plans_kb
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services import heleket
from app.services.vpn_provider import provider_name
from app.services.subscription import TrialUnavailableError, can_buy_traffic, grant_vpn
from app.utils.emojis import apply_emoji, strip_custom_emoji_tags
from app.utils.menu import send_main_menu, send_with_logo
from app.utils.notifications import notify_admins
from app.utils.tariffs import (
    PLANS, RETIRED_PLANS, TRAFFIC_TOPUP,
    format_toman, get_dealer_debit_usd, get_period_plan, get_plan_intro, get_price_display,
    get_stars_price, get_tariff, get_toman_price, get_usd_price,
    is_sale_product, usd_to_toman,
)

log = logging.getLogger(__name__)
router = Router()
router.message.middleware(I18nMiddleware())
router.callback_query.middleware(I18nMiddleware())
router.pre_checkout_query.middleware(I18nMiddleware())
EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


def _format_toman(amount: int | float, lang: str) -> str:
    if lang == "fa":
        return format_toman(int(amount))
    text = f"{int(amount):,}"
    return get_text(lang, "currency_toman", amount=text)


def _format_usd(amount: int | float, lang: str = "en") -> str:
    if lang == "fa":
        return format_toman(usd_to_toman(amount))
    text = f"{float(amount):.3f}".rstrip("0").rstrip(".")
    return f"${text}"


def _product_label(plan: str, lang: str) -> str:
    tariff = get_tariff(plan)
    if plan == TRAFFIC_TOPUP:
        return get_text(lang, "traffic_invoice_title")
    key = "monthly_product" if tariff.get("monthly") else "legacy_product"
    return get_text(
        lang, key, traffic=tariff["traffic_gb"], days=tariff["days"],
        devices=tariff.get("devices", 1),
    )


def _stars_payload(order_id: int, user_id: int, plan: str) -> str:
    return f"stars:{order_id}:{user_id}:{plan}"


def _parse_stars_payload(payload: str) -> tuple[int, int, str] | None:
    try:
        prefix, order_id, user_id, plan = payload.split(":", 3)
        if prefix != "stars":
            return None
        return int(order_id), int(user_id), plan
    except (TypeError, ValueError):
        return None


def _payment_error(lang: str, activation: bool = False) -> str:
    if activation:
        return {
            "fa": "پرداخت دریافت شد، اما فعال‌سازی خودکار انجام نشد. لطفاً با پشتیبانی تماس بگیرید.",
            "ru": "Оплата получена, но автоматическая активация не сработала. Напиши в поддержку.",
        }.get(lang, "Payment was received, but automatic activation failed. Please contact support.")
    return {
        "fa": "سفارش یا مبلغ پرداخت معتبر نیست. لطفاً دوباره تلاش کنید.",
        "ru": "Заказ или сумма оплаты не совпадают. Попробуй ещё раз.",
    }.get(lang, "The order or payment amount is invalid. Please try again.")


def _invoice_copy(lang: str, plan: str, stars: int) -> tuple[str, str, str]:
    tariff = get_tariff(plan)
    traffic = int(tariff["traffic_gb"])
    if plan == TRAFFIC_TOPUP:
        title = get_text(lang, "traffic_invoice_title")
        return title, get_text(lang, "traffic_invoice_description"), f"50 GB · {stars} Stars"
    if lang == "fa":
        return (
            f"ORION_VPN · {traffic} GB",
            _product_label(plan, lang),
            f"{traffic} GB · {stars} Stars",
        )
    if lang == "ru":
        return (
            f"ORION_VPN · {traffic} ГБ",
            _product_label(plan, lang),
            f"{traffic} ГБ · {stars} Stars",
        )
    return (
        f"ORION_VPN · {traffic} GB",
        _product_label(plan, lang),
        f"{traffic} GB · {stars} Stars",
    )


@router.callback_query(F.data.startswith("plan:"))
async def choose_plan(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    plan = callback.data.split(":")[1]
    if not is_sale_product(plan):
        await callback.answer("❌", show_alert=True)
        return

    if plan == "test":
        await state.clear()
        # Не держим callback открытым, пока панель отвечает (до 30 секунд).
        await callback.answer()
        try:
            link, expire_at = await grant_vpn(db_user.id, db_user.telegram_id, "test")
        except TrialUnavailableError:
            await callback.message.answer(t("test_unavailable"))
            return
        except Exception:
            log.exception("test grant failed")
            await callback.message.answer(t("test_activation_failed"))
            return
        await callback.message.answer(
            t("test_activated") + "\n" +
            apply_emoji(f"🔗 {h(link)}", settings.use_custom_emoji)
        )
        return

    if plan == TRAFFIC_TOPUP and not await can_buy_traffic(db_user.id):
        await callback.answer(strip_custom_emoji_tags(t("traffic_requires_subscription")), show_alert=True)
        return
    if get_tariff(plan).get("monthly"):
        await state.update_data(plan=None, order_id=None)
        await state.set_state(Purchase.choosing_devices)
        await callback.answer()
        await send_with_logo(
            callback,
            t("select_devices", months=PLANS[plan]["months"]),
            reply_markup=devices_kb(plan, t),
        )
        return
    await state.update_data(plan=plan)
    await state.set_state(Purchase.choosing_payment)
    await callback.answer()
    text = t("select_payment")
    if plan == TRAFFIC_TOPUP:
        text = t("traffic_topup_intro", price=get_price_display(plan, lang)) + "\n\n" + text
    await send_with_logo(callback, text, reply_markup=payment_kb(t))


@router.callback_query(F.data.startswith("device:"))
async def choose_devices(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    plan = callback.data.split(":")[1]
    if plan not in PLANS or not PLANS[plan].get("monthly"):
        await callback.answer(strip_custom_emoji_tags(t("invalid_plan")), show_alert=True)
        return
    await state.update_data(plan=plan, order_id=None)
    await state.set_state(Purchase.choosing_payment)
    await callback.answer()
    await send_with_logo(
        callback,
        t("purchase_summary", product=_product_label(plan, lang), price=get_price_display(plan, lang))
        + "\n\n" + t("select_payment"),
        reply_markup=payment_kb(t, f"plan:{get_period_plan(plan)}"),
    )


@router.callback_query(F.data == "back:plans")
async def back_to_plans(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    data = await state.get_data()
    with_test = (
        bool(data.get("plans_with_test", False))
        and await db_repo.can_claim_test(db_user.id)
    )
    await state.update_data(plans_with_test=with_test, plan=None, order_id=None)
    await state.set_state(Purchase.choosing_plan)
    await callback.answer()
    await send_with_logo(
        callback,
        get_plan_intro(t, lang),
        reply_markup=plans_kb(t, with_test=with_test, lang=lang),
    )


@router.callback_query(F.data.startswith("pay:"))
async def choose_payment(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    method = callback.data.split(":")[1]
    data = await state.get_data()
    plan = data.get("plan", "")
    await callback.answer()

    if not is_sale_product(plan) or plan == "test":
        await state.clear()
        await send_with_logo(callback, t("payment_error"))
        return

    if plan == TRAFFIC_TOPUP and not await can_buy_traffic(db_user.id):
        await state.clear()
        await send_with_logo(callback, t("traffic_requires_subscription"))
        return
    order_type = "traffic_topup" if plan == TRAFFIC_TOPUP else "purchase"
    if get_tariff(plan).get("monthly") and provider_name() != "remnawave":
        await send_with_logo(callback, t("sales_unavailable"))
        return
    back_callback = f"plan:{get_period_plan(plan)}" if get_tariff(plan).get("monthly") else "back:plans"

    if method == "stars":
        await state.clear()
        stars = get_stars_price(plan, settings.stars_reward_usd)
        order = await db_repo.create_order(db_user.id, plan, stars, "xtr", order_type=order_type)
        title, description, label = _invoice_copy(lang, plan, stars)
        try:
            await callback.message.answer_invoice(
                title=title,
                description=description,
                payload=_stars_payload(order.id, db_user.id, plan),
                currency="XTR",
                prices=[LabeledPrice(label=label, amount=stars)],
            )
        except Exception:
            log.exception("Stars invoice creation failed: order %s", order.id)
            await db_repo.update_order(order.id, status="failed")
            await send_with_logo(callback, t("payment_error"))
        return

    if method == "dealer":
        retail_toman = get_toman_price(plan)
        dealer_usd = get_dealer_debit_usd(
            plan,
            settings.toman_per_usd,
            settings.dealer_discount,
        )
        # order.amount для дилерского заказа всегда хранится в USD.
        order = await db_repo.create_order(db_user.id, plan, dealer_usd, "usd", order_type=order_type)
        await state.update_data(plan=plan, order_id=order.id)
        await state.set_state(Purchase.waiting_receipt)
        await send_with_logo(
            callback,
            t(
                "dealer_pay_instructions",
                amount=_format_toman(retail_toman, lang),
                card=settings.dealer_card_number,
                order_id=order.id,
            ),
            reply_markup=back_kb(t, "back:main"),
        )
        return

    if method == "heleket":
        if not settings.heleket_enabled or not heleket.is_configured():
            await send_with_logo(
                callback,
                t("pay_temporarily_disabled"),
                reply_markup=payment_kb(t, back_callback),
            )
            return
        await state.clear()
        price_usd = get_usd_price(plan)
        order = await db_repo.create_order(db_user.id, plan, price_usd, "usdt", order_type=order_type)
        try:
            invoice = await heleket.create_invoice(price_usd, order.id)
        except Exception:
            log.exception("Heleket invoice creation failed")
            await db_repo.update_order(order.id, status="failed")
            await send_with_logo(callback, t("payment_error"))
            return
        await db_repo.update_order(order.id, external_id=invoice["uuid"])
        await send_with_logo(
            callback,
            t("invoice_created", order_id=order.id, url=invoice["url"]),
            reply_markup=back_kb(t, "back:main"),
        )
        return

    await send_with_logo(
        callback,
        t("payment_placeholder") + f"\n\n{plan} ➜ {method}",
        reply_markup=payment_kb(t, back_callback),
    )


@router.pre_checkout_query()
async def stars_pre_checkout(query: types.PreCheckoutQuery, t, lang, db_user):
    parsed = _parse_stars_payload(query.invoice_payload)
    if parsed is None:
        await query.answer(ok=False, error_message=_payment_error(lang))
        return

    order_id, payload_user_id, plan = parsed
    order = await db_repo.get_order(order_id)
    # Сумма зафиксирована в заказе: обновление прайса не ломает старый invoice.
    expected = int(round(float(order.amount))) if order is not None else -1
    valid = (
        order is not None
        and order.user_id == db_user.id
        and payload_user_id == db_user.id
        and order.plan == plan
        and plan != "test"
        and (is_sale_product(plan) or plan in RETIRED_PLANS)
        and expected > 0
        and float(order.amount) == expected
        and order.currency.lower() == "xtr"
        and order.status == "pending"
        and query.currency == "XTR"
        and int(query.total_amount) == expected
    )
    if valid and plan == TRAFFIC_TOPUP:
        valid = await can_buy_traffic(db_user.id)
    await query.answer(ok=valid, error_message=None if valid else _payment_error(lang))


@router.message(F.successful_payment)
async def stars_successful_payment(message: types.Message, t, lang, db_user):
    payment = message.successful_payment
    if payment is None or payment.currency != "XTR":
        return

    parsed = _parse_stars_payload(payment.invoice_payload)
    if parsed is None:
        await notify_admins(f"⚠️ Invalid Stars payload from {db_user.telegram_id}")
        return

    order_id, payload_user_id, plan = parsed
    order = await db_repo.get_order(order_id)
    expected = int(round(float(order.amount))) if order is not None else -1
    valid = (
        order is not None
        and order.user_id == db_user.id
        and payload_user_id == db_user.id
        and order.plan == plan
        and plan != "test"
        and (is_sale_product(plan) or plan in RETIRED_PLANS)
        and expected > 0
        and float(order.amount) == expected
        and order.currency.lower() == "xtr"
        and int(payment.total_amount) == expected
    )
    if not valid:
        await notify_admins(
            f"⚠️ Stars validation failed: order #{order_id}, "
            f"user {db_user.telegram_id}, {payment.total_amount} XTR"
        )
        await message.answer(_payment_error(lang))
        return

    if order.status == "paid":
        return
    if order.status != "pending":
        log.warning("Stars order %s ignored in status %s", order.id, order.status)
        return

    charge_id = payment.telegram_payment_charge_id
    if not await db_repo.claim_payment_order(order.id, "stars_paid_processing", charge_id):
        return
    try:
        link, expire_at = await grant_vpn(db_user.id, db_user.telegram_id, plan)
    except Exception:
        log.exception("Stars paid but VPN grant failed: order %s", order.id)
        await db_repo.update_order(order.id, status="stars_paid_grant_failed")
        await notify_admins(
            f"🚨 Stars оплачены, но VPN не выдан: заказ #{order.id}, "
            f"user {db_user.telegram_id}, {payment.total_amount} XTR, charge={charge_id}"
        )
        await message.answer(_payment_error(lang, activation=True))
        return

    await db_repo.update_order(order.id, status="paid")
    await message.answer(
        t("traffic_payment_success" if plan == TRAFFIC_TOPUP else "payment_success",
          link=h(link), expire_date=expire_at.strftime("%d.%m.%Y"))
    )
    await notify_admins(
        f"⭐ Stars-оплата заказа #{order.id} ({plan}) — "
        f"{payment.total_amount} XTR, VPN выдан"
    )


@router.message(Purchase.waiting_receipt, F.photo)
async def receive_receipt(message: types.Message, t, lang, db_user, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    await state.clear()
    order = await db_repo.get_order(order_id) if order_id is not None else None
    if (
        order is None or order.user_id != db_user.id
        or order.currency.lower() != "usd" or order.status != "pending"
        or order.plan == "test"
        or (not is_sale_product(order.plan) and order.plan not in RETIRED_PLANS)
    ):
        await message.answer(t("payment_error"))
        return
    plan = order.plan

    photo = message.photo[-1].file_id
    await db_repo.update_order(order_id, receipt_photo_id=photo, status="pending_dealer")
    await message.answer(t("receipt_received"))

    client_price = get_toman_price(plan)
    dealer_price_usd = float(order.amount)
    # Старый заказ может иметь цену до обновления прайса. Не подменяем
    # указанную клиенту сумму новой розницей в уведомлении дилеру.
    current_debit = round(
        client_price / settings.toman_per_usd * settings.dealer_discount, 3,
    )
    current_price = abs(current_debit - dealer_price_usd) < 1e-9

    dealers = await db_repo.list_dealers()
    for d in dealers:
        try:
            await bot.send_photo(
                d.telegram_id,
                photo,
                caption=get_text(
                    d.lang,
                    "dealer_new_order",
                    order_id=order_id,
                    username=db_user.username or db_user.telegram_id,
                    plan=_product_label(plan, d.lang),
                    client_amount=(
                        _format_toman(client_price, d.lang)
                        if current_price else get_text(d.lang, "dealer_original_amount")
                    ),
                    dealer_amount=_format_usd(dealer_price_usd, d.lang),
                ),
                reply_markup=dealer_confirm_kb(order_id),
            )
        except Exception:
            log.exception("Не удалось уведомить дилера %s", d.telegram_id)

    await notify_admins(
        f"📎 Новый чек по заказу #{order_id} от {db_user.username or db_user.telegram_id}"
    )


@router.message(Purchase.waiting_receipt)
async def receipt_wrong_type(message: types.Message, t, lang, db_user):
    await message.answer(t("send_photo_hint"))


@router.callback_query(F.data == "cancel")
async def cancel_purchase(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    await state.clear()
    await callback.answer()
    await send_main_menu(callback, t)
