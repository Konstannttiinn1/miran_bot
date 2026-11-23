"""Phase 2 order snapshots and dynamic offer selection; uses isolated temporary DB."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.database.models import CommercialTariff, PromoCode, User, utcnow
from app.repositories import commercial_catalog_repo as catalogue, db_repo
from app.services.catalog_pricing import plan_code, retail_amount
from app.utils.promo_pricing import discounted_amount, discounted_toman


async def _published(dealer_tg: int, **kwargs):
    created = await catalogue.create_tariff(
        dealer_tg, name_fa=kwargs.pop("name_fa", "اقتصادی"),
        period_days=kwargs.pop("period_days", 30),
        devices=kwargs.pop("devices", 2),
        traffic_gb=kwargs.pop("traffic_gb", 100),
        retail_toman=kwargs.pop("retail_toman", 150000),
    )
    return await catalogue.update_tariff(
        dealer_tg, created.id, created.revision,
        action="publish", changes={},
    )


@pytest.mark.asyncio
async def test_paid_catalog_order_freezes_revision_terms_and_price(database, monkeypatch):
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", True)
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    monkeypatch.setattr(settings, "toman_per_usd", 150000.0)
    monkeypatch.setattr(settings, "dealer_discount", 0.5)
    monkeypatch.setattr(settings, "stars_reward_usd", 0.013)
    seller = await db_repo.get_or_create_user(81001)
    await db_repo.set_user_role(seller.telegram_id, "dealer")
    buyer = await db_repo.get_or_create_user(81002)
    item = await _published(seller.telegram_id)
    assert plan_code(item.period_days, item.devices, item.traffic_gb) == "month1d2"
    assert [x.id for x in await catalogue.list_published()] == [item.id]
    assert (await catalogue.get_published(item.id)).id == item.id

    for currency in ("xtr", "usd", "usdt"):
        order = await db_repo.create_order(
            buyer.id, "month1d2", -999, currency,
            catalog_tariff_id=item.id, retail_toman=9,
            apply_promo=False,
        )
        expected = retail_amount(
            150000, currency, rate=150000.0,
            stars_reward=0.013, dealer_discount=0.5,
        )
        assert order.amount == expected
        assert order.catalog_tariff_id == item.id
        assert order.catalog_snapshot["revision"] == item.revision
        assert order.catalog_snapshot["base_retail_toman"] == 150000
        assert order.catalog_snapshot["period_days"] == 30
        assert order.catalog_snapshot["devices"] == 2
        assert order.catalog_snapshot["traffic_gb"] == 100
        assert await db_repo.get_order_retail_toman(order.id) == 150000

    change = await catalogue.update_tariff(
        seller.telegram_id, item.id, item.revision,
        action="edit", changes={"retail_toman": 225000},
    )
    assert change.revision == item.revision + 1
    archived = await catalogue.update_tariff(
        seller.telegram_id, item.id, change.revision,
        action="archive", changes={},
    )
    assert archived.status == "archived"
    assert (await catalogue.list_published()) == []
    for currency, expected in (("xtr", 77), ("usd", 0.5), ("usdt", 1.0)):
        async with database() as session:
            from app.database.models import Order
            rows = list((await session.scalars(
                select(Order).where(Order.currency == currency)
            )).all())
            assert len(rows) == 1
            assert rows[0].amount == expected
            assert rows[0].catalog_snapshot["base_retail_toman"] == 150000

    with pytest.raises(ValueError):
        await db_repo.create_order(
            buyer.id, "month1d2", 999, "xtr", catalog_tariff_id=item.id,
        )


@pytest.mark.asyncio
async def test_catalog_discount_retains_discounted_toman_and_usd_snapshot(database, monkeypatch):
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", True)
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    monkeypatch.setattr(settings, "toman_per_usd", 150000.0)
    monkeypatch.setattr(settings, "dealer_discount", 0.5)
    seller = await db_repo.get_or_create_user(81101)
    await db_repo.set_user_role(seller.telegram_id, "dealer")
    buyer = await db_repo.get_or_create_user(81102)
    item = await _published(seller.telegram_id, retail_toman=234567)
    async with database() as session:
        session.add(PromoCode(
            dealer_id=seller.id, code="PROMO25",
            kind="discount", days=0, discount_percent=25, traffic_gb=0,
            expire_at=utcnow() + timedelta(days=7), max_uses=10,
            uses_count=0, is_active=True,
        ))
        await session.commit()

    original = retail_amount(234567, "usdt", rate=150000.0,
                             stars_reward=settings.stars_reward_usd, dealer_discount=0.5)
    order = await db_repo.create_order(
        buyer.id, "month1d2", 0, "usdt", catalog_tariff_id=item.id,
        apply_promo=True, promo_code="PROMO25",
    )
    assert order.amount == discounted_amount(original, 25, "usdt")
    assert await db_repo.get_order_retail_toman(order.id) == discounted_toman(234567, 25)
    assert order.catalog_snapshot["base_retail_toman"] == 234567
    assert order.catalog_snapshot["revision"] == item.revision
    with pytest.raises(ValueError):
        await db_repo.create_order(
            buyer.id, "month1d2", 0, "usdt", catalog_tariff_id=item.id,
            apply_promo=True, promo_code="PROMO25",
        )


@pytest.mark.asyncio
async def test_catalog_flag_and_unsupported_sku_do_not_create_orders(database, monkeypatch):
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", False)
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    dealer = await db_repo.get_or_create_user(81201)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    buyer = await db_repo.get_or_create_user(81202)
    item = await _published(dealer.telegram_id)
    assert await catalogue.list_published() == []
    assert await catalogue.get_published(item.id) is None
    with pytest.raises(ValueError):
        await db_repo.create_order(
            buyer.id, "month1d2", 100, "xtr", catalog_tariff_id=item.id,
        )
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", True)
    with pytest.raises(ValueError):
        await db_repo.create_order(
            buyer.id, "month2d2", 100, "xtr", catalog_tariff_id=item.id,
        )
    with pytest.raises(ValueError):
        await db_repo.create_order(
            buyer.id, "month1d2", 100, "ton", catalog_tariff_id=item.id,
        )
    async with database() as session:
        from app.database.models import Order
        assert list((await session.scalars(select(Order))).all()) == []


@pytest.mark.asyncio
async def test_checkout_rejects_changed_price_since_customer_preview(database, monkeypatch):
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", True)
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    dealer = await db_repo.get_or_create_user(81301)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    buyer = await db_repo.get_or_create_user(81302)
    item = await _published(dealer.telegram_id, retail_toman=190000)
    shown_revision = item.revision
    item = await catalogue.update_tariff(
        dealer.telegram_id, item.id, item.revision,
        action="edit", changes={"retail_toman": 240000},
    )
    with pytest.raises(ValueError, match="price changed"):
        await db_repo.create_order(
            buyer.id, "month1d2", 10, "xtr",
            catalog_tariff_id=item.id, catalog_expected_revision=shown_revision,
        )
    async with database() as session:
        from app.database.models import Order
        assert list((await session.scalars(select(Order))).all()) == []
    fresh = await db_repo.create_order(
        buyer.id, "month1d2", 10, "xtr",
        catalog_tariff_id=item.id, catalog_expected_revision=item.revision,
    )
    assert fresh.catalog_snapshot["revision"] == item.revision
    assert fresh.catalog_snapshot["base_retail_toman"] == 240000
