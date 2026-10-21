import os
from unittest.mock import AsyncMock

# Tests never connect to Telegram or to the production panel/database.
os.environ["BOT_TOKEN"] = "123456:TEST_TOKEN_FOR_LOCAL_TESTS_ONLY"
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base
from app.repositories import db_repo, dealer_tools_repo
from app.services import subscription as subscription_service
from app.services import monthly_traffic


def pytest_addoption(parser):
    parser.addoption("--postgres", action="store_true", help="Use an isolated temporary PostgreSQL")


@pytest.fixture(scope="session")
def local_postgres(request, tmp_path_factory):
    if not request.config.getoption("--postgres"):
        yield None
        return
    pgserver = pytest.importorskip("pgserver")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("Run PostgreSQL tests as a regular user: PostgreSQL does not run as root")
    server = pgserver.get_server(tmp_path_factory.mktemp("postgres"), cleanup_mode="stop")
    try:
        yield server.get_uri().replace("postgresql://", "postgresql+asyncpg://", 1)
    finally:
        server.cleanup()


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch, local_postgres):
    engine = create_async_engine(
        local_postgres or f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite'}",
        connect_args={} if local_postgres else {"timeout": 30},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_repo, "async_session_factory", factory)
    monkeypatch.setattr(dealer_tools_repo, "async_session_factory", factory)
    monkeypatch.setattr(monthly_traffic, "async_session_factory", factory)
    monthly_traffic._locks.clear()
    monkeypatch.setattr(subscription_service, "subscription_link", AsyncMock(return_value="https://vpn.example.test/sub/test-id"))
    try:
        yield factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
