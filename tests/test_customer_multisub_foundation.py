"""Isolated customer slots, limit enforcement and order-to-VPN routing."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database.models import utcnow
from app.repositories import customer_subscriptions_repo as customers, db_repo
from app.services import subscription


@pytest.mark.asyncio
async def test_extra_subscriptions_do_not_replace_primary_and_cannot_exceed_cap(
    database, monkeypatch,
):
    user = await db_repo.get_or_create_user(98201)
    primary = await db_repo.upsert_subscription(
        user.id, str(user.telegram_id), utcnow(), traffic_limit_gb=100,
    )
    monkeypatch.setattr(settings, "max_additional_subscriptions", 2)
    one = await db_repo.create_order(
        user.id, "month1", 1, "usdt", new_subscription=True,
    )
    two = await db_repo.create_order(
        user.id, "month2", 1.9, "usdt", new_subscription=True,
    )
    assert one.subscription_id != two.subscription_id
    assert len(await customers.list_subscriptions(user.id)) == 3
    assert (await db_repo.get_subscription(user.id)).id == primary.id
    assert (await db_repo.get_subscription(user.id)).xui_email == str(user.telegram_id)
    sub1 = await customers.get_subscription(user.id, one.subscription_id)
    sub2 = await customers.get_subscription(user.id, two.subscription_id)
    assert sub1.xui_email != sub2.xui_email
    assert sub1.xui_email != str(user.telegram_id)
    assert not sub1.xui_email.isdigit() and not sub2.xui_email.isdigit()

    with pytest.raises(customers.SubscriptionLimitReached):
        await db_repo.create_order(user.id, "month1", 1, "usdt", new_subscription=True)

    await db_repo.update_order(one.id, status="failed")
    assert (await customers.get_subscription(user.id, one.subscription_id)) is None
    assert await customers.count_additional(user.id) == 1
    three = await db_repo.create_order(user.id, "month1", 1, "usdt", new_subscription=True)
    assert three.subscription_id not in {one.subscription_id, two.subscription_id}


@pytest.mark.asyncio
async def test_user_cannot_modify_or_buy_another_users_subscription(database):
    owner = await db_repo.get_or_create_user(98211)
    stranger = await db_repo.get_or_create_user(98212)
    primary = await db_repo.upsert_subscription(owner.id, str(owner.telegram_id), utcnow())
    assert await customers.get_subscription(stranger.id, primary.id) is None
    assert not await customers.rename_subscription(stranger.id, primary.id, "Intrusion")
    assert not await customers.update_subscription(stranger.id, primary.id, status="cancelled")
    with pytest.raises(ValueError):
        await db_repo.create_order(
            stranger.id, "month1", 1, "usdt", subscription_id=primary.id,
        )


@pytest.mark.asyncio
async def test_paid_extra_slot_changes_only_selected_vpn(database, monkeypatch):
    user = await db_repo.get_or_create_user(98221)
    primary = await db_repo.upsert_subscription(
        user.id, str(user.telegram_id), utcnow(), traffic_limit_gb=100,
    )
    order = await db_repo.create_order(
        user.id, "month1", 1, "usdt", new_subscription=True,
    )
    target = await customers.get_subscription(user.id, order.subscription_id)
    expiry = utcnow()
    provider = SimpleNamespace()
    issue = AsyncMock(return_value=expiry)
    link = AsyncMock(return_value="https://vpn.example.invalid/extra")
    monkeypatch.setattr(subscription, "get_vpn_provider", lambda: provider)
    monkeypatch.setattr(subscription, "grant_monthly", issue)
    monkeypatch.setattr(subscription, "subscription_link", link)

    new_link, _ = await subscription.grant_paid_order(user.id, user.telegram_id, order)
    assert new_link.endswith("/extra")
    assert issue.await_args.args[1] == target.xui_email
    assert issue.await_args.kwargs["limit_ip"] == 1
    new_target = await customers.get_subscription(user.id, order.subscription_id)
    assert new_target.status == "active" and new_target.plan == "month1"
    assert (await db_repo.get_subscription(user.id)).id == primary.id
    assert (await db_repo.get_subscription(user.id)).xui_email == str(user.telegram_id)


@pytest.mark.asyncio
async def test_trial_still_once_per_telegram_account(database):
    user = await db_repo.get_or_create_user(98231)
    assert await db_repo.can_claim_test(user.id)
    test_order = await db_repo.claim_test_order(user.id, user.telegram_id)
    assert test_order is not None
    assert not await db_repo.can_claim_test(user.id)
    assert await db_repo.claim_test_order(user.id, user.telegram_id) is None
