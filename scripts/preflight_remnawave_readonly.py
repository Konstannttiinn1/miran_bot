"""Read-only sanity check for live Remnawave integration.

Run from the *existing production project directory* with the review code on
PYTHONPATH, so pydantic-settings reads the existing .env file:
    PYTHONPATH=/root/miran-multisub-review-20261109 \
      /root/miran-multisub-test-venv/bin/python \
      /root/miran-multisub-review-20261109/scripts/preflight_remnawave_readonly.py

Makes only SELECT queries and HTTP GET requests; no writes, no invoices, no links.
Never prints VPN usernames, Telegram IDs, URLs, subscription tokens, or API keys.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.services.remnawave_api import RemnawaveClient

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


async def main() -> None:
    if settings.vpn_provider.strip().lower() != "remnawave":
        raise RuntimeError("VPN_PROVIDER is not remnawave; stopping safely")

    # This is intentionally the only database query in the runner.
    # The adapter itself performs an extra read-only lookup in
    # remnawave_migrations if a short UUID has already been recorded.
    engine = create_async_engine(settings.db_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            groups = {}
            for group, query in (
                ("personal", """
                    SELECT xui_email FROM subscriptions
                    ORDER BY id LIMIT 3
                """),
                ("dealer", """
                    SELECT xui_email FROM dealer_subscriptions
                    WHERE status NOT IN ('deleted', 'cancelled')
                    ORDER BY id LIMIT 2
                """),
            ):
                groups[group] = list((await conn.execute(text(query))).scalars())
            await conn.rollback()
    finally:
        await engine.dispose()

    provider = RemnawaveClient()
    print("Remnawave configuration: present", flush=True)
    print("Mode: SELECT and HTTP GET only", flush=True)

    checked = 0
    failures = 0
    for group, emails in groups.items():
        print(f"=== {group}: {len(emails)} sampled clients ===", flush=True)
        for i, email in enumerate(emails, 1):
            try:
                user = await asyncio.wait_for(provider.get_client(email), timeout=15)
                if not isinstance(user, dict) or not user:
                    raise ValueError("not returned by Remnawave")
                username = user.get("username")
                if username != email:
                    raise ValueError("username mismatch between panel and DB")
                short_uuid = user.get("shortUuid")
                if not isinstance(short_uuid, str) or not short_uuid:
                    raise ValueError("missing subscription token")
                if user.get("expireAt") is None:
                    raise ValueError("missing expiry")
                if user.get("trafficLimitBytes") is None:
                    raise ValueError("missing traffic limit")
                checked += 1
                status = str(user.get("status") or "UNKNOWN")
                if status not in {"ACTIVE", "LIMITED", "EXPIRED", "DISABLED", "ON_HOLD"}:
                    status = "OTHER"
                print(
                    f"  #{i}: OK | status={status} | "
                    f"usage={'yes' if user.get('userTraffic') or user.get('usedTrafficBytes') is not None else 'no'} | "
                    f"devices={'yes' if user.get('hwidDeviceLimit') is not None else 'no'}",
                    flush=True,
                )
            except Exception as exc:
                failures += 1
                # Error bodies may contain user data. Print only the exception
                # *type*, never the exception text or identifying values.
                print(f"  #{i}: CHECK FAILED ({type(exc).__name__})", flush=True)

    print(f"RESULT: verified={checked}, failed={failures}", flush=True)
    if failures or checked == 0:
        raise SystemExit(
            "Read-only preflight did not fully pass; do not deploy."
        )
    print("READ-ONLY REMNAWAVE PREFLIGHT PASSED", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
