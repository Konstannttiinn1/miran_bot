"""Read-only order history and archived receipts for the admin user card."""

import logging
from datetime import timezone
from decimal import Decimal
from html import escape as h
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.middlewares.i18n import get_text
from app.repositories import db_repo
from app.utils.tariffs import TRAFFIC_TOPUP, get_tariff

log = logging.getLogger(__name__)
router = Router()
PER_PAGE = 5
MOSCOW = ZoneInfo("Europe/Moscow")
STATUSES = {
    "pending": "Ожидает оплаты или чека",
    "pending_dealer": "Ожидает проверки дилером",
    "dealer_processing": "Дилер обрабатывает заказ",
    "paid": "Оплачен",
    "failed": "Отказ или ошибка",
    "stars_paid_processing": "Оплата получена, активация в процессе",
    "crypto_paid_processing": "Оплата получена, активация в процессе",
    "stars_paid_grant_failed": "Оплата получена, активация не завершена",
    "crypto_paid_grant_failed": "Оплата получена, активация не завершена",
    "crypto_paid_user_missing": "Оплата получена, пользователь не найден",
}


def _product(plan: str) -> str:
    try:
        tariff = get_tariff(plan)
    except KeyError:
        return f"Архивный тариф: <code>{h(plan)}</code>"
    if plan == TRAFFIC_TOPUP:
        return get_text("ru", "traffic_invoice_title")
    return get_text(
        "ru", "monthly_product" if tariff.get("monthly") else "legacy_product",
        devices=tariff.get("devices", 1), traffic=tariff["traffic_gb"], days=tariff["days"],
    )


def _date(order) -> str:
    created = order.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M МСК")


def _amount(order) -> str:
    if order.currency.lower() == "usd":
        return f"Сумма дилера: ${order.amount:.3f}"
    amount = format(Decimal(str(order.amount)).normalize(), "f")
    return f"Сумма оплаты: {amount} {h(order.currency.upper())}"


def _status(order) -> str:
    return STATUSES.get(order.status, h(order.status))


def _buyer(user) -> str:
    username = f"@{h(user.username)}" if user.username else "Без username"
    return f"👤 {username} · <code>{user.telegram_id}</code>"


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def _request(callback, count: int) -> tuple[int, ...] | None:
    if callback.from_user.id not in settings.admin_list:
        await callback.answer("Нет доступа", show_alert=True)
        return None
    try:
        values = tuple(int(value) for value in callback.data.split(":")[1:])
        if len(values) != count or any(value <= 0 for value in values[:-1]):
            raise ValueError
    except (ValueError, TypeError):
        await callback.answer("Некорректный запрос", show_alert=True)
        return None
    return values


@router.callback_query(F.data.startswith("admin_payments:"))
async def user_payments(callback: types.CallbackQuery):
    request = await _request(callback, 2)
    if request is None:
        return
    tg_id, page = request
    user = await db_repo.get_user_by_tg(tg_id)
    if user is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    orders, total, page = await db_repo.list_user_orders(user.id, page, PER_PAGE)
    lines = ["💳 Заказы и оплаты", _buyer(user)]
    rows = []
    if not orders:
        lines.append("\nЗаказов на оплату пока нет.")
    else:
        pages = (total + PER_PAGE - 1) // PER_PAGE
        lines.append(f"Всего: {total} · Страница {page + 1}/{pages}")
        for order in orders:
            lines.append(
                f"\n#{order.id} · {_date(order)}\n"
                f"📦 {_product(order.plan)}\n{_amount(order)}\nСтатус: {_status(order)}"
            )
            rows.append([_button(f"📄 Заказ #{order.id}", f"admin_order:{tg_id}:{order.id}:{page}")])
        navigation = []
        if page > 0:
            navigation.append(_button("⬅️", f"admin_payments:{tg_id}:{page - 1}"))
        if page + 1 < pages:
            navigation.append(_button("➡️", f"admin_payments:{tg_id}:{page + 1}"))
        if navigation:
            rows.append(navigation)
    rows.append([_button("👤 К пользователю", f"admin_user:{tg_id}")])
    await callback.answer()
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin_order:"))
async def show_order(callback: types.CallbackQuery):
    request = await _request(callback, 3)
    if request is None:
        return
    tg_id, order_id, page = request
    user = await db_repo.get_user_by_tg(tg_id)
    order = await db_repo.get_order(order_id) if user is not None else None
    if order is None or order.user_id != user.id or order.plan == "test":
        await callback.answer("Заказ не найден у этого пользователя", show_alert=True)
        return
    lines = [
        f"📄 Заказ #{order.id}", _buyer(user), f"📅 {_date(order)}",
        f"📦 {_product(order.plan)}", _amount(order), f"Статус: {_status(order)}",
    ]
    if order.currency.lower() == "usd" and not order.order_type.startswith("managed_traffic:"):
        lines.append("Розничная сумма перевода не сохранена в архиве; сверяй с чеком.")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        _button("🔙 К оплатам", f"admin_payments:{tg_id}:{max(0, page)}"),
    ]])
    text = "\n".join(lines)
    await callback.answer()
    if order.receipt_photo_id:
        try:
            await callback.message.answer_photo(order.receipt_photo_id, caption=text, reply_markup=keyboard)
            return
        except Exception:
            log.exception("Archived receipt delivery failed for order %s", order.id)
            text += "\n⚠️ Не удалось загрузить сохранённый чек."
    elif order.currency.lower() == "usd":
        text += "\n📎 Фото чека не сохранено."
    await callback.message.answer(text, reply_markup=keyboard)
