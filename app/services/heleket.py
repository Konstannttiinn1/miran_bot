import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE = "https://api.heleket.com/v1"
PAID_STATUSES = {"paid", "paid_over", "completed", "success", "confirmed"}


class HeleketError(Exception):
    """Ошибка Heleket API."""


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.heleket_api_key}",
        "Content-Type": "application/json",
    }


async def create_invoice(amount: float, order_id: int) -> dict:
    """Создаёт инвойс. Возвращает {'uuid': ..., 'url': ...}."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{BASE}/payment",
            headers=_headers(),
            json={
                "amount": amount,
                "currency": "USDT",
                "order_id": str(order_id),
                "callback_url": settings.heleket_webhook_url,
            },
        )
        r.raise_for_status()
        data = r.json()
    log.info("Heleket invoice: %s", data)
    pay = data.get("data", data)
    return {
        "uuid": pay.get("uuid") or pay.get("id") or pay.get("invoice_id"),
        "url": pay.get("url") or pay.get("checkout_url") or pay.get("payment_url"),
    }


async def get_payment(uuid: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BASE}/payment/{uuid}", headers=_headers())
        r.raise_for_status()
        data = r.json()
    return data.get("data", data)


def is_paid(payment: dict) -> bool:
    return str(payment.get("status", "")).lower() in PAID_STATUSES