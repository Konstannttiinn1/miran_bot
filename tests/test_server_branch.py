from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database import engine as engine_module
from app.database.models import utcnow
from app.handlers import dealer_tools
from app.repositories import db_repo, dealer_tools_repo
from app.services import subscription as subscription_service
from app.services.remnawave_api import RemnawaveClient
from app.services.xui_api import XuiClient


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["xui", "remnawave"])
async def test_one_time_trial_uses_the_configured_provider(database, monkeypatch, provider_name):
    monkeypatch.setattr(settings, "vpn_provider", provider_name)
    monkeypatch.setattr(settings, "remnawave_api_url", "https://panel.example.test")
    monkeypatch.setattr(settings, "remnawave_api_token", "local-test-only")
    monkeypatch.setattr(settings, "remnawave_subscription_url", "https://sub.example.test")
    monkeypatch.setattr(settings, "remnawave_squad_uuid", "local-test-squad")
    xui, remnawave = AsyncMock(), AsyncMock()
    monkeypatch.setattr(XuiClient, "add_client", xui)
    monkeypatch.setattr(RemnawaveClient, "add_client", remnawave)
    user = await db_repo.get_or_create_user(30001)
    await subscription_service.grant_vpn(user.id, user.telegram_id, "test")
    with pytest.raises(subscription_service.TrialUnavailableError):
        await subscription_service.grant_vpn(user.id, user.telegram_id, "test")
    assert (xui.await_count, remnawave.await_count) == (
        (1, 0) if provider_name == "xui" else (0, 1)
    )


@pytest.mark.asyncio
async def test_promo_works_after_trial_without_resetting_trial(database, monkeypatch):
    user = await db_repo.get_or_create_user(30002)
    dealer = await db_repo.get_or_create_user(30003)
    provider = SimpleNamespace(add_client=AsyncMock(), extend_client=AsyncMock(), add_traffic=AsyncMock())
    monkeypatch.setattr(subscription_service, "get_vpn_provider", lambda: provider)
    monkeypatch.setattr(dealer_tools, "get_vpn_provider", lambda: provider)
    notice = AsyncMock()
    monkeypatch.setattr(dealer_tools.bot, "send_message", notice)
    await subscription_service.grant_vpn(user.id, user.telegram_id, "test")
    promo = await dealer_tools_repo.create_promo(
        dealer.id, "TRIAL-BONUS", "days_traffic", 2, 3,
        utcnow() + timedelta(days=10), 5,
    )
    message = SimpleNamespace(text=promo.code, answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock())
    for _ in range(2):
        await dealer_tools.promo_redeem(message, lambda key, **kw: key, user, state)
    provider.extend_client.assert_awaited_once_with(str(user.telegram_id), 2)
    provider.add_traffic.assert_awaited_once_with(str(user.telegram_id), 3)
    assert notice.await_count == 1
    assert message.answer.call_args.args[0] == "promo_redeem_already_used"
    assert (await db_repo.get_subscription(user.id)).traffic_limit_gb == 4
    assert await db_repo.can_claim_test(user.id) is False


@pytest.mark.asyncio
async def test_existing_dealer_test_limit_is_preserved(database):
    dealer = await db_repo.get_or_create_user(30004)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    statuses = []
    for _ in range(3):
        result = await db_repo.reserve_dealer_test_slot(dealer.id, 2, "UTC", 1, 1)
        statuses.append(result[0])
    assert statuses == ["reserved", "reserved", "limit"]


@pytest.mark.asyncio
async def test_trial_backfill_preserves_existing_promos(database, monkeypatch):
    dealer = await db_repo.get_or_create_user(30005)
    promo = await dealer_tools_repo.create_promo(
        dealer.id, "KEEP-ME", "days", 3, 0, utcnow() + timedelta(days=10), 5,
    )
    monkeypatch.setattr(engine_module, "engine", database.kw["bind"])
    await engine_module.init_db()
    saved = await dealer_tools_repo.get_dealer_promo(dealer.id, promo.id)
    assert (saved.code, saved.max_uses, saved.is_active) == ("KEEP-ME", 5, True)


def test_migration_and_dealer_routers_are_preserved():
    import main
    assert main.dealer_tools_router is dealer_tools.router
    assert main.migration_router is not None
