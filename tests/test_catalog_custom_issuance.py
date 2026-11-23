"""Custom Remnawave quotas, dealer wholesale and real-income ledger."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import settings
from app.database.models import AnalyticsSale, DealerSubscription, MonthlyTrafficQuota, utcnow
from app.repositories import commercial_catalog_repo as catalog, db_repo
from app.services import monthly_traffic, subscription
from app.services.catalog_wholesale import minimum_toman, wholesale_usd
from app.services.remnawave_api import RemnawaveClient

GB = 1024 ** 3


@pytest.fixture
def custom_panel(monkeypatch):
    for key, value in {
        "remnawave_api_url": "https://panel.example.test",
        "remnawave_api_token": "test-token",
        "remnawave_subscription_url": "https://subscriptions.example.test",
        "remnawave_squad_uuid": "main-test-squad",
        "catalog_custom_issuance_enabled": True,
        "catalog_max_devices": 10,
        "catalog_max_traffic_gb": 1000,
    }.items():
        monkeypatch.setattr(settings, key, value)
    provider = RemnawaveClient()
    users = {}

    async def find(email):
        return deepcopy(users.get(email))

    async def request(method, path, **kwargs):
        data = kwargs.get("json", {})
        if method == "POST" and path == "/api/users":
            user = {
                "id": len(users) + 1,
                "shortUuid": "subscription-test",
                "createdAt": utcnow().isoformat() + "Z",
                "lastTrafficResetAt": utcnow().isoformat() + "Z",
                "usedTrafficBytes": 0,
                **data,
            }
            users[data["username"]] = user
        elif method == "PATCH" and path == "/api/users":
            user = next(u for u in users.values() if u["id"] == data["id"])
            user.update(data)
        elif method == "POST" and path.endswith("/actions/reset-traffic"):
            user = next(u for u in users.values() if str(u["id"]) == path.split("/")[3])
            user.update(usedTrafficBytes=0, lastTrafficResetAt=utcnow().isoformat() + "Z")
        else:
            raise AssertionError((method, path, data))
        return deepcopy(user)

    monkeypatch.setattr(provider, "get_client", AsyncMock(side_effect=find))
    monkeypatch.setattr(provider, "_request", AsyncMock(side_effect=request))
    monkeypatch.setattr(provider, "_save_mapping", AsyncMock())
    return provider, users


@pytest.mark.asyncio
async def test_custom_50gb_quota_survives_rolling_reset_and_credit_expires(database, custom_panel):
    provider, users = custom_panel
    await monthly_traffic.grant_monthly(
        provider, "manual-catalog-test", 45, limit_ip=4,
        traffic_gb=50, squad_uuid="approved-extra-squad", custom_terms=True,
    )
    remote = users["manual-catalog-test"]
    assert remote["trafficLimitBytes"] == 50 * GB
    assert remote["activeInternalSquads"] == ["approved-extra-squad"]
    assert remote["hwidDeviceLimit"] == 4
    async with database() as session:
        quota = await session.get(MonthlyTrafficQuota, "manual-catalog-test")
        assert quota.base_bytes == 50 * GB and quota.initialized
    assert await monthly_traffic.add_monthly_traffic(provider, "manual-catalog-test", 50) == 100
    assert remote["trafficLimitBytes"] == 100 * GB
    remote["usedTrafficBytes"] = 0
    remote["lastTrafficResetAt"] = (utcnow() + timedelta(days=30)).isoformat() + "Z"
    await monthly_traffic.reconcile_one(provider, "manual-catalog-test")
    assert remote["trafficLimitBytes"] == 50 * GB
    async with database() as session:
        quota = await session.get(MonthlyTrafficQuota, "manual-catalog-test")
        assert quota.base_bytes == 50 * GB and quota.extra_bytes == 0
    # Renewal changes the base while preserving the still-valid subscription.
    await monthly_traffic.grant_monthly(
        provider, "manual-catalog-test", 20, limit_ip=5,
        traffic_gb=75, squad_uuid="different-squad-do-not-change-existing",
        custom_terms=True,
    )
    assert remote["trafficLimitBytes"] == 75 * GB
    assert remote["activeInternalSquads"] == ["approved-extra-squad"]


@pytest.mark.asyncio
async def test_manual_dealer_buy_respects_catalog_cost_and_records_actual_payment_once(database, monkeypatch):
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "main-test-squad")
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", True)
    monkeypatch.setattr(settings, "catalog_custom_issuance_enabled", True)
    monkeypatch.setattr(settings, "dealer_discount", 0.5)
    monkeypatch.setattr(settings, "toman_per_usd", 150000.0)
    dealer = await db_repo.get_or_create_user(91001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    await db_repo.change_dealer_balance(dealer.id, 100.0)
    item = await catalog.create_tariff(
        dealer.telegram_id, name_fa="اشتراک 45 روزه", period_days=45,
        devices=4, traffic_gb=50, retail_toman=500000,
    )
    item = await catalog.update_tariff(
        dealer.telegram_id, item.id, item.revision, action="publish", changes={},
    )
    assert minimum_toman(45, 4, 50) <= item.retail_toman
    assert catalog.provision_plan(item) == f"cat_{item.id}"
    cost = wholesale_usd(45, 4, 50)
    status, sub = await db_repo.reserve_dealer_subscription_purchase(
        dealer.id, f"cat_{item.id}", 0.01,
        catalog_tariff_id=item.id, catalog_expected_revision=item.revision,
        request_key="manual-test-key",
    )
    assert status == "reserved" and sub.catalog_snapshot["wholesale_usd"] == cost
    assert sub.catalog_snapshot["base_retail_toman"] == 500000
    status2, same = await db_repo.reserve_dealer_subscription_purchase(
        dealer.id, f"cat_{item.id}", 0.01,
        catalog_tariff_id=item.id, catalog_expected_revision=item.revision,
        request_key="manual-test-key",
    )
    assert status2 == "duplicate" and same.id == sub.id
    current_dealer = await db_repo.get_user_by_id(dealer.id)
    assert abs(current_dealer.dealer_balance - (100 - cost)) < 0.00001
    done = await db_repo.complete_dealer_subscription_purchase(
        sub.id, dealer.id, utcnow() + timedelta(days=45), 50,
    )
    assert done.status == "active"
    async with database() as session:
        sales = list((await session.scalars(select(AnalyticsSale))).all())
    assert len(sales) == 1
    assert sales[0].catalog_tariff_id == item.id
    assert sales[0].retail_toman == 500000
    assert sales[0].actual_received_toman is None
    assert await db_repo.record_manual_dealer_receipt(dealer.id, sub.id, 350000)
    assert not await db_repo.record_manual_dealer_receipt(dealer.id, sub.id, 450000)
    async with database() as session:
        refreshed = await session.get(AnalyticsSale, sales[0].id)
        assert refreshed.actual_received_toman == 350000


@pytest.mark.asyncio
async def test_extra_squad_approval_is_admin_only_and_revocable(database, monkeypatch):
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "main-test-squad")
    monkeypatch.setattr(settings, "catalog_custom_issuance_enabled", True)
    dealer = await db_repo.get_or_create_user(92001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    admin = await db_repo.get_or_create_user(92002)
    monkeypatch.setattr(settings, "admin_ids", str(admin.telegram_id))
    uuid = str(uuid4())
    with pytest.raises(catalog.CatalogDenied):
        await catalog.upsert_allowed_squad(dealer.telegram_id, uuid, "ایران")
    await catalog.upsert_allowed_squad(admin.telegram_id, uuid, "ایران")
    squads = await catalog.available_squads(dealer.telegram_id)
    assert (uuid, "ایران") in squads
    item = await catalog.create_tariff(
        dealer.telegram_id, name_fa="تست", period_days=40,
        devices=2, traffic_gb=70, retail_toman=500000,
    )
    item = await catalog.update_tariff(
        dealer.telegram_id, item.id, item.revision,
        action="edit", changes={"internal_squad_uuid": uuid},
    )
    item = await catalog.update_tariff(
        dealer.telegram_id, item.id, item.revision, action="publish", changes={},
    )
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", True)
    assert (await catalog.get_published(item.id)).id == item.id
    await catalog.upsert_allowed_squad(admin.telegram_id, uuid, "ایران", enabled=False)
    assert await catalog.get_published(item.id) is None
    with pytest.raises(catalog.CatalogInvalid):
        await catalog.upsert_allowed_squad(admin.telegram_id, "invalid", "Bad")
