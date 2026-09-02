import asyncio
import logging
from html import escape as h

from app.bot import bot
from app.middlewares.i18n import get_text
from app.repositories import db_repo
from app.services import heleket
from app.services.subscription import grant_vpn
from app.utils.notifications import notify_admins

log = logging.getLogger(__name__)


async def check_pending_payments() -> None:
    """Опрашивает Heleket по всем ожидающим крипто-заказам."""
    orders = await db_repo.list_pending_crypto_orders()
    for order in orders:
        try:
            payment = await heleket.get_payment(order.external_id)
        except Exception:
            log.exception("Heleket: не удалось проверить заказ %s", order.id)
            continue

        if not heleket.is_paid(payment):
            continue

        await db_repo.update_order(order.id, status="paid")
        user = await db_repo.get_user_by_id(order.user_id)
        if user is None:
            continue

        link, expire_at = await grant_vpn(user.id, user.telegram_id, order.plan)
        await bot.send_message(
            user.telegram_id,
            get_text(user.lang, "payment_success",
                     link=h(link), expire_date=expire_at.strftime("%d.%m.%Y")),
        )
        await notify_admins(f"💎 Крипто-оплата заказа #{order.id} ({order.plan}) — VPN выдан")
        log.info("Заказ %s оплачен, VPN выдан", order.id)


async def payment_checker_loop() -> None:
    """Фоновая задача: проверка оплат раз в 30 секунд."""
    while True:
        await asyncio.sleep(30)
        try:
            await check_pending_payments()
        except Exception:
            log.exception("payment checker loop error")