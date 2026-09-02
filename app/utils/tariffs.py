"""Тарифы: сроки, цены и лимиты трафика. Меняются только здесь."""

PLANS: dict[str, dict] = {
    "test": {"days": 1, "price_usd": 0, "traffic_gb": 1},
    "1m": {"days": 30, "price_usd": 5, "traffic_gb": 30},
    "3m": {"days": 90, "price_usd": 12, "traffic_gb": 90},
    "6m": {"days": 180, "price_usd": 20, "traffic_gb": 180},
}