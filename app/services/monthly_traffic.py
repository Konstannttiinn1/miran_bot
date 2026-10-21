"""Monthly Remnawave quotas and traffic credits valid for one reset cycle.

Remnawave resets usage using MONTH_ROLLING. A durable bonus ledger lets the
bot restore the 100 GB limit at that reset, rather than renewing paid extras.
PostgreSQL row locks serialize renewals, credits and background reconciliation.
"""
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.database.engine import async_session_factory
from app.database.models import DealerSubscription, MonthlyTrafficQuota, Subscription, utcnow

GB = 1024 ** 3
BASE_BYTES = 100 * GB
log = logging.getLogger(__name__)
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def parse_time(value) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _marker(user: dict) -> datetime:
    marker = parse_time(user.get("lastTrafficResetAt") or user.get("createdAt"))
    if marker is None:
        raise RuntimeError("Panel did not provide the traffic cycle timestamp")
    return marker


async def is_monthly(email: str) -> bool:
    async with async_session_factory() as session:
        return bool(await session.scalar(select(MonthlyTrafficQuota.initialized).where(
            MonthlyTrafficQuota.email == email,
        )))


async def sync_expiry(email: str, user: dict) -> None:
    expires = parse_time(user.get("expireAt"))
    if expires is not None:
        async with async_session_factory() as session:
            await session.execute(update(MonthlyTrafficQuota).where(
                MonthlyTrafficQuota.email == email,
            ).values(valid_until=expires))
            await session.commit()


def _active(user: dict) -> bool:
    expires = parse_time(user.get("expireAt"))
    return bool(expires and expires > utcnow() and user.get("status") != "DISABLED")


def _status_patch(user: dict, limit: int) -> dict:
    usage = user.get("usedTrafficBytes")
    if usage is None:
        usage = (user.get("userTraffic") or {}).get("usedTrafficBytes", 0)
    if user.get("status") == "LIMITED" and _active(user) and int(usage or 0) < limit:
        return {"status": "ACTIVE"}
    return {}


@asynccontextmanager
async def _locked(email: str, *, create: bool = False):
    async with _locks[email]:
        if create:
            async with async_session_factory() as session:
                dialect = session.get_bind().dialect.name
                insert = pg_insert if dialect == "postgresql" else sqlite_insert
                await session.execute(
                    insert(MonthlyTrafficQuota).values(email=email, initialized=False, extra_bytes=0)
                    .on_conflict_do_nothing(index_elements=[MonthlyTrafficQuota.email])
                )
                await session.commit()
        async with async_session_factory() as session:
            async with session.begin():
                quota = await session.scalar(
                    select(MonthlyTrafficQuota).where(MonthlyTrafficQuota.email == email).with_for_update()
                )
                yield session, quota


def _sync_cycle(quota: MonthlyTrafficQuota, user: dict) -> bool:
    marker = _marker(user)
    if quota.last_reset_at is None or marker > quota.last_reset_at:
        quota.extra_bytes = 0
        quota.last_reset_at = marker
        return True
    return False


