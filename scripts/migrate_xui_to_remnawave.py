#!/usr/bin/env python3
import argparse
import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.config import settings
from app.services.xui_api import XuiClient


DB_PATH = Path("vpn.db")
SQUAD_UUID = "378b29a0-63c0-49da-b4f4-1119a5c5c7b9"
BYTES_PER_GB = 1024 ** 3


def unwrap(data):
    return data.get("response", data) if isinstance(data, dict) else data


def iso_from_ms(value):
    return datetime.fromtimestamp(int(value) / 1000, UTC).isoformat().replace("+00:00", "Z")


def parse_db_time(value):
    text = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def source_rows():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            u.id AS user_id,
            u.telegram_id,
            u.username,
            u.is_blocked,
            s.xui_email,
            s.expire_at AS db_expire_at,
            s.traffic_limit_gb,
            s.limit_ip
        FROM subscriptions s
        JOIN users u ON u.id = s.user_id
        ORDER BY u.id
        """
    ).fetchall()
    conn.close()
    return rows


def ensure_migration_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS remnawave_migrations (
            user_id INTEGER PRIMARY KEY,
            telegram_id INTEGER NOT NULL UNIQUE,
            xui_email TEXT NOT NULL,
            remnawave_id INTEGER NOT NULL,
            remnawave_short_uuid TEXT NOT NULL,
            subscription_url TEXT NOT NULL,
            source_expire_at TEXT NOT NULL,
            source_total_bytes INTEGER NOT NULL,
            source_used_bytes INTEGER NOT NULL,
            migrated_at TEXT NOT NULL
        )
        """
    )


def save_mapping(conn, item, remote):
    conn.execute(
        """
        INSERT INTO remnawave_migrations (
            user_id, telegram_id, xui_email,
            remnawave_id, remnawave_short_uuid, subscription_url,
            source_expire_at, source_total_bytes, source_used_bytes, migrated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            remnawave_id=excluded.remnawave_id,
            remnawave_short_uuid=excluded.remnawave_short_uuid,
            subscription_url=excluded.subscription_url,
            source_expire_at=excluded.source_expire_at,
            source_total_bytes=excluded.source_total_bytes,
            source_used_bytes=excluded.source_used_bytes,
            migrated_at=excluded.migrated_at
        """,
        (
            item["user_id"],
            item["telegram_id"],
            item["xui_email"],
            remote["id"],
            remote["shortUuid"],
            remote["subscriptionUrl"],
            item["expire_at"],
            item["total_bytes"],
            item["used_bytes"],
            datetime.now(UTC).isoformat(),
        ),
    )


async def api_users(http):
    response = await http.get("/api/users", params={"start": 0, "size": 1000})
    response.raise_for_status()
    return unwrap(response.json()).get("users", [])


async def api_create_user(http, item):
    payload = {
        "username": f"tg-{item['telegram_id']}",
        "expireAt": item["expire_at"],
        "trafficLimitBytes": item["remaining_bytes"],
        "trafficLimitStrategy": "NO_RESET",
        "telegramId": item["telegram_id"],
        "description": f"Migrated from 3x-ui ({item['xui_email']})",
        "hwidDeviceLimit": item["limit_ip"],
        "activeInternalSquads": [SQUAD_UUID],
    }
    response = await http.post("/api/users", json=payload)
    response.raise_for_status()
    return unwrap(response.json())


