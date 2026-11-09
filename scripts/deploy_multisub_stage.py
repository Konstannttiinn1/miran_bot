"""Controlled stage-0 rollout of customer multi-subscriptions on Orion VPN.

Opt-in live deployment. Stops miran-bot.service, makes fresh backup, deploys
the exact code previously tested, migrates the live PostgreSQL schema, and
restarts with MAX_ADDITIONAL_SUBSCRIPTIONS=0. On failure, restores the old
code/.env and the old UNIQUE(user_id) compatibility constraint only when safe.

No panel API calls are made by this script. The restarted bot resumes normal
production operations. Review the rollback output if any step fails.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

PRODUCTION = Path("/root/miran_bot")
REVIEW = Path("/root/miran-multisub-review-20261109")
SERVICE = "miran-bot.service"
OLD = "0413fa85447108ecc3f2a3a8dd2e8c3d43e46c0b"
NEW = "96018a743b66f0129c224dcb353727edb48f88d5"
BRANCH = "feat/customer-multisub-cabinet-checkout-20261109"
LOCK = Path("/run/lock/orion-multisub-rollout.lock")


def command(args, *, cwd=None, env=None, capture=False):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def git(*args, capture=False):
    return command(["git", "-C", str(PRODUCTION), *args], capture=capture)


def service_active():
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def git_head():
    return git("rev-parse", "HEAD", capture=True).stdout.strip()


def fresh_pg_environment():
    # Use the production .env file, not the review worktree's settings.
    config = dotenv_values(PRODUCTION / ".env")
    raw = os.environ.get("DB_URL") or config.get("DB_URL")
    if not raw:
        raise RuntimeError("DB_URL missing from production configuration")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Production database must be PostgreSQL")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["PGDATABASE"] = url.database
    if url.host:
        env["PGHOST"] = url.host
    if url.port:
        env["PGPORT"] = str(url.port)
    if url.username:
        env["PGUSER"] = url.username
    if url.password:
        env["PGPASSWORD"] = url.password
    if url.query.get("sslmode"):
        env["PGSSLMODE"] = url.query["sslmode"]
    return raw, env


async def inspect_db(url, *, migrated: bool = False):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            counts = (await conn.execute(text(
                "SELECT (SELECT COUNT(*) FROM subscriptions) AS subscriptions, "
                "(SELECT COUNT(*) FROM orders) AS orders"
            ))).one()
            result = (int(counts.subscriptions), int(counts.orders))
            if migrated:
                cols = set((await conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'subscriptions'
                """))).scalars())
                if not {"is_primary", "label", "plan", "status"}.issubset(cols):
                    raise RuntimeError("Migration left required columns missing")
                extra = await conn.scalar(text(
                    "SELECT COUNT(*) FROM subscriptions WHERE is_primary IS FALSE"
                ))
                primary = await conn.scalar(text(
                    "SELECT COUNT(*) FROM subscriptions WHERE is_primary IS TRUE"
                ))
                if extra != 0 or primary != result[0]:
                    raise RuntimeError("Unexpected secondary or missing primary VPN rows")
                old_unique = await conn.scalar(text("""
                    SELECT COUNT(*) FROM pg_constraint
                    WHERE conrelid = 'subscriptions'::regclass
                      AND conname = 'subscriptions_user_id_key'
                """))
                if old_unique:
                    raise RuntimeError("Old unique constraint remained after migration")
            await conn.rollback()
            return result
    finally:
        await engine.dispose()


