"""Isolated aiogram FSM smoke test without registering application routers twice."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.handlers import commercial_catalog as ui
from app.middlewares.i18n import get_text
from app.repositories import commercial_catalog_repo as catalog
from app.repositories import db_repo


@pytest.mark.asyncio
async def test_dealer_can_create_persian_draft_with_preview_without_invoice(database, monkeypatch):
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    seller = await db_repo.get_or_create_user(55001)
    await db_repo.set_user_role(seller.telegram_id, "dealer")
    seller = await db_repo.get_user_by_tg(seller.telegram_id)
    outsider = await db_repo.get_or_create_user(55002)
    send_screen = AsyncMock()
    monkeypatch.setattr(ui, "send_with_logo", send_screen)

    t = lambda key, **kwargs: get_text("fa", key, **kwargs)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(
        bot_id=123456, chat_id=55001, user_id=55001,
    ))
    callback = SimpleNamespace(
        data="cat:open", answer=AsyncMock(), message=SimpleNamespace(),
    )
    await ui.catalog_home(callback, state, t, seller)
    assert "مرکز فروش" in send_screen.await_args.args[1]

    callback.data = "cat:new"
    await ui.catalog_new(callback, state, t, seller)
    text_message = SimpleNamespace(text=None, answer=AsyncMock())
    steps = [
        (ui.new_name, "اقتصادی"),
        (ui.new_days, "30"),
        (ui.new_devices, "1"),
        (ui.new_traffic, "100"),
        (ui.new_price, "۱۷۰٬۰۰۰"),
    ]
    for handler, raw_value in steps:
        text_message.text = raw_value
        await handler(text_message, state, t, seller)
    assert "170٬000" in text_message.answer.await_args.args[0]
    data = await state.get_data()
    callback.data = "cat:save:" + data["catalog_nonce"]
    await ui.catalog_save(callback, state, t, seller)
    await ui.catalog_save(callback, state, t, seller)

    rows, total = await catalog.list_tariffs(seller.telegram_id)
    assert total == 1 and len(rows) == 1
    assert rows[0].name_fa == "اقتصادی"
    assert rows[0].retail_toman == 170000 and rows[0].status == "draft"

    unauthorized = SimpleNamespace(data="cat:open", answer=AsyncMock(), message=SimpleNamespace())
    previous_calls = send_screen.await_count
    await ui.catalog_home(unauthorized, state, t, outsider)
    unauthorized.answer.assert_awaited_once()
    assert send_screen.await_count == previous_calls
