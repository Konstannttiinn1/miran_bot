from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.db_url, echo=False)

async_session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт все таблицы, если их ещё нет."""
    from app.database.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)