"""Dealer discounts and admin audit notices are independent of Telegram menus."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.database.models import utcnow
from app.handlers import dealer_tools
from app.middlewares.i18n import get_text
from app.repositories import db_repo, dealer_tools_repo
from app.utils import notifications


@pytest.mark.asyncio
async def test_dealer_can_create_discount_coupon_and_admin_is_notified(database, monkeypatch):
    dealer = await db_repo.get_or_create_user(97501, username="seller")
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    dealer = await db_repo.get_user_by_id(dealer.id)

    callback = SimpleNamespace(answer=AsyncMock())
    state = SimpleNamespace(
        get_data=AsyncMock(return_value={
            "promo_code": "SELLER30", "promo_kind": "discount",
            "promo_days": 0, "promo_traffic": 0, "promo_discount": 30,
            "promo_validity": 5, "promo_uses": 15,
        }),
        clear=AsyncMock(),
    )
    send_screen = AsyncMock()
    admin_notice = AsyncMock()
    monkeypatch.setattr(dealer_tools, "send_with_logo", send_screen)
    monkeypatch.setattr(notifications, "notify_admins", admin_notice)
    t = lambda key, **kw: get_text("ru", key, **kw)

    await dealer_tools.promo_confirm(callback, t, dealer, state)
    promos, total = await dealer_tools_repo.list_dealer_promos(dealer.id)
    assert total == 1
    assert promos[0].code == "SELLER30"
    assert promos[0].kind == "discount"
    assert promos[0].discount_percent == 30
    assert admin_notice.await_count == 1
    notice = admin_notice.call_args.args[0]
    assert "seller" in notice and "SELLER30" in notice and "30%" in notice
    send_screen.assert_awaited_once()


@pytest.mark.asyncio
async def test_dealer_can_choose_discount_plus_days(monkeypatch):
    dealer = SimpleNamespace(id=1, telegram_id=97502, role="dealer")
    callback = SimpleNamespace(
        data="dealer:promo:kind:days_discount", answer=AsyncMock(),
    )
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    send_screen = AsyncMock()
    monkeypatch.setattr(dealer_tools, "send_with_logo", send_screen)
    t = lambda key, **kw: get_text("ru", key, **kw)

    await dealer_tools.promo_kind(callback, t, dealer, state)
    state.set_state.assert_awaited_once_with(dealer_tools.DealerPromoForm.discount)
    send_screen.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_only_reports_dealer_actions_and_escapes_username(monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(notifications, "notify_admins", sent)
    dealer = SimpleNamespace(role="dealer", username="name<tag>", telegram_id=97503)
    admin = SimpleNamespace(role="admin", username="owner", telegram_id=97504)

    await notifications.notify_dealer_activity(dealer, "🎟️ Создан промокод", "Скидка 30%")
    assert sent.await_count == 1
    text = sent.call_args.args[0]
    assert "name&lt;tag&gt;" in text
    assert "Скидка 30%" in text

    await notifications.notify_dealer_activity(admin, "🎟️ Создан промокод")
    sent.assert_awaited_once()
