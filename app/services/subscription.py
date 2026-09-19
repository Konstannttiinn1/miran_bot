import logging
from datetime import timedelta

from app.config import settings
from app.database.models import utcnow
from app.repositories import db_repo
from app.services.vpn_provider import (
    get_vpn_provider,
    reset_subscription_link,
    subscription_link,
)
from app.utils.tariffs import PLANS

log = logging.getLogger(__name__)


async def grant_vpn(user_id: int, telegram_id: int, plan: str) -> tuple[str, object]:
    days = PLANS[plan]["days"]
    traffic_gb = PLANS[plan]["traffic_gb"]
    email = str(telegram_id)

    provider = get_vpn_provider()
    await provider.add_client(
        email=email,
        days=days,
        limit_ip=1,
        traffic_gb=traffic_gb,
    )
    link = await subscription_link(email)

    sub = await db_repo.get_subscription(user_id)
    base = sub.expire_at if sub and sub.expire_at > utcnow() else utcnow()
    expire_at = base + timedelta(days=days)

    await db_repo.upsert_subscription(user_id, email, expire_at, traffic_limit_gb=traffic_gb)
    log.info("VPN выдан: user=%s plan=%s till=%s traffic=%sGB", user_id, plan, expire_at, traffic_gb)
    return link, expire_at


async def extend_subscription(user, days: int) -> None:
    email = str(user.telegram_id)
    provider = get_vpn_provider()
    await provider.extend_client(email, days)

    sub = await db_repo.get_subscription(user.id)
    if sub is None:
        return
    base = sub.expire_at if sub.expire_at > utcnow() else utcnow()
    await db_repo.upsert_subscription(user.id, email, base + timedelta(days=days),
                                      traffic_limit_gb=sub.traffic_limit_gb)
    log.info("Продление %s на %s дн.", email, days)


async def reset_link(user) -> str:
    return await reset_subscription_link(str(user.telegram_id))


async def set_blocked(user, blocked: bool) -> None:
    await db_repo.set_user_blocked(user.telegram_id, blocked)
    provider = get_vpn_provider()
    await provider.set_enabled(str(user.telegram_id), enabled=not blocked)