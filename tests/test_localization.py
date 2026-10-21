import ast
import json
from datetime import timedelta
from pathlib import Path
import re
from string import Formatter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.database.models import utcnow
from app.handlers import client, dealer, dealer_tools, migration, start
from app.keyboards import builders
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services import reminder


LOCALES = Path(__file__).resolve().parents[1] / "app" / "locales"
LANGS = ("ru", "en", "fa")


def translator(lang):
    return lambda key, **kwargs: get_text(lang, key, **kwargs)


def assert_language(text, lang):
    assert "\\n" not in text
    arabic = re.search(r"[\u0600-\u06ff]", text)
    cyrillic = re.search(r"[А-Яа-яЁё]", text)
    if lang == "ru":
        assert cyrillic and not arabic, text
    elif lang == "en":
        assert not arabic and not cyrillic, text
    else:
        assert arabic and not cyrillic, text


def keyboard_text(keyboard):
    return "\n".join(b.text for row in keyboard.inline_keyboard for b in row)


def test_translation_catalogs_have_matching_keys_and_named_fields():
    catalogs = {lang: json.loads((LOCALES / f"{lang}.json").read_text()) for lang in LANGS}
    keys = set(catalogs["en"])
    for lang, catalog in catalogs.items():
        assert set(catalog) == keys, lang
        for key, value in catalog.items():
            assert "\\n" not in value, (lang, key)
            fields = {field for _, field, _, _ in Formatter().parse(value) if field}
            expected = {field for _, field, _, _ in Formatter().parse(catalogs["en"][key]) if field}
            assert fields == expected, (lang, key)


def test_all_literal_translation_lookups_exist():
    keys = json.loads((LOCALES / "en.json").read_text())
    for path in LOCALES.parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            index = 1 if node.func.id == "get_text" else 0 if node.func.id in {"t", "nt"} else None
            if index is None or len(node.args) <= index:
                continue
            key = node.args[index]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                assert key.value in keys, (path, node.lineno, key.value)


@pytest.mark.parametrize("lang", LANGS)
def test_form_text_has_real_newlines_without_decoding_customer_input(lang):
    text = get_text(lang, "dealer_tests_title", total=0, page=1, pages=1)
    assert "\n\n" in text
    assert_language(text, lang)
    name = r"Customer\nname"
    link = r"https://example.test/sub/keep\new"
    text = get_text(lang, "dealer_test_card", name=name, link=link, traffic=1, expire="01.01.2030", status="OK")
    assert name in text and link in text


