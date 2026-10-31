import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest

from app.bot import bot
from app.config import settings
from app.database.models import utcnow
from app.repositories import db_repo


def init_data(telegram_id=99001, *, age=0, extra=None):
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "test-query",
        "user": json.dumps({"id": telegram_id, "first_name": "Viewer", **(extra or {})}, separators=(",", ":")),
    }
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


async def call(path, *, viewer=99001, raw=None, method="GET", payload=None):
    from app.webapp import app
    headers = {"Authorization": "tma " + (init_data(viewer) if raw is None else raw)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.local") as client:
        return await client.request(method, path, headers=headers, json=payload)


@pytest.fixture(autouse=True)
def web_settings(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    monkeypatch.setattr(bot, "me", AsyncMock(return_value=SimpleNamespace(username="OrionTestBot")))


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["empty", "tampered", "expired", "future", "duplicate"])
async def test_miniapp_rejects_untrusted_or_stale_telegram_data(database, invalid):
    raw = {
        "empty": "", "tampered": init_data().replace("99001", "99002"),
        "expired": init_data(age=90000), "future": init_data(age=-300),
        "duplicate": init_data() + "&auth_date=1",
    }[invalid]
    result = await call("/api/me", raw=raw)
    assert result.status_code == 401
    assert settings.bot_token not in result.text and "test-query" not in result.text


@pytest.mark.asyncio
async def test_miniapp_uses_database_roles_and_hides_foreign_dealer_scopes(database):
    customer = await db_repo.get_or_create_user(82001)
    first = await db_repo.get_or_create_user(80001)
    second = await db_repo.get_or_create_user(80002)
    for user in (first, second):
        await db_repo.set_user_role(user.telegram_id, "dealer")
    result = await call("/api/me", viewer=customer.telegram_id,
                        raw=init_data(customer.telegram_id, extra={"role": "admin"}))
    assert result.status_code == 403
    assert (await call("/api/me", viewer=first.telegram_id)).json()["viewer"]["role"] == "dealer"
    for path in ("/api/overview?dealer=80002", "/api/users?dealer=80002", "/api/campaigns?dealer=80002"):
        assert (await call(path, viewer=first.telegram_id)).status_code == 403
    await db_repo.set_user_blocked(first.telegram_id, True)
    assert (await call("/api/me", viewer=first.telegram_id)).status_code == 403


@pytest.mark.asyncio
async def test_campaign_api_creates_real_links_and_scopes_visitors_and_buyers(database):
    from app.repositories import analytics_repo as ar
    for tg in (80001, 80002):
        dealer = await db_repo.get_or_create_user(tg)
        await db_repo.set_user_role(tg, "dealer")
    created = await call("/api/campaigns", viewer=80001, method="POST", payload={"name": "  Иран канал  "})
    assert created.status_code == 201
    campaign = created.json()
    assert campaign["name"] == "Иран канал"
    assert campaign["link"] == f"https://t.me/OrionTestBot?start={campaign['code']}"
    buyer = await db_repo.get_or_create_user(82002)
    unpaid = await db_repo.get_or_create_user(82003)
    for user in (buyer, unpaid):
        await ar.track_start(user, "/start " + campaign["code"], at=utcnow() - timedelta(minutes=1))
    order = await db_repo.create_order(buyer.id, "month3d2", 4.05, "usdt")
    await db_repo.claim_payment_order(order.id, "crypto_paid_processing")
    campaigns = (await call("/api/campaigns", viewer=80001)).json()["items"]
    assert len(campaigns) == 1 and campaigns[0]["visitors"] == 2
    assert campaigns[0]["buyers"] == 1 and campaigns[0]["conversion"] == 50
    assert (await call("/api/campaigns", viewer=80002)).json()["items"] == []
    assert (await call(f"/api/users?campaign={campaign['id']}", viewer=80002)).status_code == 403
    visitors = (await call(f"/api/users?campaign={campaign['id']}", viewer=80001)).json()["items"]
    assert {x["telegram_id"]: x["paid"] for x in visitors} == {82002: True, 82003: False}
    paid = (await call(f"/api/users?campaign={campaign['id']}&paid=paid", viewer=80001)).json()
    assert paid["total"] == 1 and paid["items"][0]["telegram_id"] == buyer.telegram_id


@pytest.mark.asyncio
async def test_reports_keep_currencies_and_dealer_prices_separate(database):
    buyer = await db_repo.get_or_create_user(82004, "paid_buyer")
    other = await db_repo.get_or_create_user(82005)
    dealer = await db_repo.get_or_create_user(80001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer.id, 20)
    order = await db_repo.create_order(buyer.id, "month2d3", 1.615, "usd", retail_toman=484500)
    await db_repo.update_order(order.id, status="pending_dealer")
    await db_repo.claim_dealer_order(order.id, dealer.id)
    await db_repo.complete_dealer_order(order.id, dealer.id)
    for currency, amount, status in (("xtr", 77, "stars_paid_processing"), ("usdt", 1.9, "crypto_paid_processing")):
        paid = await db_repo.create_order(buyer.id, "month1", amount, currency)
        await db_repo.claim_payment_order(paid.id, status)
    await db_repo.create_order(other.id, "month1", 999, "usdt")
    await db_repo.upsert_subscription(buyer.id, str(buyer.telegram_id), utcnow() + timedelta(days=60), traffic_limit_gb=100)
    overview = (await call("/api/overview?period=30d")).json()
    assert overview["sales"]["subscriptions"] == 3 and overview["sales"]["buyers"] == 1
    totals = {x["currency"]: x["amount"] for x in overview["totals"]}
    assert float(totals["USD"]) == 1.615 and float(totals["XTR"]) == 77 and float(totals["USDT"]) == 1.9
    assert overview["retail_toman"] == 484500 and overview["estimated_count"] == 0
    own = (await call("/api/overview?period=30d", viewer=80001)).json()
    assert own["sales"]["subscriptions"] == 1 and len(own["totals"]) == 1
    users = (await call("/api/users?paid=paid")).json()
    assert users["total"] == 1 and users["items"][0]["telegram_id"] == 82004
    assert users["items"][0]["active"] is True and users["items"][0]["expires_at"]
    unpaid = (await call("/api/users?paid=unpaid")).json()
    assert unpaid["total"] == 1 and unpaid["items"][0]["telegram_id"] == other.telegram_id


@pytest.mark.asyncio
async def test_expired_paying_customer_and_managed_subscription_remain_visible(database):
    buyer = await db_repo.get_or_create_user(82006)
    paid = await db_repo.create_order(buyer.id, "month3d3", 2.295, "usd")
    from app.repositories import analytics_repo as ar
    await db_repo.update_order(paid.id, status="paid")
    await ar.backfill_sales()
    await db_repo.upsert_subscription(buyer.id, str(buyer.telegram_id), utcnow() - timedelta(days=1), traffic_limit_gb=100)
    dealer = await db_repo.get_or_create_user(80001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer.id, 10)
    _, sub = await db_repo.reserve_dealer_subscription_purchase(dealer.id, "month2d2", 1.425)
    await db_repo.complete_dealer_subscription_purchase(sub.id, dealer.id, utcnow() + timedelta(days=60), 100)
    rows = (await call("/api/users?paid=paid")).json()["items"]
    assert rows[0]["paid"] is True and rows[0]["active"] is False
    assert rows[0]["product"]["devices"] == 3 and rows[0]["product"]["days"] == 90
    managed = (await call("/api/users?kind=managed", viewer=80001)).json()
    assert managed["total"] == 1 and managed["items"][0]["kind"] == "managed"
    assert managed["items"][0]["telegram_id"] is None and managed["items"][0]["product"]["days"] == 60


def test_moscow_period_boundaries_are_calendar_based():
    from app.services.analytics import report_period
    start, end = report_period("today", at=datetime(2026, 10, 31, 21, 30))
    assert start == datetime(2026, 10, 31, 21) and end == datetime(2026, 11, 1, 21)
    start, end = report_period("prev_month", at=datetime(2026, 10, 31, 21, 30))
    assert start == datetime(2026, 9, 30, 21) and end == datetime(2026, 10, 31, 21)


@pytest.mark.asyncio
async def test_api_input_bounds_empty_state_and_cache_control(database):
    response = await call("/api/overview?period=30d")
    assert response.status_code == 200 and response.json()["sales"]["subscriptions"] == 0
    assert response.headers["cache-control"] == "no-store"
    assert (await call("/api/overview?period=bad")).status_code in (400, 422)
    assert (await call("/api/users?page=-1")).status_code in (400, 422)
    assert (await call("/api/campaigns", method="POST", payload={"name": "   "})).status_code in (400, 422)
    assert (await call("/api/campaigns", method="POST", payload={"name": "x" * 81})).status_code in (400, 422)
    from app.webapp import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.local") as client:
        assert (await client.get("/api/me")).status_code == 401


def test_analytics_launch_button_requires_https_and_uses_dealer_language(monkeypatch):
    from app.keyboards.builders import admin_menu_kb, dealer_menu_kb
    from app.middlewares.i18n import get_text
    for url in ("", "http://unsafe.example/", "https://user:pass@example.test/", "https://["):
        monkeypatch.setattr(settings, "analytics_webapp_url", url)
        assert not any(b.web_app for row in admin_menu_kb().inline_keyboard for b in row)
    monkeypatch.setattr(settings, "analytics_webapp_url", "https://stats.example.test/analytics/")
    buttons = [b for row in admin_menu_kb().inline_keyboard for b in row if b.web_app]
    assert len(buttons) == 1 and buttons[0].web_app.url == "https://stats.example.test/analytics/"
    for lang, word in (("ru", "Аналитика"), ("en", "Analytics"), ("fa", "آمار")):
        buttons = [b for row in dealer_menu_kb(lambda key: get_text(lang, key)).inline_keyboard for b in row if b.web_app]
        assert len(buttons) == 1 and word in buttons[0].text


@pytest.mark.asyncio
async def test_campaign_body_cannot_assign_another_dealer_as_owner(database):
    for tg in (80001, 80002):
        await db_repo.get_or_create_user(tg)
        await db_repo.set_user_role(tg, "dealer")
    response = await call("/api/campaigns", viewer=80001, method="POST", payload={
        "name": "Own campaign", "owner_telegram_id": 80002,
    })
    assert response.status_code == 201 and response.json()["owner_telegram_id"] == 80001
    assert (await call("/api/campaigns", viewer=80002)).json()["items"] == []
