"""Тарифы: все пакеты на 30 дней, отличается трафик.

Цены и объёмы меняются ТОЛЬКО здесь.
"""

PLANS: dict[str, dict] = {
    "test": {"days": 1, "price_usd": 0, "traffic_gb": 1},
    "10gb": {"days": 30, "price_usd": 3, "traffic_gb": 10},
    "20gb": {"days": 30, "price_usd": 4, "traffic_gb": 20},
    "30gb": {"days": 30, "price_usd": 5, "traffic_gb": 30},
    "50gb": {"days": 30, "price_usd": 7, "traffic_gb": 50},
    "100gb": {"days": 30, "price_usd": 10, "traffic_gb": 100},
}

TEST_TARIFF = "test"


def get_tariff(plan: str) -> dict:
    """Возвращает тариф по ключу."""
    return PLANS[plan]