import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, select

from app.database import engine as engine_module
from app.database.models import Order, TrialClaim, utcnow
from app.handlers import client
from app.repositories import db_repo
from app.services.subscription import TrialUnavailableError, grant_vpn
from app.services import subscription as subscription_service


def trial_callback():
    return SimpleNamespace(
        data="plan:test",
        answer=AsyncMock(),
        message=SimpleNamespace(answer=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_old_trial_button_cannot_issue_a_second_trial(database, monkeypatch):
    user = await db_repo.get_or_create_user(10001)
    provision = AsyncMock(return_value="test-sub-id")
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    state = SimpleNamespace(clear=AsyncMock())
    for _ in range(2):
        await client.choose_plan(trial_callback(), lambda key: key, "en", user, state)
    assert provision.await_count == 1


@pytest.mark.asyncio
async def test_history_accepts_multiple_legacy_trial_orders(database):
    user = await db_repo.get_or_create_user(10002)
    await db_repo.create_order(user.id, "test", 0, "test")
    await db_repo.create_order(user.id, "test", 0, "test")
    assert await db_repo.user_has_order(user.id, "test") is True


@pytest.mark.asyncio
async def test_parallel_callbacks_reserve_exactly_one_test(database, monkeypatch):
    user = await db_repo.get_or_create_user(10003)
    provision = AsyncMock(return_value="test-sub-id")
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    callbacks = [trial_callback() for _ in range(16)]
    await asyncio.gather(*(
        client.choose_plan(cb, lambda key: key, "en", user, SimpleNamespace(clear=AsyncMock()))
        for cb in callbacks
    ))
    assert provision.await_count == 1
    replies = [cb.message.answer.call_args.args[0] for cb in callbacks]
    assert sum(text.startswith("test_activated") for text in replies) == 1
    assert replies.count("test_unavailable") == 15
    async with database() as session:
        assert await session.scalar(select(func.count(Order.id))) == 1
        assert await session.scalar(select(func.count(TrialClaim.telegram_id))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["paid", "pending", "failed", "test_processing"])
async def test_legacy_test_order_blocks_the_service(database, monkeypatch, status):
    user = await db_repo.get_or_create_user(10004)
    order = await db_repo.create_order(user.id, "test", 0, "test")
    await db_repo.update_order(order.id, status=status)
    provision = AsyncMock()
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    with pytest.raises(TrialUnavailableError):
        await grant_vpn(user.id, user.telegram_id, "test")
    provision.assert_not_awaited()
    assert await db_repo.can_claim_test(user.id) is False


@pytest.mark.asyncio
async def test_failed_or_ambiguous_provisioning_does_not_allow_repeat(database, monkeypatch):
    user = await db_repo.get_or_create_user(10005)
    provision = AsyncMock(side_effect=TimeoutError("panel response lost"))
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    for _ in range(2):
        await client.choose_plan(
            trial_callback(), lambda key: key, "en", user, SimpleNamespace(clear=AsyncMock()),
        )
    assert provision.await_count == 1
    async with database() as session:
        assert (await session.scalar(select(Order))).status == "failed"
        assert await session.get(TrialClaim, user.telegram_id) is not None


@pytest.mark.asyncio
async def test_expiration_and_user_recreation_do_not_reset_trial(database, monkeypatch):
    user = await db_repo.get_or_create_user(10006)
    provision = AsyncMock(return_value="test-sub-id")
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    await grant_vpn(user.id, user.telegram_id, "test")
    await db_repo.update_subscription(user.id, expire_at=utcnow() - timedelta(days=1))
    with pytest.raises(TrialUnavailableError):
        await grant_vpn(user.id, user.telegram_id, "test")
    await db_repo.delete_user_full(user.telegram_id)
    user = await db_repo.get_or_create_user(user.telegram_id)
    assert await db_repo.can_claim_test(user.id) is False
    with pytest.raises(TrialUnavailableError):
        await grant_vpn(user.id, user.telegram_id, "test")
    assert provision.await_count == 1


@pytest.mark.asyncio
async def test_active_paid_subscription_cannot_be_replaced_by_trial(database, monkeypatch):
    user = await db_repo.get_or_create_user(10007)
    expiry = utcnow() + timedelta(days=20)
    await db_repo.upsert_subscription(user.id, str(user.telegram_id), expiry, 100)
    provision = AsyncMock()
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    with pytest.raises(TrialUnavailableError):
        await grant_vpn(user.id, user.telegram_id, "test")
    provision.assert_not_awaited()
    sub = await db_repo.get_subscription(user.id)
    assert (sub.expire_at, sub.traffic_limit_gb) == (expiry, 100)


@pytest.mark.asyncio
async def test_trial_identity_must_match_database_user(database):
    user = await db_repo.get_or_create_user(10008)
    assert await db_repo.claim_test_order(user.id, 999999) is None
    assert await db_repo.can_claim_test(user.id) is True


@pytest.mark.asyncio
async def test_stale_back_navigation_does_not_restore_trial_button(database, monkeypatch):
    user = await db_repo.get_or_create_user(10009)
    await db_repo.create_order(user.id, "test", 0, "test")
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"plans_with_test": True}),
        set_state=AsyncMock(), update_data=AsyncMock(),
    )
    render = AsyncMock()
    monkeypatch.setattr(client, "send_with_logo", render)
    await client.back_to_plans(trial_callback(), lambda key, **kwargs: key, "en", user, state)
    keyboard = render.call_args.kwargs["reply_markup"]
    assert "plan:test" not in {
        button.callback_data for row in keyboard.inline_keyboard for button in row
    }


@pytest.mark.asyncio
async def test_startup_backfills_duplicate_legacy_tests_idempotently(database, monkeypatch):
    user = await db_repo.get_or_create_user(10010)
    first = await db_repo.create_order(user.id, "test", 0, "test")
    await db_repo.create_order(user.id, "test", 0, "test")
    monkeypatch.setattr(engine_module, "engine", database.kw["bind"])
    await engine_module.init_db()
    await engine_module.init_db()
    async with database() as session:
        assert await session.scalar(select(func.count(TrialClaim.telegram_id))) == 1
        claim = await session.get(TrialClaim, user.telegram_id)
        assert claim.created_at == first.created_at
    await db_repo.delete_user_full(user.telegram_id)
    new_user = await db_repo.get_or_create_user(user.telegram_id)
    assert await db_repo.can_claim_test(new_user.id) is False


@pytest.mark.asyncio
async def test_claim_rolls_back_when_order_cannot_be_saved(database):
    user = await db_repo.get_or_create_user(10011)

    def fail_order_insert(mapper, connection, target):
        raise RuntimeError("simulated database failure")

    event.listen(Order, "before_insert", fail_order_insert)
    try:
        with pytest.raises(RuntimeError, match="simulated database failure"):
            await db_repo.claim_test_order(user.id, user.telegram_id)
    finally:
        event.remove(Order, "before_insert", fail_order_insert)
    async with database() as session:
        assert await session.get(TrialClaim, user.telegram_id) is None
    assert await db_repo.can_claim_test(user.id) is True


@pytest.mark.asyncio
async def test_paid_purchase_after_trial_still_works(database, monkeypatch):
    user = await db_repo.get_or_create_user(10012)
    provision = AsyncMock(return_value="test-sub-id")
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    await grant_vpn(user.id, user.telegram_id, "test")
    await grant_vpn(user.id, user.telegram_id, "15gb")
    assert provision.await_count == 2
    assert (await db_repo.get_subscription(user.id)).traffic_limit_gb == 15
