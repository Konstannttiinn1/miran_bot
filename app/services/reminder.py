import asyncio
import logging

from sqlalchemy import select

from app.bot import bot
from app.database.engine import async_session_factory
from app.database.models import Subscription, utcnow
from app.middlewares.i18n import get_text
from app.repositories import db_repo

log = logging.getLogger(__name__)


async def _send(user, key: str, sub) -> None:
    try:
        await bot.send_message(
            user.telegram_id,
            get_text(user.lang or "fa", key,
                     expire_date=sub.expire_at.strftime("%d.%m.%Y")),
        )
    except Exception:
        log.exception("reminder failed user=%s", user.telegram_id)


async def _set_flag(sub_id: int, field: str) -> None:
    async with async_session_factory() as session:
        sub = await session.get(Subscription, sub_id)
        if sub is not None:
            setattr(sub, field, True)
            await session.commit()


async def check_subscriptions() -> None:
    now = utcnow()
    async with async_session_factory() as session:
        result = await session.execute(select(Subscription))
        subs = list(result.scalars().all())

    for sub in subs:
        if sub.expire_at <= now:
            continue
        days_left = (sub.expire_at - now).total_seconds() / 86400
        user = await db_repo.get_user_by_id(sub.user_id)
        if user is None:
            continue

        if days_left <= 3 and not sub.notified_3d:
            await _send(user, "expiring_3d", sub)
            await _set_flag(sub.id, "notified_3d")
        if days_left <= 1 and not sub.notified_1d:
            await _send(user, "expiring_1d", sub)
            await _set_flag(sub.id, "notified_1d")


async def reminder_loop() -> None:
    """Раз в час проверяет сроки подписок."""
    while True:
        try:
            await check_subscriptions()
        except Exception:
            log.exception("reminder loop error")
        await asyncio.sleep(3600)