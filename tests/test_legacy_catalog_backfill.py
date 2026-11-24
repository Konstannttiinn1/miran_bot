"""Regression test for historical dealer rows during a real database migration.

Historical sales are reconstructed from DealerLog into SimpleNamespace objects
without the catalogue fields introduced after those logs were written.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.database.models import AnalyticsSale
from app.repositories import db_repo
from app.repositories.analytics_repo import record_managed_sale


@pytest.mark.asyncio
async def test_backfill_old_dealer_sale_without_catalog_fields(database):
    dealer = await db_repo.get_or_create_user(99401)
    await db_repo.set_user_role(dealer.telegram_id, "dealer")
    legacy = SimpleNamespace(
        id=12345,
        client_name="Old Iranian customer",
        created_at=None,
        plan="month1",
    )
    async with database() as session:
        await record_managed_sale(
            session, legacy, dealer.id, 0.5,
            "dealer-log:legacy-issue-123",
            estimated=True,
        )
        await session.commit()
        sale = await session.scalar(
            select(AnalyticsSale).where(
                AnalyticsSale.event_key == "dealer-log:legacy-issue-123"
            )
        )
        assert sale is not None
        assert sale.catalog_tariff_id is None
        assert sale.catalog_revision is None
        assert sale.catalog_title_fa is None
        assert sale.retail_toman is None
        assert sale.amount == 0.5
