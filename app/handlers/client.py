import logging
from html import escape as h

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from app.bot import bot
from app.config import settings
from app.handlers.states import Purchase
from app.keyboards.builders import dealer_confirm_kb, payment_kb, plans_kb, sub_link_kb
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services import heleket
from app.services.subscription import grant_vpn
from app.utils.emojis import apply_emoji
from app.utils.menu import send_main_menu, send_with_logo
from app.utils.notifications import notify_admins
from app.utils.tariffs import PLANS, get_price_display

log = logging.getLogger(__name__)

router = Router()
router.message.middleware(I18nMiddleware())
router.callback_query.middleware(I18nMiddleware())

EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


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
            t("test_activated")
            + "\n"
            + apply_emoji(f"🔗 {h(link)}", settings.use_custom_emoji)
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

    if method == "dealer":
        # Для дилера: цена в туманах с 50% скидкой
        price_toman = PLANS[plan]["price_dealer_toman"]
        order = await db_repo.create_order(db_user.id, plan, price_toman, "dealer_credit")
        await state.update_data(plan=plan, order_id=order.id)
        await state.set_state(Purchase.waiting_receipt)
        await send_with_logo(
            callback,
            t("dealer_pay_instructions", card=settings.dealer_card_number, order_id=order.id)
        )
        return

    await state.clear()

    if method == "heleket":
        if not settings.heleket_enabled:
            await send_with_logo(callback, t("pay_temporarily_disabled"))
            return

        # Для крипто: цена в USD
        price_usd = PLANS[plan]["price_usd"]
        order = await db_repo.create_order(db_user.id, plan, price_usd, "usdt")
        try:
            invoice = await heleket.create_invoice(price_usd, order.id)
        except Exception:
            log.exception("Heleket: ошибка создания инвойса")
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

    await send_with_logo(callback, t("payment_placeholder") + f"\n\n {plan} ➜ {method}")


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
            # Для дилера: показываем цену в туманах (50% скидка)
            dealer_price = PLANS[plan]["price_dealer_toman"]
            await bot.send_photo(
                d.telegram_id,
                photo,
                caption=get_text(
                    d.lang,
                    "dealer_new_order",
                    order_id=order_id,
                    username=db_user.username or db_user.telegram_id,
                    plan=plan,
                    amount=dealer_price,
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