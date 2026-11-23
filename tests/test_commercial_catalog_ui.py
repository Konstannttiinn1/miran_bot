"""End-to-end aiogram FSM smoke test with an in-memory Telegram transport."""
import pytest

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageCaption, EditMessageText, SendMessage, SendPhoto
from aiogram.types import Message, Update

from app.config import settings
from app.handlers.commercial_catalog import router as catalog_router
from app.handlers.start import router as start_router
from app.repositories import commercial_catalog_repo as catalog
from app.repositories import db_repo


class FakeTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        return None

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("No download expected")

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, (EditMessageText, EditMessageCaption)):
            return True
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(
                message_id=len(self.calls), date=1,
                chat={"id": method.chat_id, "type": "private"},
            ).as_(bot)
        raise AssertionError(f"Unexpected Telegram request: {type(method).__name__}")


@pytest.mark.asyncio
async def test_dealer_can_create_persian_draft_with_preview_without_sending_invoice(database, monkeypatch):
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "test-squad")
    seller = await db_repo.get_or_create_user(55001)
    await db_repo.set_user_role(seller.telegram_id, "dealer")
    await db_repo.set_user_lang(seller.telegram_id, "fa")
    await db_repo.get_or_create_user(55002)

    transport = FakeTelegram()
    bot = Bot(settings.bot_token, session=transport)
    dp = Dispatcher()
    dp.include_router(start_router)
    dp.include_router(catalog_router)
    next_id = 0

    async def feed(user_id, *, callback=None, message=None):
        nonlocal next_id
        next_id += 1
        actor = {"id": user_id, "is_bot": False, "first_name": "Test"}
        envelope = {
            "message_id": next_id, "date": 1,
            "chat": {"id": user_id, "type": "private"},
        }
        if callback is not None:
            update = {"callback_query": {
                "id": str(next_id), "chat_instance": "test",
                "data": callback, "from": actor,
                "message": {**envelope, "text": "menu"},
            }}
        else:
            update = {"message": {**envelope, "from": actor, "text": message}}
        await dp.feed_update(bot, Update.model_validate({"update_id": next_id, **update}))

    def last_screen():
        return next(
            call for call in reversed(transport.calls)
            if isinstance(call, (SendMessage, EditMessageText))
        )

    await feed(55002, callback="cat:open")
    assert not any(isinstance(x, (EditMessageText, SendMessage)) for x in transport.calls)

    await feed(55001, callback="cat:open")
    markup = last_screen().reply_markup
    assert any(b.callback_data == "cat:new" for row in markup.inline_keyboard for b in row)
    await feed(55001, callback="cat:new")
    for value in ["اقتصادی", "30", "1", "100", "170000"]:
        await feed(55001, message=value)
    screen = last_screen()
    assert "170٬000" in screen.text
    send_button = next(
        b.callback_data for row in screen.reply_markup.inline_keyboard for b in row
        if b.callback_data.startswith("cat:save:")
    )
    await feed(55001, callback=send_button)
    await feed(55001, callback=send_button)
    items, total = await catalog.list_tariffs(seller.telegram_id)
    assert total == 1
    assert len(items) == 1 and items[0].name_fa == "اقتصادی"
    assert items[0].retail_toman == 170000 and items[0].status == "draft"
    assert not any(type(call).__name__ == "SendInvoice" for call in transport.calls)
