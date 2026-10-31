import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.config import settings
from app.database import models
from app.database.models import DealerLog, utcnow
from app.repositories import db_repo


def analytics():
    from app.repositories import analytics_repo
    return analytics_repo


@pytest.mark.asyncio
async def test_campaign_launches_are_unique_and_first_source_cannot_be_replaced(database):
    ar = analytics()
    first = await ar.create_campaign(80001, "Первый канал")
    second = await ar.create_campaign(80002, "Второй канал")
    user = await db_repo.get_or_create_user(81001, "buyer")
    before = utcnow() - timedelta(minutes=5)
    await asyncio.gather(*[ar.track_start(user, f"/start {first.code}", at=before) for _ in range(3)])
    await ar.track_start(user, f"/start {second.code}")
    async with database() as session:
        visits = list(await session.scalars(select(models.CampaignVisit)))
        source = await session.get(models.CampaignAttribution, user.telegram_id)
    assert {(v.campaign_id, v.telegram_id) for v in visits} == {
        (first.id, user.telegram_id), (second.id, user.telegram_id),
    }
    assert source.campaign_id == first.id and source.arrived_at == before
    assert first.code.startswith("ad_") and len(first.code) <= 64


@pytest.mark.asyncio
async def test_bad_campaigns_and_own_dealer_launches_do_not_create_visitors(database, monkeypatch):
    ar = analytics()
    campaign = await ar.create_campaign(80001, "Campaign")
    user = await db_repo.get_or_create_user(81002)
    await ar.track_start(user, "/start ad_unknown")
    await ar.track_start(user, "/start")
    await db_repo.set_user_role(user.telegram_id, "dealer")
    user = await db_repo.get_user_by_tg(user.telegram_id)
    await ar.track_start(user, f"/start {campaign.code}")
    monkeypatch.setattr(settings, "admin_ids", str(user.telegram_id))
    await ar.track_start(user, f"/start {campaign.code}")
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(models.CampaignVisit)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status,currency,amount", [
    ("stars_paid_processing", "xtr", 77), ("crypto_paid_processing", "usdt", 1),
])
async def test_confirmed_payment_is_attributed_once_before_provisioning(database, status, currency, amount):
    ar = analytics()
    campaign = await ar.create_campaign(80001, "Sale source")
    user = await db_repo.get_or_create_user(81003)
    # A previously paid order must never become a conversion for today's link.
    old = await db_repo.create_order(user.id, "month1", 0.5, "usd")
    await db_repo.update_order(old.id, status="paid", created_at=utcnow() - timedelta(days=10))
    await ar.backfill_sales()
    await ar.track_start(user, f"/start {campaign.code}", at=utcnow() - timedelta(minutes=1))
    order = await db_repo.create_order(user.id, "month1", amount, currency)
    assert await db_repo.claim_payment_order(order.id, status, "charge-1") is True
    assert await db_repo.claim_payment_order(order.id, status, "charge-1") is False
    # Funds are confirmed even if the next step cannot provision VPN.
    await db_repo.update_order(order.id, status=status.replace("processing", "grant_failed"))
    await ar.backfill_sales()
    async with database() as session:
        sales = list(await session.scalars(select(models.AnalyticsSale).order_by(models.AnalyticsSale.id)))
    assert len(sales) == 2
    assert sales[0].campaign_id is None and sales[0].time_estimated is True
    assert sales[1].campaign_id == campaign.id
    assert sales[1].amount == Decimal(str(amount)) and sales[1].currency == currency.upper()
    assert sales[1].time_estimated is False


