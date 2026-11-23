"""Dealer-editable commercial catalogue. Legacy checkout remains isolated in phase 1.

All writes require an active dealer/admin and use an optimistic revision check.
No panel calls or financial mutations are performed here.
"""
from __future__ import annotations

from secrets import token_hex

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.database.models import CommercialTariff, CommercialTariffChange, User, utcnow
from app.repositories import db_repo
from app.utils.tariffs import PLANS, get_toman_price


class CatalogDenied(PermissionError):
    pass


class CatalogConflict(RuntimeError):
    pass


class CatalogInvalid(ValueError):
    pass


STATUSES = ("draft", "published", "paused", "archived")
EDITABLE = {"name_fa", "period_days", "devices", "traffic_gb", "retail_toman"}


def _valid_name(value: object) -> str:
    if not isinstance(value, str):
        raise CatalogInvalid("Invalid name")
    name = " ".join(value.split()).strip()
    if not 1 <= len(name) <= 80 or any(ord(ch) < 32 for ch in name):
        raise CatalogInvalid("Name must contain 1–80 printable characters")
    return name


def _validate_fields(changes: dict) -> dict:
    if not changes or not set(changes).issubset(EDITABLE):
        raise CatalogInvalid("Unsupported catalogue field")
    result = {}
    for key, value in changes.items():
        if key == "name_fa":
            result[key] = _valid_name(value)
        else:
            if not isinstance(value, int) or isinstance(value, bool):
                raise CatalogInvalid("Whole positive numbers are required")
            low, high = {
                "period_days": (1, 365),
                "devices": (1, 20),
                "traffic_gb": (1, 10000),
                "retail_toman": (1, 1_000_000_000_000),
            }[key]
            if not low <= value <= high:
                raise CatalogInvalid(f"{key} must be between {low} and {high}")
            result[key] = value
    return result


def supports_issuance(tariff: CommercialTariff) -> bool:
    """Only configurations already supported by grant_monthly may publish.

    This does NOT activate retail checkout. Phase 2 must implement quote snapshots.
    """
    return (
        tariff.period_days in (30, 60, 90)
        and tariff.devices in (1, 2, 3)
        and tariff.traffic_gb == 100
        and bool(settings.remnawave_squad_uuid.strip())
        and tariff.internal_squad_uuid == settings.remnawave_squad_uuid.strip()
        and tariff.retail_toman > 0
    )


def _snapshot(t: CommercialTariff) -> dict:
    return {
        "name_fa": t.name_fa,
        "period_days": t.period_days,
        "devices": t.devices,
        "traffic_gb": t.traffic_gb,
        "retail_toman": t.retail_toman,
        "internal_squad_uuid": t.internal_squad_uuid,
        "status": t.status,
        "revision": t.revision,
    }


async def _check_actor(session, actor_tg: int) -> None:
    actor = await session.scalar(select(User).where(User.telegram_id == actor_tg))
    if actor is None or (actor.is_blocked and actor_tg not in settings.admin_list):
        raise CatalogDenied("Access denied")
    if actor_tg not in settings.admin_list and actor.role != "dealer":
        raise CatalogDenied("Only an administrator or dealer can edit the catalogue")


async def create_tariff(
    actor_tg: int, *, name_fa: str, period_days: int,
    devices: int, traffic_gb: int, retail_toman: int,
) -> CommercialTariff:
    values = _validate_fields({
        "name_fa": name_fa, "period_days": period_days,
        "devices": devices, "traffic_gb": traffic_gb,
        "retail_toman": retail_toman,
    })
    async with db_repo.async_session_factory() as session:
        async with session.begin():
            await _check_actor(session, actor_tg)
            tariff = CommercialTariff(
                sku="cat-" + token_hex(10),
                internal_squad_uuid=settings.remnawave_squad_uuid.strip(),
                created_by_tg=actor_tg, updated_by_tg=actor_tg,
                status="draft", **values,
            )
            session.add(tariff)
            await session.flush()
            session.add(CommercialTariffChange(
                tariff_id=tariff.id, actor_tg=actor_tg, action="create",
                before=None, after=_snapshot(tariff),
            ))
        return tariff


async def get_tariff(actor_tg: int, tariff_id: int) -> CommercialTariff | None:
    async with db_repo.async_session_factory() as session:
        await _check_actor(session, actor_tg)
        return await session.get(CommercialTariff, tariff_id)


async def list_tariffs(
    actor_tg: int, *, page: int = 0, per_page: int = 6,
) -> tuple[list[CommercialTariff], int]:
    page = max(0, page)
    per_page = max(1, min(10, per_page))
    async with db_repo.async_session_factory() as session:
        await _check_actor(session, actor_tg)
        total = await session.scalar(select(func.count(CommercialTariff.id)))
        rows = await session.scalars(
            select(CommercialTariff).order_by(
                CommercialTariff.status.asc(), CommercialTariff.id.desc(),
            ).offset(page * per_page).limit(per_page)
        )
        return list(rows.all()), int(total or 0)


