from sqlalchemy import select

from app.database.engine import async_session_factory
from app.database.models import DealerLog, Order, Subscription, User


async def get_or_create_user(telegram_id: int, username: str | None = None) -> User:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(telegram_id=telegram_id, username=username)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


async def get_user_by_id(user_id: int) -> User | None:
    async with async_session_factory() as session:
        return await session.get(User, user_id)


async def get_user_by_tg(telegram_id: int) -> User | None:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()


async def set_user_role(telegram_id: int, role: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is not None:
            user.role = role
            await session.commit()


async def set_user_lang(telegram_id: int, lang: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is not None:
            user.lang = lang
            user.lang_selected = True
            await session.commit()


async def set_user_blocked(telegram_id: int, blocked: bool) -> None:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is not None:
            user.is_blocked = blocked
            await session.commit()


async def delete_user_full(telegram_id: int) -> None:
    """Удаляет юзера вместе с подпиской, заказами и логами."""
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            return
        uid = user.id
        for model, column in ((Subscription, Subscription.user_id),
                              (Order, Order.user_id),
                              (DealerLog, DealerLog.dealer_id)):
            rows = await session.execute(select(model).where(column == uid))
            for row in rows.scalars().all():
                await session.delete(row)
        await session.delete(user)
        await session.commit()


async def list_dealers() -> list[User]:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.role == "dealer"))
        return list(result.scalars().all())


async def change_dealer_balance(user_id: int, delta: float) -> None:
    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if user is not None:
            user.dealer_balance = float(user.dealer_balance) + delta
            await session.commit()


async def create_dealer_log(dealer_id: int, action: str, order_id: int | None = None, details: dict | None = None) -> None:
    async with async_session_factory() as session:
        session.add(DealerLog(dealer_id=dealer_id, action=action, order_id=order_id, details=details))
        await session.commit()


async def list_dealer_logs(dealer_id: int, limit: int = 10) -> list[DealerLog]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DealerLog)
            .where(DealerLog.dealer_id == dealer_id)
            .order_by(DealerLog.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


async def list_recent_logs(limit: int = 20) -> list[DealerLog]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DealerLog).order_by(DealerLog.id.desc()).limit(limit)
        )
        return list(result.scalars().all())


async def count_users() -> int:
    from sqlalchemy import func
    async with async_session_factory() as session:
        return (await session.execute(select(func.count(User.id)))).scalar_one()


async def list_users_page(page: int, per_page: int = 10) -> list[User]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).order_by(User.id).limit(per_page).offset(page * per_page)
        )
        return list(result.scalars().all())


async def user_has_order(user_id: int, plan: str) -> bool:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Order).where(Order.user_id == user_id, Order.plan == plan)
        )
        return result.scalar_one_or_none() is not None


async def get_subscription(user_id: int) -> Subscription | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        return result.scalar_one_or_none()


# Алиас для совместимости с payment_checker.py
get_subscription_by_user_id = get_subscription


async def upsert_subscription(user_id: int, xui_email: str, expire_at, traffic_limit_gb: int = 0) -> Subscription:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        sub = result.scalar_one_or_none()
        if sub is None:
            sub = Subscription(user_id=user_id, xui_email=xui_email, expire_at=expire_at,
                               traffic_limit_gb=traffic_limit_gb)
            session.add(sub)
        else:
            if expire_at > sub.expire_at:
                sub.notified_3d = False
                sub.notified_1d = False
            sub.expire_at = expire_at
            sub.traffic_limit_gb = traffic_limit_gb
        await session.commit()
        await session.refresh(sub)
        return sub


async def update_subscription(user_id: int, **fields) -> None:
    """Обновляет поля подписки (expire_at, traffic_limit_gb и т.д.)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        sub = result.scalar_one_or_none()
        if sub is not None:
            for key, value in fields.items():
                setattr(sub, key, value)
            await session.commit()


async def create_order(user_id: int, plan: str, amount: float, currency: str, order_type: str = "purchase") -> Order:
    async with async_session_factory() as session:
        order = Order(user_id=user_id, plan=plan, amount=amount, currency=currency, order_type=order_type)
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return order


async def get_order(order_id: int) -> Order | None:
    async with async_session_factory() as session:
        return await session.get(Order, order_id)


async def update_order(order_id: int, **fields) -> None:
    async with async_session_factory() as session:
        order = await session.get(Order, order_id)
        if order is not None:
            for key, value in fields.items():
                setattr(order, key, value)
            await session.commit()


async def list_pending_crypto_orders() -> list[Order]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Order).where(
                Order.status == "pending",
                Order.currency.in_(["usdt", "ton", "btc"]),
                Order.external_id.isnot(None),
            )
        )
        return list(result.scalars().all())