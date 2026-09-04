import base64
import hashlib
import json
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)
BASE = "https://api.heleket.com/v1"
PAID_STATUSES = {"paid", "paid_over"}


class HeleketError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.heleket_merchant_id and settings.heleket_api_key)


def _payload(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _headers(payload: str) -> dict[str, str]:
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    sign = hashlib.md5((encoded + settings.heleket_api_key).encode("utf-8")).hexdigest()
    return {
        "merchant": settings.heleket_merchant_id,
        "sign": sign,
        "Content-Type": "application/json",
    }


async def _post(path: str, data: dict) -> dict:
    if not is_configured():
        raise HeleketError("Heleket merchant ID / API key are not configured")
    payload = _payload(data)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{BASE}{path}",
            headers=_headers(payload),
            content=payload.encode("utf-8"),
        )
        response.raise_for_status()
        body = response.json()
    if body.get("state") not in (None, 0):
        raise HeleketError(f"Heleket API error: {body}")
    result = body.get("result", body.get("data", body))
    if not isinstance(result, dict):
        raise HeleketError(f"Unexpected Heleket response: {body}")
    return result


async def create_invoice(amount: float, order_id: int) -> dict:
    data = {
        "amount": f"{amount:.2f}",
        "currency": "USD",
        "order_id": str(order_id),
    }
    if settings.heleket_webhook_url:
        data["url_callback"] = settings.heleket_webhook_url
    payment = await _post("/payment", data)
    uuid = payment.get("uuid")
    url = payment.get("url")
    if not uuid or not url:
        raise HeleketError(f"Heleket invoice has no uuid/url: {payment}")
    log.info("Heleket invoice created: order=%s uuid=%s", order_id, uuid)
    return {"uuid": uuid, "url": url}


async def get_payment(uuid: str) -> dict:
    return await _post("/payment/info", {"uuid": uuid})


def is_paid(payment: dict) -> bool:
    status = payment.get("payment_status") or payment.get("status") or ""
    return str(status).lower() in PAID_STATUSES
