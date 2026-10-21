from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.handlers import client
from app.keyboards.builders import dealer_buy_plans_kb, dealer_renew_plans_kb, plans_kb
from app.middlewares.i18n import get_text
from app.repositories import db_repo
from app.services.subscription import grant_vpn
from app.services import subscription as subscription_service
from app.utils.tariffs import PLANS, get_dealer_debit_usd, get_price_display, get_stars_price, get_usd_price


EXPECTED_PRICES = {"month1": 1, "month2": 2, "month3": 3}


def test_exact_price_list_and_derived_payment_amounts(monkeypatch):
    assert {key: value["price_usd"] for key, value in PLANS.items() if key != "test"} == EXPECTED_PRICES
    for key, usd in EXPECTED_PRICES.items():
        assert PLANS[key]["days"] == int(key[-1]) * 30
        assert PLANS[key]["traffic_gb"] == 100
        assert PLANS[key]["monthly"] is True
        assert get_usd_price(key) == usd
        assert get_stars_price(key, settings.stars_reward_usd) * settings.stars_reward_usd >= usd
        assert get_dealer_debit_usd(key, 235000, 0.5) == usd * 0.5
    for rate in (100000, 235000, 500000):
        monkeypatch.setattr(settings, "toman_per_usd", rate)
        assert get_usd_price("month1") == 1
        assert get_usd_price("traffic50") == 0.5
    assert get_price_display("month1", "ru") == "$1"
    assert get_price_display("month2", "fa") == "$2"
    assert PLANS["test"]["price_toman"] == 0


def test_all_sales_menus_have_new_plans_and_no_retired_plan():
    for kb, prefix in [
        (plans_kb(lambda key: key, lang="en"), "plan:"),
        (dealer_buy_plans_kb(), "dealer:buyplan:"),
        (dealer_renew_plans_kb(123, 0), "dealer:renewplan:123:"),
    ]:
        callbacks = {b.callback_data for row in kb.inline_keyboard for b in row}
        for key in EXPECTED_PRICES:
            assert any(data.startswith(prefix + key) for data in callbacks)
        assert not any("gb" in data or "traffic50" in data for data in callbacks)


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", ["10gb", "40gb"])
async def test_existing_stars_invoice_keeps_its_original_amount(database, plan):
    user = await db_repo.get_or_create_user(20001)
    order = await db_repo.create_order(user.id, plan, 123, "xtr")
    query = SimpleNamespace(
        invoice_payload=client._stars_payload(order.id, user.id, plan),
        currency="XTR", total_amount=123, answer=AsyncMock(),
    )
    await client.stars_pre_checkout(query, lambda key: key, "en", user)
    assert query.answer.call_args.kwargs["ok"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", ["10gb", "40gb"])
async def test_existing_stars_payment_is_fulfilled_after_price_update(database, monkeypatch, plan):
    user = await db_repo.get_or_create_user(20002)
    order = await db_repo.create_order(user.id, plan, 123, "xtr")
    message = SimpleNamespace(
        successful_payment=SimpleNamespace(
            invoice_payload=client._stars_payload(order.id, user.id, plan),
            currency="XTR", total_amount=123, telegram_payment_charge_id="test-charge",
        ),
        answer=AsyncMock(),
    )
    provision = AsyncMock(return_value="sub-token")
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    monkeypatch.setattr(client, "notify_admins", AsyncMock())
    await client.stars_successful_payment(message, lambda key, **kwargs: key, "en", user)
    assert (await db_repo.get_order(order.id)).status == "paid"
    assert provision.call_args.kwargs["traffic_gb"] == int(plan[:-2])


@pytest.mark.asyncio
async def test_stars_payment_with_wrong_amount_is_rejected(database, monkeypatch):
    user = await db_repo.get_or_create_user(20003)
    order = await db_repo.create_order(user.id, "10gb", 123, "xtr")
    query = SimpleNamespace(
        invoice_payload=client._stars_payload(order.id, user.id, "10gb"),
        currency="XTR", total_amount=1, answer=AsyncMock(),
    )
    await client.stars_pre_checkout(query, lambda key: key, "en", user)
    assert query.answer.call_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_retired_plan_can_be_fulfilled_for_legacy_dealer_or_crypto_order(database, monkeypatch):
    user = await db_repo.get_or_create_user(20004)
    provision = AsyncMock(return_value="sub-token")
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: SimpleNamespace(add_client=provision))
    await grant_vpn(user.id, user.telegram_id, "40gb")
    assert provision.call_args.kwargs["traffic_gb"] == 40
    assert "40gb" not in PLANS


@pytest.mark.asyncio
@pytest.mark.parametrize("plan,original_toman,is_current", [
    ("10gb", 39000, True), ("10gb", 70000, False), ("40gb", 265000, True),
])
async def test_receipt_uses_saved_dealer_amount(database, monkeypatch, plan, original_toman, is_current):
    user = await db_repo.get_or_create_user(20005)
    saved_usd = round(original_toman / settings.toman_per_usd * settings.dealer_discount, 3)
    order = await db_repo.create_order(user.id, plan, saved_usd, "usd")
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"order_id": order.id, "plan": plan}),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="local-receipt")], answer=AsyncMock(),
    )
    photo = AsyncMock()
    monkeypatch.setattr(client.bot, "send_photo", photo)
    monkeypatch.setattr(db_repo, "list_dealers", AsyncMock(return_value=[
        SimpleNamespace(telegram_id=999, lang="en"),
    ]))
    monkeypatch.setattr(client, "notify_admins", AsyncMock())
    await client.receive_receipt(
        message, lambda key, **kwargs: get_text("en", key, **kwargs), "en", user, state,
    )
    caption = photo.call_args.kwargs["caption"]
    assert client._format_usd(saved_usd) in caption
    if is_current:
        assert client._format_toman(original_toman, "en") in caption
    else:
        assert get_text("en", "dealer_original_amount") in caption
        assert "39,000 Toman" not in caption
    assert (await db_repo.get_order(order.id)).amount == saved_usd
    assert (await db_repo.get_order(order.id)).status == "pending_dealer"
