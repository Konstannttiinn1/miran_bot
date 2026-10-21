import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.config import settings
from app.database.models import MonthlyTrafficQuota, Order, utcnow
from app.handlers import client, dealer
from app.keyboards import builders
from app.middlewares.i18n import get_text
from app.repositories import db_repo
from app.services import monthly_traffic, subscription
from app.services.remnawave_api import RemnawaveApiError, RemnawaveClient
from app.utils.tariffs import TRAFFIC_TOPUP, get_stars_price

GB = 1024 ** 3


@pytest.fixture
def panel(monkeypatch):
    for key, value in {
        "remnawave_api_url": "https://panel.example.test",
        "remnawave_api_token": "local-test-token",
        "remnawave_subscription_url": "https://sub.example.test",
        "remnawave_squad_uuid": "local-test-squad",
    }.items():
        monkeypatch.setattr(settings, key, value)
    provider = RemnawaveClient()
    users = {}

    async def get_client(email):
        return deepcopy(users.get(email))

    async def request(method, path, **kwargs):
        data = kwargs.get("json", {})
        if method == "POST" and path == "/api/users":
            user = {
                "id": len(users) + 1, "shortUuid": "local-token-" + data["username"],
                "createdAt": utcnow().isoformat() + "Z",
                "lastTrafficResetAt": utcnow().isoformat() + "Z",
                "usedTrafficBytes": 0, **data,
            }
            users[data["username"]] = user
        elif method == "PATCH" and path == "/api/users":
            user = next(u for u in users.values() if u["id"] == data["id"])
            user.update(data)
        elif method == "POST" and path.endswith("/actions/extend"):
            user = next(u for u in users.values() if str(u["id"]) == path.split("/")[3])
            expiry = monthly_traffic.parse_time(user["expireAt"])
            user["expireAt"] = (expiry + timedelta(days=data["days"])).isoformat() + "Z"
        elif method == "POST" and path.endswith("/actions/reset-traffic"):
            user = next(u for u in users.values() if str(u["id"]) == path.split("/")[3])
            user.update(usedTrafficBytes=0, lastTrafficResetAt=utcnow().isoformat() + "Z")
        else:
            raise AssertionError((method, path, data))
        return deepcopy(user)

    monkeypatch.setattr(provider, "get_client", AsyncMock(side_effect=get_client))
    monkeypatch.setattr(provider, "_request", AsyncMock(side_effect=request))
    monkeypatch.setattr(provider, "_save_mapping", AsyncMock())
    monkeypatch.setattr(subscription, "get_vpn_provider", lambda: provider)
    monkeypatch.setattr(dealer, "get_vpn_provider", lambda: provider)
    return provider, users


@pytest.mark.asyncio
@pytest.mark.parametrize("months", [1, 2, 3])
async def test_paid_duration_has_a_monthly_100gb_limit(database, panel, months):
    provider, users = panel
    user = await db_repo.get_or_create_user(50000 + months)
    await subscription.grant_vpn(user.id, user.telegram_id, f"month{months}")
    remote = users[str(user.telegram_id)]
    assert remote["trafficLimitBytes"] == 100 * GB
    assert remote["trafficLimitStrategy"] == "MONTH_ROLLING"
    sub = await db_repo.get_subscription(user.id)
    assert months * 30 * 86400 - 2 < (sub.expire_at - utcnow()).total_seconds() <= months * 30 * 86400
    assert sub.traffic_limit_gb == 100
    async with database() as session:
        quota = await session.get(MonthlyTrafficQuota, str(user.telegram_id))
        assert quota.initialized and quota.extra_bytes == 0
    assert provider._save_mapping.await_count == 1


async def create_monthly_user(panel, tg=50100):
    user = await db_repo.get_or_create_user(tg)
    await subscription.grant_vpn(user.id, user.telegram_id, "month2")
    return user, panel[1][str(user.telegram_id)]


@pytest.mark.asyncio
async def test_topup_reactivates_limited_user_without_extending_or_resetting(database, panel):
    user, remote = await create_monthly_user(panel)
    remote.update(status="LIMITED", usedTrafficBytes=100 * GB)
    expiry = remote["expireAt"]
    local_expiry = (await db_repo.get_subscription(user.id)).expire_at
    await subscription.grant_vpn(user.id, user.telegram_id, TRAFFIC_TOPUP)
    assert remote["status"] == "ACTIVE"
    assert remote["usedTrafficBytes"] == 100 * GB
    assert remote["trafficLimitBytes"] == 150 * GB
    assert remote["expireAt"] == expiry
    sub = await db_repo.get_subscription(user.id)
    assert sub.expire_at == local_expiry and sub.traffic_limit_gb == 150
    patch = panel[0]._request.call_args.kwargs["json"]
    assert set(patch) == {"id", "trafficLimitBytes", "status"}


