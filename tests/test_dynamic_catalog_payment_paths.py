"""Customer offer -> invoice handoff without network or real payment."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from app.config import settings
from app.database.models import Order
from app.handlers import commercial_checkout as shop
from app.handlers import client
from app.handlers.states import Purchase
from app.middlewares.i18n import get_text
from app.repositories import commercial_catalog_repo as catalogue, db_repo
from app.services.catalog_pricing import plan_code, retail_amount


@pytest.mark.asyncio
@pytest.mark.parametrize("method,currency", [
    ("stars", "xtr"), ("dealer", "usd"), ("heleket", "usdt"),
])
async def test_dynamic_storefront_creates_immutable_order_in_every_payment_path(
    database, monkeypatch, method, currency,
):
    monkeypatch.setattr(settings, "dynamic_catalog_enabled", True)
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    monkeypatch.setattr(settings, "toman_per_usd", 150000.0)
    monkeypatch.setattr(settings, "dealer_discount", 0.5)
    monkeypatch.setattr(settings, "stars_reward_usd", 0.013)
    monkeypatch.setattr(settings, "heleket_enabled", True)
    monkeypatch.setattr(client, "provider_name", lambda: "remnawave")
    monkeypatch.setattr(client.heleket, "is_configured", lambda: True)
    monkeypatch.setattr(client.heleket, "create_invoice", AsyncMock(return_value={
        "uuid": "test-only-payment", "url": "https://example.test/invoice",
    }))
    screen = AsyncMock()
    monkeypatch.setattr(shop, "send_with_logo", screen)
    monkeypatch.setattr(client, "send_with_logo", screen)

    dealer = await db_repo.get_or_create_user(83001)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    buyer = await db_repo.get_or_create_user(83002)
    offer = await catalogue.create_tariff(
        dealer.telegram_id, name_fa="اشتراک اقتصادی", period_days=60,
        devices=3, traffic_gb=100, retail_toman=243000,
    )
    offer = await catalogue.update_tariff(
        dealer.telegram_id, offer.id, offer.revision, action="publish", changes={},
    )
    t = lambda key, **kw: get_text("fa", key, **kw)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(
        bot_id=123456, chat_id=buyer.telegram_id, user_id=buyer.telegram_id,
    ))
    await state.set_state(Purchase.choosing_plan)
    await state.set_data({
        "purchase_mode": "primary", "subscription_id": None,
        "plan": None, "order_id": None, "checkout_promo_code": None,
    })
    callback = SimpleNamespace(
        data=f"cshop:choose:{offer.id}", answer=AsyncMock(),
        message=SimpleNamespace(answer_invoice=AsyncMock()),
    )
    await shop.choose_offer(callback, state, t, "fa", buyer)
    data = await state.get_data()
    assert data["catalog_tariff_id"] == offer.id
    assert data["plan"] == plan_code(60, 3, 100)
    assert await state.get_state() == Purchase.choosing_payment.state

    callback.data = f"pay:{method}"
    await client.choose_payment(callback, t, "fa", buyer, state)
    async with database() as session:
        orders = list((await session.scalars(select(Order))).all())
    assert len(orders) == 1
    order = orders[0]
    assert order.plan == "month2d3" and order.currency == currency
    assert order.catalog_tariff_id == offer.id
    assert order.catalog_snapshot["base_retail_toman"] == 243000
    assert order.catalog_snapshot["period_days"] == 60
    assert order.catalog_snapshot["devices"] == 3
    expected = retail_amount(
        243000, currency, rate=150000.0,
        stars_reward=0.013, dealer_discount=0.5,
    )
    assert order.amount == expected
    assert await db_repo.get_order_retail_toman(order.id) == 243000
    if method == "stars":
        invoice = callback.message.answer_invoice
        invoice.assert_awaited_once()
        assert invoice.await_args.kwargs["prices"][0].amount == expected
        assert order.status == "pending"
    elif method == "dealer":
        assert await state.get_state() == Purchase.waiting_receipt.state
        assert order.status == "pending"
    else:
        client.heleket.create_invoice.assert_awaited_once_with(expected, order.id)
        assert (await db_repo.get_order(order.id)).external_id == "test-only-payment"

    updated = await catalogue.update_tariff(
        dealer.telegram_id, offer.id, offer.revision,
        action="edit", changes={"retail_toman": 400000},
    )
    saved = await db_repo.get_order(order.id)
    assert saved.amount == expected
    assert saved.catalog_snapshot["revision"] != updated.revision
    assert saved.catalog_snapshot["base_retail_toman"] == 243000
