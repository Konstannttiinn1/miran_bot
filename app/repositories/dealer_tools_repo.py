from datetime import datetime

from sqlalchemy import func, select

from app.database.engine import async_session_factory
from app.database.models import DealerTest, PromoCode, PromoRedemption, utcnow


async def create_dealer_test(
    dealer_id: int,
    client_name: str,
    xui_email: str,
    expire_at: datetime,
    traffic_gb: int,
) -> DealerTest:
    async with async_session_factory() as session:
        test = DealerTest(
            dealer_id=dealer_id,
            client_name=client_name,
            xui_email=xui_email,
            expire_at=expire_at,
            traffic_limit_gb=traffic_gb,
            status="active",
        )
        session.add(test)
        await session.commit()
        await session.refresh(test)
        return test


async def list_dealer_tests(
    dealer_id: int,
    page: int = 0,
    per_page: int = 5,
) -> tuple[list[DealerTest], int]:
    async with async_session_factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(DealerTest).where(
                DealerTest.dealer_id == dealer_id,
                DealerTest.status != "deleted",
            )
        )
        result = await session.execute(
            select(DealerTest)
            .where(
                DealerTest.dealer_id == dealer_id,
                DealerTest.status != "deleted",
            )
            .order_by(DealerTest.created_at.desc())
            .offset(max(0, page) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), int(total or 0)


async def get_dealer_test(dealer_id: int, test_id: int) -> DealerTest | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DealerTest).where(
                DealerTest.id == test_id,
                DealerTest.dealer_id == dealer_id,
                DealerTest.status != "deleted",
            )
        )
        return result.scalar_one_or_none()


async def update_dealer_test_expiry(
    dealer_id: int,
    test_id: int,
    expire_at: datetime,
) -> bool:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DealerTest)
            .where(DealerTest.id == test_id, DealerTest.dealer_id == dealer_id)
            .with_for_update()
        )
        test = result.scalar_one_or_none()
        if test is None or test.status != "active":
            return False
        test.expire_at = expire_at
        await session.commit()
        return True


async def set_dealer_test_status(
    dealer_id: int,
    test_id: int,
    status: str,
) -> bool:
    if status not in {"active", "disabled", "deleted"}:
        raise ValueError("invalid test status")
    async with async_session_factory() as session:
        result = await session.execute(
            select(DealerTest)
            .where(DealerTest.id == test_id, DealerTest.dealer_id == dealer_id)
            .with_for_update()
        )
        test = result.scalar_one_or_none()
        if test is None or test.status == "deleted":
            return False
        test.status = status
        await session.commit()
        return True


async def create_promo(
    dealer_id: int,
    code: str,
    kind: str,
    days: int,
    traffic_gb: int,
    expire_at: datetime,
    max_uses: int,
) -> PromoCode:
    if kind not in {"days", "days_traffic"}:
        raise ValueError("invalid promo kind")
    async with async_session_factory() as session:
        exists = await session.scalar(
            select(PromoCode.id).where(func.lower(PromoCode.code) == code.lower())
        )
        if exists is not None:
            raise ValueError("promo code already exists")
        promo = PromoCode(
            dealer_id=dealer_id,
            code=code.upper(),
            kind=kind,
            days=days,
            traffic_gb=traffic_gb,
            expire_at=expire_at,
            max_uses=max_uses,
        )
        session.add(promo)
        await session.commit()
        await session.refresh(promo)
        return promo


async def list_dealer_promos(
    dealer_id: int,
    page: int = 0,
    per_page: int = 5,
) -> tuple[list[PromoCode], int]:
    async with async_session_factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(PromoCode).where(
                PromoCode.dealer_id == dealer_id
            )
        )
        result = await session.execute(
            select(PromoCode)
            .where(PromoCode.dealer_id == dealer_id)
            .order_by(PromoCode.created_at.desc())
            .offset(max(0, page) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), int(total or 0)


async def get_dealer_promo(dealer_id: int, promo_id: int) -> PromoCode | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(PromoCode).where(
                PromoCode.id == promo_id,
                PromoCode.dealer_id == dealer_id,
            )
        )
        return result.scalar_one_or_none()


async def deactivate_promo(dealer_id: int, promo_id: int) -> bool:
    async with async_session_factory() as session:
        result = await session.execute(
            select(PromoCode)
            .where(PromoCode.id == promo_id, PromoCode.dealer_id == dealer_id)
            .with_for_update()
        )
        promo = result.scalar_one_or_none()
        if promo is None or not promo.is_active:
            return False
        promo.is_active = False
        await session.commit()
        return True


async def reserve_promo_redemption(
    user_id: int,
    raw_code: str,
) -> tuple[str, PromoCode | None]:
    code = raw_code.strip().upper()
    now = utcnow()
    async with async_session_factory() as session:
        async with session.begin():
            result = await session.execute(
                select(PromoCode)
                .where(func.upper(PromoCode.code) == code)
                .with_for_update()
            )
            promo = result.scalar_one_or_none()
            if promo is None:
                return "not_found", None
            if not promo.is_active or promo.expire_at <= now:
                return "expired", None
            if promo.uses_count >= promo.max_uses:
                return "exhausted", None

            already = await session.scalar(
                select(PromoRedemption.id).where(
                    PromoRedemption.promo_id == promo.id,
                    PromoRedemption.user_id == user_id,
                )
            )
            if already is not None:
                return "already_used", None

            promo.uses_count += 1
            session.add(PromoRedemption(promo_id=promo.id, user_id=user_id))
            await session.flush()
            return "reserved", promo


async def rollback_promo_redemption(user_id: int, promo_id: int) -> None:
    async with async_session_factory() as session:
        async with session.begin():
            promo = (
                await session.execute(
                    select(PromoCode).where(PromoCode.id == promo_id).with_for_update()
                )
            ).scalar_one_or_none()
            redemption = (
                await session.execute(
                    select(PromoRedemption).where(
                        PromoRedemption.promo_id == promo_id,
                        PromoRedemption.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if redemption is not None:
                await session.delete(redemption)
                if promo is not None and promo.uses_count > 0:
                    promo.uses_count -= 1