@pytest.mark.asyncio
async def test_native_monthly_reset_expires_bonus_once_and_updates_local_card(database, panel):
    user, remote = await create_monthly_user(panel)
    await subscription.grant_vpn(user.id, user.telegram_id, TRAFFIC_TOPUP)
    remote.update(lastTrafficResetAt=(utcnow() + timedelta(days=30)).isoformat() + "Z", usedTrafficBytes=0)
    await monthly_traffic.reconcile_one(panel[0], str(user.telegram_id))
    assert remote["trafficLimitBytes"] == 100 * GB
    assert (await db_repo.get_subscription(user.id)).traffic_limit_gb == 100
    count = panel[0]._request.await_count
    await monthly_traffic.reconcile_one(panel[0], str(user.telegram_id))
    assert panel[0]._request.await_count == count
    async with database() as session:
        assert (await session.get(MonthlyTrafficQuota, str(user.telegram_id))).extra_bytes == 0


@pytest.mark.asyncio
async def test_advance_renewal_preserves_current_cycle_usage_and_paid_bonus(database, panel):
    user, remote = await create_monthly_user(panel)
    await subscription.grant_vpn(user.id, user.telegram_id, TRAFFIC_TOPUP)
    remote["usedTrafficBytes"] = 140 * GB
    expiry = monthly_traffic.parse_time(remote["expireAt"])
    await subscription.grant_vpn(user.id, user.telegram_id, "month3")
    assert remote["usedTrafficBytes"] == 140 * GB
    assert remote["trafficLimitBytes"] == 150 * GB
    assert monthly_traffic.parse_time(remote["expireAt"]) == expiry + timedelta(days=90)
    assert not any("reset-traffic" in call.args[1] for call in panel[0]._request.call_args_list)


@pytest.mark.asyncio
async def test_expired_monthly_renewal_starts_fresh_without_old_bonus(database, panel):
    user, remote = await create_monthly_user(panel)
    await subscription.grant_vpn(user.id, user.telegram_id, TRAFFIC_TOPUP)
    remote.update(expireAt=(utcnow() - timedelta(days=1)).isoformat() + "Z", usedTrafficBytes=140 * GB, status="EXPIRED")
    await db_repo.update_subscription(user.id, expire_at=utcnow() - timedelta(days=1))
    await subscription.grant_vpn(user.id, user.telegram_id, "month1")
    assert remote["trafficLimitBytes"] == 100 * GB
    assert remote["usedTrafficBytes"] == 0 and remote["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_parallel_credits_accumulate_and_survive_new_provider_instance(database, panel):
    user, remote = await create_monthly_user(panel)
    await asyncio.gather(*(panel[0].add_traffic(str(user.telegram_id), 50) for _ in range(3)))
    assert remote["trafficLimitBytes"] == 250 * GB
    assert (await db_repo.get_subscription(user.id)).traffic_limit_gb == 250
    second = RemnawaveClient()
    second.get_client, second._request = panel[0].get_client, panel[0]._request
    remote.update(lastTrafficResetAt=(utcnow() + timedelta(days=30)).isoformat() + "Z", usedTrafficBytes=0)
    await monthly_traffic.reconcile_one(second, str(user.telegram_id))
    assert remote["trafficLimitBytes"] == 100 * GB


@pytest.mark.asyncio
async def test_new_credit_after_unreconciled_reset_does_not_roll_over_old_bonus(database, panel):
    user, remote = await create_monthly_user(panel)
    await panel[0].add_traffic(str(user.telegram_id), 50)
    remote.update(lastTrafficResetAt=(utcnow() + timedelta(days=30)).isoformat() + "Z", usedTrafficBytes=0)
    await panel[0].add_traffic(str(user.telegram_id), 50)
    assert remote["trafficLimitBytes"] == 150 * GB


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["missing", "expired", "disabled"])
async def test_topup_cannot_create_or_revive_an_ineligible_subscription(database, panel, state):
    user = await db_repo.get_or_create_user(50101)
    if state != "missing":
        await subscription.grant_vpn(user.id, user.telegram_id, "month1")
        if state == "expired":
            await db_repo.update_subscription(user.id, expire_at=utcnow() - timedelta(seconds=1))
        else:
            panel[1][str(user.telegram_id)]["status"] = "DISABLED"
    count = panel[0]._request.await_count
    with pytest.raises(ValueError):
        await subscription.grant_vpn(user.id, user.telegram_id, TRAFFIC_TOPUP)
    assert panel[0]._request.await_count == count


