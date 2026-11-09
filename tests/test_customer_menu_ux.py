"""Customer purchase entry points share one flow; promo stays at checkout.

These tests mock DB and Telegram interactions. No actual purchase or panel API.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.models import utcnow
from app.handlers import customer_subscriptions as cabinet, start
from app.keyboards import builders
from app.middlewares.i18n import get_text


@pytest.mark.parametrize("lang", ["fa", "en", "ru"])
def test_main_menu_has_buy_instead_of_promo_and_checkout_keeps_promo(lang):
    t = lambda key, **kw: get_text(lang, key, **kw)
    menu = builders.main_menu_kb(t)
    actions = [button.callback_data for row in menu.inline_keyboard for button in row]
    assert actions == ["menu:buy", "menu:my_vpn", "menu:support", "menu:lang"]
    assert "menu:promo" not in actions
    assert menu.inline_keyboard[0][0].text == get_text(lang, "btn_buy")

    payment = builders.payment_kb(t)
    payment_actions = [b.callback_data for row in payment.inline_keyboard for b in row]
    assert "checkout:promo" in payment_actions


@pytest.mark.asyncio
async def test_main_buy_routes_to_the_same_customer_add_handler(monkeypatch):
    add = AsyncMock()
    monkeypatch.setattr(cabinet, "add", add)
    callback = SimpleNamespace(data="menu:buy")
    t = lambda key, **kw: get_text("ru", key, **kw)
    state = AsyncMock()
    user = SimpleNamespace(id=42)
    await start.buy(callback, t, "ru", user, state)
    add.assert_awaited_once_with(callback, t, "ru", user, state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "has_primary,expected_mode",
    [(False, "primary"), (True, "new")],
)
async def test_buy_first_or_add_another_uses_existing_purchase_flow(
    monkeypatch, has_primary, expected_mode,
):
    primary = SimpleNamespace(id=10) if has_primary else None
    monkeypatch.setattr(
        cabinet.db_repo, "get_subscription",
        AsyncMock(return_value=primary),
    )
    monkeypatch.setattr(
        cabinet.db_repo, "can_claim_test",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        cabinet.customer_repo, "count_additional",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(cabinet, "send_with_logo", AsyncMock())
    monkeypatch.setattr(cabinet.settings, "max_additional_subscriptions", 10)

    callback = SimpleNamespace(answer=AsyncMock())
    user = SimpleNamespace(id=42, is_blocked=False)
    state = AsyncMock()
    t = lambda key, **kw: get_text("ru", key, **kw)
    await cabinet.add(callback, t, "ru", user, state)

    state.update_data.assert_awaited_once()
    data = state.update_data.await_args.kwargs
    assert data["purchase_mode"] == expected_mode
    assert data["subscription_id"] is None
    assert data["checkout_promo_code"] is None
    cabinet.send_with_logo.assert_awaited_once()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["fa", "en", "ru"])
@pytest.mark.parametrize("has_primary", [False, True])
async def test_cabinet_buy_first_or_add_another_label(
    monkeypatch, lang, has_primary,
):
    sub = SimpleNamespace(
        id=12,
        is_primary=True,
        status="active",
        expire_at=utcnow() + timedelta(days=12),
        label="",
    )
    monkeypatch.setattr(
        cabinet.customer_repo, "list_subscriptions",
        AsyncMock(return_value=[sub] if has_primary else []),
    )
    send = AsyncMock()
    monkeypatch.setattr(cabinet, "send_with_logo", send)
    user = SimpleNamespace(id=42)
    t = lambda key, **kw: get_text(lang, key, **kw)

    await cabinet.show_cabinet(SimpleNamespace(), t, user)

    markup = send.await_args.kwargs["reply_markup"]
    buttons = [
        b for row in markup.inline_keyboard for b in row
        if b.callback_data == "cust:add"
    ]
    assert len(buttons) == 1
    expected = "customer_add" if has_primary else "btn_buy"
    assert buttons[0].text == get_text(lang, expected)


@pytest.mark.parametrize(
    "has_primary,trial_available,is_dealer,expected",
    [
        (False, True, False, True),
        (True, True, False, False),
        (False, False, False, False),
        (False, True, True, False),
    ],
)
@pytest.mark.asyncio
async def test_main_trial_only_for_first_time_eligible_customers(
    monkeypatch, has_primary, trial_available, is_dealer, expected,
):
    from app.repositories import db_repo
    from app.utils import menu

    owner = SimpleNamespace(
        id=42, telegram_id=7777, role="dealer" if is_dealer else "user",
        is_blocked=False,
    )
    monkeypatch.setattr(db_repo, "get_user_by_tg", AsyncMock(return_value=owner))
    monkeypatch.setattr(
        db_repo, "get_subscription",
        AsyncMock(return_value=SimpleNamespace(id=10) if has_primary else None),
    )
    monkeypatch.setattr(
        db_repo, "can_claim_test",
        AsyncMock(return_value=trial_available),
    )
    send = AsyncMock()
    monkeypatch.setattr(menu, "send_with_logo", send)
    callback = SimpleNamespace(from_user=SimpleNamespace(id=7777))
    t = lambda key, **kw: get_text("fa", key, **kw)

    await menu.send_main_menu(callback, t)
    buttons = [
        b.callback_data
        for row in send.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert ("menu:trial" in buttons) is expected
    if expected:
        assert buttons == [
            "menu:buy", "menu:my_vpn", "menu:trial", "menu:support", "menu:lang"
        ]
        assert db_repo.can_claim_test.await_count == 1
    else:
        assert buttons == ["menu:buy", "menu:my_vpn", "menu:support", "menu:lang"]


@pytest.mark.asyncio
async def test_main_trial_reuses_existing_trial_activation(monkeypatch):
    from app.handlers import client

    trial = AsyncMock()
    monkeypatch.setattr(client, "activate_trial", trial)
    callback = SimpleNamespace(answer=AsyncMock())
    user = SimpleNamespace(id=42, role="user", is_blocked=False)
    state = AsyncMock()
    t = lambda key, **kw: get_text("ru", key, **kw)

    await start.menu_trial(callback, t, user, state)

    state.clear.assert_awaited_once()
    trial.assert_awaited_once_with(callback, t, user, state)


@pytest.mark.parametrize("status", ["active", "pending", "expired"])
@pytest.mark.parametrize("lang", ["ru", "fa", "en"])
def test_all_subscription_buttons_share_custom_icon_regardless_of_status(
    status, lang,
):
    t = lambda key, **kw: get_text(lang, key, **kw)
    sub = SimpleNamespace(
        id=37, is_primary=False, label="", status=status,
        expire_at=utcnow() + timedelta(days=10),
    )
    markup = cabinet._cabinet_markup(t, [sub], with_custom_icon=True)
    button = markup.inline_keyboard[0][0]
    assert button.icon_custom_emoji_id == "5361587754985628543"
    assert button.callback_data == "cust:card:37"
    assert all(dot not in button.text for dot in ("🔴", "🟡", "🟢"))
    assert button.text == get_text(lang, "customer_unnamed", id=37)
    fallback = cabinet._cabinet_markup(t, [sub], with_custom_icon=False)
    plain = fallback.inline_keyboard[0][0]
    assert plain.icon_custom_emoji_id is None
    assert plain.text.startswith("🔖 ")


@pytest.mark.asyncio
async def test_cabinet_custom_icon_bad_request_falls_back_to_plain(monkeypatch):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText

    sub = SimpleNamespace(
        id=37, is_primary=True, label="",
        status="active", expire_at=utcnow() + timedelta(days=2),
    )
    monkeypatch.setattr(
        cabinet.customer_repo, "list_subscriptions",
        AsyncMock(return_value=[sub]),
    )
    rejection = TelegramBadRequest(
        EditMessageText(chat_id=123, message_id=42, text="test"),
        "Bad Request: BUTTON_ICON_INVALID",
    )
    send = AsyncMock(side_effect=[rejection, None])
    monkeypatch.setattr(cabinet, "send_with_logo", send)
    await cabinet.show_cabinet(
        SimpleNamespace(), lambda key, **kw: get_text("ru", key, **kw),
        SimpleNamespace(id=42),
    )
    assert send.await_count == 2
    assert send.await_args.kwargs["reply_markup"].inline_keyboard[0][0].icon_custom_emoji_id is None


def test_orion_support_contact_default():
    from app.config import Settings

    assert Settings.model_fields["support_username"].default == "@Orion_Ehsan"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["ru", "fa", "en"])
async def test_support_button_uses_ehsan_contact(monkeypatch, lang):
    from app.config import settings

    monkeypatch.setattr(settings, "support_username", "@Orion_Ehsan")
    send = AsyncMock()
    monkeypatch.setattr(start, "send_with_logo", send)
    callback = SimpleNamespace(answer=AsyncMock())
    t = lambda key, **kw: get_text(lang, key, **kw)

    await start.support(callback, t, lang, SimpleNamespace(id=42))

    callback.answer.assert_awaited_once()
    assert "@Orion_Ehsan" in send.await_args.args[1]
    assert "@L_Konstantinn" not in send.await_args.args[1]
