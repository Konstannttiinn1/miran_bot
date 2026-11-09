"""Checkout coupon use and VPN-isolated customer actions."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.models import PromoRedemption, utcnow
from app.handlers import customer_subscriptions as cabinet
from app.middlewares.i18n import get_text
from app.repositories import customer_subscriptions_repo as customers, db_repo, dealer_tools_repo
from app.services import subscription


@pytest.mark.asyncio
async def test_checkout_promo_is_not_consumed_until_invoice_and_targets_extra(database):
    admin = await db_repo.get_or_create_user(98301)
    user = await db_repo.get_or_create_user(98302)
    await db_repo.upsert_subscription(
        user.id, str(user.telegram_id), utcnow() + timedelta(days=5),
        traffic_limit_gb=100,
    )
    promo = await dealer_tools_repo.create_promo(
        admin.id, "FAMILY30", "days_discount", 6, 0,
        utcnow() + timedelta(days=7), 5, discount_percent=30,
    )

    status, preview = await dealer_tools_repo.preview_checkout_promo(user.id, "family30")
    assert status == "ok" and preview.id == promo.id
    assert (await dealer_tools_repo.get_dealer_promo(admin.id, promo.id)).uses_count == 0

    order = await db_repo.create_order(
        user.id, "month1", 1.00, "usdt", apply_promo=True,
        new_subscription=True, promo_code="FAMILY30",
    )
    assert order.amount == 0.70
    assert order.subscription_id is not None
    assert await db_repo.get_order_promo_bonus_days(order.id) == 6
    assert (await dealer_tools_repo.get_dealer_promo(admin.id, promo.id)).uses_count == 1
    assert (await dealer_tools_repo.preview_checkout_promo(user.id, "FAMILY30"))[0] == "already_used"
    with pytest.raises(ValueError):
        await db_repo.create_order(
            user.id, "month1", 1, "usdt", apply_promo=True,
            new_subscription=True, promo_code="FAMILY30",
        )
    assert await customers.count_additional(user.id) == 1


@pytest.mark.asyncio
async def test_promo_invoice_failure_restores_slot_and_coupon(database):
    admin = await db_repo.get_or_create_user(98311)
    user = await db_repo.get_or_create_user(98312)
    await db_repo.upsert_subscription(
        user.id, str(user.telegram_id), utcnow() + timedelta(days=7),
    )
    await dealer_tools_repo.create_promo(
        admin.id, "TRY30", "discount", 0, 0,
        utcnow() + timedelta(days=7), 2, discount_percent=30,
    )
    order = await db_repo.create_order(
        user.id, "month1", 1.00, "usdt", apply_promo=True,
        new_subscription=True, promo_code="TRY30",
    )
    await db_repo.update_order(order.id, status="failed")
    assert await customers.count_additional(user.id) == 0
    assert (await dealer_tools_repo.preview_checkout_promo(user.id, "TRY30"))[0] == "ok"


@pytest.mark.asyncio
async def test_selected_vpn_traffic_topup_never_changes_primary(database, monkeypatch):
    user = await db_repo.get_or_create_user(98321)
    future = utcnow() + timedelta(days=10)
    primary = await db_repo.upsert_subscription(
        user.id, str(user.telegram_id), future, traffic_limit_gb=100,
    )
    order = await db_repo.create_order(
        user.id, "month1", 1, "usdt", new_subscription=True,
    )
    extra = await customers.get_subscription(user.id, order.subscription_id)
    await customers.update_subscription(
        user.id, extra.id, status="active", expire_at=future, traffic_limit_gb=100,
    )

    provider = SimpleNamespace(
        get_client=AsyncMock(return_value={
            "status": "ACTIVE", "expireAt": future.isoformat(),
            "trafficLimitBytes": 100 * 1024 ** 3,
        }),
        add_traffic=AsyncMock(return_value=150),
    )
    monkeypatch.setattr(subscription, "get_vpn_provider", lambda: provider)
    monkeypatch.setattr(subscription, "subscription_link", AsyncMock(
        return_value="https://vpn.example.invalid/extra",
    ))
    monkeypatch.setattr(subscription, "is_monthly", AsyncMock(return_value=False))

    await subscription.grant_traffic_topup(user.id, extra.id)
    provider.add_traffic.assert_awaited_once_with(extra.xui_email, 50)
    assert (await customers.get_subscription(user.id, extra.id)).traffic_limit_gb == 150
    assert (await db_repo.get_subscription(user.id)).id == primary.id
    assert (await db_repo.get_subscription(user.id)).traffic_limit_gb == 100


@pytest.mark.asyncio
async def test_reset_only_revokes_selected_vpn_and_uses_per_slot_cooldown(database, monkeypatch):
    user = await db_repo.get_or_create_user(98331)
    future = utcnow() + timedelta(days=10)
    primary = await db_repo.upsert_subscription(
        user.id, str(user.telegram_id), future, traffic_limit_gb=100,
    )
    order = await db_repo.create_order(
        user.id, "month1", 1, "usdt", new_subscription=True,
    )
    extra = await customers.get_subscription(user.id, order.subscription_id)
    await customers.update_subscription(
        user.id, extra.id, status="active", expire_at=future, traffic_limit_gb=100,
    )
    reset = AsyncMock(return_value="https://vpn.example.invalid/new")
    screen = AsyncMock()
    monkeypatch.setattr(cabinet, "reset_subscription_link", reset)
    monkeypatch.setattr(cabinet, "send_with_logo", screen)
    callback = SimpleNamespace(
        data=f"cust:reset:confirm:{extra.id}",
        from_user=SimpleNamespace(id=user.telegram_id),
        answer=AsyncMock(),
    )
    t = lambda key, **kw: get_text("ru", key, **kw)

    await cabinet.reset_confirm(callback, t, user)
    reset.assert_awaited_once_with(extra.xui_email)
    assert screen.await_count == 1
    assert (await db_repo.get_subscription(user.id)).xui_email == primary.xui_email
    assert (await db_repo.get_subscription(user.id)).expire_at == future
    assert (await customers.get_subscription(user.id, extra.id)).expire_at == future

    await cabinet.reset_confirm(callback, t, user)
    reset.assert_awaited_once_with(extra.xui_email)


@pytest.mark.asyncio
async def test_cannot_open_or_reset_other_owners_vpn(database, monkeypatch):
    owner = await db_repo.get_or_create_user(98341)
    stranger = await db_repo.get_or_create_user(98342)
    primary = await db_repo.upsert_subscription(
        owner.id, str(owner.telegram_id), utcnow() + timedelta(days=10),
    )
    reset = AsyncMock()
    screen = AsyncMock()
    monkeypatch.setattr(cabinet, "reset_subscription_link", reset)
    monkeypatch.setattr(cabinet, "send_with_logo", screen)
    callback = SimpleNamespace(
        data=f"cust:reset:confirm:{primary.id}",
        from_user=SimpleNamespace(id=stranger.telegram_id),
        answer=AsyncMock(),
    )
    t = lambda key, **kw: get_text("ru", key, **kw)
    await cabinet.reset_confirm(callback, t, stranger)
    reset.assert_not_awaited()
    screen.assert_not_awaited()
    assert callback.answer.call_args.kwargs["show_alert"] is True
