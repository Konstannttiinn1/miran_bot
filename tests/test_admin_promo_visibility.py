"""Admin sees every promo; dealers may manage only codes they created."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.models import utcnow
from app.handlers import dealer_tools
from app.middlewares.i18n import get_text
from app.repositories import db_repo, dealer_tools_repo


@pytest.mark.asyncio
async def test_admin_lists_all_dealer_promos_and_can_deactivate(database):
    admin = await db_repo.get_or_create_user(97801, username="owner")
    dealer1 = await db_repo.get_or_create_user(97802, username="first_seller")
    dealer2 = await db_repo.get_or_create_user(97803, username="second_seller")
    first = await dealer_tools_repo.create_promo(
        dealer1.id, "DEALER_ONE", "discount", 0, 0,
        utcnow() + timedelta(days=7), 10, discount_percent=30,
    )
    second = await dealer_tools_repo.create_promo(
        dealer2.id, "DEALER_TWO", "days", 5, 0,
        utcnow() + timedelta(days=7), 10,
    )
    own = await dealer_tools_repo.create_promo(
        admin.id, "ADMIN_ONLY", "discount", 0, 0,
        utcnow() + timedelta(days=7), 10, discount_percent=20,
    )
    all_rows, total = await dealer_tools_repo.list_all_promos()
    assert total == 3
    assert {p.code for p, _ in all_rows} == {"DEALER_ONE", "DEALER_TWO", "ADMIN_ONLY"}
    owners = {p.code: owner.username for p, owner in all_rows}
    assert owners == {
        "DEALER_ONE": "first_seller",
        "DEALER_TWO": "second_seller",
        "ADMIN_ONLY": "owner",
    }
    own_rows, count = await dealer_tools_repo.list_dealer_promos(dealer1.id)
    assert count == 1 and own_rows[0].id == first.id
    assert await dealer_tools_repo.get_dealer_promo(dealer1.id, second.id) is None
    assert await dealer_tools_repo.get_dealer_promo(dealer1.id, own.id) is None
    assert not await dealer_tools_repo.deactivate_promo(dealer1.id, second.id)
    assert await dealer_tools_repo.deactivate_any_promo(second.id)
    assert not (await dealer_tools_repo.get_any_promo(second.id)).is_active
    assert await dealer_tools_repo.deactivate_promo(dealer1.id, first.id)
    assert (await dealer_tools_repo.get_any_promo(own.id)).is_active


@pytest.mark.asyncio
async def test_admin_menu_shows_owners_and_disables_dealer_coupon(database, monkeypatch):
    admin = await db_repo.get_or_create_user(97811)
    dealer = await db_repo.get_or_create_user(97812, username="promoseller")
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    dealer = await db_repo.get_user_by_id(dealer.id)
    outsider = await db_repo.get_or_create_user(97813, username="other_seller")
    await db_repo.set_user_role(outsider.telegram_id, "dealer")
    outsider = await db_repo.get_user_by_id(outsider.id)
    promo = await dealer_tools_repo.create_promo(
        dealer.id, "CONTROL30", "discount", 0, 0,
        utcnow() + timedelta(days=7), 4, discount_percent=30,
    )
    monkeypatch.setattr(dealer_tools.settings, "admin_ids", str(admin.telegram_id))
    send_screen = AsyncMock()
    monkeypatch.setattr(dealer_tools, "send_with_logo", send_screen)
    t = lambda key, **kw: get_text("ru", key, **kw)
    state = SimpleNamespace(clear=AsyncMock())
    callback = SimpleNamespace(data="admin:promos", answer=AsyncMock())

    await dealer_tools.admin_promos(callback, t, admin, state)
    admin_menu = send_screen.call_args.kwargs["reply_markup"]
    button_text = " ".join(btn.text for row in admin_menu.inline_keyboard for btn in row)
    assert "CONTROL30" in button_text
    assert "@promoseller" in button_text

    send_screen.reset_mock()
    callback.data = f"dealer:promocard:{promo.id}:0"
    await dealer_tools.promo_card(callback, t, admin)
    assert "CONTROL30" in send_screen.call_args.args[1]
    assert "@promoseller" in send_screen.call_args.args[1]

    send_screen.reset_mock()
    await dealer_tools.promo_card(callback, t, dealer)
    assert "CONTROL30" in send_screen.call_args.args[1]

    send_screen.reset_mock()
    await dealer_tools.promo_card(callback, t, outsider)
    send_screen.assert_not_awaited()
    assert callback.answer.call_args.kwargs["show_alert"] is True

    callback.data = f"dealer:promo:disable:{promo.id}:0"
    await dealer_tools.promo_disable(callback, t, outsider)
    assert (await dealer_tools_repo.get_any_promo(promo.id)).is_active

    callback.data = f"dealer:promo:disable:{promo.id}:0"
    await dealer_tools.promo_disable(callback, t, admin)
    assert not (await dealer_tools_repo.get_any_promo(promo.id)).is_active
