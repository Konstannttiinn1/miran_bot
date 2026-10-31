"""Attribution and idempotent sales ledger. No price recalculation or VPN calls."""

import re
import secrets
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.database.models import (
    AdvertisingCampaign, AnalyticsSale, CampaignAttribution, CampaignVisit,
    DealerLog, DealerSubscription, Order, OrderPriceSnapshot, User, utcnow,
)
from app.repositories import db_repo


def _insert(session, model):
    return (sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert)(model)


def order_key(order: Order) -> str:
    return f"order:{order.id}:{order.created_at.replace(tzinfo=None).isoformat()}"


def dealer_log_key(entry: DealerLog) -> str:
    return f"dealer-log:{entry.id}:{entry.created_at.replace(tzinfo=None).isoformat()}"


async def create_campaign(owner_telegram_id: int, name: str):
    name = " ".join(name.split())
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("Название: от 1 до 80 символов")
    async with db_repo.async_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(AdvertisingCampaign).where(
            AdvertisingCampaign.owner_telegram_id == owner_telegram_id,
        ))
        if count >= 200:
            raise ValueError("Достигнут лимит кампаний")
        campaign = AdvertisingCampaign(code="ad_" + secrets.token_hex(8), name=name, owner_telegram_id=owner_telegram_id)
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        return campaign


async def track_start(user, text: str, *, at=None) -> bool:
    parts = text.split(maxsplit=1)
    if (user.role != "client" or user.telegram_id in settings.admin_list or len(parts) != 2
            or parts[0].split("@")[0] != "/start" or not re.fullmatch(r"ad_[A-Za-z0-9_-]{1,60}", parts[1])):
        return False
    async with db_repo.async_session_factory() as session:
        # Serialize with payment recording on the same customer row. Without
        # this PostgreSQL can miss an attribution that is not committed yet.
        locked = await session.get(User, user.id, with_for_update=True)
        if locked is None or locked.telegram_id != user.telegram_id or locked.role != "client":
            return False
        campaign = await session.scalar(select(AdvertisingCampaign).where(
            AdvertisingCampaign.code == parts[1], AdvertisingCampaign.is_active.is_(True),
        ))
        if campaign is None or campaign.owner_telegram_id == user.telegram_id:
            return False
        values = {"campaign_id": campaign.id, "telegram_id": user.telegram_id, "arrived_at": at or utcnow()}
        await session.execute(_insert(session, CampaignVisit).values(**values).on_conflict_do_nothing(
            index_elements=["campaign_id", "telegram_id"],
        ))
        await session.execute(_insert(session, CampaignAttribution).values(**values).on_conflict_do_nothing(
            index_elements=["telegram_id"],
        ))
        await session.commit()
        return True


async def save_quote(session, order: Order, retail_toman: int) -> None:
    if not isinstance(retail_toman, int) or isinstance(retail_toman, bool) or retail_toman <= 0:
        raise ValueError("A positive original Toman quote is required")
    await session.execute(_insert(session, OrderPriceSnapshot).values(
        order_key=order_key(order), retail_toman=retail_toman,
    ).on_conflict_do_nothing(index_elements=["order_key"]))


async def preserve_quote(order: Order, retail_toman: int) -> None:
    async with db_repo.async_session_factory() as session:
        await save_quote(session, order, retail_toman)
        await session.commit()


