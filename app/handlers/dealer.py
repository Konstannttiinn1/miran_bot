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


async def _dealer_only(callback: types.CallbackQuery, db_user) -> bool:
    if db_user.role == "dealer":
        return True
    await callback.answer("Dealer access only", show_alert=True)
    return False


def _balance_text(value: float) -> str:
    amount = float(value)
    return f"{int(amount):,}" if amount.is_integer() else f"{amount:,.2f}"


@router.callback_query(F.data == "back:dealer")
async def back_dealer(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user):
        return
    await callback.answer()
    await callback.message.edit_text(t("dealer_menu_text"), reply_markup=dealer_menu_kb(t))


@router.callback_query(F.data == "dealer:balance")
async def dealer_balance(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user):
        return
    await callback.answer()
    await callback.message.answer(
        t("dealer_balance_msg", balance=_balance_text(db_user.dealer_balance)),
        reply_markup=back_kb(t, "back:dealer"),
    )


@router.callback_query(F.data == "dealer:history")
async def dealer_history(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user):
        return
    logs = await db_repo.list_dealer_logs(db_user.id)
    await callback.answer()
    if not logs:
        await callback.message.answer(
            t("dealer_history_msg", logs="—"),
            reply_markup=back_kb(t, "back:dealer"),
        )
        return
    lines = [
        f"• {lg.created_at:%d.%m %H:%M} — {lg.action}"
        + (f" — #{lg.order_id}" if lg.order_id else "")
        for lg in logs
    ]
    await callback.message.answer(
        t("dealer_history_msg", logs="\n".join(lines)),
        reply_markup=back_kb(t, "back:dealer"),
    )


@router.callback_query(F.data.startswith("dealer_ok:"))
async def dealer_approve(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user):
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
        await callback.answer("Access denied", show_alert=True)
        return

    await callback.answer("⏳")
    user = await db_repo.get_user_by_id(order.user_id)
    if user is None:
        await db_repo.rollback_dealer_order(order_id, db_user.id)
        await callback.message.answer("❌ User not found. Balance returned.")
        return

    try:
        link, expire_at = await grant_vpn(user.id, user.telegram_id, order.plan)
    except Exception:
        await db_repo.rollback_dealer_order(order_id, db_user.id)
        log.exception("3x-UI failed at dealer confirm, balance rollback done")
        await callback.message.answer("❌ VPN activation failed. Balance returned.")
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
    await callback.message.answer("✅ Order confirmed. VPN activated.")
    await notify_admins(
        f"💰 Дилер {db_user.username or db_user.telegram_id} подтвердил заказ "
        f"#{order_id} ({order.plan}); списано {float(order.amount):,.0f} туман."
    )
    log.info("Dealer %s confirmed order %s", db_user.telegram_id, order_id)


@router.callback_query(F.data.startswith("dealer_no:"))
async def dealer_reject(callback: types.CallbackQuery, t, lang, db_user):
    if not await _dealer_only(callback, db_user):
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
