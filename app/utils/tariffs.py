"""Тарифы: все пакеты на 30 дней, отличается трафик.

Цены и объёмы меняются ТОЛЬКО здесь.
Клиент (fa): видит цены в туманах
Клиент (en/ru): видит цены в USD
Дилер: платит 50% от клиентской цены (в туманах)
"""

PLANS: dict[str, dict] = {
    "test": {
        "days": 1,
        "price_usd": 0,
        "price_toman": 0,
        "price_dealer_toman": 0,
        "traffic_gb": 1
    },
    "10gb": {
        "days": 30,
        "price_usd": 1.15,
        "price_toman": 70000,
        "price_dealer_toman": 35000,
        "traffic_gb": 10
    },
    "20gb": {
        "days": 30,
        "price_usd": 2.30,
        "price_toman": 140000,
        "price_dealer_toman": 70000,
        "traffic_gb": 20
    },
    "30gb": {
        "days": 30,
        "price_usd": 3.45,
        "price_toman": 210000,
        "price_dealer_toman": 105000,
        "traffic_gb": 30
    },
    "40gb": {
        "days": 30,
        "price_usd": 4.35,
        "price_toman": 265000,
        "price_dealer_toman": 132500,
        "traffic_gb": 40
    },
    "50gb": {
        "days": 30,
        "price_usd": 5.25,
        "price_toman": 320000,
        "price_dealer_toman": 160000,
        "traffic_gb": 50
    },
    "100gb": {
        "days": 30,
        "price_usd": 9.85,
        "price_toman": 600000,
        "price_dealer_toman": 300000,
        "traffic_gb": 100
    },
}

TEST_TARIFF = "test"

def get_tariff(plan: str) -> dict:
    """Возвращает тариф по ключу."""
    return PLANS[plan]

def get_price_display(plan: str, lang: str) -> str:
    """Возвращает цену для отображения в зависимости от языка."""
    tariff = PLANS[plan]
    if lang == "fa":
        toman = tariff["price_toman"]
        return f"{toman:,} تومان".replace(",", "،")
    else:
        usd = tariff["price_usd"]
        return f"${usd}"

def get_dealer_price(plan: str) -> int:
    """Возвращает цену для дилера (в туманах, 50% скидка)."""
    return PLANS[plan]["price_dealer_toman"]