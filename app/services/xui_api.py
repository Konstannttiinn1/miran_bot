import json
import logging
import random
import string
import time
import uuid as uuid_lib

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class XuiApiError(Exception):
    """Ошибка 3x-UI API."""


class XuiClient:
    """Клиент 3x-UI v3.6 (API: /panel/api/clients/*)."""

    def __init__(self) -> None:
        host = settings.xui_host.rstrip("/")
        path = settings.xui_base_path.strip("/")
        self.base = f"{host}/{path}" if path else host
        self._http: httpx.AsyncClient | None = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            headers = {}
            if settings.xui_token:
                headers["Authorization"] = f"Bearer {settings.xui_token}"
            self._http = httpx.AsyncClient(timeout=30, verify=False, headers=headers)
        return self._http

    async def add_client(self, email: str, days: int, limit_ip: int = 1, traffic_gb: int = 0) -> str:
        """Создаёт клиента, а если он уже есть — обновляет срок/трафик."""
        http = await self._client()
        expiry = int((time.time() + days * 86400) * 1000)
        traffic = int(traffic_gb * 1024 ** 3)

        existing = await self.get_client(email)
        if existing is not None:
            sub_id = existing.get("subId") or "".join(
                random.choices(string.ascii_lowercase + string.digits, k=16))
            await self.update_client(
                email, expiryTime=expiry, totalGB=traffic,
                enable=True, limitIp=limit_ip,
            )
            log.info("3x-UI: клиент %s обновлён", email)
            return sub_id

        sub_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=16))
        client = {
            "id": str(uuid_lib.uuid4()),
            "flow": "xtls-rprx-vision",
            "email": email,
            "limitIp": limit_ip,
            "totalGB": traffic,
            "expiryTime": expiry,
            "enable": True,
            "subId": sub_id,
        }
        r = await http.post(
            f"{self.base}/panel/api/clients/add",
            json={"client": client, "inboundIds": [settings.xui_inbound_id]},
        )
        data = r.json()
        if not data.get("success"):
            raise XuiApiError(f"3x-UI clients/add failed: {data}")
        log.info("3x-UI: клиент %s создан", email)
        return sub_id

    async def get_client(self, email: str) -> dict | None:
        http = await self._client()
        r = await http.get(f"{self.base}/panel/api/clients/get/{email}")
        data = r.json()
        if not data.get("success"):
            return None
        obj = data.get("obj")
        if isinstance(obj, dict) and "client" in obj and isinstance(obj["client"], dict):
            return obj["client"]
        return obj

    @staticmethod
    def _sanitize(client: dict) -> dict:
        """Чинит поля, которые панель отдаёт в одном типе, а принимает в другом."""
        allowed = client.get("allowedIPs")
        if allowed is not None and not isinstance(allowed, list):
            client["allowedIPs"] = [x for x in str(allowed).split(",") if x.strip()]
        if not isinstance(client.get("id"), str):
            # панель отдала числовой ID строки; API ждёт строку-UUID
            client["id"] = str(client.get("uuid") or uuid_lib.uuid4())
        return client

    async def update_client(self, email: str, **changes) -> None:
        client = await self.get_client(email)
        if client is None:
            raise XuiApiError(f"client {email} not found in panel")
        client.update(changes)
        client["email"] = email
        self._sanitize(client)
        http = await self._client()
        r = await http.post(f"{self.base}/panel/api/clients/update/{email}", json=client)
        data = r.json()
        if not data.get("success"):
            raise XuiApiError(f"3x-UI clients/update failed: {data}")

    async def extend_client(self, email: str, days: int) -> None:
        client = await self.get_client(email)
        if client is None:
            raise XuiApiError(f"client {email} not found in panel")
        current = client.get("expiryTime") or 0
        base = max(current, int(time.time() * 1000))
        await self.update_client(email, expiryTime=base + days * 86400 * 1000)

    async def set_enabled(self, email: str, enabled: bool) -> None:
        await self.update_client(email, enable=enabled)

    async def delete_client(self, email: str) -> None:
        http = await self._client()
        r = await http.post(f"{self.base}/panel/api/clients/del/{email}")
        data = r.json()
        if not data.get("success"):
            raise XuiApiError(f"3x-UI clients/del failed: {data}")

    async def get_links(self, email: str) -> list[str]:
        http = await self._client()
        r = await http.get(f"{self.base}/panel/api/clients/links/{email}")
        data = r.json()
        if not data.get("success"):
            raise XuiApiError(f"3x-UI links failed: {data}")
        return data.get("obj") or []