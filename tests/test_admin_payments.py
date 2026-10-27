from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.handlers import admin
from app.repositories import db_repo


async def press(data, viewer=99001, photo_error=None):
    callback = SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=viewer, username=None),
        answer=AsyncMock(), message=SimpleNamespace(
            answer=AsyncMock(), answer_photo=AsyncMock(side_effect=photo_error),
        ),
    )
    await admin.router.propagate_event("callback_query", callback)
    return callback


def callbacks(message):
    return [b.callback_data for row in message.call_args.kwargs["reply_markup"].inline_keyboard for b in row]


@pytest.mark.asyncio
async def test_profile_opens_existing_orders_with_amounts_and_statuses(database, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(71001, "history_buyer")
    crypto = await db_repo.create_order(buyer.id, "month2d2", 2.85, "usdt")
    await db_repo.update_order(crypto.id, status="paid")
    bank = await db_repo.create_order(buyer.id, "month1", 0.5, "usd")
    await db_repo.update_order(bank.id, status="pending_dealer", receipt_photo_id="old-receipt")

    card = await press("admin_user:71001")
    assert "admin_payments:71001:0" in callbacks(card.message.answer)
    history = await press("admin_payments:71001:0")
    history.message.answer.assert_awaited_once()
    text = history.message.answer.call_args.args[0]
    assert "@history_buyer" in text and "71001" in text
    assert f"#{crypto.id}" in text and "2.85 USDT" in text and "Оплачен" in text
    assert f"#{bank.id}" in text and "Ожидает проверки дилером" in text
    assert "дилера" in text and "$0.500" in text
    assert f"admin_order:71001:{bank.id}:0" in callbacks(history.message.answer)
    assert f"admin_order:71001:{crypto.id}:0" in callbacks(history.message.answer)
    assert "admin_user:71001" in callbacks(history.message.answer)


@pytest.mark.asyncio
@pytest.mark.parametrize("plan,debit,product", [
    ("month2d3", 1.615, "3 устр. · 100 ГБ/мес · 60 дней"),
    ("40gb", 0.565, "40 ГБ · 30 дней"),
])
async def test_old_dealer_order_opens_saved_receipt_without_repricing(database, monkeypatch, plan, debit, product):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    monkeypatch.setattr(settings, "toman_per_usd", 500000)
    monkeypatch.setattr(settings, "dealer_discount", 0.9)
    buyer = await db_repo.get_or_create_user(71002)
    order = await db_repo.create_order(buyer.id, plan, debit, "usd")
    await db_repo.update_order(order.id, status="pending_dealer", receipt_photo_id="archived-receipt",
                               created_at=datetime(2026, 10, 26, 22, 0))

    card = await press(f"admin_order:71002:{order.id}:0")
    card.message.answer_photo.assert_awaited_once()
    assert card.message.answer_photo.call_args.args[0] == "archived-receipt"
    text = card.message.answer_photo.call_args.kwargs["caption"]
    assert f"#{order.id}" in text and product in text
    assert f"${debit:.3f}" in text and "Сумма дилера" in text
    assert "27.10.2026 01:00 МСК" in text
    assert "Ожидает проверки дилером" in text
    assert "Розничная сумма" in text and "не сохранена" in text
    assert len(text) < 1024
    assert "admin_payments:71002:0" in callbacks(card.message.answer_photo)
    stored = await db_repo.get_order(order.id)
    assert stored.amount == debit and stored.status == "pending_dealer"
    assert stored.receipt_photo_id == "archived-receipt"


@pytest.mark.asyncio
@pytest.mark.parametrize("plan,currency,amount,status,expected", [
    ("month1", "xtr", 150, "paid", "Оплачен"),
    ("month2", "usdt", 1.9, "crypto_paid_grant_failed", "Оплата получена, активация не завершена"),
    ("traffic50", "ton", 0.12345678, "failed", "Отказ или ошибка"),
    ("<old-plan>", "<coin>", 8, "<old-status>", "&lt;old-status&gt;"),
])
async def test_history_preserves_stored_currency_and_handles_legacy_values(database, monkeypatch, plan, currency, amount, status, expected):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(71003, "buyer")
    order = await db_repo.create_order(buyer.id, plan, amount, currency)
    await db_repo.update_order(order.id, status=status)

    card = await press(f"admin_order:71003:{order.id}:0")
    card.message.answer.assert_awaited_once()
    card.message.answer_photo.assert_not_awaited()
    text = card.message.answer.call_args.args[0]
    assert expected in text
    assert str(amount) in text and currency.upper().replace("<", "&lt;").replace(">", "&gt;") in text
    if plan == "<old-plan>":
        assert "&lt;old-plan&gt;" in text and "<old-plan>" not in text
    assert (await db_repo.get_order(order.id)).status == status


@pytest.mark.asyncio
async def test_history_pagination_is_stable_and_scoped_to_one_user(database, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(71004)
    other = await db_repo.get_or_create_user(71005)
    orders = []
    for _ in range(6):
        order = await db_repo.create_order(buyer.id, "month1", 1, "usdt")
        await db_repo.update_order(order.id, created_at=datetime(2026, 10, 20, 12, 0))
        orders.append(order)
    trial = await db_repo.create_order(buyer.id, "test", 0, "test")
    foreign = await db_repo.create_order(other.id, "month3", 2.7, "usdt")

    first = await press("admin_payments:71004:0")
    first.message.answer.assert_awaited_once()
    order_buttons = [c for c in callbacks(first.message.answer) if c.startswith("admin_order:")]
    assert order_buttons == [f"admin_order:71004:{o.id}:0" for o in reversed(orders[1:])]
    assert "admin_payments:71004:1" in callbacks(first.message.answer)
    text = first.message.answer.call_args.args[0]
    assert "Всего: 6" in text and "Страница 1/2" in text
    assert f"#{trial.id} ·" not in text and f"#{foreign.id} ·" not in text

    last = await press("admin_payments:71004:999999")
    assert "Страница 2/2" in last.message.answer.call_args.args[0]
    assert f"admin_order:71004:{orders[0].id}:1" in callbacks(last.message.answer)
    assert "admin_payments:71004:0" in callbacks(last.message.answer)


@pytest.mark.asyncio
async def test_empty_payment_history_excludes_free_trial(database, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(71006)
    await db_repo.create_order(buyer.id, "test", 0, "test")
    history = await press("admin_payments:71006:0")
    history.message.answer.assert_awaited_once()
    assert "Заказов на оплату пока нет" in history.message.answer.call_args.args[0]
    assert callbacks(history.message.answer) == ["admin_user:71006"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["admin_payments:71007:0", "admin_order:71007:1:0"])
async def test_payment_history_and_receipts_are_admin_only(database, monkeypatch, action):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(71007)
    await db_repo.create_order(buyer.id, "month1", 0.5, "usd")
    callback = await press(action, viewer=71008)
    callback.message.answer.assert_not_awaited()
    callback.message.answer_photo.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["foreign", "trial", "missing", "malformed"])
async def test_invalid_history_order_does_not_expose_receipts(database, monkeypatch, kind):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(71009)
    other = await db_repo.get_or_create_user(71010)
    order = await db_repo.create_order(other.id if kind == "foreign" else buyer.id,
                                        "test" if kind == "trial" else "month1", 0.5, "usd")
    await db_repo.update_order(order.id, receipt_photo_id="private-receipt")
    identifier = "bad-id" if kind == "malformed" else str(999999 if kind == "missing" else order.id)
    card = await press(f"admin_order:71009:{identifier}:0")
    card.message.answer.assert_not_awaited()
    card.message.answer_photo.assert_not_awaited()
    card.answer.assert_awaited_once()
    assert card.answer.call_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_unavailable_archived_receipt_keeps_order_details(database, monkeypatch):
    monkeypatch.setattr(settings, "admin_ids", "99001")
    buyer = await db_repo.get_or_create_user(71011)
    order = await db_repo.create_order(buyer.id, "month1", 0.5, "usd")
    await db_repo.update_order(order.id, receipt_photo_id="unavailable-receipt")
    card = await press(f"admin_order:71011:{order.id}:0", photo_error=RuntimeError("Telegram photo unavailable"))
    card.message.answer_photo.assert_awaited_once()
    card.message.answer.assert_awaited_once()
    text = card.message.answer.call_args.args[0]
    assert f"#{order.id}" in text and "$0.500" in text and "Не удалось загрузить" in text
    assert "admin_payments:71011:0" in callbacks(card.message.answer)