@pytest.mark.asyncio
async def test_duplicate_stars_topup_event_credits_only_once(database, panel, monkeypatch):
    user, remote = await create_monthly_user(panel)
    stars = get_stars_price(TRAFFIC_TOPUP, settings.stars_reward_usd)
    order = await db_repo.create_order(user.id, TRAFFIC_TOPUP, stars, "xtr", "traffic_topup")
    message = SimpleNamespace(answer=AsyncMock(), successful_payment=SimpleNamespace(
        currency="XTR", total_amount=stars,
        invoice_payload=client._stars_payload(order.id, user.id, TRAFFIC_TOPUP),
        telegram_payment_charge_id="local-charge",
    ))
    monkeypatch.setattr(client, "notify_admins", AsyncMock())
    for _ in range(2):
        await client.stars_successful_payment(message, lambda key, **kw: key, "en", user)
    assert remote["trafficLimitBytes"] == 150 * GB
    assert (await db_repo.get_order(order.id)).status == "paid"
    assert message.answer.await_count == 1
    assert message.answer.call_args.args[0] == "traffic_payment_success"


@pytest.mark.asyncio
async def test_payment_claim_is_atomic_with_two_workers(database):
    user = await db_repo.get_or_create_user(50103)
    order = await db_repo.create_order(user.id, TRAFFIC_TOPUP, 0.5, "usdt", "traffic_topup")
    results = await asyncio.gather(*(db_repo.claim_payment_order(order.id, "crypto_paid_processing") for _ in range(5)))
    assert results.count(True) == 1


@pytest.mark.parametrize("lang,label", [("ru", "Купить VPN"), ("en", "Buy VPN"), ("fa", "خرید VPN")])
def test_main_button_is_renamed_and_topup_is_separate_from_duration_menu(lang, label):
    t = lambda key, **kw: get_text(lang, key, **kw)
    main = builders.main_menu_kb(t)
    assert label in main.inline_keyboard[0][0].text
    assert main.inline_keyboard[0][0].callback_data == "menu:my_vpn"
    assert {b.callback_data for row in builders.plans_kb(t).inline_keyboard for b in row} == {
        "plan:month1", "plan:month2", "plan:month3", "back:main",
    }
    assert any(b.callback_data == "plan:traffic50" for row in builders.sub_link_kb(t).inline_keyboard for b in row)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PATCH"])
async def test_mutating_api_404_is_a_failure_not_silent_success(monkeypatch, panel, method):
    original = httpx.AsyncClient
    seen = []
    async def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(404, json={"message": "Not found"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(RemnawaveApiError, match="HTTP 404"):
        await RemnawaveClient()._request(method, "/api/users", json={"id": 123})
    assert seen == ["Bearer local-test-token"]
    assert await RemnawaveClient()._request("GET", "/api/users/by-username/missing") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["stars", "heleket", "dealer"])
async def test_topup_payment_creation_uses_correct_saved_amount(database, panel, monkeypatch, method):
    user, _ = await create_monthly_user(panel)
    monkeypatch.setattr(settings, "heleket_enabled", True)
    monkeypatch.setattr(client.heleket, "is_configured", lambda: True)
    invoice = AsyncMock(return_value={"uuid": "local-invoice", "url": "https://pay.example.test"})
    monkeypatch.setattr(client.heleket, "create_invoice", invoice)
    monkeypatch.setattr(client, "send_with_logo", AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"plan": TRAFFIC_TOPUP}),
        set_state=AsyncMock(), update_data=AsyncMock(), clear=AsyncMock(),
    )
    callback = SimpleNamespace(data=f"pay:{method}", answer=AsyncMock(), message=SimpleNamespace(answer_invoice=AsyncMock()))
    await client.choose_payment(callback, lambda key, **kw: get_text("en", key, **kw), "en", user, state)
    async with database() as session:
        order = await session.scalar(select(Order).where(Order.user_id == user.id))
    assert order.plan == TRAFFIC_TOPUP and order.order_type == "traffic_topup"
    expected = {"stars": get_stars_price(TRAFFIC_TOPUP, settings.stars_reward_usd), "heleket": 0.5, "dealer": 0.25}
    assert order.amount == expected[method]
    if method == "stars":
        assert callback.message.answer_invoice.call_args.kwargs["description"] == get_text("en", "traffic_invoice_description")
    if method == "heleket":
        invoice.assert_awaited_once_with(0.5, order.id)