@pytest.mark.asyncio
async def test_bank_quote_and_completed_dealer_sale_are_preserved(database, monkeypatch):
    ar = analytics()
    buyer = await db_repo.get_or_create_user(81004)
    dealer = await db_repo.get_or_create_user(80001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer.id, 10)
    campaign = await ar.create_campaign(dealer.telegram_id, "Bank source")
    await ar.track_start(buyer, f"/start {campaign.code}", at=utcnow() - timedelta(minutes=1))
    order = await db_repo.create_order(buyer.id, "month2d3", 1.615, "usd", retail_toman=484500)
    await ar.preserve_quote(order, 999999)  # A later quote cannot rewrite this order.
    monkeypatch.setattr(settings, "toman_per_usd", 900000)
    monkeypatch.setattr(settings, "dealer_discount", 0.9)
    await db_repo.update_order(order.id, status="pending_dealer")
    assert (await db_repo.claim_dealer_order(order.id, dealer.id))[0] == "claimed"
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(models.AnalyticsSale)) == 0
    assert await db_repo.complete_dealer_order(order.id, dealer.id) is True
    assert await db_repo.complete_dealer_order(order.id, dealer.id) is False
    await ar.backfill_sales()
    async with database() as session:
        sale = (await session.scalars(select(models.AnalyticsSale))).one()
    assert sale.amount == Decimal("1.615") and sale.retail_toman == 484500
    assert sale.dealer_telegram_id == dealer.telegram_id and sale.campaign_id == campaign.id
    assert sale.user_telegram_id == buyer.telegram_id and sale.time_estimated is False
    assert (await db_repo.get_user_by_id(dealer.id)).dealer_balance == pytest.approx(8.385)


@pytest.mark.asyncio
async def test_refunded_reservation_trials_and_pending_orders_are_not_sales(database):
    ar = analytics()
    buyer = await db_repo.get_or_create_user(81005)
    dealer = await db_repo.get_or_create_user(80001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer.id, 10)
    order = await db_repo.create_order(buyer.id, "month1", 0.5, "usd")
    await db_repo.update_order(order.id, status="pending_dealer")
    await db_repo.claim_dealer_order(order.id, dealer.id)
    await db_repo.rollback_dealer_order(order.id, dealer.id)
    trial = await db_repo.create_order(buyer.id, "test", 0, "test")
    await db_repo.update_order(trial.id, status="paid")
    await db_repo.create_order(buyer.id, "month1", 1, "usdt")
    await ar.backfill_sales()
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(models.AnalyticsSale)) == 0
    assert (await db_repo.get_user_by_id(dealer.id)).dealer_balance == 10


@pytest.mark.asyncio
async def test_managed_purchase_renewal_and_topup_are_distinct_sales(database):
    ar = analytics()
    dealer = await db_repo.get_or_create_user(80001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer.id, 10)
    _, sub = await db_repo.reserve_dealer_subscription_purchase(dealer.id, "month1d2", 0.75)
    expiry = utcnow() + timedelta(days=30)
    await db_repo.complete_dealer_subscription_purchase(sub.id, dealer.id, expiry, 100)
    _, _, log_id = await db_repo.reserve_dealer_subscription_renewal(dealer.id, sub.id, "month2d3", 1.615)
    assert await db_repo.complete_dealer_subscription_renewal(dealer.id, sub.id, log_id, "month2d3", expiry + timedelta(days=60), 100)
    order = await db_repo.create_order(dealer.id, "traffic50", 0.25, "usd", order_type=f"managed_traffic:{sub.id}")
    await db_repo.update_order(order.id, status="pending_dealer")
    await db_repo.claim_dealer_order(order.id, dealer.id)
    await db_repo.complete_dealer_order(order.id, dealer.id)
    await ar.backfill_sales()
    async with database() as session:
        sales = list(await session.scalars(select(models.AnalyticsSale)))
    assert len(sales) == 3
    assert sorted(s.amount for s in sales) == [Decimal("0.25"), Decimal("0.75"), Decimal("1.615")]
    assert all(s.dealer_telegram_id == dealer.telegram_id and s.managed_subscription_id == sub.id for s in sales)
    assert all(s.user_telegram_id is None for s in sales)
    assert sorted(s.kind for s in sales) == ["subscription", "subscription", "traffic"]


@pytest.mark.asyncio
async def test_backfill_uses_confirm_log_time_and_survives_user_deletion(database):
    ar = analytics()
    buyer = await db_repo.get_or_create_user(81006, "historical")
    dealer = await db_repo.get_or_create_user(80001)
    order = await db_repo.create_order(buyer.id, "month1", 0.5, "usd")
    await db_repo.update_order(order.id, status="paid", dealer_id=dealer.id, created_at=utcnow() - timedelta(days=10))
    confirmed = utcnow() - timedelta(days=1)
    async with database() as session:
        session.add(DealerLog(dealer_id=dealer.id, action="confirm", order_id=order.id, created_at=confirmed))
        await session.commit()
    await ar.backfill_sales()
    await ar.backfill_sales()
    await db_repo.delete_user_full(buyer.telegram_id)
    async with database() as session:
        sale = (await session.scalars(select(models.AnalyticsSale))).one()
    assert sale.confirmed_at == confirmed and sale.time_estimated is False
    assert sale.retail_toman is None and sale.user_telegram_id == 81006


