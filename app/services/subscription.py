import logging
import random
import string
import uuid as uuid_lib
from datetime import timedelta

from app.config import settings
from app.database.models import utcnow
from app.repositories import db_repo
from app.services.xui_api import XuiClient
from app.utils.tariffs import TEST_TARIFF, get_tariff

log = logging.getLogger(__name__)


class TrialUnavailableError(Exception):
    """Тест уже использован/зарезервирован или есть активная подписка."""


async def grant_vpn(user_id: int, telegram_id: int, plan: str) -> tuple[str, object]:
    if plan != TEST_TARIFF:
        return await _grant_vpn(user_id, telegram_id, plan)

    order = await db_repo.claim_test_order(user_id, telegram_id)
    if order is None:
        raise TrialUnavailableError
    try:
        result = await _grant_vpn(user_id, telegram_id, plan)
        await db_repo.update_order(order.id, status="paid")
    except Exception:
        await db_repo.update_order(order.id, status="failed")
        raise
    return result


async def _grant_vpn(user_id: int, telegram_id: int, plan: str) -> tuple[str, object]:
    tariff = get_tariff(plan)
    days = tariff["days"]
    traffic_gb = tariff["traffic_gb"]
    email = str(telegram_id)

    xui = XuiClient()
    sub_id = await xui.add_client(email=email, days=days, limit_ip=1, traffic_gb=traffic_gb)
    link = f"{settings.xui_sub_url.rstrip('/')}/{sub_id}"

    sub = await db_repo.get_subscription(user_id)
    base = sub.expire_at if sub and sub.expire_at > utcnow() else utcnow()
    expire_at = base + timedelta(days=days)

    await db_repo.upsert_subscription(user_id, email, expire_at, traffic_limit_gb=traffic_gb)
    log.info("VPN выдан: user=%s plan=%s till=%s traffic=%sGB", user_id, plan, expire_at, traffic_gb)
    return link, expire_at


async def extend_subscription(user, days: int) -> None:
    email = str(user.telegram_id)
    xui = XuiClient()
    await xui.extend_client(email, days)

    sub = await db_repo.get_subscription(user.id)
    if sub is None:
        return
    base = sub.expire_at if sub.expire_at > utcnow() else utcnow()
    await db_repo.upsert_subscription(user.id, email, base + timedelta(days=days),
                                      traffic_limit_gb=sub.traffic_limit_gb)
    log.info("Продление %s на %s дн.", email, days)


async def reset_link(user) -> str:
    email = str(user.telegram_id)
    new_sub = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))
    xui = XuiClient()
    await xui.update_client(email, id=str(uuid_lib.uuid4()), subId=new_sub)
    return new_sub


async def set_blocked(user, blocked: bool) -> None:
    await db_repo.set_user_blocked(user.telegram_id, blocked)
    xui = XuiClient()
    await xui.set_enabled(str(user.telegram_id), enabled=not blocked)