async def record_order_sale(session, order: Order, *, at=None, estimated=False, lock_customer=True) -> None:
    if order.plan == "test" or order.currency.lower() == "test" or Decimal(str(order.amount)) <= 0:
        return
    at = at or utcnow()
    user = await session.get(User, order.user_id, with_for_update=lock_customer)
    dealer = await session.get(User, order.dealer_id) if order.dealer_id else None
    user_tg = user.telegram_id if user else None
    managed_id = None
    managed_created = None
    name = (user.username or str(user.telegram_id)) if user else "Удалённый пользователь"
    if order.order_type.startswith("managed_traffic:"):
        managed_id = int(order.order_type.split(":", 1)[1])
        sub = await session.get(DealerSubscription, managed_id)
        if sub and sub.created_at <= order.created_at:
            managed_created = sub.created_at
        name = sub.client_name if sub else f"Подписка #{managed_id}"
        user_tg = None
    source = await session.get(CampaignAttribution, user_tg) if user_tg else None
    snapshot = await session.get(OrderPriceSnapshot, order_key(order))
    await session.execute(_insert(session, AnalyticsSale).values(
        event_key=order_key(order), order_id=order.id, user_telegram_id=user_tg,
        dealer_telegram_id=dealer.telegram_id if dealer else None,
        managed_subscription_id=managed_id, managed_created_at=managed_created, customer_name=name,
        campaign_id=source.campaign_id if source and at >= source.arrived_at else None,
        kind="traffic" if order.plan == "traffic50" else "subscription", plan=order.plan,
        amount=Decimal(str(order.amount)), currency=order.currency.upper(),
        retail_toman=snapshot.retail_toman if snapshot else None,
        confirmed_at=at, time_estimated=estimated,
    ).on_conflict_do_nothing(index_elements=["event_key"]))


async def record_managed_sale(session, sub, dealer_id, amount, event_key, *, at=None, estimated=False):
    if Decimal(str(amount)) <= 0:
        return
    dealer = await session.get(User, dealer_id)
    await session.execute(_insert(session, AnalyticsSale).values(
        event_key=event_key, user_telegram_id=None,
        dealer_telegram_id=dealer.telegram_id if dealer else None,
        managed_subscription_id=sub.id, managed_created_at=sub.created_at, customer_name=sub.client_name,
        kind="subscription", plan=sub.plan, amount=Decimal(str(amount)), currency="USD",
        confirmed_at=at or utcnow(), time_estimated=estimated,
    ).on_conflict_do_nothing(index_elements=["event_key"]))


async def backfill_sales() -> None:
    """Import retained successful history once; label unknown payment times."""
    statuses = ("paid", "stars_paid_processing", "stars_paid_grant_failed",
                "crypto_paid_processing", "crypto_paid_grant_failed", "crypto_paid_user_missing")
    async with db_repo.async_session_factory() as session:
        existing = set(await session.scalars(select(AnalyticsSale.event_key)))
        confirmations = {}
        logs = list(await session.scalars(select(DealerLog).where(DealerLog.action.in_(
            ("confirm", "dealer_sub_purchase", "dealer_sub_renew"),
        )).order_by(DealerLog.id)))
        for entry in logs:
            if entry.action == "confirm" and entry.order_id:
                confirmations.setdefault(entry.order_id, entry.created_at)
        orders = list(await session.scalars(select(Order).where(Order.status.in_(statuses), Order.plan != "test")))
        for order in orders:
            if order_key(order) in existing:
                continue
            if order.currency.lower() == "usd" and order.status != "paid":
                continue
            at = confirmations.get(order.id)
            # Historical import must not lock many customers while live dealer
            # payments hold balance/order locks. New financial hooks already
            # record their events atomically before history can observe them.
            await record_order_sale(session, order, at=at or order.created_at, estimated=at is None, lock_customer=False)
        for entry in logs:
            if entry.action not in ("dealer_sub_purchase", "dealer_sub_renew") or dealer_log_key(entry) in existing:
                continue
            data = entry.details or {}
            sub_id = data.get("managed_subscription_id")
            if not sub_id or not data.get("plan") or not data.get("amount_usd"):
                continue
            current = await session.get(DealerSubscription, sub_id)
            if current and (current.dealer_id != entry.dealer_id or current.created_at > entry.created_at):
                current = None
            sub = SimpleNamespace(id=sub_id, client_name=current.client_name if current else f"Подписка #{sub_id}",
                                  created_at=current.created_at if current else None, plan=data["plan"])
            await record_managed_sale(session, sub, entry.dealer_id, data["amount_usd"],
                dealer_log_key(entry), at=entry.created_at, estimated=entry.action == "dealer_sub_renew")
        await session.commit()
