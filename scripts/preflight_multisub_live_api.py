"""Opt-in live-API integration test of one disposable personal VPN.

WARNING: This test writes to the *live Remnawave PANEL*, exclusively a freshly
generated synthetic username, then verifies deletion. It NEVER writes to the
production PostgreSQL database, never sends Telegram messages, and never
creates a real invoice or charges a payment provider.

Usage from the production project's directory (to load its existing .env):
    PYTHONPATH=/root/miran-multisub-review-20261109 \
      /root/miran-multisub-test-venv/bin/python \
      /root/miran-multisub-review-20261109/scripts/preflight_multisub_live_api.py \
      /absolute/path/to/production_snapshot.dump --confirm-live-panel-writes

The sample database is an isolated, ephemeral PostgreSQL 16 Docker container.
This test confirms the backend paid-order provisioning method, not checkout UI
or payment-provider webhook behavior.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.engine import URL, make_url

MARKER = "--confirm-live-panel-writes"


def run(command, *, env=None, **kwargs):
    subprocess.run(command, env=env, check=True, **kwargs)


async def live_worker():
    # All app imports happen after the isolated DB_URL environment is installed.
    from app.config import settings
    from app.database.models import utcnow
    from app.repositories import customer_subscriptions_repo as customer_repo
    from app.repositories import db_repo
    from app.services.subscription import grant_paid_order, grant_traffic_topup
    from app.services.vpn_provider import get_vpn_provider, reset_subscription_link
    from app.utils.tariffs import get_usd_price

    db_url = make_url(settings.db_url)
    if (
        db_url.get_backend_name() != "postgresql"
        or db_url.host != "127.0.0.1"
        or db_url.username != "orion_clone"
        or db_url.database != "orion_clone"
        or not os.environ.get("ORION_DB_CLONE_ONLY")
    ):
        raise RuntimeError("Clone database guard failed; refusing ALL API writes")

    if settings.vpn_provider.strip().lower() != "remnawave":
        raise RuntimeError("Live integration requires VPN_PROVIDER=remnawave")

    provider = get_vpn_provider()
    # No real Telegram ID: synthetic, far above the public Telegram ID range,
    # and still within signed BIGINT.
    telegram_id = 7900000000000000000 + secrets.randbelow(100000000000000)
    test_email = None
    stage = "preparation"
    cleanup_ok = False
    success = False

    try:
        stage = "creating synthetic owner and primary on cloned DB"
        owner = await db_repo.get_or_create_user(telegram_id)
        primary = await db_repo.upsert_subscription(
            owner.id, str(telegram_id), utcnow() + timedelta(days=32),
            traffic_limit_gb=100,
        )
        original_primary = (
            primary.id, primary.xui_email, primary.expire_at, primary.traffic_limit_gb
        )
        print("  OK: synthetic owner and primary exist ONLY in clone", flush=True)

        stage = "reserving the new VPN and its synthetic order"
        plan = "month1d2"
        order = await db_repo.create_order(
            owner.id, plan, get_usd_price(plan), "usd",
            new_subscription=True, apply_promo=False,
        )
        target = await customer_repo.get_subscription(owner.id, order.subscription_id)
        if target is None or target.status != "pending" or target.is_primary:
            raise AssertionError("New additional VPN was not reserved independently")
        test_email = target.xui_email

        # Guard against deleting another user's account: the generated name
        # must not be present in the live panel before the first POST.
        existing = await provider._request(
            "GET", f"/api/users/by-username/{quote(test_email, safe='')}"
        )
        if existing:
            raise RuntimeError("Synthetic username already exists in panel")
        print("  OK: distinct additional slot reserved, no existing panel username", flush=True)

        stage = "creating the disposable VPN in live Remnawave"
        first_link, first_expiry = await grant_paid_order(owner.id, telegram_id, order)
        current = await provider.get_client(test_email)
        if not current or current.get("username") != test_email:
            raise AssertionError("Live panel did not return the synthetic VPN")
        old_short = current.get("shortUuid")
        if not old_short or not first_link.endswith("/" + old_short):
            raise AssertionError("New VPN token/link is missing")
        if int(current.get("trafficLimitBytes") or -1) != 100 * 1024**3:
            raise AssertionError("Initial 100 GB allowance is wrong")
        if int(current.get("hwidDeviceLimit") or 0) != 2:
            raise AssertionError("Selected two-device plan was not applied")
        updated = await customer_repo.get_subscription(owner.id, order.subscription_id)
        if not updated or updated.status != "active" or updated.plan != plan:
            raise AssertionError("Cloned DB did not mark extra VPN active")
        if updated.expire_at != first_expiry:
            raise AssertionError("Cloned DB and panel expiry differ")
        print("  OK: disposable 2-device VPN created with 100 GB", flush=True)

        stage = "checking per-owner isolation and renaming"
        await customer_repo.rename_subscription(owner.id, target.id, "Test extra VPN")
        other = await db_repo.get_or_create_user(telegram_id + 1)
        if await customer_repo.get_subscription(other.id, target.id) is not None:
            raise AssertionError("Cross-account read unexpectedly succeeded")
        if await customer_repo.rename_subscription(other.id, target.id, "Wrong owner"):
            raise AssertionError("Cross-account rename unexpectedly succeeded")
        print("  OK: ownership and label isolation", flush=True)

        stage = "adding test traffic to synthetic VPN"
        _, _ = await grant_traffic_topup(owner.id, target.id)
        current = await provider.get_client(test_email)
        if not current or int(current.get("trafficLimitBytes") or -1) != 150 * 1024**3:
            raise AssertionError("150 GB limit was not applied after top-up")
        print("  OK: targeted +50 GB credited", flush=True)

        stage = "renewing test VPN without losing the traffic bonus"
        renewal = await db_repo.create_order(
            owner.id, plan, get_usd_price(plan), "usd",
            subscription_id=target.id, apply_promo=False,
        )
        _, renewed_expiry = await grant_paid_order(owner.id, telegram_id, renewal)
        current = await provider.get_client(test_email)
        if not current or int(current.get("trafficLimitBytes") or -1) != 150 * 1024**3:
            raise AssertionError("Renewal lost the ongoing +50 GB credit")
        if renewed_expiry <= first_expiry:
            raise AssertionError("Renewal did not extend the expiration date")
        print("  OK: renewal extended expiry, retained 150 GB", flush=True)

        stage = "resetting only synthetic test subscription link"
        reset_link = await reset_subscription_link(test_email)
        current = await provider.get_client(test_email)
        if not current or current.get("username") != test_email:
            raise AssertionError("Cannot find disposable VPN after link reset")
        new_short = current.get("shortUuid")
        if not new_short or new_short == old_short:
            raise AssertionError("Revoke did not rotate synthetic subscription token")
        if not reset_link.endswith("/" + new_short):
            raise AssertionError("Reset returned a different subscription token")
        if int(current.get("trafficLimitBytes") or -1) != 150 * 1024**3:
            raise AssertionError("Reset unintentionally changed traffic limit")
        print("  OK: link rotated without changing traffic limit", flush=True)

        stage = "checking synthetic primary was not modified"
        final_primary = await db_repo.get_subscription(owner.id)
        if (
            final_primary.id, final_primary.xui_email, final_primary.expire_at,
            final_primary.traffic_limit_gb
        ) != original_primary:
            raise AssertionError("New account operations changed synthetic primary")
        print("  OK: primary subscription unaffected", flush=True)
        success = True
    except Exception as exc:
        # Don't include API response bodies, URLs, credentials, or client data.
        print(f"  FAILED at '{stage}' ({type(exc).__name__})", flush=True)
    finally:
        if test_email:
            print("=== Removing the synthetic client from live Remnawave ===", flush=True)
            try:
                user = await provider._request(
                    "GET", f"/api/users/by-username/{quote(test_email, safe='')}"
                )
                if user:
                    if user.get("username") != test_email or user.get("id") is None:
                        raise RuntimeError("Cleanup target identity check failed")
                    await provider._request(
                        "DELETE", f"/api/users/{int(user['id'])}"
                    )
                remaining = await provider._request(
                    "GET", f"/api/users/by-username/{quote(test_email, safe='')}"
                )
                cleanup_ok = not bool(remaining)
                if cleanup_ok:
                    print("  OK: synthetic test client absent from live panel", flush=True)
            except Exception as exc:
                print(f"  CLEANUP FAILED: {type(exc).__name__}", flush=True)
            if not cleanup_ok:
                print(
                    f"  MANUAL CLEANUP REQUIRED: search exact username '{test_email}' "
                    "in Remnawave panel",
                    flush=True,
                )

    if not cleanup_ok:
        raise RuntimeError("Cleanup could not be confirmed; manual action required")
    if not success:
        raise RuntimeError("Live integration failed; test client was cleaned up")
    print("LIVE MULTI-SUB INTEGRATION PASSED; temporary panel client deleted.", flush=True)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--worker":
        if os.environ.get("ORION_LIVE_TEST_CONFIRM") != "YES":
            raise SystemExit("Worker safety check failed")
        asyncio.run(live_worker())
        return

    if len(sys.argv) != 3 or sys.argv[2] != MARKER:
        raise SystemExit(
            f"Usage: {Path(__file__).name} /absolute/path/snapshot.dump {MARKER}\n"
            "This script creates and deletes ONE synthetic client in the live Remnawave panel."
        )
    backup = Path(sys.argv[1])
    if not backup.is_absolute() or backup.suffix != ".dump" or not backup.is_file():
        raise SystemExit("Backup file not found")
    if not backup.stat().st_size:
        raise SystemExit("Backup file is empty")
    if not (Path.cwd() / ".env").is_file():
        raise SystemExit("Run from the production bot directory containing .env")

    container = "orion-multisub-live-" + secrets.token_hex(5)
    password = secrets.token_urlsafe(32)
    started = False
    try:
        print("=== Starting isolated PostgreSQL clone ===", flush=True)
        docker_env = os.environ.copy()
        docker_env["POSTGRES_PASSWORD"] = password
        run([
            "docker", "run", "--rm", "-d", "--name", container,
            "-e", "POSTGRES_PASSWORD",
            "-e", "POSTGRES_USER=orion_clone",
            "-e", "POSTGRES_DB=orion_clone",
            "-p", "127.0.0.1::5432", "postgres:16",
        ], env=docker_env, stdout=subprocess.DEVNULL)
        started = True

        address = subprocess.check_output(
            ["docker", "port", container, "5432/tcp"], text=True
        ).strip().splitlines()[0]
        port = int(address.rsplit(":", 1)[1])
        pg_env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        pg_env.update({
            "PGHOST": "127.0.0.1", "PGPORT": str(port),
            "PGUSER": "orion_clone", "PGPASSWORD": password,
            "PGDATABASE": "orion_clone",
        })
        for _ in range(45):
            status = subprocess.run(
                ["pg_isready", "-q"], env=pg_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if status.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Temporary PostgreSQL did not become ready")

        print("=== Restoring existing snapshot to clone ===", flush=True)
        run([
            "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
            "-d", "orion_clone", str(backup),
        ], env=pg_env)

        clone_url = URL.create(
            "postgresql+asyncpg",
            username="orion_clone", password=password,
            host="127.0.0.1", port=port, database="orion_clone",
        ).render_as_string(hide_password=False)

        script = "import asyncio; from app.database.engine import init_db; asyncio.run(init_db())"
        worker_env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        project_root = str(Path(__file__).resolve().parent.parent)
        worker_env.update({
            "PYTHONPATH": project_root,
            "BOT_TOKEN": "123456:LOCAL_INTEGRATION_ONLY",
            "DB_URL": clone_url,
            "ORION_DB_CLONE_ONLY": "YES",
            "ORION_LIVE_TEST_CONFIRM": "YES",
        })

        print("=== Migrating ONLY the cloned database ===", flush=True)
        run([sys.executable, "-c", script], env=worker_env, cwd=str(Path.cwd()))

        print("=== Real Remnawave test with synthetic username ===", flush=True)
        run(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            env=worker_env, cwd=str(Path.cwd()),
        )
        print("=== COMPLETE: temporary user removed, production DB untouched ===", flush=True)
    finally:
        if started:
            subprocess.run(
                ["docker", "stop", container], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print("Isolated PostgreSQL container stopped and removed.", flush=True)


if __name__ == "__main__":
    main()
