from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.db_url, echo=False)

async_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы и сохраняет историю тестов из старых заказов."""
    from app.database.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