@pytest.mark.asyncio
async def test_duplicate_crypto_checks_do_not_duplicate_topup(database, panel, monkeypatch):
    from app.services import payment_checker
    user, remote = await create_monthly_user(panel)
    order = await db_repo.create_order(user.id, TRAFFIC_TOPUP, 0.5, "usdt", "traffic_topup")
    await db_repo.update_order(order.id, external_id="local-invoice")
    monkeypatch.setattr(settings, "heleket_enabled", True)
    monkeypatch.setattr(payment_checker.heleket, "is_configured", lambda: True)
    monkeypatch.setattr(payment_checker.heleket, "get_payment", AsyncMock(return_value={"paid": True}))
    monkeypatch.setattr(payment_checker.heleket, "is_paid", lambda data: True)
    monkeypatch.setattr(payment_checker.bot, "send_message", AsyncMock())
    monkeypatch.setattr(payment_checker, "notify_admins", AsyncMock())
    await asyncio.gather(payment_checker.check_pending_payments(), payment_checker.check_pending_payments())
    assert remote["trafficLimitBytes"] == 150 * GB
    assert (await db_repo.get_order(order.id)).status == "paid"


async def create_managed(panel, monkeypatch):
    monkeypatch.setattr(settings, "vpn_provider", "remnawave")
    owner = await db_repo.get_or_create_user(50200)
    await db_repo.set_user_role(owner.telegram_id, "dealer")
    await db_repo.change_dealer_balance(owner.id, 10)
    owner = await db_repo.get_user_by_id(owner.id)
    status, sub = await db_repo.reserve_dealer_subscription_purchase(owner.id, "month2", 0.95)
    assert status == "reserved"
    await monthly_traffic.grant_monthly(panel[0], sub.xui_email, 60)
    await db_repo.complete_dealer_subscription_purchase(sub.id, owner.id, utcnow() + timedelta(days=60), 100)
    monkeypatch.setattr(dealer, "send_with_logo", AsyncMock())
    monkeypatch.setattr(dealer, "_show_subscription_card", AsyncMock())
    monkeypatch.setattr(dealer, "notify_admins", AsyncMock())
    return owner, await db_repo.get_dealer_subscription(owner.id, sub.id)


def callback(data):
    return SimpleNamespace(data=data, answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))


@pytest.mark.asyncio
async def test_managed_topup_confirmation_debits_and_credits_once(database, panel, monkeypatch):
    owner, sub = await create_managed(panel, monkeypatch)
    expiry = sub.expire_at
    await dealer.dealer_traffic_review(callback(f"dealer:traffic:{sub.id}:0"), lambda key, **kw: key, "en", owner)
    async with database() as session:
        intent = await session.scalar(select(Order).where(Order.user_id == owner.id))
    assert intent.amount == 0.25 and intent.order_type == f"managed_traffic:{sub.id}"
    cb = callback(f"dealer:trafficconfirm:{intent.id}:0")
    for _ in range(2):
        await dealer.dealer_traffic_confirm(cb, lambda key, **kw: key, "en", owner)
    assert (await db_repo.get_user_by_id(owner.id)).dealer_balance == pytest.approx(8.80)
    assert (await db_repo.get_order(intent.id)).status == "paid"
    updated = await db_repo.get_dealer_subscription(owner.id, sub.id)
    assert updated.traffic_limit_gb == 150 and updated.expire_at == expiry
    assert updated.plan == "month2"
    assert panel[1][sub.xui_email]["trafficLimitBytes"] == 150 * GB


