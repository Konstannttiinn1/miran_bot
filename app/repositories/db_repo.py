from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database.engine import async_session_factory
from app.database.models import (
    DealerLog,
    DealerSubscription,
    DealerTest,
    Order,
    PromoCode,
    PromoRedemption,
    Subscription,
    TrialClaim,
    User,
    utcnow,
)

async def get_or_create_user(telegram_id: int, username: str | None = None) -> User:
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(telegram_id=telegram_id, username=username)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        elif username and user.username != username:
            user.username = username
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


async def get_user_by_username(username: str) -> User | None:
    """Ищет уже известного боту пользователя по username, без @."""
    clean = username.lstrip("@").strip().lower()
    if not clean:
        return None
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(func.lower(User.username) == clean)
        )
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
        promo_ids = [
            value for value in (
                await session.execute(
                    select(PromoCode.id).where(PromoCode.dealer_id == uid)
                )
            ).scalars().all()
        ]
        redemptions = await session.execute(
            select(PromoRedemption).where(
                (PromoRedemption.user_id == uid)
                | (PromoRedemption.promo_id.in_(promo_ids))
            )
        )
        for redemption in redemptions.scalars().all():
            await session.delete(redemption)
        for model, column in ((Subscription, Subscription.user_id),
                              (Order, Order.user_id),
                              (DealerSubscription, DealerSubscription.dealer_id),
                              (DealerTest, DealerTest.dealer_id),
                              (PromoCode, PromoCode.dealer_id),
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


async def claim_dealer_order(order_id: int, dealer_id: int) -> tuple[str, Order | None]:
    """Атомарно резервирует заказ за дилером и списывает его внутренний баланс."""
    async with async_session_factory() as session:
        async with session.begin():
            order = (
                await session.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
            ).scalar_one_or_none()
            if order is None or order.status != "pending_dealer":
                return "processed", order
            if order.order_type.startswith("managed_traffic:") and order.user_id != dealer_id:
                return "not_dealer", order

            dealer = (
                await session.execute(
                    select(User).where(User.id == dealer_id).with_for_update()
                )
            ).scalar_one_or_none()
            if dealer is None or dealer.role != "dealer":
                return "not_dealer", order

            price = float(order.amount)
            if float(dealer.dealer_balance) < price:
                return "insufficient", order

            claimed = await session.execute(
                update(Order).where(Order.id == order_id, Order.status == "pending_dealer")
                .values(status="dealer_processing").execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                return "processed", order

            dealer.dealer_balance = float(dealer.dealer_balance) - price
            order.status = "dealer_processing"
            order.dealer_id = dealer_id
            await session.flush()
            return "claimed", order


async def complete_dealer_order(order_id: int, dealer_id: int) -> bool:
    async with async_session_factory() as session:
        async with session.begin():
            order = (
                await session.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                order is None
                or order.status != "dealer_processing"
                or order.dealer_id != dealer_id
            ):
                return False
            order.status = "paid"
            session.add(
                DealerLog(
                    dealer_id=dealer_id,
                    action="confirm",
                    order_id=order_id,
                    details={"amount": float(order.amount)},
                )
            )
            return True


async def rollback_dealer_order(order_id: int, dealer_id: int) -> bool:
    """Возвращает баланс, если после подтверждения не удалось выдать VPN."""
    async with async_session_factory() as session:
        async with session.begin():
            order = (
                await session.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                order is None
                or order.status != "dealer_processing"
                or order.dealer_id != dealer_id
            ):
                return False

            dealer = (
                await session.execute(
                    select(User).where(User.id == dealer_id).with_for_update()
                )
            ).scalar_one_or_none()
            if dealer is not None:
                dealer.dealer_balance = float(dealer.dealer_balance) + float(order.amount)

            order.status = "failed"
            session.add(
                DealerLog(
                    dealer_id=dealer_id,
                    action="rollback",
                    order_id=order_id,
                    details={"amount": float(order.amount)},
                )
            )
            return True


async def reject_dealer_order(order_id: int, dealer_id: int) -> Order | None:
    """Первый дилер, отклонивший ещё ожидающий заказ, закрывает его."""
    async with async_session_factory() as session:
        async with session.begin():
            order = (
                await session.execute(
                    select(Order).where(Order.id == order_id).with_for_update()
                )
            ).scalar_one_or_none()
            if order is None or order.status != "pending_dealer":
                return None
            order.status = "failed"
            order.dealer_id = dealer_id
            session.add(
                DealerLog(
                    dealer_id=dealer_id,
                    action="reject",
                    order_id=order_id,
                )
            )
            await session.flush()
            return order




async def reserve_dealer_test_slot(
    dealer_id: int,
    daily_limit: int,
    tz_name: str,
    days: int,
    traffic_gb: int,
) -> tuple[str, int | None, int]:
    """Атомарно резервирует один дневной слот на выдачу тестовой ссылки.

    Возвращает (status, log_id, used_after_reservation).
    В лимит входят уже выданные ссылки и незавершённые резервы.
    """
    if daily_limit <= 0:
        return "limit", None, 0

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    now_utc = datetime.now(timezone.utc)
    local_now = now_utc.astimezone(tz)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = local_end.astimezone(timezone.utc).replace(tzinfo=None)

    async with async_session_factory() as session:
        async with session.begin():
            dealer = (
                await session.execute(
                    select(User).where(User.id == dealer_id).with_for_update()
                )
            ).scalar_one_or_none()
            if dealer is None or dealer.role != "dealer":
                return "not_dealer", None, 0

            used = (
                await session.execute(
                    select(func.count(DealerLog.id)).where(
                        DealerLog.dealer_id == dealer_id,
                        DealerLog.action.in_(["test_link_reserved", "test_link_issued"]),
                        DealerLog.created_at >= start_utc,
                        DealerLog.created_at < end_utc,
                    )
                )
            ).scalar_one()

            if int(used) >= daily_limit:
                return "limit", None, int(used)

            entry = DealerLog(
                dealer_id=dealer_id,
                action="test_link_reserved",
                details={"days": days, "traffic_gb": traffic_gb},
            )
            session.add(entry)
            await session.flush()
            return "reserved", entry.id, int(used) + 1


async def complete_dealer_test_slot(log_id: int, xui_email: str) -> bool:
    """Помечает зарезервированный слот успешно выданным, не сохраняя subId."""
    async with async_session_factory() as session:
        async with session.begin():
            entry = (
                await session.execute(
                    select(DealerLog).where(DealerLog.id == log_id).with_for_update()
                )
            ).scalar_one_or_none()
            if entry is None or entry.action != "test_link_reserved":
                return False
            details = dict(entry.details or {})
            details["xui_email"] = xui_email
            entry.details = details
            entry.action = "test_link_issued"
            return True


async def fail_dealer_test_slot(log_id: int, error: str) -> bool:
    """Освобождает дневной лимит после ошибки 3x-UI, сохраняя запись для аудита."""
    async with async_session_factory() as session:
        async with session.begin():
            entry = (
                await session.execute(
                    select(DealerLog).where(DealerLog.id == log_id).with_for_update()
                )
            ).scalar_one_or_none()
            if entry is None or entry.action != "test_link_reserved":
                return False
            details = dict(entry.details or {})
            details["error"] = str(error)[:200]
            entry.details = details
            entry.action = "test_link_failed"
            return True


async def reserve_dealer_subscription_purchase(
    dealer_id: int,
    plan: str,
    amount: float,
) -> tuple[str, DealerSubscription | None]:
    """Списывает дилерскую цену и создаёт pending-запись отдельной подписки."""
    if amount <= 0:
        return "invalid", None

    async with async_session_factory() as session:
        async with session.begin():
            dealer = (
                await session.execute(
                    select(User).where(User.id == dealer_id).with_for_update()
                )
            ).scalar_one_or_none()
            if dealer is None or dealer.role != "dealer":
                return "not_dealer", None
            if float(dealer.dealer_balance) < amount:
                return "insufficient", None

            dealer.dealer_balance = float(dealer.dealer_balance) - amount
            sub = DealerSubscription(
                dealer_id=dealer_id,
                client_name="",
                xui_email=f"dsub-{dealer_id}-{uuid4().hex[:12]}",
                plan=plan,
                traffic_limit_gb=0,
                status="pending",
                price_paid_usd=amount,
            )
            session.add(sub)
            await session.flush()
            sub.client_name = f"اشتراک #{sub.id}"
            await session.flush()
            return "reserved", sub


async def complete_dealer_subscription_purchase(
    sub_id: int,
    dealer_id: int,
    expire_at: datetime,
    traffic_gb: int,
) -> DealerSubscription | None:
    async with async_session_factory() as session:
        async with session.begin():
            sub = (
                await session.execute(
                    select(DealerSubscription)
                    .where(
                        DealerSubscription.id == sub_id,
                        DealerSubscription.dealer_id == dealer_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if sub is None or sub.status != "pending":
                return None
            sub.expire_at = expire_at
            sub.traffic_limit_gb = traffic_gb
            sub.status = "active"
            session.add(
                DealerLog(
                    dealer_id=dealer_id,
                    action="dealer_sub_purchase",
                    details={
                        "managed_subscription_id": sub.id,
                        "plan": sub.plan,
                        "amount_usd": float(sub.price_paid_usd),
                    },
                )
            )
            await session.flush()
            return sub


async def rollback_dealer_subscription_purchase(
    sub_id: int,
    dealer_id: int,
    error: str,
) -> bool:
    async with async_session_factory() as session:
        async with session.begin():
            sub = (
                await session.execute(
                    select(DealerSubscription)
                    .where(
                        DealerSubscription.id == sub_id,
                        DealerSubscription.dealer_id == dealer_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if sub is None or sub.status != "pending":
                return False
            dealer = (
                await session.execute(
                    select(User).where(User.id == dealer_id).with_for_update()
                )
            ).scalar_one_or_none()
            if dealer is not None:
                dealer.dealer_balance = (
                    float(dealer.dealer_balance) + float(sub.price_paid_usd)
                )
            sub.status = "failed"
            session.add(
                DealerLog(
                    dealer_id=dealer_id,
                    action="dealer_sub_purchase_rollback",
                    details={
                        "managed_subscription_id": sub.id,
                        "amount_usd": float(sub.price_paid_usd),
                        "error": str(error)[:200],
                    },
                )
            )
            return True


async def list_dealer_subscriptions(
    dealer_id: int,
    page: int = 0,
    per_page: int = 5,
) -> tuple[list[DealerSubscription], int]:
    page = max(0, page)
    async with async_session_factory() as session:
        where = (
            DealerSubscription.dealer_id == dealer_id,
            DealerSubscription.status != "failed",
        )
        total = (
            await session.execute(
                select(func.count(DealerSubscription.id)).where(*where)
            )
        ).scalar_one()
        result = await session.execute(
            select(DealerSubscription)
            .where(*where)
            .order_by(DealerSubscription.id.desc())
            .limit(per_page)
            .offset(page * per_page)
        )
        return list(result.scalars().all()), int(total)


async def get_dealer_subscription(
    dealer_id: int,
    sub_id: int,
) -> DealerSubscription | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DealerSubscription).where(
                DealerSubscription.id == sub_id,
                DealerSubscription.dealer_id == dealer_id,
                DealerSubscription.status != "failed",
            )
        )
        return result.scalar_one_or_none()


async def rename_dealer_subscription(
    dealer_id: int,
    sub_id: int,
    client_name: str,
) -> bool:
    clean = " ".join(client_name.split()).strip()
    if not clean:
        return False
    async with async_session_factory() as session:
        async with session.begin():
            sub = (
                await session.execute(
                    select(DealerSubscription)
                    .where(
                        DealerSubscription.id == sub_id,
                        DealerSubscription.dealer_id == dealer_id,
                        DealerSubscription.status != "failed",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if sub is None:
                return False
            sub.client_name = clean[:64]
            return True


async def search_dealer_subscriptions(
    dealer_id: int,
    query: str,
    limit: int = 10,
) -> list[DealerSubscription]:
    clean = query.strip().lstrip("#")
    async with async_session_factory() as session:
        base = [
            DealerSubscription.dealer_id == dealer_id,
            DealerSubscription.status != "failed",
        ]
        if clean.isdigit():
            stmt = select(DealerSubscription).where(
                *base,
                DealerSubscription.id == int(clean),
            )
        else:
            stmt = select(DealerSubscription).where(
                *base,
                DealerSubscription.client_name.ilike(f"%{clean}%"),
            )
        result = await session.execute(
            stmt.order_by(DealerSubscription.id.desc()).limit(limit)
        )
        return list(result.scalars().all())


async def reserve_dealer_subscription_renewal(
    dealer_id: int,
    sub_id: int,
    plan: str,
    amount: float,
) -> tuple[str, DealerSubscription | None, int | None]:
    """Атомарно резервирует деньги на продление конкретной подписки."""
    if amount <= 0:
        return "invalid", None, None
    async with async_session_factory() as session:
        async with session.begin():
            sub = (
                await session.execute(
                    select(DealerSubscription)
                    .where(
                        DealerSubscription.id == sub_id,
                        DealerSubscription.dealer_id == dealer_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if sub is None or sub.status == "failed":
                return "not_found", None, None
            if sub.status in {"pending", "renew_processing"}:
                return "processing", sub, None

            dealer = (
                await session.execute(
                    select(User).where(User.id == dealer_id).with_for_update()
                )
            ).scalar_one_or_none()
            if dealer is None or dealer.role != "dealer":
                return "not_dealer", sub, None
            if float(dealer.dealer_balance) < amount:
                return "insufficient", sub, None

            dealer.dealer_balance = float(dealer.dealer_balance) - amount
            sub.status = "renew_processing"
            entry = DealerLog(
                dealer_id=dealer_id,
                action="dealer_sub_renew_reserved",
                details={
                    "managed_subscription_id": sub.id,
                    "plan": plan,
                    "amount_usd": amount,
                },
            )
            session.add(entry)
            await session.flush()
            return "reserved", sub, entry.id


async def complete_dealer_subscription_renewal(
    dealer_id: int,
    sub_id: int,
    log_id: int,
    plan: str,
    expire_at: datetime,
    traffic_gb: int | None,
) -> bool:
    async with async_session_factory() as session:
        async with session.begin():
            sub = (
                await session.execute(
                    select(DealerSubscription)
                    .where(
                        DealerSubscription.id == sub_id,
                        DealerSubscription.dealer_id == dealer_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            entry = (
                await session.execute(
                    select(DealerLog).where(DealerLog.id == log_id).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                sub is None
                or sub.status != "renew_processing"
                or entry is None
                or entry.action != "dealer_sub_renew_reserved"
            ):
                return False
            sub.plan = plan
            sub.expire_at = expire_at
            if traffic_gb is not None:
                sub.traffic_limit_gb = traffic_gb
            sub.status = "active"
            entry.action = "dealer_sub_renew"
            return True


async def set_dealer_subscription_traffic(dealer_id: int, sub_id: int, traffic_gb: int) -> None:
    async with async_session_factory() as session:
        await session.execute(update(DealerSubscription).where(
            DealerSubscription.id == sub_id, DealerSubscription.dealer_id == dealer_id,
        ).values(traffic_limit_gb=traffic_gb))
        await session.commit()


async def rollback_dealer_subscription_renewal(
    dealer_id: int,
    sub_id: int,
    log_id: int,
    error: str,
) -> bool:
    async with async_session_factory() as session:
        async with session.begin():
            sub = (
                await session.execute(
                    select(DealerSubscription)
                    .where(
                        DealerSubscription.id == sub_id,
                        DealerSubscription.dealer_id == dealer_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            entry = (
                await session.execute(
                    select(DealerLog).where(DealerLog.id == log_id).with_for_update()
                )
            ).scalar_one_or_none()
            if (
                sub is None
                or sub.status != "renew_processing"
                or entry is None
                or entry.action != "dealer_sub_renew_reserved"
            ):
                return False

            dealer = (
                await session.execute(
                    select(User).where(User.id == dealer_id).with_for_update()
                )
            ).scalar_one_or_none()
            amount = float((entry.details or {}).get("amount_usd", 0))
            if dealer is not None:
                dealer.dealer_balance = float(dealer.dealer_balance) + amount
            sub.status = "active"
            details = dict(entry.details or {})
            details["error"] = str(error)[:200]
            entry.details = details
            entry.action = "dealer_sub_renew_rollback"
            return True


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
            select(Order.id).where(Order.user_id == user_id, Order.plan == plan).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def can_claim_test(user_id: int) -> bool:
    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if user is None or user.is_blocked:
            return False
        if await session.get(TrialClaim, user.telegram_id) is not None:
            return False
        has_history = await session.scalar(
            select(Order.id).where(Order.user_id == user_id, Order.plan == "test").limit(1)
        )
        if has_history is not None:
            return False
        active = await session.scalar(
            select(Subscription.id).where(
                Subscription.user_id == user_id,
                Subscription.expire_at > utcnow(),
            ).limit(1)
        )
        return active is None


async def claim_test_order(user_id: int, telegram_id: int) -> Order | None:
    """Резервирует тест и заказ одной транзакцией ДО обращения к панели.

    Уникальный Telegram ID в trial_claims защищает и от параллельных
    запросов из разных процессов. Ошибка/таймаут панели не освобождает
    резерв: VPN мог быть создан до потери ответа.
    """
    async with async_session_factory() as session:
        async with session.begin():
            user = await session.get(User, user_id)
            if user is None or user.telegram_id != telegram_id or user.is_blocked:
                return None
            if await session.scalar(
                select(Order.id).where(Order.user_id == user_id, Order.plan == "test").limit(1)
            ) is not None:
                return None
            if await session.scalar(
                select(Subscription.id).where(
                    Subscription.user_id == user_id,
                    Subscription.expire_at > utcnow(),
                ).limit(1)
            ) is not None:
                return None

            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                insert = pg_insert
            elif dialect == "sqlite":
                insert = sqlite_insert
            else:
                raise RuntimeError(f"Unsupported trial database: {dialect}")
            claimed = await session.scalar(
                insert(TrialClaim)
                .values(telegram_id=telegram_id, created_at=utcnow())
                .on_conflict_do_nothing(index_elements=[TrialClaim.telegram_id])
                .returning(TrialClaim.telegram_id)
            )
            if claimed is None:
                return None

            order = Order(
                user_id=user_id, plan="test", amount=0,
                currency="test", status="test_processing",
            )
            session.add(order)
            await session.flush()
        return order


async def get_subscription(user_id: int) -> Subscription | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        return result.scalar_one_or_none()


# Алиас для совместимости с payment_checker.py
get_subscription_by_user_id = get_subscription


async def upsert_subscription(
    user_id: int, xui_email: str, expire_at,
    traffic_limit_gb: int | None = 0, *, only_extend: bool = False,
) -> Subscription:
    """Atomic insert/update; paid concurrent grants cannot shorten validity."""
    async with async_session_factory() as session:
        dialect = session.get_bind().dialect.name
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        later = expire_at > Subscription.expire_at
        statement = insert(Subscription).values(
            user_id=user_id, xui_email=xui_email, expire_at=expire_at,
            traffic_limit_gb=traffic_limit_gb or 0,
        ).on_conflict_do_update(index_elements=[Subscription.user_id], set_={
            "expire_at": case((later, expire_at), else_=Subscription.expire_at) if only_extend else expire_at,
            "traffic_limit_gb": traffic_limit_gb if traffic_limit_gb is not None else Subscription.traffic_limit_gb,
            "notified_3d": case((later, False), else_=Subscription.notified_3d),
            "notified_1d": case((later, False), else_=Subscription.notified_1d),
        }).returning(Subscription)
        sub = (await session.execute(statement)).scalar_one()
        await session.commit()
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


async def claim_payment_order(order_id: int, status: str, external_id: str | None = None) -> bool:
    """A payment is fulfilled once even with duplicate events or two workers."""
    values = {"status": status}
    if external_id is not None:
        values["external_id"] = external_id
    async with async_session_factory() as session:
        result = await session.execute(
            update(Order).where(Order.id == order_id, Order.status == "pending").values(**values)
        )
        await session.commit()
        return result.rowcount == 1


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