async def restore_old_uniqueness(url):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            columns = set((await conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'subscriptions'
            """))).scalars())
            if "is_primary" in columns:
                secondary = await conn.scalar(text(
                    "SELECT COUNT(*) FROM subscriptions WHERE is_primary IS FALSE"
                ))
                if secondary:
                    raise RuntimeError(
                        "Cannot roll back old code: extra VPNs already exist"
                    )
                missing_primary = await conn.scalar(text(
                    "SELECT COUNT(*) FROM subscriptions WHERE is_primary IS NOT TRUE"
                ))
                if missing_primary:
                    raise RuntimeError("Some primary flags are missing: manual rollback required")
            duplicates = await conn.scalar(text("""
                SELECT COUNT(*) FROM (
                    SELECT user_id FROM subscriptions
                    GROUP BY user_id HAVING COUNT(*) > 1
                ) AS duplicates
            """))
            if duplicates:
                raise RuntimeError(
                    "Cannot restore UNIQUE(user_id): duplicate owners"
                )
            exists = await conn.scalar(text("""
                SELECT COUNT(*) FROM pg_constraint
                WHERE conrelid = 'subscriptions'::regclass
                  AND conname = 'subscriptions_user_id_key'
            """))
            if not exists:
                await conn.execute(text(
                    "ALTER TABLE subscriptions "
                    "ADD CONSTRAINT subscriptions_user_id_key UNIQUE (user_id)"
                ))
    finally:
        await engine.dispose()


def set_stage_zero():
    env_file = PRODUCTION / ".env"
    content = env_file.read_text()
    content = "".join(
        line for line in content.splitlines(keepends=True)
        if not re.match(r"^\s*MAX_ADDITIONAL_SUBSCRIPTIONS\s*=", line, flags=re.I)
    )
    if content and not content.endswith("\n"):
        content += "\n"
    content += "MAX_ADDITIONAL_SUBSCRIPTIONS=0\n"
    env_file.write_text(content)
    os.chmod(env_file, 0o600)


def confirm_stage_setting():
    # Confirm pydantic-settings resolves the staged configuration.
    command(
        [str(PRODUCTION / "venv/bin/python"), "-c",
         "from app.config import settings; "
         "assert settings.max_additional_subscriptions == 0"],
        cwd=PRODUCTION,
    )


def inspect_running_process_setting():
    pid = int(command([
        "systemctl", "show", SERVICE, "--property=MainPID", "--value",
    ], capture=True).stdout.strip())
    if pid <= 0:
        raise RuntimeError("No running service PID")
    payload = (Path("/proc") / str(pid) / "environ").read_bytes()
    for item in payload.split(b"\x00"):
        if item.startswith(b"MAX_ADDITIONAL_SUBSCRIPTIONS="):
            if item != b"MAX_ADDITIONAL_SUBSCRIPTIONS=0":
                raise RuntimeError(
                    "Service environment overrides staged extra VPN limit"
                )
    return pid


def rollback(old_url, env_backup):
    print("=== Rolling back to the old bot (no database restore) ===", flush=True)
    failures = []
    try:
        command(["systemctl", "stop", SERVICE])
    except Exception:
        failures.append("Could not stop service")
    try:
        if git_head() != OLD:
            git("switch", "--detach", OLD)
        shutil.copy2(env_backup, PRODUCTION / ".env")
        os.chmod(PRODUCTION / ".env", 0o600)
    except Exception:
        failures.append("Could not restore old code or .env")

    # Mandatory before starting the old code: its ON CONFLICT(user_id) depends
    # on the original full UNIQUE constraint, not the new partial index.
    try:
        asyncio.run(restore_old_uniqueness(old_url))
        print("Old UNIQUE(user_id) compatibility restored.", flush=True)
    except Exception:
        failures.append("Could not safely restore UNIQUE(user_id)")

    if not failures and git_head() == OLD:
        try:
            command(["systemctl", "start", SERVICE])
            if not service_active():
                raise RuntimeError("Old bot is not active")
            print("OLD BOT RESTORED AND ACTIVE", flush=True)
        except Exception:
            failures.append("Old service did not restart")

    if failures:
        print("URGENT: automatic rollback incomplete. Service may be stopped.", flush=True)
        for message in failures:
            print(" - " + message, flush=True)
        print("Keep the backup folder; inspect service/DB before further actions.", flush=True)
    return not failures


def main():
    if sys.argv[1:] != ["--apply-stage-zero"]:
        raise SystemExit(
            "This deploys a LIVE production bot and migrates the LIVE database. "
            "Use --apply-stage-zero to proceed."
        )
    if os.geteuid() != 0:
        raise SystemExit("Root is required to manage the existing bot service")

    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another rollout process holds the deployment lock")

    print("=== Guard checks before stopping production ===", flush=True)
    if not service_active():
        raise RuntimeError("The production bot is not active")
    working_dir = command([
        "systemctl", "show", SERVICE, "--property=WorkingDirectory", "--value",
    ], capture=True).stdout.strip()
    if working_dir != str(PRODUCTION):
        raise RuntimeError("Unexpected systemd working directory")
    if git_head() != OLD:
        raise RuntimeError("Production Git SHA changed unexpectedly")
    if git("status", "--porcelain", "--untracked-files=no", capture=True).stdout.strip():
        raise RuntimeError("Production repo has modified tracked files")
    if command([
        "git", "-C", str(REVIEW), "rev-parse", "HEAD",
    ], capture=True).stdout.strip() != NEW:
        raise RuntimeError("Review code not pinned to the 251-test commit")
    if not (PRODUCTION / ".env").is_file():
        raise RuntimeError("Missing production .env")
    tracked_env = subprocess.run(
        ["git", "-C", str(PRODUCTION), "ls-files", "--error-unmatch", ".env"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if tracked_env:
        raise RuntimeError("Production .env is tracked: staged edit would affect Git checkout")
    if not (PRODUCTION / "venv/bin/python").is_file():
        raise RuntimeError("Missing production virtualenv")

    # Already fetched and reviewed/tested code; no network needed after stop.
    git("cat-file", "-e", f"{NEW}^{{commit}}")
    command([
        str(PRODUCTION / "venv/bin/python"), "-m", "compileall",
        "-q", "app", "main.py",
    ], cwd=REVIEW)
    old_url, pg_env = fresh_pg_environment()

    # Prepare a recoverable configuration copy BEFORE stopping the service.
    # A failure creating the folder must never leave production stopped.
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    backup_dir = Path(f"/root/miran-multisub-rollout-{stamp}-{os.getpid()}")
    backup_dir.mkdir(mode=0o700)
    env_backup = backup_dir / "production.env.before"
    dump = backup_dir / "production.before.dump"
    shutil.copy2(PRODUCTION / ".env", env_backup)
    os.chmod(env_backup, 0o600)
    print("Backups directory:", backup_dir, flush=True)

    try:
        print("=== Stopping the existing bot ===", flush=True)
        command(["systemctl", "stop", SERVICE])
        if service_active():
            raise RuntimeError("Service failed to stop; do not migrate DB")
        before = asyncio.run(inspect_db(old_url))
        print(f"Snapshot baseline: subscriptions={before[0]}, orders={before[1]}", flush=True)
        print("=== Fresh PostgreSQL backup, while bot is stopped ===", flush=True)
        command([
            "pg_dump", "-Fc", "--no-owner", "--no-acl",
            "-f", str(dump),
        ], env=pg_env)
        if not dump.is_file() or dump.stat().st_size < 100:
            raise RuntimeError("PostgreSQL backup is missing/empty")
        command(["pg_restore", "--list", str(dump)],
                capture=True)

        print("=== Switching to tested code and staging limit 0 ===", flush=True)
        git("switch", "--detach", NEW)
        set_stage_zero()
        confirm_stage_setting()

        print("=== Migrating LIVE PostgreSQL ===", flush=True)
        command([
            str(PRODUCTION / "venv/bin/python"), "-c",
            "import asyncio; from app.database.engine import init_db; asyncio.run(init_db())",
        ], cwd=PRODUCTION)
        after = asyncio.run(inspect_db(old_url, migrated=True))
        if after != before:
            raise RuntimeError("Subscription/order counts changed during migration")
        print(f"Data preserved: subscriptions={after[0]}, orders={after[1]}", flush=True)

        print("=== Starting bot with extra subscriptions disabled ===", flush=True)
        command(["systemctl", "start", SERVICE])
        for i in range(8):
            time.sleep(1)
            if not service_active():
                raise RuntimeError("New service stopped shortly after startup")
            inspect_running_process_setting()
        if git_head() != NEW:
            raise RuntimeError("Production source SHA unexpectedly changed")

        print("=== STAGE ZERO DEPLOYED SUCCESSFULLY ===", flush=True)
        print("Running commit:", git_head()[:12], flush=True)
        print("Additional personal subscriptions: 0 (staging lock)", flush=True)
        print("Backup folder:", backup_dir, flush=True)
        print("Old code and backup retained for rollback.", flush=True)
    except BaseException as exc:
        # Do not dump SQL text, environment variables or provider credentials.
        print(f"DEPLOYMENT FAILED ({type(exc).__name__})", flush=True)
        safe = rollback(old_url, env_backup)
        if safe:
            raise SystemExit("New version NOT deployed; previous bot restored")
        raise SystemExit("Rollback incomplete: manual intervention required")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