async def main():
    parser = argparse.ArgumentParser(
        description="Migrate active Miran 3x-ui subscriptions to Remnawave."
    )
    parser.add_argument("--apply", action="store_true", help="Actually create users.")
    parser.add_argument("--only-telegram-id", type=int)
    parser.add_argument("--include-expired", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.apply and args.confirm != "MIGRATE_ACTIVE_USERS":
        raise SystemExit(
            "For a real import add: --apply --confirm MIGRATE_ACTIVE_USERS"
        )

    if not settings.remnawave_api_url or not settings.remnawave_api_token:
        raise SystemExit("REMNAWAVE_API_URL or REMNAWAVE_API_TOKEN is not configured")

    now = datetime.now(UTC)
    rows = source_rows()

    if args.only_telegram_id is not None:
        rows = [r for r in rows if r["telegram_id"] == args.only_telegram_id]

    async with httpx.AsyncClient(
        base_url=settings.remnawave_api_url.rstrip("/"),
        headers={"Authorization": f"Bearer {settings.remnawave_api_token}"},
        timeout=30,
    ) as http:
        existing_users = await api_users(http)

        existing_by_tg = {
            str(user["telegramId"]): user
            for user in existing_users
            if user.get("telegramId") is not None
        }

        xui = XuiClient()
        plan = []
        skipped = []

        try:
            for row in rows:
                tg_id = int(row["telegram_id"])
                xui_email = str(row["xui_email"])

                if row["is_blocked"]:
                    skipped.append((tg_id, "blocked in Miran"))
                    continue

                client = await xui.get_client(xui_email)
                if client is None:
                    skipped.append((tg_id, f"not found in 3x-ui: {xui_email}"))
                    continue

                enabled = bool(client.get("enable", False))
                if not enabled:
                    skipped.append((tg_id, "disabled in 3x-ui"))
                    continue

                expiry_ms = int(client.get("expiryTime") or 0)
                expire_at = (
                    iso_from_ms(expiry_ms)
                    if expiry_ms > 0
                    else parse_db_time(row["db_expire_at"]).isoformat().replace("+00:00", "Z")
                )

                expire_dt = parse_db_time(expire_at)
                if expire_dt <= now and not args.include_expired:
                    skipped.append((tg_id, f"expired: {expire_at}"))
                    continue

                total_bytes = int(client.get("totalGB") or 0)
                used_bytes = int(client.get("up") or 0) + int(client.get("down") or 0)

                if total_bytes == 0:
                    remaining_bytes = 0
                else:
                    remaining_bytes = max(total_bytes - used_bytes, 0)

                item = {
                    "user_id": int(row["user_id"]),
                    "telegram_id": tg_id,
                    "xui_email": xui_email,
                    "expire_at": expire_at,
                    "total_bytes": total_bytes,
                    "used_bytes": used_bytes,
                    "remaining_bytes": remaining_bytes,
                    "limit_ip": max(0, int(client.get("limitIp") or row["limit_ip"] or 0)),
                }
                plan.append(item)
        finally:
            if xui._http is not None:
                await xui._http.aclose()

        print("===== MIGRATION PLAN =====")
        print(f"Source rows: {len(rows)}")
        print(f"Ready to migrate: {len(plan)}")
        print(f"Skipped: {len(skipped)}")

        for item in plan:
            kind = "EXISTS" if str(item["telegram_id"]) in existing_by_tg else "CREATE"
            traffic = (
                "unlimited"
                if item["total_bytes"] == 0
                else f"{item['remaining_bytes'] / BYTES_PER_GB:.2f} GB remaining"
            )
            print(
                f"{kind} tg={item['telegram_id']} xui={item['xui_email']} "
                f"expires={item['expire_at']} traffic={traffic} ip_limit={item['limit_ip']}"
            )

        if skipped:
            print("\n===== SKIPPED =====")
            for tg_id, reason in skipped:
                print(f"SKIP tg={tg_id}: {reason}")

        if not args.apply:
            print("\nDry-run complete. No users or database records were changed.")
            return

        conn = sqlite3.connect(DB_PATH)
        try:
            ensure_migration_table(conn)
            created = 0
            mapped = 0

            for item in plan:
                existing = existing_by_tg.get(str(item["telegram_id"]))
                if existing:
                    remote = existing
                    action = "mapped existing"
                else:
                    remote = await api_create_user(http, item)
                    existing_by_tg[str(item["telegram_id"])] = remote
                    action = "created"
                    created += 1

                required = ("id", "shortUuid", "subscriptionUrl")
                missing = [key for key in required if not remote.get(key)]
                if missing:
                    raise RuntimeError(
                        f"Remnawave returned incomplete user for tg={item['telegram_id']}: {missing}"
                    )

                save_mapping(conn, item, remote)
                conn.commit()
                mapped += 1
                print(f"{action}: tg={item['telegram_id']} → {remote['shortUuid']}")

            print(f"\nDone. Created: {created}; mapped: {mapped}")
        finally:
            conn.close()


if __name__ == "__main__":
    asyncio.run(main())
