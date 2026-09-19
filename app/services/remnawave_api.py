from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy import text

from app.config import settings
from app.database.engine import engine


class RemnawaveApiError(RuntimeError):
    """Ошибка Remnawave API."""


def _unwrap(data: object) -> dict:
    if not isinstance(data, dict):
        return {}
    payload = data.get("response", data)
    if not isinstance(payload, dict):
        return {}
    user = payload.get("user")
    return user if isinstance(user, dict) else payload


class RemnawaveClient:
    def __init__(self) -> None:
        self.base = settings.remnawave_api_url.rstrip("/")
        self.token = settings.remnawave_api_token.strip()
        self.sub_base = settings.remnawave_subscription_url.rstrip("/")
        self.squad_uuid = settings.remnawave_squad_uuid.strip()

        if not self.base or not self.token:
            raise RemnawaveApiError(
                "REMNAWAVE_API_URL or REMNAWAVE_API_TOKEN is not configured"
            )
        if not self.sub_base:
            raise RemnawaveApiError(
                "REMNAWAVE_SUBSCRIPTION_URL is not configured"
            )
        if not self.squad_uuid:
            raise RemnawaveApiError(
                "REMNAWAVE_SQUAD_UUID is not configured"
            )

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(
            base_url=self.base,
            headers=headers,
            timeout=30,
        ) as http:
            response = await http.request(method, path, **kwargs)

        if response.status_code == 404:
            return {}
        if response.status_code >= 400:
            raise RemnawaveApiError(
                f"Remnawave {method} {path} failed: "
                f"HTTP {response.status_code} {response.text[:500]}"
            )
        if not response.content:
            return {}
        try:
            return _unwrap(response.json())
        except ValueError as exc:
            raise RemnawaveApiError(
                f"Remnawave returned non-JSON response: "
                f"HTTP {response.status_code}"
            ) from exc

    async def _mapped_short_uuid(self, email: str) -> str | None:
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("""
                            SELECT remnawave_short_uuid
                            FROM remnawave_migrations
                            WHERE legacy_email = :email
                        """),
                        {"email": email},
                    )
                ).first()
        except Exception:
            return None
        return str(row[0]) if row and row[0] else None

    async def _save_mapping(self, email: str, user: dict) -> None:
        telegram_id = int(email) if email.isdigit() else None
        async with engine.begin() as conn:
            await conn.execute(
                text("""
                    CREATE TABLE IF NOT EXISTS remnawave_migrations (
                        legacy_email TEXT PRIMARY KEY,
                        remnawave_user_id BIGINT,
                        remnawave_short_uuid TEXT,
                        telegram_id BIGINT,
                        source_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
            )
            await conn.execute(
                text("""
                    INSERT INTO remnawave_migrations (
                        legacy_email,
                        remnawave_user_id,
                        remnawave_short_uuid,
                        telegram_id,
                        source_enabled
                    )
                    VALUES (
                        :email,
                        :user_id,
                        :short_uuid,
                        :telegram_id,
                        :source_enabled
                    )
                    ON CONFLICT (legacy_email) DO UPDATE SET
                        remnawave_user_id = EXCLUDED.remnawave_user_id,
                        remnawave_short_uuid = EXCLUDED.remnawave_short_uuid,
                        telegram_id = EXCLUDED.telegram_id,
                        updated_at = NOW()
                """),
                {
                    "email": email,
                    "user_id": user.get("id"),
                    "short_uuid": user.get("shortUuid"),
                    "telegram_id": telegram_id,
                    "source_enabled": True,
                },
            )

    async def get_client(self, email: str) -> dict | None:
        short_uuid = await self._mapped_short_uuid(email)
        if short_uuid:
            user = await self._request(
                "GET",
                f"/api/users/by-short-uuid/{quote(short_uuid, safe='')}",
            )
            if user:
                return user

        user = await self._request(
            "GET",
            f"/api/users/by-username/{quote(email, safe='')}",
        )
        return user or None

    @staticmethod
    def _user_id(user: dict) -> int:
        user_id = user.get("id")
        if user_id is None:
            raise RemnawaveApiError("Remnawave user has no id")
        return int(user_id)

    async def subscription_link(self, email: str) -> str:
        user = await self.get_client(email)
        short_uuid = (user or {}).get("shortUuid")
        if not short_uuid:
            raise RemnawaveApiError(
                f"Remnawave subscription token not found for {email}"
            )
        return f"{self.sub_base}/{short_uuid}"

    async def add_client(
        self,
        email: str,
        days: int,
        limit_ip: int = 1,
        traffic_gb: int = 0,
    ) -> str:
        if days <= 0:
            raise ValueError("days must be positive")

        existing = await self.get_client(email)
        if existing:
            await self._request(
                "PATCH",
                "/api/users",
                json={
                    "id": self._user_id(existing),
                    "trafficLimitBytes": int(traffic_gb) * 1024 ** 3,
                    "trafficLimitStrategy": "NO_RESET",
                    "hwidDeviceLimit": max(0, int(limit_ip)),
                },
            )
            await self.extend_client(email, days)
            updated = await self.get_client(email)
            return str((updated or existing)["shortUuid"])

        expires = datetime.now(UTC) + timedelta(days=days)
        payload = {
            "username": email,
            "status": "ACTIVE",
            "trafficLimitBytes": int(traffic_gb) * 1024 ** 3,
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": expires.isoformat().replace("+00:00", "Z"),
            "hwidDeviceLimit": max(0, int(limit_ip)),
            "activeInternalSquads": [self.squad_uuid],
        }
        if email.isdigit():
            payload["telegramId"] = int(email)

        user = await self._request("POST", "/api/users", json=payload)
        if not user.get("shortUuid"):
            raise RemnawaveApiError(
                f"Remnawave did not return a subscription token for {email}"
            )

        await self._save_mapping(email, user)
        return str(user["shortUuid"])

    async def extend_client(self, email: str, days: int) -> None:
        if days == 0:
            raise ValueError("days must not be zero")

        user = await self.get_client(email)
        if not user:
            raise RemnawaveApiError(f"Remnawave user {email} not found")

        if days > 0:
            await self._request(
                "POST",
                f"/api/users/{self._user_id(user)}/actions/extend",
                json={"days": days},
            )
            return

        raw_expire_at = str(user.get("expireAt") or "").replace("Z", "+00:00")
        try:
            expire_at = datetime.fromisoformat(raw_expire_at)
        except ValueError as exc:
            raise RemnawaveApiError(
                f"Invalid Remnawave expiry for {email}: {raw_expire_at!r}"
            ) from exc

        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=UTC)

        new_expire_at = expire_at + timedelta(days=days)
        if new_expire_at <= datetime.now(UTC):
            raise RemnawaveApiError(
                "Cannot move expiry to the past through Remnawave API"
            )

        await self._request(
            "PATCH",
            "/api/users",
            json={
                "id": self._user_id(user),
                "expireAt": new_expire_at.isoformat().replace("+00:00", "Z"),
            },
        )

    async def add_traffic(self, email: str, extra_gb: int) -> None:
        if extra_gb <= 0:
            raise ValueError("extra_gb must be positive")

        user = await self.get_client(email)
        if not user:
            raise RemnawaveApiError(f"Remnawave user {email} not found")

        current = int(user.get("trafficLimitBytes") or 0)
        if current == 0:
            return  # Already unlimited.

        await self._request(
            "PATCH",
            "/api/users",
            json={
                "id": self._user_id(user),
                "trafficLimitBytes": current + extra_gb * 1024 ** 3,
            },
        )

    async def set_enabled(self, email: str, enabled: bool) -> None:
        user = await self.get_client(email)
        if not user:
            raise RemnawaveApiError(f"Remnawave user {email} not found")

        action = "enable" if enabled else "disable"
        await self._request(
            "POST",
            f"/api/users/{self._user_id(user)}/actions/{action}",
        )

    async def delete_client(self, email: str) -> None:
        user = await self.get_client(email)
        if not user:
            return

        await self._request(
            "DELETE",
            f"/api/users/{self._user_id(user)}",
        )

    async def reset_subscription_link(self, email: str) -> str:
        user = await self.get_client(email)
        if not user:
            raise RemnawaveApiError(f"Remnawave user {email} not found")

        updated = await self._request(
            "POST",
            f"/api/users/{self._user_id(user)}/actions/revoke",
            json={"revokeOnlyPasswords": False},
        )
        short_uuid = updated.get("shortUuid")
        if not short_uuid:
            raise RemnawaveApiError(
                f"Remnawave did not return a new token for {email}"
            )

        await self._save_mapping(email, updated)
        return f"{self.sub_base}/{short_uuid}"

    async def get_links(self, email: str) -> list[str]:
        return [await self.subscription_link(email)]