async def managed_customer(telegram_id):
    dealer = await db_repo.get_or_create_user(telegram_id)
    await db_repo.set_user_role(telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer.id, 10)
    _, sub = await db_repo.reserve_dealer_subscription_purchase(dealer.id, "month1", 0.5)
    return dealer, sub


@pytest.mark.asyncio
async def test_deleted_dealer_log_id_reuse_does_not_drop_a_new_sale(database):
    ar = analytics()
    first, old = await managed_customer(83001)
    await db_repo.complete_dealer_subscription_purchase(old.id, first.id, utcnow() + timedelta(days=30), 100)
    await db_repo.delete_user_full(first.telegram_id)
    second, new = await managed_customer(83002)
    await db_repo.complete_dealer_subscription_purchase(new.id, second.id, utcnow() + timedelta(days=30), 100)
    await ar.backfill_sales()
    async with database() as session:
        sales = list(await session.scalars(select(models.AnalyticsSale)))
    assert len(sales) == 2
    assert {s.dealer_telegram_id for s in sales} == {83001, 83002}


@pytest.mark.asyncio
async def test_reused_managed_id_does_not_inherit_a_deleted_customers_paid_state(database):
    from app.services.analytics import users
    first, old = await managed_customer(83003)
    await db_repo.complete_dealer_subscription_purchase(old.id, first.id, utcnow() + timedelta(days=30), 100)
    await db_repo.delete_user_full(first.telegram_id)
    second, new = await managed_customer(83004)
    # A reserved, not completed purchase is not an old customer's paid subscription.
    result = await users(dealer=second.telegram_id, kind="managed")
    assert result["total"] == 1 and result["items"][0]["paid"] is False


@pytest.mark.asyncio
async def test_source_and_payment_emit_the_same_postgres_customer_lock(database):
    # Verify our PostgreSQL boundary contract even on the SQLite test backend.
    # SQLite serializes writers; PostgreSQL needs an explicit common row lock.
    ar = analytics()
    user = await db_repo.get_or_create_user(83005)
    campaign = await ar.create_campaign(80001, "Concurrent source")
    order = await db_repo.create_order(user.id, "month1", 1, "usdt")
    statements = []

    def capture(execution):
        if execution.is_select:
            statements.append(str(execution.statement.compile(dialect=postgresql.dialect())))

    event.listen(Session, "do_orm_execute", capture)
    try:
        await ar.track_start(user, f"/start {campaign.code}", at=utcnow() - timedelta(minutes=1))
        await db_repo.claim_payment_order(order.id, "crypto_paid_processing")
    finally:
        event.remove(Session, "do_orm_execute", capture)
    locks = [sql for sql in statements if "FROM users" in sql and "FOR UPDATE" in sql]
    assert len(locks) == 2


@pytest.mark.asyncio
async def test_historical_import_does_not_hold_live_customer_locks(database):
    ar = analytics()
    user = await db_repo.get_or_create_user(83006)
    order = await db_repo.create_order(user.id, "month1", 1, "usdt")
    await db_repo.update_order(order.id, status="paid")
    statements = []

    def capture(execution):
        if execution.is_select:
            statements.append(str(execution.statement.compile(dialect=postgresql.dialect())))

    event.listen(Session, "do_orm_execute", capture)
    try:
        await ar.backfill_sales()
    finally:
        event.remove(Session, "do_orm_execute", capture)
    assert not any("FROM users" in sql and "FOR UPDATE" in sql for sql in statements)


@pytest.mark.asyncio
async def test_first_start_records_campaign_before_language_selection(database, monkeypatch):
    from app.handlers import start
    from app.middlewares.i18n import get_text
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    ar = analytics()
    campaign = await ar.create_campaign(80001, "First launch")
    user = await db_repo.get_or_create_user(83007)
    monkeypatch.setattr(start, "send_with_logo", AsyncMock())
    message = SimpleNamespace(text=f"/start {campaign.code}", from_user=SimpleNamespace(id=user.telegram_id))
    await start.cmd_start(message, lambda key, **kw: get_text("en", key, **kw), "en", user)
    async with database() as session:
        assert (await session.get(models.CampaignAttribution, user.telegram_id)).campaign_id == campaign.id
