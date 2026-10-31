from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.handlers import admin, client
from app.middlewares.i18n import get_text
from app.repositories import db_repo
from app.utils.notifications import notify_admins


@pytest.mark.asyncio
@pytest.mark.parametrize("username", [None, "ali_vpn"])
async def test_admin_receives_receipt_with_original_quote_and_customer(database, monkeypatch, username):
    monkeypatch.setattr(settings, "admin_ids", "99001,99002")
    # The bank quote predates this exchange-rate change.
    monkeypatch.setattr(settings, "toman_per_usd", 300000)
    user = await db_repo.get_or_create_user(70001, username)
    dealer = await db_repo.get_or_create_user(70002)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    order = await db_repo.create_order(user.id, "month2d3", 1.615, "usd")
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={"order_id": order.id, "retail_toman": 484500}),
        clear=AsyncMock(),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(full_name="Ali <VPN> & Co"),
        photo=[SimpleNamespace(file_id="small-receipt"), SimpleNamespace(file_id="full-receipt")],
        answer=AsyncMock(),
    )
    photos, texts = AsyncMock(), AsyncMock()
    monkeypatch.setattr(client.bot, "send_photo", photos)
    monkeypatch.setattr(client.bot, "send_message", texts)

    await client.receive_receipt(message, lambda key, **kw: get_text("fa", key, **kw), "fa", user, state)

    admin_photos = [c for c in photos.call_args_list if c.args[0] in (99001, 99002)]
    assert [c.args[0] for c in admin_photos] == [99001, 99002]
    for call in admin_photos:
        assert call.args[1] == "full-receipt"
        caption = call.kwargs["caption"]
        assert f"#{order.id}" in caption
        assert "Ali &lt;VPN&gt; &amp; Co" in caption
        assert "<code>70001</code>" in caption
        assert "@ali_vpn" in caption if username else "не указан" in caption
        assert "3 устр." in caption and "100 ГБ/мес" in caption and "60 дней" in caption
        assert "484,500 туманов" in caption and "969,000" not in caption
        assert "$1.615" in caption
        assert "ожидает проверки дилером" in caption.lower()
        assert len(caption) <= 1024
        buttons = [b for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
        assert [b.callback_data for b in buttons] == ["admin_user:70001"]
    dealer_call = next(c for c in photos.call_args_list if c.args[0] == dealer.telegram_id)
    assert "۴۸۴٬۵۰۰ تومان" in dealer_call.kwargs["caption"]
    assert "3 устр." not in dealer_call.kwargs["caption"]
    assert any(b.callback_data == f"dealer_ok:{order.id}"
               for row in dealer_call.kwargs["reply_markup"].inline_keyboard for b in row)
    texts.assert_not_awaited()
    saved = await db_repo.get_order(order.id)
    assert saved.receipt_photo_id == "full-receipt"
    assert saved.amount == 1.615 and saved.status == "pending_dealer"


@pytest.mark.asyncio
@pytest.mark.parametrize("plan,amount,quote,product", [
    ("month1", 0.5, None, "1 устр. · 100 ГБ/мес · 30 дней"),
    ("traffic50", 0.25, 75000, "+50 ГБ"),
])
async def test_admin_receipt_handles_unknown_quote_and_traffic_purchase(database, monkeypatch, plan, amount, quote, product):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    user = await db_repo.get_or_create_user(70003)
    order = await db_repo.create_order(user.id, plan, amount, "usd")
    data = {"order_id": order.id, "retail_toman": quote}
    state = SimpleNamespace(get_data=AsyncMock(return_value=data), clear=AsyncMock())
    photos = AsyncMock()
    monkeypatch.setattr(client.bot, "send_photo", photos)
    monkeypatch.setattr(client.bot, "send_message", AsyncMock())

    await client.receive_receipt(
        SimpleNamespace(photo=[SimpleNamespace(file_id="receipt")], answer=AsyncMock()),
        lambda key, **kw: get_text("en", key, **kw), "en", user, state,
    )

    caption = photos.call_args.kwargs["caption"]
    assert product in caption
    if quote is None:
        assert "По исходным реквизитам заказа" in caption
        assert "150,000" not in caption
    else:
        assert "75,000 туманов" in caption and "$0.25" in caption


@pytest.mark.asyncio
async def test_photo_failure_keeps_summary_and_continues_other_admins(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "99001,99002")

    async def send_photo(admin_id, photo, **kwargs):
        if admin_id == 99001:
            raise RuntimeError("Photo delivery failed")

    photos, texts = AsyncMock(side_effect=send_photo), AsyncMock()
    monkeypatch.setattr(client.bot, "send_photo", photos)
    monkeypatch.setattr(client.bot, "send_message", texts)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Пользователь", callback_data="admin_user:70001"),
    ]])

    await notify_admins("Order #129: 60 days, 484500 Toman", photo="receipt", reply_markup=keyboard)

    assert [c.args[0] for c in photos.call_args_list] == [99001, 99002]
    texts.assert_awaited_once_with(99001, "Order #129: 60 days, 484500 Toman", reply_markup=keyboard)


@pytest.mark.asyncio
async def test_text_only_admin_notifications_still_reach_other_admins(monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "99001,99002")

    async def send_text(admin_id, text, **kwargs):
        if admin_id == 99001:
            raise RuntimeError("Admin blocked bot")

    photos, texts = AsyncMock(), AsyncMock(side_effect=send_text)
    monkeypatch.setattr(client.bot, "send_photo", photos)
    monkeypatch.setattr(client.bot, "send_message", texts)
    await notify_admins("Existing notification")
    photos.assert_not_awaited()
    assert [c.args[:2] for c in texts.call_args_list] == [
        (99001, "Existing notification"), (99002, "Existing notification"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer_id,allowed", [(99001, True), (70005, False)])
async def test_receipt_user_card_is_only_accessible_to_an_admin(database, monkeypatch, viewer_id, allowed):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(70004, "receipt_buyer")
    viewer = await db_repo.get_or_create_user(viewer_id)
    callback = SimpleNamespace(
        data="admin_user:70004", from_user=SimpleNamespace(id=viewer_id),
        answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()),
    )
    await admin.open_user(callback, lambda key, **kw: key, "ru", viewer)
    if allowed:
        text = callback.message.answer.call_args.args[0]
        assert "@receipt_buyer" in text and str(buyer.telegram_id) in text
    else:
        callback.message.answer.assert_not_awaited()
        assert callback.answer.call_args.kwargs["show_alert"] is True
