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
        # Existing single-subscription installations need a safe, additive migration.
        # We do not modify existing rows' xui_email, expiration or quota.
        sub_cols = await conn.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("subscriptions")}
        )
        new_sub_cols = {
            "is_primary": "BOOLEAN NOT NULL DEFAULT TRUE",
            "label": "VARCHAR(64) NOT NULL DEFAULT ''",
            "plan": "VARCHAR(64) NOT NULL DEFAULT ''",
            "status": "VARCHAR(24) NOT NULL DEFAULT 'active'",
        }
        for column, definition in new_sub_cols.items():
            if column not in sub_cols:
                await conn.execute(text(f"ALTER TABLE subscriptions ADD COLUMN {column} {definition}"))

        order_cols = await conn.run_sync(
            lambda sync: {column["name"] for column in inspect(sync).get_columns("orders")}
        )
        if "subscription_id" not in order_cols:
            await conn.execute(text(
                "ALTER TABLE orders ADD COLUMN subscription_id INTEGER REFERENCES subscriptions(id)"
            ))

        uniques = await conn.run_sync(
            lambda sync: inspect(sync).get_unique_constraints("subscriptions")
        )
        for unique in uniques:
            if sorted(unique.get("column_names") or []) != ["user_id"]:
                continue
            if conn.dialect.name == "postgresql":
                name = conn.dialect.identifier_preparer.quote(unique["name"])
                await conn.execute(text(f"ALTER TABLE subscriptions DROP CONSTRAINT {name}"))
            else:
                raise RuntimeError(
                    "Existing SQLite subscriptions.user_id UNIQUE requires an offline "
                    "table-rebuild migration. Refusing to change a live database."
                )
        # A partial unique index preserves one primary subscription per customer.
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_user_primary "
            "ON subscriptions(user_id) WHERE is_primary = TRUE"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_subscriptions_user_id "
            "ON subscriptions(user_id)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_xui_email "
            "ON subscriptions(xui_email)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_orders_subscription_id "
            "ON orders(subscription_id)"
        ))
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