@pytest.mark.asyncio
async def test_another_dealer_and_generic_approval_cannot_steal_managed_intent(database, panel, monkeypatch):
    owner, sub = await create_managed(panel, monkeypatch)
    other = await db_repo.get_or_create_user(50201)
    await db_repo.set_user_role(other.telegram_id, "dealer")
    other = await db_repo.get_user_by_id(other.id)
    await dealer.dealer_traffic_review(callback(f"dealer:traffic:{sub.id}:0"), lambda key, **kw: key, "en", owner)
    async with database() as session:
        intent = await session.scalar(select(Order).where(Order.user_id == owner.id))
    for who in (other, owner):
        cb = callback(f"dealer_ok:{intent.id}")
        await dealer.dealer_approve(cb, lambda key, **kw: key, "en", who)
        assert cb.answer.call_args.kwargs["show_alert"]
    await dealer.dealer_traffic_confirm(callback(f"dealer:trafficconfirm:{intent.id}:0"), lambda key, **kw: key, "en", other)
    assert (await db_repo.get_order(intent.id)).status == "pending_dealer"
    assert panel[1][sub.xui_email]["trafficLimitBytes"] == 100 * GB


@pytest.mark.asyncio
async def test_unknown_credit_outcome_stays_reserved_and_cannot_be_replayed(database, panel, monkeypatch):
    owner, sub = await create_managed(panel, monkeypatch)
    await dealer.dealer_traffic_review(callback(f"dealer:traffic:{sub.id}:0"), lambda key, **kw: key, "en", owner)
    async with database() as session:
        intent = await session.scalar(select(Order).where(Order.user_id == owner.id))
    credit = AsyncMock(side_effect=httpx.ReadTimeout("local simulated timeout"))
    monkeypatch.setattr(panel[0], "add_traffic", credit)
    cb = callback(f"dealer:trafficconfirm:{intent.id}:0")
    for _ in range(2):
        await dealer.dealer_traffic_confirm(cb, lambda key, **kw: key, "en", owner)
    assert credit.await_count == 1
    assert (await db_repo.get_order(intent.id)).status == "dealer_processing"
    assert (await db_repo.get_user_by_id(owner.id)).dealer_balance == pytest.approx(8.80)


@pytest.mark.asyncio
async def test_dealer_approval_debits_same_customer_order_once(database):
    dealer_user = await db_repo.get_or_create_user(50202)
    await db_repo.set_user_role(dealer_user.telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer_user.id, 10)
    user = await db_repo.get_or_create_user(50203)
    order = await db_repo.create_order(user.id, TRAFFIC_TOPUP, 0.25, "usd", "traffic_topup")
    await db_repo.update_order(order.id, status="pending_dealer")
    results = await asyncio.gather(*(db_repo.claim_dealer_order(order.id, dealer_user.id) for _ in range(3)))
    assert [r[0] for r in results].count("claimed") == 1
    assert (await db_repo.get_user_by_id(dealer_user.id)).dealer_balance == 9.75


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["renew", "topup"])
async def test_native_reset_during_panel_write_cannot_carry_old_bonus(database, panel, monkeypatch, operation):
    user, remote = await create_monthly_user(panel)
    await panel[0].add_traffic(str(user.telegram_id), 50)
    original = panel[0]._request
    crossed = False
    async def crossing_request(method, path, **kwargs):
        nonlocal crossed
        if not crossed and method == "PATCH":
            crossed = True
            remote.update(lastTrafficResetAt=(utcnow() + timedelta(days=30)).isoformat() + "Z", usedTrafficBytes=0)
        return await original(method, path, **kwargs)
    monkeypatch.setattr(panel[0], "_request", AsyncMock(side_effect=crossing_request))
    if operation == "renew":
        await subscription.grant_vpn(user.id, user.telegram_id, "month1")
        expected = 100
    else:
        await panel[0].add_traffic(str(user.telegram_id), 50)
        expected = 150
    assert remote["trafficLimitBytes"] == expected * GB
    assert (await db_repo.get_subscription(user.id)).traffic_limit_gb == expected
    await monthly_traffic.reconcile_one(panel[0], str(user.telegram_id))
    assert remote["trafficLimitBytes"] == expected * GB


@pytest.mark.asyncio
async def test_promo_time_extension_keeps_bonus_reconciliation_scheduled(database, panel, monkeypatch):
    user, remote = await create_monthly_user(panel)
    await panel[0].add_traffic(str(user.telegram_id), 50)
    remote["expireAt"] = (utcnow() + timedelta(days=90)).isoformat() + "Z"
    original = panel[0]._request
    async def extended(method, path, **kwargs):
        if path.endswith("/actions/extend"):
            return deepcopy(remote)
        return await original(method, path, **kwargs)
    monkeypatch.setattr(panel[0], "_request", AsyncMock(side_effect=extended))
    await panel[0].extend_client(str(user.telegram_id), 30)
    async with database() as session:
        quota = await session.get(MonthlyTrafficQuota, str(user.telegram_id))
        assert quota.valid_until == monthly_traffic.parse_time(remote["expireAt"])


