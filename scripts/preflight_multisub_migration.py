"""Safe, repeatable multi-subscription migration preflight.

Usage:
    python scripts/preflight_multisub_migration.py /absolute/path/to/snapshot.dump

Requires docker, pg_restore, pg_isready and local PostgreSQL 16 Docker image.
Restores from the provided dump into an ephemeral Docker container.
Never connects to the production database, panel or Telegram.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

TABLES = (
    "subscriptions",
    "orders",
    "monthly_traffic_quotas",
    "remnawave_migrations",
    "dealer_subscriptions",
)


def run(args: list[str], *, env: dict[str, str] | None = None, **kwargs) -> None:
    subprocess.run(args, env=env, check=True, **kwargs)


async def verify(clone_url: str, repo_dir: Path, env: dict[str, str]) -> None:
    engine = create_async_engine(clone_url, poolclass=NullPool)

    async def snapshot(reference_columns=None):
        hashes, columns = {}, {}
        async with engine.connect() as conn:
            for table in TABLES:
                if reference_columns is None:
                    column_names = list((await conn.execute(
                        text("""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = :table
                            ORDER BY ordinal_position
                        """), {"table": table},
                    )).scalars())
                else:
                    column_names = reference_columns[table]
                if not column_names:
                    raise RuntimeError(f"Missing table: {table}")
                columns[table] = column_names
                fields = ", ".join('"' + n.replace('"', '""') + '"' for n in column_names)
                rows = (await conn.execute(text(f'SELECT {fields} FROM "{table}"'))).all()
                serialized = sorted(
                    json.dumps(list(row), default=str, ensure_ascii=False)
                    for row in rows
                )
                digest = hashlib.sha256("\n".join(serialized).encode("utf-8")).hexdigest()
                hashes[table] = (len(rows), digest)
        return hashes, columns

    try:
        print("=== BEFORE: row counts and private fingerprints ===", flush=True)
        before, columns = await snapshot()
        for table, (count, _) in before.items():
            print(f"  {table}: {count}", flush=True)

        print("=== Applying the migration twice (clone only) ===", flush=True)
        script = "import asyncio; from app.database.engine import init_db; asyncio.run(init_db())"
        for index in (1, 2):
            print(f"  run #{index}", flush=True)
            run(
                [sys.executable, "-c", script],
                env=env,
                cwd=str(repo_dir),
            )

        print("=== Checking data preservation ===", flush=True)
        after, _ = await snapshot(columns)
        for table in TABLES:
            if before[table] != after[table]:
                raise RuntimeError(f"DATA CHANGED: {table}")
            print(f"  OK: {table}", flush=True)

        async with engine.connect() as conn:
            sub_columns = set((await conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'subscriptions'
            """))).scalars())
            order_columns = set((await conn.execute(text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'orders'
            """))).scalars())
            assert {"is_primary", "label", "plan", "status"} <= sub_columns
            assert "subscription_id" in order_columns

            primaries = await conn.scalar(text(
                "SELECT COUNT(*) FROM subscriptions WHERE is_primary IS TRUE"
            ))
            assert primaries == before["subscriptions"][0], "Legacy VPNs are not all primary"

            legacy_unique = await conn.scalar(text("""
                SELECT COUNT(*) FROM pg_constraint
                WHERE conrelid = 'subscriptions'::regclass
                  AND conname = 'subscriptions_user_id_key'
            """))
            assert legacy_unique == 0, "Legacy UNIQUE(user_id) was not dropped"

            index = (await conn.execute(text("""
                SELECT ix.indisunique, pg_get_expr(ix.indpred, ix.indrelid) AS predicate
                FROM pg_index ix
                JOIN pg_class c ON c.oid = ix.indexrelid
                WHERE c.relname = 'uq_subscriptions_user_primary'
                  AND ix.indrelid = 'subscriptions'::regclass
            """))).first()
            assert index is not None and index.indisunique and index.predicate

            print(f"  OK: {primaries} legacy VPNs preserved as primary", flush=True)
            print("  OK: old uniqueness removed, partial unique index present", flush=True)

        print("=== Testing second VPN on same owner (rollback-only) ===", flush=True)
        async with engine.connect() as conn:
            user_id = await conn.scalar(text(
                "SELECT user_id FROM subscriptions ORDER BY id LIMIT 1"
            ))
            if user_id is None:
                raise RuntimeError("No existing subscription to test")
            try:
                await conn.execute(text("""
                    INSERT INTO subscriptions (
                        user_id, xui_email, expire_at, is_primary, label, plan,
                        status, traffic_limit_gb, notified_3d, notified_1d, created_at
                    ) VALUES (
                        :uid, :email, NOW(), FALSE, 'preflight only', 'month1',
                        'pending', 0, FALSE, FALSE, NOW()
                    )
                """), {"uid": user_id, "email": f"cs-preflight-{secrets.token_hex(12)}"})
                count = await conn.scalar(text(
                    "SELECT COUNT(*) FROM subscriptions WHERE user_id = :uid"
                ), {"uid": user_id})
                assert count == 2, "Second personal VPN was not accepted"
                print("  OK: two subscriptions can share one owner", flush=True)
            finally:
                # No test row persists even in the temporary database.
                await conn.rollback()

        final, _ = await snapshot(columns)
        assert final == before, "Rollback of the test row did not preserve data"
        print("SUCCESS: two migration runs, original rows preserved, extra VPN works.", flush=True)
    finally:
        await engine.dispose()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: preflight_multisub_migration.py /absolute/path/snapshot.dump")

    backup = Path(sys.argv[1])
    if not backup.is_absolute() or not backup.is_file() or backup.stat().st_size == 0:
        raise SystemExit("Missing non-empty absolute-path backup file")
    if not backup.name.endswith(".dump"):
        raise SystemExit("Expected a PostgreSQL custom-format .dump file")

    repo_dir = Path(__file__).resolve().parent.parent
    container = "orion-multisub-check-" + secrets.token_hex(5)
    password = secrets.token_urlsafe(32)
    started = False

    try:
        print("=== Starting an isolated PostgreSQL 16 clone ===", flush=True)
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

        bound = subprocess.check_output(
            ["docker", "port", container, "5432/tcp"], text=True
        ).strip().splitlines()[0]
        port = int(bound.rsplit(":", 1)[1])
        env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        env.update({
            "PGHOST": "127.0.0.1",
            "PGPORT": str(port),
            "PGUSER": "orion_clone",
            "PGPASSWORD": password,
            "PGDATABASE": "orion_clone",
        })
        for _ in range(45):
            result = subprocess.run(
                ["pg_isready", "-q"], env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Clone PostgreSQL failed its readiness check")

        print("=== Restoring existing backup into the clone ===", flush=True)
        run([
            "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
            "-d", "orion_clone", str(backup),
        ], env=env)

        clone_url = URL.create(
            "postgresql+asyncpg", username="orion_clone", password=password,
            host="127.0.0.1", port=port, database="orion_clone",
        ).render_as_string(hide_password=False)
        migration_env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
        migration_env.update({
            "DB_URL": clone_url,
            "BOT_TOKEN": "123456:LOCAL_PREFLIGHT_ONLY",
        })

        # One loop owns every asyncpg connection from start to finish.
        asyncio.run(verify(clone_url, repo_dir, migration_env))
    finally:
        if started:
            subprocess.run(
                ["docker", "stop", container], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print("Isolated PostgreSQL container stopped and removed.", flush=True)


if __name__ == "__main__":
    main()
