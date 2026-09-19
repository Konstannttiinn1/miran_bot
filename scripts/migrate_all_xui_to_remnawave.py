#!/usr/bin/env python3
import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text

from app.config import settings
from app.database.engine import engine
from app.services.xui_api import XuiClient

SQUAD_UUID = "378b29a0-63c0-49da-b4f4-1119a5c5c7b9"
BYTES_PER_GB = 1024 ** 3
FAR_FUTURE = "2099-12-31T23:59:59Z"


def unwrap(data: Any) -> dict:
    return data.get("response", data) if isinstance(data, dict) else {}


def iso_from_ms(value: Any) -> str:
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return FAR_FUTURE
    return (
        datetime.fromtimestamp(value / 1000, UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def numeric_telegram_id(email: str) -> int | None:
    return int(email) if email.isdigit() else None


async def xui_inventory() -> dict[str, dict]:
    xui = XuiClient()
    http = await xui._client()
    try:
        response = await http.get(f"{xui.base}/panel/api/inbounds/list")
        response.raise_for_status()
        data = response.json()
    finally:
        await http.aclose()

    if not data.get("success"):
        raise RuntimeError(f"3x-ui inventory failed: {data}")

    clients: dict[str, dict] = {}

    for inbound in data.get("obj") or []:
        inbound_id = inbound.get("id")
        raw = inbound.get("settings") or "{}"
        try:
            inbound_settings = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            inbound_settings = {}

        stats_by_email = {
            item.get("email"): item
            for item in (inbound.get("clientStats") or [])
            if item.get("email")
        }

        for raw_client in inbound_settings.get("clients") or []:
            email = str(raw_client.get("email") or "").strip()
            if not email:
                continue

            client = clients.setdefault(
                email,
                {
                    "email": email,
                    "enable": bool(raw_client.get("enable")),
                    "expiryTime": raw_client.get("expiryTime") or 0,
                    "totalGB": raw_client.get("totalGB") or 0,
                    "limitIp": raw_client.get("limitIp") or 0,
                    "usedBytes": 0,
                    "inbounds": [],
                },
            )
            client["inbounds"].append(inbound_id)

            stat = stats_by_email.get(email) or {}
            used = int(stat.get("up") or 0) + int(stat.get("down") or 0)
            client["usedBytes"] = max(client["usedBytes"], used)

    return clients


async def postgres_preflight(source: dict[str, dict]) -> None:
    async with engine.connect() as conn:
        normal_rows = (
            await conn.execute(
                text("""
                    SELECT s.xui_email, u.telegram_id
                    FROM subscriptions s
                    JOIN users u ON u.id = s.user_id
                """)
            )
        ).mappings().all()

        dealer_rows = (
            await conn.execute(
                text("SELECT xui_email FROM dealer_subscriptions")
            )
        ).mappings().all()

    mismatched = [
        row for row in normal_rows
        if str(row["telegram_id"]) != str(row["xui_email"])
    ]
    missing_normal = [
        row["xui_email"] for row in normal_rows
        if row["xui_email"] not in source
    ]
    missing_dealer = [
        row["xui_email"] for row in dealer_rows
        if row["xui_email"] not in source
    ]

    if mismatched:
        raise RuntimeError(
            f"Telegram ID and xui_email differ: {mismatched[:3]}"
        )
    if missing_normal or missing_dealer:
        raise RuntimeError(
            "PostgreSQL records are missing in 3x-ui: "
            f"normal={missing_normal[:5]}, dealer={missing_dealer[:5]}"
        )

    print("===== POSTGRES CHECK =====")
    print("normal subscriptions =", len(normal_rows))
    print("dealer subscriptions =", len(dealer_rows))


async def api_list_users(http: httpx.AsyncClient) -> list[dict]:
    users: list[dict] = []
    start = 0
    size = 100

    while True:
        response = await http.get(
            "/api/users",
            params={"start": start, "size": size},
        )
        response.raise_for_status()
        payload = unwrap(response.json())
        batch = payload.get("users") or []
        users.extend(batch)

        total = int(payload.get("total") or 0)
        if not batch or len(users) >= total:
            return users
        start += len(batch)


def created_user(data: dict) -> dict:
    payload = unwrap(data)
    if isinstance(payload.get("user"), dict):
        return payload["user"]
    return payload if isinstance(payload, dict) else {}


async def ensure_mapping_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                CREATE TABLE IF NOT EXISTS remnawave_migrations (
                    legacy_email TEXT PRIMARY KEY,
                    remnawave_user_id BIGINT,
                    remnawave_short_uuid TEXT,
                    telegram_id BIGINT,
                    source_enabled BOOLEAN NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        )


async def save_mapping(source: dict, remote: dict) -> None:
    async with engine.begin() as conn:
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
                    :legacy_email,
                    :remnawave_user_id,
                    :remnawave_short_uuid,
                    :telegram_id,
                    :source_enabled
                )
                ON CONFLICT (legacy_email) DO UPDATE SET
                    remnawave_user_id = EXCLUDED.remnawave_user_id,
                    remnawave_short_uuid = EXCLUDED.remnawave_short_uuid,
                    telegram_id = EXCLUDED.telegram_id,
                    source_enabled = EXCLUDED.source_enabled,
                    updated_at = NOW()
            """),
            {
                "legacy_email": source["email"],
                "remnawave_user_id": remote.get("id"),
                "remnawave_short_uuid": remote.get("shortUuid"),
                "telegram_id": numeric_telegram_id(source["email"]),
                "source_enabled": bool(source["enable"]),
            },
        )


def payload_for(source: dict) -> dict:
    total = int(source["totalGB"] or 0)
    used = int(source["usedBytes"] or 0)
    enabled = bool(source["enable"])

    if total == 0:
        traffic_limit = 0 if enabled else 1
    else:
        remaining = max(total - used, 0)
        traffic_limit = max(1, remaining)

    payload = {
        "username": source["email"],
        "expireAt": iso_from_ms(source["expiryTime"]),
        "trafficLimitBytes": traffic_limit,
        "trafficLimitStrategy": "NO_RESET",
        "hwidDeviceLimit": max(0, int(source["limitIp"] or 0)),
        "activeInternalSquads": [SQUAD_UUID],
    }

    telegram_id = numeric_telegram_id(source["email"])
    if telegram_id is not None:
        payload["telegramId"] = telegram_id

    return payload


async def main(apply: bool) -> None:
    source = await xui_inventory()
    if len(source) != 48:
        raise RuntimeError(
            f"Safety stop: expected 48 unique 3x-ui clients, got {len(source)}"
        )

    await postgres_preflight(source)

    active = sum(bool(item["enable"]) for item in source.values())
    print("\n===== 3X-UI SOURCE =====")
    print("unique clients =", len(source))
    print("enabled =", active)
    print("disabled =", len(source) - active)

    if not apply:
        print("\nDry-run complete. No changes made.")
        return

    await ensure_mapping_table()

    headers = {"Authorization": f"Bearer {settings.remnawave_api_token}"}
    async with httpx.AsyncClient(
        base_url=settings.remnawave_api_url.rstrip("/"),
        headers=headers,
        timeout=30,
    ) as http:
        remote_users = await api_list_users(http)
        by_tg = {
            int(user["telegramId"]): user
            for user in remote_users
            if user.get("telegramId") is not None
        }
        by_username = {
            str(user.get("username")): user
            for user in remote_users
            if user.get("username")
        }

        created = mapped = 0

        for email in sorted(source):
            item = source[email]
            telegram_id = numeric_telegram_id(email)
            remote = (
                by_tg.get(telegram_id)
                if telegram_id is not None
                else by_username.get(email)
            )

            if remote is None:
                response = await http.post("/api/users", json=payload_for(item))
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Remnawave create failed for {email}: "
                        f"HTTP {response.status_code} {response.text}"
                    )

                remote = created_user(response.json())
                if not remote.get("shortUuid"):
                    refreshed = await api_list_users(http)
                    remote = next(
                        (
                            user for user in refreshed
                            if (
                                telegram_id is not None
                                and user.get("telegramId") == telegram_id
                            )
                            or (
                                telegram_id is None
                                and user.get("username") == email
                            )
                        ),
                        {},
                    )

                if not remote:
                    raise RuntimeError(
                        f"Remnawave created {email}, but could not read it back"
                    )

                by_username[email] = remote
                if telegram_id is not None:
                    by_tg[telegram_id] = remote

                created += 1
                action = "created"
            else:
                mapped += 1
                action = "existing"

            await save_mapping(item, remote)
            print(
                f"{action}: {email} -> "
                f"{remote.get('shortUuid', '-')} "
                f"(enabled={item['enable']})"
            )

    print(f"\nDone. Created: {created}; already existed: {mapped}; mapped: {len(source)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.apply and args.confirm != "MIGRATE_ALL_48":
        raise SystemExit(
            "For real migration add: --confirm MIGRATE_ALL_48"
        )

    try:
        asyncio.run(main(args.apply))
    finally:
        asyncio.run(engine.dispose())