@pytest.mark.asyncio
async def test_concurrent_paid_durations_match_panel_and_cannot_shorten_local_expiry(database, panel):
    user = await db_repo.get_or_create_user(50300)
    await asyncio.gather(
        subscription.grant_vpn(user.id, user.telegram_id, "month1"),
        subscription.grant_vpn(user.id, user.telegram_id, "month2"),
    )
    remote_expiry = monthly_traffic.parse_time(panel[1][str(user.telegram_id)]["expireAt"])
    sub = await db_repo.get_subscription(user.id)
    assert sub.expire_at == remote_expiry
    assert 90 * 86400 - 2 < (sub.expire_at - utcnow()).total_seconds() <= 90 * 86400
    assert sub.traffic_limit_gb == 100


@pytest.mark.asyncio
async def test_explicit_admin_shortening_still_works_and_quota_update_is_preserved(database, panel):
    user, remote = await create_monthly_user(panel)
    await panel[0].add_traffic(str(user.telegram_id), 50)
    sub = await db_repo.get_subscription(user.id)
    shorter = sub.expire_at - timedelta(days=7)
    await db_repo.upsert_subscription(user.id, sub.xui_email, shorter, traffic_limit_gb=None)
    updated = await db_repo.get_subscription(user.id)
    assert updated.expire_at == shorter and updated.traffic_limit_gb == 150


@pytest.mark.asyncio
async def test_legacy_pending_order_does_not_disable_monthly_policy_or_erase_bonus(database, panel):
    user, remote = await create_monthly_user(panel)
    await panel[0].add_traffic(str(user.telegram_id), 50)
    expires = monthly_traffic.parse_time(remote["expireAt"])
    await subscription.grant_vpn(user.id, user.telegram_id, "10gb")
    assert remote["trafficLimitStrategy"] == "MONTH_ROLLING"
    assert remote["trafficLimitBytes"] == 160 * GB
    assert monthly_traffic.parse_time(remote["expireAt"]) == expires + timedelta(days=30)
    assert (await db_repo.get_subscription(user.id)).traffic_limit_gb == 160


@pytest.mark.asyncio
async def test_managed_purchase_and_renewal_use_duration_prices_and_keep_bonus(database, panel, monkeypatch):
    monkeypatch.setattr(settings, "vpn_provider", "remnawave")
    owner = await db_repo.get_or_create_user(50301)
    await db_repo.set_user_role(owner.telegram_id, "dealer")
    await db_repo.change_dealer_balance(owner.id, 10)
    owner = await db_repo.get_user_by_id(owner.id)
    monkeypatch.setattr(dealer, "subscription_link", AsyncMock(return_value="https://sub.example.test/local-test"))
    monkeypatch.setattr(dealer, "send_with_logo", AsyncMock())
    monkeypatch.setattr(dealer, "_show_subscription_card", AsyncMock())
    state = SimpleNamespace(set_state=AsyncMock(), update_data=AsyncMock())
    await dealer.dealer_buy_confirm(callback("dealer:buyconfirm:month3"), lambda key, **kw: key, "en", owner, state)
    items, count = await db_repo.list_dealer_subscriptions(owner.id)
    assert count == 1
    sub = items[0]
    assert sub.plan == "month3" and sub.traffic_limit_gb == 100
    assert (await db_repo.get_user_by_id(owner.id)).dealer_balance == pytest.approx(8.645)
    await panel[0].add_traffic(sub.xui_email, 50)
    remote = panel[1][sub.xui_email]
    remote["usedTrafficBytes"] = 120 * GB
    await dealer.dealer_subscription_renew_confirm(
        callback(f"dealer:renewconfirm:{sub.id}:month2:0"), lambda key, **kw: key, "en", owner,
    )
    renewed = await db_repo.get_dealer_subscription(owner.id, sub.id)
    assert renewed.expire_at == sub.expire_at + timedelta(days=60)
    assert renewed.traffic_limit_gb == 150 and renewed.plan == "month2"
    assert remote["usedTrafficBytes"] == 120 * GB
    assert (await db_repo.get_user_by_id(owner.id)).dealer_balance == pytest.approx(7.695)
