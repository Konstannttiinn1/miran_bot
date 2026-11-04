"""Regression checks for the two opt-in promo and self-service link features."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.models import PromoRedemption, utcnow
from app.handlers import start
from app.middlewares.i18n import get_text
from app.repositories import db_repo, dealer_tools_repo
from app.services import subscription


@pytest.mark.asyncio
async def test_discount_applies_once_and_uses_real_invoice_amount(database):
    admin = await db_repo.get_or_create_user(96001)
    user = await db_repo.get_or_create_user(96002)
    promo = await dealer_tools_repo.create_promo(
        admin.id, "WELCOME20", "discount", 0, 0,
        utcnow() + timedelta(days=7), 2, discount_percent=20,
    )
    status, redeemed = await dealer_tools_repo.reserve_promo_redemption(user.id, "welcome20")
    assert status == "reserved" and redeemed.id == promo.id
    assert (await dealer_tools_repo.reserve_promo_redemption(user.id, "WELCOME20"))[0] == "already_used"

    first = await db_repo.create_order(
        user.id, "month1", 100, "xtr", apply_promo=True,
    )
    assert first.amount == 80
    assert await db_repo.get_order_promo_bonus_days(first.id) == 0
    second = await db_repo.create_order(
        user.id, "month1", 100, "xtr", apply_promo=True,
    )
    assert second.amount == 100

    # A failed invoice must not eat a customer's activated voucher.
    await db_repo.update_order(first.id, status="failed")
    again = await db_repo.create_order(
        user.id, "month1", 100, "xtr", apply_promo=True,
    )
    assert again.amount == 80
    async with database() as session:
        redemption = await session.get(PromoRedemption, 1)
        assert redemption.order_id == again.id


@pytest.mark.asyncio
async def test_discount_plus_days_are_snapshotted_to_paid_order(database, monkeypatch):
    admin = await db_repo.get_or_create_user(96011)
    user = await db_repo.get_or_create_user(96012)
    await dealer_tools_repo.create_promo(
        admin.id, "EXTRA10", "days_discount", 7, 0,
        utcnow() + timedelta(days=7), 5, discount_percent=10,
    )
    assert (await dealer_tools_repo.reserve_promo_redemption(user.id, "EXTRA10"))[0] == "reserved"
    purchase = await db_repo.create_order(
        user.id, "month2", 2.70, "usdt", apply_promo=True,
    )
    assert purchase.amount == 2.43
    assert await db_repo.get_order_promo_bonus_days(purchase.id) == 7
    grant = AsyncMock(return_value=("https://example.invalid/new", utcnow()))
    monkeypatch.setattr(subscription, "grant_vpn", grant)
    await subscription.grant_paid_order(user.id, user.telegram_id, purchase)
    grant.assert_awaited_once_with(user.id, user.telegram_id, "month2", bonus_days=7)


@pytest.mark.asyncio
async def test_deactivated_coupon_is_not_applied_to_checkout(database):
    admin = await db_repo.get_or_create_user(96021)
    user = await db_repo.get_or_create_user(96022)
    promo = await dealer_tools_repo.create_promo(
        admin.id, "STOPPED", "discount", 0, 0,
        utcnow() + timedelta(days=7), 5, discount_percent=50,
    )
    assert (await dealer_tools_repo.reserve_promo_redemption(user.id, "STOPPED"))[0] == "reserved"
    assert await dealer_tools_repo.deactivate_promo(admin.id, promo.id)
    order = await db_repo.create_order(
        user.id, "month1", 100, "xtr", apply_promo=True,
    )
    assert order.amount == 100


@pytest.mark.asyncio
async def test_dealer_bank_quote_is_discounted_and_persisted(database):
    admin = await db_repo.get_or_create_user(96031)
    user = await db_repo.get_or_create_user(96032)
    await dealer_tools_repo.create_promo(
        admin.id, "BANK25", "discount", 0, 0,
        utcnow() + timedelta(days=7), 5, discount_percent=25,
    )
    await dealer_tools_repo.reserve_promo_redemption(user.id, "BANK25")
    order = await db_repo.create_order(
        user.id, "month1", 0.5, "usd",
        retail_toman=150000, apply_promo=True,
    )
    assert order.amount == 0.375
    assert await db_repo.get_order_retail_toman(order.id) == 112500


@pytest.mark.asyncio
async def test_link_reset_only_for_active_own_subscription(database, monkeypatch):
    user = SimpleNamespace(id=42, telegram_id=96042, is_blocked=False)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=user.telegram_id),
        answer=AsyncMock(),
    )
    screen = AsyncMock()
    reset = AsyncMock(return_value="https://sub.example.invalid/new")
    monkeypatch.setattr(start, "send_with_logo", screen)
    monkeypatch.setattr(start, "reset_link", reset)
    subscription_data = SimpleNamespace(
        expire_at=utcnow() + timedelta(days=10),
        xui_email=str(user.telegram_id),
    )
    getter = AsyncMock(return_value=subscription_data)
    monkeypatch.setattr(db_repo, "get_subscription", getter)
    t = lambda key, **kw: get_text("ru", key, **kw)

    await start.reset_link_confirm(callback, t, user)
    reset.assert_awaited_once_with(user)
    assert "new" in screen.call_args.args[1]

    reset.reset_mock()
    screen.reset_mock()
    getter.return_value = SimpleNamespace(
        expire_at=subscription_data.expire_at, xui_email="someone-else",
    )
    await start.reset_link_confirm(callback, t, user)
    reset.assert_not_awaited()
    screen.assert_not_awaited()
