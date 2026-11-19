import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, SendMessage, SendPhoto
from aiogram.types import Message, MessageEntity, Update

from app.config import settings
from app.database.models import Subscription
from app.keyboards.builders import admin_menu_kb, dealer_menu_kb
from app.middlewares.i18n import get_text
from app.repositories import db_repo
from app.utils import notifications


@pytest.mark.asyncio
async def test_audiences_classify_users_once_by_any_active_personal_subscription(database):
    now = datetime(2026, 11, 19, 12)
    users = {id_: await db_repo.get_or_create_user(id_) for id_ in range(9101, 9108)}
    await db_repo.set_user_blocked(9107, True)
    async with database() as session:
        session.add_all([
            Subscription(
                user_id=users[tg_id].id, xui_email=f"acct-{i}", is_primary=primary,
                expire_at=now + timedelta(days=days), status=status,
            )
            for i, (tg_id, primary, days, status) in enumerate([
                (9101, True, -1, "active"),
                (9101, False, 1, "active"),
                (9101, False, 3, "active"),
                (9102, True, 0, "active"),
                (9103, True, 2, "pending"),
                (9104, True, 2, "disabled"),
                (9105, True, 2, "active"),
                (9107, True, 2, "active"),
            ])
        ])
        await session.commit()

    assert await db_repo.list_broadcast_recipients("all", now=now) == list(range(9101, 9107))
    assert await db_repo.list_broadcast_recipients("active", now=now) == [9101, 9105]
    assert await db_repo.list_broadcast_recipients("inactive", now=now) == [9102, 9103, 9104, 9106]
    with pytest.raises(ValueError):
        await db_repo.list_broadcast_recipients("invalid", now=now)


class RecordingTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.blocked = set()
        self.retry_once = set()
        self.photo_gates = {}

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("No downloads are needed")

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        assert isinstance(method, (SendMessage, SendPhoto)), method
        if method.chat_id in self.retry_once:
            self.retry_once.remove(method.chat_id)
            raise TelegramRetryAfter(method, "Slow down", 1)
        if method.chat_id in self.blocked:
            raise TelegramForbiddenError(method, "Bot blocked by user")
        if isinstance(method, SendPhoto) and method.chat_id in self.photo_gates:
            arrived, release = self.photo_gates.pop(method.chat_id)
            arrived.set()
            await release.wait()
        return Message(
            message_id=len(self.calls), date=1,
            chat={"id": method.chat_id, "type": "private"},
        ).as_(bot)

    def text_calls(self, chat):
        return [c for c in self.calls if isinstance(c, SendMessage) and c.chat_id == chat]

    def photo_calls(self):
        return [c for c in self.calls if isinstance(c, SendPhoto)]


def button(markup, prefix):
    return next(
        b.callback_data for row in markup.inline_keyboard for b in row
        if b.callback_data.startswith(prefix)
    )


