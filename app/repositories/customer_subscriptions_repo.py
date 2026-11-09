"""Personal VPN subscriptions. Dealer-managed subscriptions are deliberately separate."""
from sqlalchemy import func, select

from app.database.engine import async_session_factory
from app.database.models import Subscription, User


class SubscriptionLimitReached(ValueError):
    """Customer has reached the configured number of extra VPN accounts."""


async def list_subscriptions(user_id: int) -> list[Subscription]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status != "cancelled",
            )
            .order_by(Subscription.is_primary.desc(), Subscription.created_at, Subscription.id)
        )
        return list(result.scalars().all())


async def get_subscription(user_id: int, sub_id: int) -> Subscription | None:
    async with async_session_factory() as session:
        return await session.scalar(
            select(Subscription).where(
                Subscription.id == sub_id,
                Subscription.user_id == user_id,
                Subscription.status != "cancelled",
            )
        )


async def rename_subscription(user_id: int, sub_id: int, new_label: str) -> bool:
    label = new_label.strip()
    if not label or len(label) > 64 or label.startswith("/"):
        return False
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.id == sub_id,
                Subscription.user_id == user_id,
                Subscription.status != "cancelled",
            )
            .with_for_update()
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            return False
        sub.label = label
        await session.commit()
        return True


async def update_subscription(user_id: int, sub_id: int, **fields) -> bool:
    """Update only the requested customer's chosen VPN, never another slot."""
    allowed = {"expire_at", "traffic_limit_gb", "plan", "status", "notified_3d", "notified_1d"}
    if set(fields) - allowed:
        raise ValueError("Unsupported subscription update")
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription)
            .where(Subscription.id == sub_id, Subscription.user_id == user_id)
            .with_for_update()
        )
        sub = result.scalar_one_or_none()
        if sub is None or sub.status == "cancelled":
            return False
        for name, value in fields.items():
            setattr(sub, name, value)
        await session.commit()
        return True


async def count_additional(user_id: int) -> int:
    async with async_session_factory() as session:
        return int(await session.scalar(
            select(func.count()).select_from(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.is_primary.is_(False),
                Subscription.status != "cancelled",
            )
        ) or 0)
