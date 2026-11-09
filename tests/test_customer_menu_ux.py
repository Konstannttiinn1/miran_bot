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
    assert actions == ["menu:my_vpn", "menu:buy", "menu:support", "menu:lang"]
    assert "menu:promo" not in actions
    assert menu.inline_keyboard[1][0].text == get_text(lang, "btn_buy")

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
