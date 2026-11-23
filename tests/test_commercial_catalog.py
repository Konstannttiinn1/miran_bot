"""Phase 1 commercial catalogue: database authorization, draft lifecycle and idempotent import.

No test creates an invoice or contacts the actual VPN panel.
"""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.database.models import CommercialTariff, CommercialTariffChange
from app.keyboards.builders import admin_menu_kb, dealer_menu_kb
from app.middlewares.i18n import get_text
from app.repositories import commercial_catalog_repo as catalog
from app.repositories import db_repo


@pytest.mark.asyncio
async def test_dealer_creates_and_edits_draft_without_touching_legacy_orders(database, monkeypatch):
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    dealer = await db_repo.get_or_create_user(54001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    plan = await catalog.create_tariff(
        dealer.telegram_id, name_fa=" اقتصادی ", period_days=30,
        devices=1, traffic_gb=100, retail_toman=150000,
    )
    assert plan.name_fa == "اقتصادی"
    assert plan.status == "draft" and plan.revision == 1
    assert plan.sku.startswith("cat-")
    assert plan.internal_squad_uuid == "test-squad"
    assert await catalog.counts(dealer.telegram_id) == {
        "draft": 1, "published": 0, "paused": 0, "archived": 0, "all": 1,
    }

    plan = await catalog.update_tariff(
        dealer.telegram_id, plan.id, plan.revision,
        action="edit", changes={"retail_toman": 180000},
    )
    assert plan.retail_toman == 180000 and plan.revision == 2
    with pytest.raises(catalog.CatalogConflict):
        await catalog.update_tariff(
            dealer.telegram_id, plan.id, 1,
            action="edit", changes={"retail_toman": 200000},
        )
    plan = await catalog.update_tariff(
        dealer.telegram_id, plan.id, plan.revision, action="publish", changes={},
    )
    assert plan.status == "published"
    with pytest.raises(catalog.CatalogInvalid):
        await catalog.update_tariff(
            dealer.telegram_id, plan.id, plan.revision,
            action="edit", changes={"traffic_gb": 500},
        )
    plan = await catalog.update_tariff(
        dealer.telegram_id, plan.id, plan.revision, action="pause", changes={},
    )
    plan = await catalog.update_tariff(
        dealer.telegram_id, plan.id, plan.revision, action="edit",
        changes={"traffic_gb": 400},
    )
    assert not catalog.supports_issuance(plan)
    with pytest.raises(catalog.CatalogInvalid):
        await catalog.update_tariff(
            dealer.telegram_id, plan.id, plan.revision, action="resume", changes={},
        )
    plan = await catalog.update_tariff(
        dealer.telegram_id, plan.id, plan.revision,
        action="edit", changes={"traffic_gb": 100},
    )
    plan = await catalog.update_tariff(
        dealer.telegram_id, plan.id, plan.revision, action="resume", changes={},
    )
    plan = await catalog.update_tariff(
        dealer.telegram_id, plan.id, plan.revision, action="archive", changes={},
    )
    assert plan.status == "archived"
    with pytest.raises(catalog.CatalogInvalid):
        await catalog.update_tariff(
            dealer.telegram_id, plan.id, plan.revision,
            action="edit", changes={"retail_toman": 1},
        )
    async with database() as session:
        from sqlalchemy import select
        changes = list((await session.scalars(
            select(CommercialTariffChange).where(CommercialTariffChange.tariff_id == plan.id)
            .order_by(CommercialTariffChange.id)
        )).all())
        assert len(changes) == 7
        assert changes[0].action == "create"
        assert changes[-1].after["status"] == "archived"
    assert "bc:open" in str(dealer_menu_kb(lambda key, **kw: get_text("fa", key, **kw)))


@pytest.mark.asyncio
async def test_legacy_import_is_idempotent_and_preserves_changes(database, monkeypatch):
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    dealer = await db_repo.get_or_create_user(54011)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    assert await catalog.import_legacy(dealer.telegram_id) == 9
    assert await catalog.import_legacy(dealer.telegram_id) == 0
    all_items, total = await catalog.list_tariffs(dealer.telegram_id, per_page=10)
    assert total == 9 and len(all_items) == 9
    assert {x.sku for x in all_items} == {
        "legacy-month1", "legacy-month1d2", "legacy-month1d3",
        "legacy-month2", "legacy-month2d2", "legacy-month2d3",
        "legacy-month3", "legacy-month3d2", "legacy-month3d3",
    }
    target = all_items[0]
    updated = await catalog.update_tariff(
        dealer.telegram_id, target.id, target.revision,
        action="edit", changes={"retail_toman": 543210},
    )
    assert await catalog.import_legacy(dealer.telegram_id) == 0
    current = await catalog.get_tariff(dealer.telegram_id, target.id)
    assert current.retail_toman == 543210 and current.revision == updated.revision
    cloned = await catalog.clone_tariff(dealer.telegram_id, target.id)
    assert cloned.status == "draft" and cloned.sku.startswith("cat-")
    assert cloned.id != target.id and cloned.retail_toman == target.retail_toman


@pytest.mark.asyncio
async def test_role_ownership_and_input_validations(database, monkeypatch):
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    dealer = await db_repo.get_or_create_user(54021)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    outsider = await db_repo.get_or_create_user(54022)
    with pytest.raises(catalog.CatalogDenied):
        await catalog.create_tariff(
            outsider.telegram_id, name_fa="Unauthorized", period_days=30,
            devices=1, traffic_gb=100, retail_toman=1000,
        )
    tariff = await catalog.create_tariff(
        dealer.telegram_id, name_fa="برنامه", period_days=7,
        devices=4, traffic_gb=20, retail_toman=10000,
    )
    assert not catalog.supports_issuance(tariff)
    with pytest.raises(catalog.CatalogInvalid):
        await catalog.update_tariff(
            dealer.telegram_id, tariff.id, tariff.revision,
            action="publish", changes={},
        )
    for patch in (
        {"retail_toman": 0}, {"retail_toman": -10}, {"retail_toman": 0.7},
        {"devices": 0}, {"period_days": 400}, {"traffic_gb": 0},
        {"name_fa": ""}, {"internal_squad_uuid": "arbitrary"},
    ):
        with pytest.raises(catalog.CatalogInvalid):
            await catalog.update_tariff(
                dealer.telegram_id, tariff.id, tariff.revision,
                action="edit", changes=patch,
            )
    await db_repo.set_user_blocked(dealer.telegram_id, True)
    with pytest.raises(catalog.CatalogDenied):
        await catalog.get_tariff(dealer.telegram_id, tariff.id)
    with pytest.raises(catalog.CatalogDenied):
        await catalog.counts(outsider.telegram_id)


def test_catalogue_menu_is_available_for_administrator_and_dealer():
    t = lambda key, **kw: get_text("fa", key, **kw)
    for kb in (dealer_menu_kb(t), admin_menu_kb()):
        assert any(button.callback_data == "cat:open"
                   for row in kb.inline_keyboard for button in row)
    for lang in ("fa", "en", "ru"):
        for key in ("catalog_new", "catalog_heading", "catalog_invalid",
                    "catalog_status_published", "catalog_unsupported"):
            assert get_text(lang, key) != key or key == "catalog_heading"


def test_private_auth_checks_do_not_grant_every_client_dealer_access(monkeypatch):
    from app.handlers.commercial_catalog import _guard, _number
    monkeypatch.setattr(settings, "admin_ids", "12345")
    assert not _guard(SimpleNamespace(telegram_id=123, role="client", is_blocked=False))
    assert _guard(SimpleNamespace(telegram_id=12345, role="client", is_blocked=True))
    assert _guard(SimpleNamespace(telegram_id=777, role="dealer", is_blocked=False))
    assert not _guard(SimpleNamespace(telegram_id=777, role="dealer", is_blocked=True))
    assert _number("۱۵۰٬۰۰۰") == 150000
