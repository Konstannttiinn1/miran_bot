"""Read models for the analytics Mini App; financial amounts stay in native units."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import ceil
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import String, cast, func, or_, select

from app.config import settings
from app.database.models import (
    AdvertisingCampaign as Campaign, AnalyticsSale as Sale, CampaignAttribution as Attribution,
    CampaignVisit as Visit, DealerSubscription, Subscription, User, utcnow,
)
from app.repositories import db_repo
from app.utils.tariffs import get_tariff

MOSCOW = ZoneInfo("Europe/Moscow")
PAGE_SIZE = 20


def report_period(key: str, *, at=None):
    current = at or utcnow()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    today = current.astimezone(MOSCOW).replace(hour=0, minute=0, second=0, microsecond=0)
    end = today + timedelta(days=1)
    if key == "today":
        start = today
    elif key == "yesterday":
        start, end = today - timedelta(days=1), today
    elif key in ("7d", "30d"):
        start = today - timedelta(days=int(key[:-1]) - 1)
    elif key in ("month", "prev_month"):
        first = today.replace(day=1)
        if key == "month":
            start = first
            end = (first + timedelta(days=32)).replace(day=1)
        else:
            start, end = (first - timedelta(days=1)).replace(day=1), first
    elif key == "all":
        return datetime(1970, 1, 1), end.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        raise ValueError("Unknown period")
    return start.astimezone(timezone.utc).replace(tzinfo=None), end.astimezone(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def number(value) -> str:
    return format(Decimal(str(value or 0)).normalize(), "f")


def product(plan):
    if not plan:
        return None
    try:
        data = get_tariff(plan)
    except KeyError:
        return {"code": plan, "days": None, "devices": None, "traffic_gb": None, "monthly": False}
    return {"code": plan, "days": data["days"], "devices": data.get("devices", 1),
            "traffic_gb": data["traffic_gb"], "monthly": bool(data.get("monthly"))}


def _sales_scope(dealer):
    return [] if dealer is None else [Sale.dealer_telegram_id == dealer]


def _customer_scope(dealer):
    base = [User.role == "client", User.telegram_id.not_in(settings.admin_list)]
    if dealer is not None:
        campaigns = select(Campaign.id).where(Campaign.owner_telegram_id == dealer)
        base.append(or_(
            User.telegram_id.in_(select(Sale.user_telegram_id).where(Sale.dealer_telegram_id == dealer)),
            User.telegram_id.in_(select(Visit.telegram_id).where(Visit.campaign_id.in_(campaigns))),
        ))
    return base


async def overview(period="30d", dealer=None):
    start, end = report_period(period)
    where = [*_sales_scope(dealer), Sale.confirmed_at >= start, Sale.confirmed_at < end]
    async with db_repo.async_session_factory() as session:
        totals = (await session.execute(select(Sale.currency, func.sum(Sale.amount)).where(*where).group_by(Sale.currency))).all()
        kinds = dict((await session.execute(select(Sale.kind, func.count()).where(*where).group_by(Sale.kind))).all())
        identities = (await session.execute(select(Sale.user_telegram_id, Sale.managed_subscription_id,
                      Sale.managed_created_at, Sale.dealer_telegram_id).where(*where).distinct())).all()
        buyers = {("telegram", tg) if tg is not None else (("managed", sid, created) if created else ("legacy", owner, sid))
                  for tg, sid, created, owner in identities}
        retail = await session.scalar(select(func.sum(Sale.retail_toman)).where(*where))
        estimated = await session.scalar(select(func.count()).select_from(Sale).where(*where, Sale.time_estimated.is_(True)))
        paid = select(Sale.id).where(Sale.user_telegram_id == User.telegram_id).exists()
        customer_where = _customer_scope(dealer)
        paid_users = await session.scalar(select(func.count()).select_from(User).where(*customer_where, paid))
        unpaid_users = await session.scalar(select(func.count()).select_from(User).where(*customer_where, ~paid))
        managed_where = [] if dealer is None else [DealerSubscription.dealer_id.in_(select(User.id).where(User.telegram_id == dealer))]
        managed_count = await session.scalar(select(func.count()).select_from(DealerSubscription).where(*managed_where))
        recent = list(await session.scalars(select(Sale).where(*where).order_by(Sale.confirmed_at.desc(), Sale.id.desc()).limit(5)))
    return {
        "period": {"key": period, "start": iso(start), "end": iso(end)},
        "sales": {"subscriptions": kinds.get("subscription", 0), "traffic_addons": kinds.get("traffic", 0), "buyers": len(buyers)},
        "totals": [{"currency": currency, "amount": number(amount)} for currency, amount in totals],
        "retail_toman": int(retail) if retail is not None else None, "estimated_count": int(estimated or 0),
        "users": {"paid": int(paid_users or 0), "unpaid": int(unpaid_users or 0), "managed": int(managed_count or 0)},
        "recent": [{"name": s.customer_name, "date": iso(s.confirmed_at), "amount": number(s.amount),
                    "currency": s.currency, "product": product(s.plan), "kind": s.kind} for s in recent],
    }


def _clamp(total, page):
    return min(max(0, page), max(0, (total - 1) // PAGE_SIZE))


def _state(expires):
    active = bool(expires and expires > utcnow())
    return {"active": active, "expires_at": iso(expires),
            "remaining_days": max(0, ceil((expires - utcnow()).total_seconds() / 86400)) if expires else None}


async def users(*, dealer=None, paid="all", q="", page=0, campaign=None, kind="telegram"):
    async with db_repo.async_session_factory() as session:
        if kind == "managed" and campaign is None:
            where = [] if dealer is None else [DealerSubscription.dealer_id.in_(select(User.id).where(User.telegram_id == dealer))]
            exists = select(Sale.id).where(Sale.managed_subscription_id == DealerSubscription.id,
                                          Sale.managed_created_at == DealerSubscription.created_at).exists()
            if paid != "all":
                where.append(exists if paid == "paid" else ~exists)
            if q:
                where.append(DealerSubscription.client_name.ilike(f"%{q}%"))
            total = int(await session.scalar(select(func.count()).select_from(DealerSubscription).where(*where)) or 0)
            page = _clamp(total, page)
            rows = list(await session.scalars(select(DealerSubscription).where(*where).order_by(DealerSubscription.id.desc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE)))
            paid_ids = set((await session.execute(select(Sale.managed_subscription_id, Sale.managed_created_at)
                           .where(Sale.managed_subscription_id.in_([s.id for s in rows])))).all())
            items = [{"key": f"managed:{s.id}", "kind": "managed", "telegram_id": None, "name": s.client_name,
                      "paid": (s.id, s.created_at) in paid_ids, "product": product(s.plan), "campaign_name": None,
                      **_state(s.expire_at)} for s in rows]
            return {"items": items, "total": total, "page": page, "has_more": (page + 1) * PAGE_SIZE < total}

        if campaign is None:
            where = _customer_scope(dealer)
            bought = select(Sale.id).where(Sale.user_telegram_id == User.telegram_id).exists()
            if paid != "all":
                where.append(bought if paid == "paid" else ~bought)
            if q:
                where.append(or_(func.lower(func.coalesce(User.username, "")).contains(q.lower().lstrip("@")), cast(User.telegram_id, String).contains(q)))
            total = int(await session.scalar(select(func.count()).select_from(User).where(*where)) or 0)
            page = _clamp(total, page)
            rows = (await session.execute(select(User, Subscription).outerjoin(Subscription, Subscription.user_id == User.id)
                    .where(*where).order_by(User.created_at.desc(), User.id.desc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE))).all()
            customers = [(user, sub, None) for user, sub in rows]
        else:
            where = [Visit.campaign_id == campaign]
            bought = select(Sale.id).where(Sale.user_telegram_id == Visit.telegram_id, Sale.campaign_id == campaign).exists()
            if paid != "all":
                where.append(bought if paid == "paid" else ~bought)
            if q:
                where.append(or_(func.lower(func.coalesce(User.username, "")).contains(q.lower().lstrip("@")), cast(Visit.telegram_id, String).contains(q)))
            base = select(Visit).outerjoin(User, User.telegram_id == Visit.telegram_id)
            total = int(await session.scalar(select(func.count()).select_from(base.where(*where).subquery())) or 0)
            page = _clamp(total, page)
            rows = (await session.execute(select(Visit, User, Subscription).outerjoin(User, User.telegram_id == Visit.telegram_id)
                    .outerjoin(Subscription, Subscription.user_id == User.id).where(*where)
                    .order_by(Visit.arrived_at.desc(), Visit.id.desc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE))).all()
            customers = [(u or SimpleNamespace(telegram_id=v.telegram_id, username=None), sub, v.arrived_at) for v, u, sub in rows]
        ids = [u.telegram_id for u, _, _ in customers]
        sales = list(await session.scalars(select(Sale).where(Sale.user_telegram_id.in_(ids)).order_by(Sale.confirmed_at.desc(), Sale.id.desc())))
        sources = (await session.execute(select(Attribution.telegram_id, Campaign.name, Campaign.owner_telegram_id)
                   .join(Campaign, Attribution.campaign_id == Campaign.id).where(Attribution.telegram_id.in_(ids)))).all()
        source_names = {tg: name for tg, name, owner in sources if dealer is None or owner == dealer}
        items = []
        for user, sub, arrived in customers:
            history = [s for s in sales if s.user_telegram_id == user.telegram_id]
            first_product = next((s for s in history if s.kind == "subscription"), None)
            did_buy = bool(history) if campaign is None else any(s.campaign_id == campaign for s in history)
            items.append({"key": f"telegram:{user.telegram_id}", "kind": "telegram", "telegram_id": user.telegram_id,
                "name": f"@{user.username}" if user.username else str(user.telegram_id), "paid": did_buy,
                "paid_before": bool(history) and not did_buy,
                "product": product(first_product.plan) if first_product else None,
                "campaign_name": source_names.get(user.telegram_id), "arrived_at": iso(arrived),
                **_state(sub.expire_at if sub else None)})
        return {"items": items, "total": total, "page": page, "has_more": (page + 1) * PAGE_SIZE < total}


async def campaigns(dealer=None):
    async with db_repo.async_session_factory() as session:
        where = [] if dealer is None else [Campaign.owner_telegram_id == dealer]
        rows = list(await session.scalars(select(Campaign).where(*where).order_by(Campaign.id.desc()).limit(500)))
        ids = [c.id for c in rows]
        visits = dict((await session.execute(select(Visit.campaign_id, func.count()).where(Visit.campaign_id.in_(ids)).group_by(Visit.campaign_id))).all())
        buyers = dict((await session.execute(select(Sale.campaign_id, func.count(func.distinct(Sale.user_telegram_id)))
                     .where(Sale.campaign_id.in_(ids)).group_by(Sale.campaign_id))).all())
    return [{"id": c.id, "name": c.name, "code": c.code, "owner_telegram_id": c.owner_telegram_id,
             "visitors": visits.get(c.id, 0), "buyers": buyers.get(c.id, 0),
             "conversion": round(buyers.get(c.id, 0) / visits[c.id] * 100, 1) if visits.get(c.id) else 0,
             "created_at": iso(c.created_at)} for c in rows]