@pytest.mark.asyncio
async def test_dealer_previews_and_confirms_once_for_the_whole_active_base(database, monkeypatch):
    from app.handlers import broadcast, start
    from app.services import broadcast as service
    from app.database.models import utcnow

    bot_session = RecordingTelegram()
    bot = Bot(settings.bot_token, session=bot_session)
    dp = Dispatcher()
    dp.include_router(start.router)
    dp.include_router(broadcast.router)
    monkeypatch.setattr(settings, "admin_ids", "9200")
    monkeypatch.setattr(notifications, "bot", bot)
    monkeypatch.setattr(service, "_SEND_INTERVAL", 0)
    for tg_id in range(9200, 9208):
        await db_repo.get_or_create_user(tg_id, "seller" if tg_id == 9201 else None)
        await db_repo.set_user_lang(tg_id, "ru")
    await db_repo.set_user_role(9201, "dealer")
    async with database() as session:
        for tg_id in (9203, 9204, 9205, 9206):
            user = await db_repo.get_user_by_tg(tg_id)
            session.add(Subscription(
                user_id=user.id, xui_email=f"acct-{tg_id}",
                expire_at=utcnow() + timedelta(days=1), status="active",
            ))
        await session.commit()
    bot_session.blocked.add(9204)
    update_id = 0

    async def feed(sender, *, callback=None, chat_type="private", **message):
        nonlocal update_id
        update_id += 1
        chat_id = sender if chat_type == "private" else -1000200
        actor = {"id": sender, "first_name": "Test", "is_bot": False}
        msg = {"message_id": update_id, "date": 1, "chat": {"id": chat_id, "type": chat_type}}
        if callback is None:
            body = {"message": {**msg, **message, "from": actor}}
        else:
            body = {"callback_query": {
                "id": str(update_id), "chat_instance": "test", "data": callback, "from": actor,
                "message": {**msg, "text": "button"},
            }}
        await dp.feed_update(bot, Update.model_validate({"update_id": update_id, **body}))

    def last(sender):
        return bot_session.text_calls(sender)[-1]

    t = lambda key, **kwargs: get_text("ru", key, **kwargs)
    assert button(admin_menu_kb(), "bc:open") == "bc:open"
    assert button(dealer_menu_kb(t), "bc:open") == "bc:open"

    await feed(9202, callback="bc:open")
    assert not bot_session.text_calls(9202)
    await feed(9201, callback="bc:open", chat_type="group")
    assert not bot_session.text_calls(9201)

    await feed(9201, callback="bc:open")
    await feed(9201, callback=button(last(9201).reply_markup, "bc:aud:").rsplit(":", 1)[0] + ":active")
    arrived, release = asyncio.Event(), asyncio.Event()
    bot_session.photo_gates[9201] = arrived, release
    preview_task = asyncio.create_task(feed(
        9201, photo=[{"file_id": "photo", "file_unique_id": "unique", "width": 50, "height": 50}],
        caption="Акция <3", caption_entities=[{"offset": 0, "length": 5, "type": "bold"}],
        has_media_spoiler=True, show_caption_above_media=True,
    ))
    await asyncio.wait_for(arrived.wait(), timeout=2)
    try:
        # An unrelated admin /start must not wait for the dealer's photo upload.
        await asyncio.wait_for(feed(9200, text="/start"), timeout=1)
    finally:
        release.set()
        await preview_task
    preview = bot_session.photo_calls()
    assert len(preview) == 1 and preview[0].chat_id == 9201
    assert (preview[0].photo, preview[0].caption, preview[0].parse_mode) == ("photo", "Акция <3", None)
    assert preview[0].caption_entities[0].type == "bold"
    assert preview[0].has_spoiler is True and preview[0].show_caption_above_media is True
    confirm = button(last(9201).reply_markup, "bc:send:")
    assert "4" in last(9201).text
    await feed(9202, callback=confirm)
    assert len(bot_session.photo_calls()) == 1
    await db_repo.set_user_blocked(9206, True)
    late_user = await db_repo.get_user_by_tg(9207)
    async with database() as session:
        session.add(Subscription(
            user_id=late_user.id, xui_email="acct-9207",
            expire_at=utcnow() + timedelta(days=1), status="active",
        ))
        await session.commit()
    await asyncio.gather(feed(9201, callback=confirm), feed(9201, callback=confirm))
    sent = bot_session.photo_calls()
    assert [c.chat_id for c in sent] == [9201, 9203, 9204, 9205]
    assert all(c.caption == "Акция <3" and c.caption_entities == preview[0].caption_entities for c in sent)
    assert last(9201).text == t("broadcast_result", sent=2, failed=1, skipped=1)
    assert all(c.has_spoiler is True and c.show_caption_above_media is True for c in sent)
    audit = [call for call in bot_session.text_calls(9200) if "Действие дилера" in call.text]
    assert len(audit) == 1 and "9201" in audit[0].text and last(9201).text in audit[0].text

    await feed(9200, callback="bc:open")
    await feed(9200, callback=button(last(9200).reply_markup, "bc:aud:").rsplit(":", 1)[0] + ":inactive")
    await feed(9200, text="Текст <без HTML>", link_preview_options={"is_disabled": True})
    result = bot_session.text_calls(9200)
    assert result[-2].text == "Текст <без HTML>" and result[-2].parse_mode is None
    assert result[-2].link_preview_options.is_disabled is True
    cancel = button(result[-1].reply_markup, "bc:cancel:")
    stale = button(result[-1].reply_markup, "bc:send:")
    await feed(9200, callback=cancel)
    all_calls = len(bot_session.text_calls(9200))
    await feed(9200, callback=stale)
    assert len(bot_session.text_calls(9200)) == all_calls


@pytest.mark.asyncio
async def test_retry_after_pauses_and_retries_only_the_failed_recipient(monkeypatch):
    from app.services import broadcast

    session = RecordingTelegram()
    session.retry_once.add(9301)
    bot = Bot(settings.bot_token, session=session)
    sleep = AsyncMock()
    monkeypatch.setattr(broadcast, "sleep", sleep)
    monkeypatch.setattr(broadcast, "_SEND_INTERVAL", 0)

    payload = broadcast.BroadcastContent(text="Hello", entities=[MessageEntity(type="bold", offset=0, length=5)])
    sent, failed = await broadcast.deliver(bot, [9301, 9302], payload)
    assert (sent, failed) == (2, 0)
    assert [c.chat_id for c in session.calls] == [9301, 9301, 9302]
    sleep.assert_awaited_once_with(1)
