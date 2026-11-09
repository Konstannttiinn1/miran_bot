"""Regression checks for target selection throughout each payment channel.

No live Telegram, payment-provider or Remnawave requests: mocks record exactly
which order is fulfilled and database state is isolated by the shared fixture.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database.models import utcnow
from app.handlers import client, dealer
from app.repositories import customer_subscriptions_repo as customers, db_repo
from app.services import payment_checker
from app.utils.tariffs import TRAFFIC_TOPUP, get_stars_price


async def customer_with_extra(telegram_id, *, primary_active=True, extra_active=True):
    owner = await db_repo.get_or_create_user(telegram_id)
    future = utcnow() + timedelta(days=12)
    past = utcnow() - timedelta(days=2)
    primary = await db_repo.upsert_subscription(
        owner.id, str(owner.telegram_id), future if primary_active else past,
        traffic_limit_gb=100,
    )
    slot_order = await db_repo.create_order(
        owner.id, "month1", 1.0, "usd", new_subscription=True,
    )
    extra = await customers.get_subscription(owner.id, slot_order.subscription_id)
    assert extra is not None
    assert await customers.update_subscription(
        owner.id, extra.id, status="active",
        expire_at=future if extra_active else past, traffic_limit_gb=100,
    )
    return owner, primary, extra


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "primary_active,extra_active,expected",
    [(False, True, True), (True, False, False)],
)
async def test_stars_precheckout_uses_selected_extra_not_primary(
    database, primary_active, extra_active, expected,
):
    owner, primary, extra = await customer_with_extra(
        98401, primary_active=primary_active, extra_active=extra_active,
    )
    stars = get_stars_price(TRAFFIC_TOPUP, settings.stars_reward_usd)
    order = await db_repo.create_order(
        owner.id, TRAFFIC_TOPUP, stars, "xtr", order_type="traffic_topup",
        subscription_id=extra.id,
    )
    query = SimpleNamespace(
        invoice_payload=client._stars_payload(order.id, owner.id, TRAFFIC_TOPUP),
        currency="XTR", total_amount=stars, answer=AsyncMock(),
    )
    await client.stars_pre_checkout(
        query, lambda key, **kw: key, "en", owner,
    )
    assert query.answer.call_args.kwargs["ok"] is expected
    assert (await db_repo.get_subscription(owner.id)).id == primary.id


@pytest.mark.asyncio
async def test_stars_paid_callback_provisions_selected_extra_only_once(database, monkeypatch):
    owner, primary, extra = await customer_with_extra(98411)
    stars = get_stars_price("month1", settings.stars_reward_usd)
    order = await db_repo.create_order(
        owner.id, "month1", stars, "xtr", subscription_id=extra.id,
    )
    paid = AsyncMock(return_value=(
        "https://vpn.example.invalid/test",
        utcnow() + timedelta(days=30),
    ))
    monkeypatch.setattr(client, "grant_paid_order", paid)
    monkeypatch.setattr(client, "notify_admins", AsyncMock())
    message = SimpleNamespace(
        answer=AsyncMock(),
        successful_payment=SimpleNamespace(
            invoice_payload=client._stars_payload(order.id, owner.id, "month1"),
            currency="XTR", total_amount=stars,
            telegram_payment_charge_id="synthetic-stars-charge",
        ),
    )
    for _ in range(2):
        await client.stars_successful_payment(
            message, lambda key, **kw: key, "en", owner,
        )
    paid.assert_awaited_once()
    assert paid.await_args.args[2].subscription_id == extra.id
    assert (await db_repo.get_order(order.id)).status == "paid"
    assert (await db_repo.get_subscription(owner.id)).id == primary.id


@pytest.mark.asyncio
async def test_heleket_paid_poll_provisions_selected_extra_only_once(database, monkeypatch):
    owner, primary, extra = await customer_with_extra(98421)
    order = await db_repo.create_order(
        owner.id, "month1", 1, "usdt", subscription_id=extra.id,
    )
    await db_repo.update_order(order.id, external_id="synthetic-heleket-invoice")
    paid = AsyncMock(return_value=(
        "https://vpn.example.invalid/test",
        utcnow() + timedelta(days=30),
    ))
    monkeypatch.setattr(payment_checker, "grant_paid_order", paid)
    monkeypatch.setattr(payment_checker.settings, "heleket_enabled", True)
    monkeypatch.setattr(payment_checker.heleket, "is_configured", lambda: True)
    monkeypatch.setattr(
        payment_checker.heleket, "get_payment",
        AsyncMock(return_value={"payment_status": "paid"}),
    )
    monkeypatch.setattr(payment_checker.bot, "send_message", AsyncMock())
    monkeypatch.setattr(payment_checker, "notify_admins", AsyncMock())

    for _ in range(2):
        await payment_checker.check_pending_payments()

    paid.assert_awaited_once()
    assert paid.await_args.args[2].subscription_id == extra.id
    assert (await db_repo.get_order(order.id)).status == "paid"
    assert (await db_repo.get_subscription(owner.id)).id == primary.id


@pytest.mark.asyncio
async def test_dealer_bank_confirmation_provisions_extra_only_once(database, monkeypatch):
    buyer, primary, extra = await customer_with_extra(98431)
    approver = await db_repo.get_or_create_user(98432)
    await db_repo.set_user_role(approver.telegram_id, "dealer")
    await db_repo.change_dealer_balance(approver.id, 10)
    approver = await db_repo.get_user_by_id(approver.id)

    order = await db_repo.create_order(
        buyer.id, "month1", 0.5, "usd", subscription_id=extra.id,
    )
    await db_repo.update_order(order.id, status="pending_dealer")

    paid = AsyncMock(return_value=(
        "https://vpn.example.invalid/test",
        utcnow() + timedelta(days=30),
    ))
    monkeypatch.setattr(dealer, "grant_paid_order", paid)
    monkeypatch.setattr(dealer.bot, "send_message", AsyncMock())
    monkeypatch.setattr(dealer, "notify_admins", AsyncMock())
    callback = SimpleNamespace(
        data=f"dealer_ok:{order.id}",
        answer=AsyncMock(),
        message=SimpleNamespace(
            answer=AsyncMock(), edit_reply_markup=AsyncMock(),
        ),
    )
    for _ in range(2):
        await dealer.dealer_approve(
            callback, lambda key, **kw: key, "en", approver,
        )

    paid.assert_awaited_once()
    assert paid.await_args.args[2].subscription_id == extra.id
    assert (await db_repo.get_order(order.id)).status == "paid"
    assert (await db_repo.get_user_by_id(approver.id)).dealer_balance == pytest.approx(9.5)
    assert (await db_repo.get_subscription(buyer.id)).id == primary.id


@pytest.mark.parametrize("enabled", [True, False])
def test_payment_keyboard_hides_disabled_heleket(monkeypatch, enabled):
    from app.keyboards import builders

    monkeypatch.setattr(builders.settings, "heleket_enabled", enabled)
    markup = builders.payment_kb(lambda key, **kw: key)
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard for button in row
    ]
    assert ("pay:heleket" in callbacks) is enabled
    assert "pay:stars" in callbacks
    assert "pay:dealer" in callbacks
    assert "checkout:promo" in callbacks
