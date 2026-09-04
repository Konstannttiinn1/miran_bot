import logging
from html import escape as h

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, LabeledPrice

from app.bot import bot
from app.config import settings
from app.handlers.states import Purchase
from app.keyboards.builders import dealer_confirm_kb, payment_kb
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services import heleket
from app.services.subscription import grant_vpn
from app.utils.emojis import apply_emoji
from app.utils.menu import send_main_menu, send_with_logo
from app.utils.notifications import notify_admins
from app.utils.tariffs import PLANS, get_dealer_debit_usd, get_stars_price

log = logging.getLogger(__name__)
router = Router()
router.message.middleware(I18nMiddleware())
router.callback_query.middleware(I18nMiddleware())
router.pre_checkout_query.middleware(I18nMiddleware())
EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


def _format_toman(amount: int | float, lang: str) -> str:
    text = f"{int(amount):,}"
    if lang == "fa":
        text = text.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")).replace(",", "،")
        return f"{text} تومان"
    return f"{text} Toman"


def _format_usd(amount: int | float) -> str:
    text = f"{float(amount):.3f}".rstrip("0").rstrip(".")
    return f"${text}"


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


def _invoice_copy(lang: str, traffic: int, stars: int) -> tuple[str, str, str]:
    if lang == "fa":
        return (
            f"IR2_VPN · {traffic} GB",
            f"اشتراک ۳۰ روزه VPN با {traffic} گیگابایت ترافیک",
            f"{traffic} GB · {stars} Stars",
        )
    if lang == "ru":
        return (
            f"IR2_VPN · {traffic} ГБ",
            f"VPN на 30 дней, пакет {traffic} ГБ",
            f"{traffic} ГБ · {stars} Stars",
        )
    return (
        f"IR2_VPN · {traffic} GB",
        f"30-day VPN plan with {traffic} GB of traffic",
        f"{traffic} GB · {stars} Stars",
    )


@router.callback_query(F.data.startswith("plan:"))
async def choose_plan(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    plan = callback.data.split(":")[1]
    if plan not in PLANS:
        await callback.answer("❌", show_alert=True)
        return

    if plan == "test":
        await state.clear()
        order = await db_repo.create_order(db_user.id, "test", 0, "test")
        try:
            link, expire_at = await grant_vpn(db_user.id, db_user.telegram_id, "test")
        except Exception:
            log.exception("test grant failed")
            await db_repo.update_order(order.id, status="failed")
            await callback.message.answer(t("payment_error"))
            return
        await db_repo.update_order(order.id, status="paid")
        await callback.answer()
        await callback.message.answer(
            t("test_activated") + "\n" +
            apply_emoji(f"🔗 {h(link)}", settings.use_custom_emoji)
        )
        return

    await state.update_data(plan=plan)
    await state.set_state(Purchase.choosing_payment)
    await callback.answer()
    await send_with_logo(callback, t("select_payment"), reply_markup=payment_kb(t))


@router.callback_query(F.data.startswith("pay:"))
async def choose_payment(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    method = callback.data.split(":")[1]
    data = await state.get_data()
    plan = data.get("plan", "30gb")
    await callback.answer()

    if plan not in PLANS or plan == "test":
        await state.clear()
        await send_with_logo(callback, t("payment_error"))
        return

    if method == "stars":
        await state.clear()
        stars = get_stars_price(plan, settings.stars_reward_usd)
        order = await db_repo.create_order(db_user.id, plan, stars, "xtr")
        traffic = int(PLANS[plan]["traffic_gb"])
        title, description, label = _invoice_copy(lang, traffic, stars)
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
        retail_toman = int(PLANS[plan]["price_toman"])
        dealer_usd = get_dealer_debit_usd(
            plan,
            settings.toman_per_usd,
            settings.dealer_discount,
        )
        # order.amount для дилерского заказа всегда хранится в USD.
        order = await db_repo.create_order(db_user.id, plan, dealer_usd, "usd")
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
        )
        return

    await state.clear()

    if method == "heleket":
        if not settings.heleket_enabled or not heleket.is_configured():
            await send_with_logo(callback, t("pay_temporarily_disabled"))
            return
        price_usd = PLANS[plan]["price_usd"]
        order = await db_repo.create_order(db_user.id, plan, price_usd, "usdt")
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
            disable_web_page_preview=True,
        )
        return

    await send_with_logo(callback, t("payment_placeholder") + f"\n\n{plan} ➜ {method}")


@router.pre_checkout_query()
async def stars_pre_checkout(query: types.PreCheckoutQuery, t, lang, db_user):
    parsed = _parse_stars_payload(query.invoice_payload)
    if parsed is None:
        await query.answer(ok=False, error_message=_payment_error(lang))
        return

    order_id, payload_user_id, plan = parsed
    order = await db_repo.get_order(order_id)
    expected = get_stars_price(plan, settings.stars_reward_usd) if plan in PLANS else -1
    valid = (
        order is not None
        and order.user_id == db_user.id
        and payload_user_id == db_user.id
        and order.plan == plan
        and order.currency.lower() == "xtr"
        and order.status == "pending"
        and query.currency == "XTR"
        and int(query.total_amount) == expected
        and int(round(float(order.amount))) == expected
    )
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
    expected = get_stars_price(plan, settings.stars_reward_usd) if plan in PLANS else -1
    valid = (
        order is not None
        and order.user_id == db_user.id
        and payload_user_id == db_user.id
        and order.plan == plan
        and order.currency.lower() == "xtr"
        and int(payment.total_amount) == expected
        and int(round(float(order.amount))) == expected
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
    if order.status not in {"pending", "stars_paid_grant_failed"}:
        log.warning("Stars order %s ignored in status %s", order.id, order.status)
        return

    charge_id = payment.telegram_payment_charge_id
    await db_repo.update_order(order.id, status="stars_paid_processing", external_id=charge_id)
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
        t("payment_success", link=h(link), expire_date=expire_at.strftime("%d.%m.%Y"))
    )
    await notify_admins(
        f"⭐ Stars-оплата заказа #{order.id} ({plan}) — "
        f"{payment.total_amount} XTR, VPN выдан"
    )


@router.message(Purchase.waiting_receipt, F.photo)
async def receive_receipt(message: types.Message, t, lang, db_user, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    plan = data.get("plan", "30gb")
    await state.clear()

    photo = message.photo[-1].file_id
    await db_repo.update_order(order_id, receipt_photo_id=photo, status="pending_dealer")
    await message.answer(t("receipt_received"))

    dealers = await db_repo.list_dealers()
    for d in dealers:
        try:
            client_price = int(PLANS[plan]["price_toman"])
            dealer_price_usd = get_dealer_debit_usd(
                plan,
                settings.toman_per_usd,
                settings.dealer_discount,
            )
            await bot.send_photo(
                d.telegram_id,
                photo,
                caption=get_text(
                    d.lang,
                    "dealer_new_order",
                    order_id=order_id,
                    username=db_user.username or db_user.telegram_id,
                    plan=plan,
                    client_amount=_format_toman(client_price, d.lang),
                    dealer_amount=_format_usd(dealer_price_usd),
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
    try:
        await callback.message.edit_reply_markup(reply_markup=EMPTY_KB)
    except Exception:
        pass
    await send_main_menu(callback.message, t)