async def _record_limit(session, email: str, limit_bytes: int) -> None:
    for model in (Subscription, DealerSubscription):
        await session.execute(
            update(model).where(model.xui_email == email)
            .values(traffic_limit_gb=limit_bytes // GB)
        )


async def grant_monthly(provider, email: str, days: int, limit_ip: int = 1) -> datetime:
    """Sell time; an active monthly cycle is never reset by advance renewal."""
    from app.services.remnawave_api import RemnawaveClient
    if not isinstance(provider, RemnawaveClient):
        raise RuntimeError("Monthly subscriptions require the configured Remnawave provider")
    if days not in (30, 60, 90):
        raise ValueError("Unsupported monthly duration")

    async with _locked(email, create=True) as (session, quota):
        user = await provider.get_client(email)
        now = utcnow()
        old_expiry = parse_time((user or {}).get("expireAt"))
        expires = max(old_expiry or now, now) + timedelta(days=days)
        fresh_cycle = not quota.initialized or not old_expiry or old_expiry <= now
        if user and user.get("status") == "DISABLED":
            raise RuntimeError("Cannot sell a disabled VPN client")
        if fresh_cycle:
            quota.extra_bytes = 0
        elif user:
            _sync_cycle(quota, user)
        limit = BASE_BYTES + quota.extra_bytes
        payload = {
            "expireAt": expires.isoformat() + "Z",
            "trafficLimitBytes": limit,
            "trafficLimitStrategy": "MONTH_ROLLING",
            "hwidDeviceLimit": max(0, int(limit_ip)),
        }
        if user:
            # Start a genuinely new cycle after a trial, old product or expiry.
            # Reset first; a failed request never silently falls through as success.
            if fresh_cycle:
                reset = await provider._request(
                    "POST", f"/api/users/{provider._user_id(user)}/actions/reset-traffic",
                )
                if not reset:
                    raise RuntimeError("Panel did not confirm the traffic reset")
                user = reset
                quota.last_reset_at = _marker(user)
                payload["status"] = "ACTIVE"
            else:
                payload.update(_status_patch(user, limit))
            updated = await provider._request(
                "PATCH", "/api/users", json={"id": provider._user_id(user), **payload},
            )
        else:
            payload.update(username=email, status="ACTIVE", activeInternalSquads=[provider.squad_uuid])
            if email.isdigit():
                payload["telegramId"] = int(email)
            updated = await provider._request("POST", "/api/users", json=payload)
            if not updated.get("shortUuid"):
                raise RuntimeError("Panel did not confirm subscription creation")
            await provider._save_mapping(email, updated)
        if not updated.get("shortUuid"):
            raise RuntimeError("Panel did not confirm the monthly subscription")
        if (not fresh_cycle and quota.last_reset_at is not None
                and _marker(updated) > quota.last_reset_at):
            # A native reset may race the renewal PATCH. Do not retain the old
            # bonus under the new cycle timestamp.
            quota.extra_bytes = 0
            limit = BASE_BYTES
            updated = await provider._request("PATCH", "/api/users", json={
                "id": provider._user_id(updated), "trafficLimitBytes": limit,
                **_status_patch(updated, limit),
            })
            if not updated.get("shortUuid"):
                raise RuntimeError("Panel did not confirm the renewed allowance")
        quota.initialized = True
        quota.valid_until = expires
        quota.last_reset_at = _marker(updated)
        await _record_limit(session, email, limit)
        return expires


async def add_monthly_traffic(provider, email: str, extra_gb: int) -> int | None:
    """Return the new GB limit, or None for a legacy client."""
    async with _locked(email) as (session, quota):
        if quota is None or not quota.initialized:
            return None
        user = await provider.get_client(email)
        if not user or not _active(user):
            raise RuntimeError("Traffic top-up requires an active subscription")
        _sync_cycle(quota, user)
        quota.extra_bytes += int(extra_gb) * GB
        limit = BASE_BYTES + quota.extra_bytes
        updated = await provider._request(
            "PATCH", "/api/users", json={
                "id": provider._user_id(user), "trafficLimitBytes": limit,
                **_status_patch(user, limit),
            },
        )
        if not updated.get("shortUuid"):
            raise RuntimeError("Panel did not confirm traffic credit")
        if quota.last_reset_at is not None and _marker(updated) > quota.last_reset_at:
            # The new purchase belongs to the new cycle if reset raced credit.
            quota.extra_bytes = int(extra_gb) * GB
            quota.last_reset_at = _marker(updated)
            limit = BASE_BYTES + quota.extra_bytes
            updated = await provider._request("PATCH", "/api/users", json={
                "id": provider._user_id(updated), "trafficLimitBytes": limit,
                **_status_patch(updated, limit),
            })
            if not updated.get("shortUuid"):
                raise RuntimeError("Panel did not confirm the new-cycle credit")
        quota.valid_until = parse_time(updated.get("expireAt"))
        await _record_limit(session, email, limit)
        return limit // GB


async def reconcile_one(provider, email: str) -> None:
    async with _locked(email) as (session, quota):
        if quota is None or not quota.initialized or not quota.extra_bytes:
            return
        user = await provider.get_client(email)
        if not user or not _active(user):
            return
        if not _sync_cycle(quota, user):
            return
        updated = await provider._request(
            "PATCH", "/api/users", json={
                "id": provider._user_id(user), "trafficLimitBytes": BASE_BYTES,
                **_status_patch(user, BASE_BYTES),
            },
        )
        if not updated.get("shortUuid"):
            raise RuntimeError("Panel did not confirm monthly bonus expiry")
        await _record_limit(session, email, BASE_BYTES)


async def reconcile_monthly_traffic() -> None:
    from app.services.vpn_provider import get_vpn_provider, provider_name
    if provider_name() != "remnawave":
        return
    async with async_session_factory() as session:
        emails = list((await session.scalars(
            select(MonthlyTrafficQuota.email).where(
                MonthlyTrafficQuota.initialized.is_(True),
                MonthlyTrafficQuota.extra_bytes > 0,
                MonthlyTrafficQuota.valid_until > utcnow(),
            )
        )).all())
    if not emails:
        return
    provider = get_vpn_provider()
    for email in emails:
        try:
            await reconcile_one(provider, email)
        except Exception:
            log.exception("Monthly traffic reconciliation failed for %s", email)


async def monthly_traffic_loop() -> None:
    while True:
        try:
            await reconcile_monthly_traffic()
        except Exception:
            log.exception("Monthly traffic loop failed")
        await asyncio.sleep(30)
