from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.db_url, echo=False)

async_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы и сохраняет историю тестов из старых заказов."""
    from app.database.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all() does not upgrade existing promo tables.
        promo_cols = await conn.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("promo_codes")}
        )
        if "discount_percent" not in promo_cols:
            await conn.execute(text(
                "ALTER TABLE promo_codes ADD COLUMN discount_percent INTEGER NOT NULL DEFAULT 0"
            ))
        redeemed_cols = await conn.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("promo_redemptions")}
        )
        if "order_id" not in redeemed_cols:
            await conn.execute(text(
                "ALTER TABLE promo_redemptions ADD COLUMN order_id INTEGER"
            ))
        # PostgreSQL и SQLite: повторный запуск безопасен, старые дубли
        # сводятся к одной записи на Telegram ID. Рабочие заказы не меняются.
        await conn.execute(text("""
            INSERT INTO trial_claims (telegram_id, created_at)
            SELECT users.telegram_id, MIN(orders.created_at)
            FROM users JOIN orders ON orders.user_id = users.id
            WHERE orders.plan = 'test'
            GROUP BY users.telegram_id
            ON CONFLICT (telegram_id) DO NOTHING
        """))

    from app.repositories.analytics_repo import backfill_sales
    await backfill_sales()