async def counts(actor_tg: int) -> dict[str, int]:
    async with db_repo.async_session_factory() as session:
        await _check_actor(session, actor_tg)
        rows = await session.execute(
            select(CommercialTariff.status, func.count(CommercialTariff.id))
            .group_by(CommercialTariff.status)
        )
        count = {s: 0 for s in STATUSES}
        for status, amount in rows:
            count[status] = int(amount)
        count["all"] = sum(count.values())
        return count


async def update_tariff(
    actor_tg: int, tariff_id: int, revision: int, *,
    action: str, changes: dict,
) -> CommercialTariff:
    if action not in {"edit", "publish", "pause", "resume", "archive"}:
        raise CatalogInvalid("Unknown action")
    if action == "edit":
        changes = _validate_fields(changes)
    else:
        new_status = {
            "publish": "published",
            "pause": "paused",
            "resume": "published",
            "archive": "archived",
        }[action]
        if changes:
            raise CatalogInvalid("Status operation cannot edit catalogue parameters")
        changes = {"status": new_status}

    async with db_repo.async_session_factory() as session:
        async with session.begin():
            await _check_actor(session, actor_tg)
            item = await session.get(CommercialTariff, tariff_id)
            if item is None or item.status == "archived":
                raise CatalogInvalid("Tariff unavailable")
            if revision != item.revision:
                raise CatalogConflict("Tariff was changed; reopen it")
            if action == "publish" and item.status != "draft":
                raise CatalogInvalid("Only a draft can be published")
            if action == "pause" and item.status != "published":
                raise CatalogInvalid("Only a published product can be paused")
            if action == "resume" and item.status != "paused":
                raise CatalogInvalid("Only a paused product can be resumed")
            if action in {"publish", "resume"} and not supports_issuance(item):
                raise CatalogInvalid("This product cannot be published until issuance supports its parameters")
            before = _snapshot(item)
            changed = await session.execute(
                update(CommercialTariff)
                .where(
                    CommercialTariff.id == tariff_id,
                    CommercialTariff.revision == revision,
                    CommercialTariff.status != "archived",
                )
                .values(**changes, revision=revision + 1,
                        updated_by_tg=actor_tg, updated_at=utcnow())
            )
            if changed.rowcount != 1:
                raise CatalogConflict("Tariff was changed concurrently")
            await session.refresh(item)
            session.add(CommercialTariffChange(
                tariff_id=tariff_id, actor_tg=actor_tg, action=action,
                before=before, after=_snapshot(item),
            ))
        return item


async def clone_tariff(actor_tg: int, tariff_id: int) -> CommercialTariff:
    async with db_repo.async_session_factory() as session:
        await _check_actor(session, actor_tg)
        origin = await session.get(CommercialTariff, tariff_id)
        if origin is None:
            raise CatalogInvalid("Tariff unavailable")
        values = _snapshot(origin)
    return await create_tariff(
        actor_tg,
        name_fa=values["name_fa"][:70] + " (کپی)",
        period_days=values["period_days"], devices=values["devices"],
        traffic_gb=values["traffic_gb"], retail_toman=values["retail_toman"],
    )


async def import_legacy(actor_tg: int) -> int:
    """Create draft copies of nine current products; never overwrite dealer edits."""
    from app.utils.tariffs import get_tariff
    added = 0
    async with db_repo.async_session_factory() as session:
        async with session.begin():
            await _check_actor(session, actor_tg)
            dialect = session.bind.dialect.name
            if dialect == "postgresql":
                stmt_factory = pg_insert
            elif dialect == "sqlite":
                stmt_factory = sqlite_insert
            else:
                raise CatalogInvalid(f"Unsupported database: {dialect}")
            for key, tariff in sorted(
                PLANS.items(),
                key=lambda it: (it[1].get("months", 0), it[1].get("devices", 0)),
            ):
                if not tariff.get("monthly"):
                    continue
                new = dict(
                    sku="legacy-" + key, name_fa=f"اشتراک {tariff['months']} ماهه · {tariff['devices']} دستگاه",
                    period_days=tariff["days"], devices=tariff["devices"],
                    traffic_gb=tariff["traffic_gb"], retail_toman=get_toman_price(key),
                    internal_squad_uuid=settings.remnawave_squad_uuid.strip(),
                    status="draft", revision=1, created_by_tg=actor_tg, updated_by_tg=actor_tg,
                )
                inserted = await session.scalar(
                    stmt_factory(CommercialTariff).values(**new)
                    .on_conflict_do_nothing(index_elements=[CommercialTariff.sku])
                    .returning(CommercialTariff.id)
                )
                if inserted is not None:
                    added += 1
                    session.add(CommercialTariffChange(
                        tariff_id=inserted, actor_tg=actor_tg, action="import_legacy",
                        before=None, after={**new, "revision": 1},
                    ))
    return added