@pytest.mark.parametrize("lang", LANGS)
def test_all_dealer_keyboards_use_the_selected_language(lang):
    t = translator(lang)
    keyboards = [
        builders.dealer_buy_plans_kb(t=t),
        builders.dealer_buy_confirm_kb("month1", t=t),
        builders.dealer_subscriptions_kb([], 0, 1, t=t),
        builders.dealer_subscription_card_kb(7, 0, "https://example.test", t=t),
        builders.dealer_renew_plans_kb(7, 0, t=t),
        builders.dealer_renew_confirm_kb(7, "month1", 0, t=t),
        builders.dealer_created_name_kb(7, "https://example.test", t=t),
        builders.dealer_search_results_kb([], t=t),
    ]
    for keyboard in keyboards:
        assert_language(keyboard_text(keyboard), lang)
    assert "$0.500" in keyboard_text(keyboards[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("screen", ["buy", "buy_review", "list", "card", "search", "renew", "renew_review"])
async def test_dealer_screens_render_in_the_selected_language(monkeypatch, lang, screen):
    user = SimpleNamespace(id=1, telegram_id=40001, role="dealer", dealer_balance=0)
    callback = SimpleNamespace(data="dealer:buyplan:month1", answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())
    render = AsyncMock()
    monkeypatch.setattr(dealer, "send_with_logo", render)
    sub = SimpleNamespace(id=7, client_name="Ali <test>", expire_at=utcnow() + timedelta(days=5), traffic_limit_gb=10)
    monkeypatch.setattr(db_repo, "get_dealer_subscription", AsyncMock(return_value=sub))
    monkeypatch.setattr(db_repo, "list_dealer_subscriptions", AsyncMock(return_value=([], 0)))
    monkeypatch.setattr(dealer, "_subscription_link", AsyncMock(return_value="https://example.test/sub/id"))
    t = translator(lang)
    if screen == "buy":
        await dealer.dealer_buy_subscription(callback, t, lang, user, state)
    elif screen == "buy_review":
        await dealer.dealer_buy_plan(callback, t, lang, user)
    elif screen == "list":
        await dealer.dealer_subscriptions_first(callback, t, lang, user, state)
    elif screen == "card":
        await dealer._show_subscription_card(callback, t, user.id, 7)
    elif screen == "search":
        await dealer.dealer_subscription_search(callback, t, lang, user, state)
    elif screen == "renew":
        callback.data = "dealer:renew:7:0"
        await dealer.dealer_subscription_renew(callback, t, lang, user)
    else:
        callback.data = "dealer:renewplan:7:month1:0"
        await dealer.dealer_subscription_renew_plan(callback, t, lang, user)
    assert_language(render.call_args.args[1], lang)
    assert_language(keyboard_text(render.call_args.kwargs["reply_markup"]), lang)
    if screen in {"card", "renew_review"}:
        assert "Ali &lt;test&gt;" in render.call_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("screen", ["test_start", "test_list", "promo_list"])
async def test_dealer_tools_forms_and_lists_have_real_line_breaks(monkeypatch, lang, screen):
    from app.repositories import dealer_tools_repo
    user = SimpleNamespace(id=1, role="dealer")
    callback = SimpleNamespace(answer=AsyncMock())
    state = SimpleNamespace(clear=AsyncMock(), set_state=AsyncMock())
    render = AsyncMock()
    monkeypatch.setattr(dealer_tools, "send_with_logo", render)
    monkeypatch.setattr(dealer_tools_repo, "list_dealer_tests", AsyncMock(return_value=([], 0)))
    monkeypatch.setattr(dealer_tools_repo, "list_dealer_promos", AsyncMock(return_value=([], 0)))
    if screen == "test_start":
        await dealer_tools.test_start(callback, translator(lang), user, state)
    elif screen == "test_list":
        await dealer_tools.tests_first(callback, translator(lang), user, state)
    else:
        await dealer_tools.promos_first(callback, translator(lang), user, state)
    text = render.call_args.args[1]
    assert_language(text, lang)
    assert "\n\n" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("old", [False, True])
async def test_old_and_new_connection_screens_are_localized(monkeypatch, lang, old):
    callback = SimpleNamespace(answer=AsyncMock())
    render = AsyncMock()
    monkeypatch.setattr(migration, "send_with_logo", render)
    monkeypatch.setattr(migration, "subscription_link", AsyncMock(return_value="https://example.test/new"))
    monkeypatch.setattr(migration, "_old_xui_link", AsyncMock(return_value="https://example.test/old"))
    await migration._send_key(callback, translator(lang), email="123", old=old, back_callback="back:main")
    assert_language(render.call_args.args[1], lang)
    assert ("/old" if old else "/new") in render.call_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", LANGS)
async def test_saved_language_is_used_on_the_next_callback(database, monkeypatch, lang):
    monkeypatch.setattr(settings, "admin_ids", "")
    user = await db_repo.get_or_create_user(40002)
    event = SimpleNamespace(data="set_lang:" + lang, from_user=SimpleNamespace(id=user.telegram_id, username=None), answer=AsyncMock())
    monkeypatch.setattr(start, "send_main_menu", AsyncMock())
    await start.set_lang(event, translator("fa"), "fa", user)
    handler = AsyncMock()
    await I18nMiddleware()(handler, event, {})
    context = handler.call_args.args[1]
    assert context["lang"] == lang
    assert_language(context["t"]("select_plan"), lang)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", LANGS)
async def test_reminders_use_the_recipient_language(monkeypatch, lang):
    send = AsyncMock()
    monkeypatch.setattr(reminder.bot, "send_message", send)
    await reminder._send(SimpleNamespace(telegram_id=40003, lang=lang), "expiring_3d", SimpleNamespace(expire_at=utcnow()))
    assert_language(send.call_args.args[1], lang)


@pytest.mark.asyncio
async def test_custom_emoji_tags_do_not_leak_into_russian_alerts(monkeypatch):
    monkeypatch.setattr(settings, "use_custom_emoji", True)
    callback = SimpleNamespace(data="dealer:buyplan:unknown", answer=AsyncMock())
    await dealer.dealer_buy_plan(callback, translator("ru"), "ru", SimpleNamespace(role="dealer"))
    text = callback.answer.call_args.args[0]
    assert_language(text, "ru")
    assert "<tg-emoji" not in text


def test_russian_payment_amount_is_labeled_in_russian():
    assert client._format_toman(39000, "ru") == "39,000 туманов"


@pytest.mark.asyncio
async def test_invalid_language_button_does_not_change_saved_language(database):
    user = await db_repo.get_or_create_user(40004)
    event = SimpleNamespace(
        data="set_lang:unknown", from_user=SimpleNamespace(id=user.telegram_id), answer=AsyncMock(),
    )
    await start.set_lang(event, translator("ru"), "ru", user)
    assert (await db_repo.get_user_by_id(user.id)).lang == "fa"
    assert event.answer.call_args.kwargs["show_alert"] is True
    assert_language(event.answer.call_args.args[0], "ru")


@pytest.mark.asyncio
async def test_unknown_stored_language_uses_one_consistent_fallback(monkeypatch):
    from app.middlewares import i18n
    monkeypatch.setattr(settings, "admin_ids", "")
    monkeypatch.setattr(i18n, "get_or_create_user", AsyncMock(return_value=SimpleNamespace(lang="unknown", is_blocked=False)))
    event = SimpleNamespace(from_user=SimpleNamespace(id=40005, username=None))
    handler = AsyncMock()
    await I18nMiddleware()(handler, event, {})
    data = handler.call_args.args[1]
    assert data["lang"] == "fa"
    assert_language(data["t"]("select_plan"), "fa")
