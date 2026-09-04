import logging
from html import escape as h

from aiogram import F, Router, types

from app.bot import bot
from app.keyboards.builders import back_kb, dealer_menu_kb
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services.subscription import grant_vpn
from app.utils.emojis import strip_custom_emoji_tags
from app.utils.notifications import notify_admins

log = logging.getLogger(__name__)

router = Router()
router.callback_query.middleware(I18nMiddleware())


@router.callback_query(F.data == "back:dealer")
async def back_dealer(callback: types.CallbackQuery, t, lang, db_user):
    await callback.answer()
    await callback.message.edit_text(t("dealer_menu_text"), reply_markup=dealer_menu_kb(t))


@router.callback_query(F.data == "dealer:balance")
async def dealer_balance(callback: types.CallbackQuery, t, lang, db_user):
    await callback.answer()
    await callback.message.answer(
        t("dealer_balance_msg", balance=float(db_user.dealer_balance)),
        reply_markup=back_kb(t, "back:dealer"),
    )


@router.callback_query(F.data == "dealer:history")
async def dealer_history(callback: types.CallbackQuery, t, lang, db_user):
    logs = await db_repo.list_dealer_logs(db_user.id)
    await callback.answer()
    if not logs:
        await callback.message.answer(t("dealer_history_msg", logs="—"),
                                      reply_markup=back_kb(t, "back:dealer"))
        return
    lines = [
        f"• {lg.created_at:%d.%m %H:%M} — {lg.action}" + (f" — #{lg.order_id}" if lg.order_id else "")
        for lg in logs
    ]
    await callback.message.answer(t("dealer_history_msg", logs="\n".join(lines)),
                                  reply_markup=back_kb(t, "back:dealer"))


@router.callback_query(F.data.startswith("dealer_ok:"))
async def dealer_approve(callback: types.CallbackQuery, t, lang, db_user):
    order_id = int(callback.data.split(":")[1])
    order = await db_repo.get_order(order_id)

    if order is None or order.status != "pending_dealer":
        await callback.answer(strip_custom_emoji_tags(t("dealer_already_processed")), show_alert=True)
        return

    price = float(order.amount)
    if float(db_user.dealer_balance) < price:
        await callback.answer(strip_custom_emoji_tags(t("dealer_insufficient")), show_alert=True)
        return

    await db_repo.change_dealer_balance(db_user.id, -price)
    await db_repo.update_order(order_id, status="confirmed_by_dealer", dealer_id=db_user.id)
    await db_repo.create_dealer_log(db_user.id, "confirm", order_id, {"amount": price})

    user = await db_repo.get_user_by_id(order.user_id)
    try:
        link, expire_at = await grant_vpn(user.id, user.telegram_id, order.plan)
    except Exception:
        await db_repo.change_dealer_balance(db_user.id, price)
        await db_repo.update_order(order_id, status="failed")
        log.exception("3x-UI failed at dealer confirm, rollback done")
        await callback.answer("❌ 3x-UI", show_alert=True)
        return

    await callback.answer("✅")
    await bot.send_message(
        user.telegram_id,
        get_text(user.lang, "payment_success",
                 link=h(link), expire_date=expire_at.strftime("%d.%m.%Y")),
    )
    await notify_admins(
        f"💰 Дилер {db_user.username or db_user.telegram_id} подтвердил заказ "
        f"#{order_id} ({order.plan}, ${price})."
    )
    log.info("Dealer %s confirmed order %s", db_user.telegram_id, order_id)


@router.callback_query(F.data.startswith("dealer_no:"))
async def dealer_reject(callback: types.CallbackQuery, t, lang, db_user):
    order_id = int(callback.data.split(":")[1])
    order = await db_repo.get_order(order_id)

    if order is None or order.status != "pending_dealer":
        await callback.answer(strip_custom_emoji_tags(t("dealer_already_processed")), show_alert=True)
        return

    await db_repo.update_order(order_id, status="failed")
    await db_repo.create_dealer_log(db_user.id, "reject", order_id)

    user = await db_repo.get_user_by_id(order.user_id)
    await callback.answer("❌")
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