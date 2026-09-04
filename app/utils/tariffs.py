"""Единый справочник тарифов IR2_VPN."""

from math import ceil, floor

PLANS: dict[str, dict] = {
    "test": {"days": 1, "price_usd": 0, "price_toman": 0, "traffic_gb": 1},
    "10gb": {"days": 30, "price_usd": 1.15, "price_toman": 70000, "traffic_gb": 10},
    "20gb": {"days": 30, "price_usd": 2.30, "price_toman": 140000, "traffic_gb": 20},
    "30gb": {"days": 30, "price_usd": 3.45, "price_toman": 210000, "traffic_gb": 30},
    "40gb": {"days": 30, "price_usd": 4.35, "price_toman": 265000, "traffic_gb": 40},
    "50gb": {"days": 30, "price_usd": 5.25, "price_toman": 320000, "traffic_gb": 50},
    "100gb": {"days": 30, "price_usd": 9.85, "price_toman": 600000, "traffic_gb": 100},
}

TEST_TARIFF = "test"
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def get_tariff(plan: str) -> dict:
    return PLANS[plan]


def get_rub_price(plan: str, rub_per_usd: float) -> int:
    if plan == TEST_TARIFF:
        return 0
    raw = float(PLANS[plan]["price_usd"]) * rub_per_usd
    return max(10, int(floor(raw / 10 + 0.5) * 10))


def get_stars_price(plan: str, star_reward_usd: float) -> int:
    if plan == TEST_TARIFF:
        return 0
    if star_reward_usd <= 0:
        raise ValueError("star_reward_usd must be positive")
    return max(1, ceil(float(PLANS[plan]["price_usd"]) / star_reward_usd))


def get_price_display(plan: str, lang: str, rub_per_usd: float = 90.0) -> str:
    tariff = PLANS[plan]
    if lang == "fa":
        toman = f'{int(tariff["price_toman"]):,}'.replace(",", "،")
        return f"{toman.translate(_FA_DIGITS)} تومان"
    if lang == "ru":
        return f"{get_rub_price(plan, rub_per_usd)} ₽"
    return f'${float(tariff["price_usd"]):.2f}'


def get_plan_button_text(plan: str, lang: str, rub_per_usd: float = 90.0) -> str:
    traffic = int(PLANS[plan]["traffic_gb"])
    price = get_price_display(plan, lang, rub_per_usd)
    if lang == "fa":
        return f"🕒 ۱ ماه | {str(traffic).translate(_FA_DIGITS)} گیگ — {price}"
    if lang == "ru":
        return f"🕒 1 мес | {traffic} ГБ — {price}"
    return f"🕒 1 month | {traffic} GB — {price}"


def get_dealer_debit_usd(
    plan: str,
    toman_per_usd: float,
    dealer_discount: float = 0.5,
) -> float:
    """Конвертирует розничную цену в туманах в USD и применяет дилерскую скидку."""
    if toman_per_usd <= 0:
        raise ValueError("toman_per_usd must be positive")
    if not 0 < dealer_discount <= 1:
        raise ValueError("dealer_discount must be in (0, 1]")
    retail_toman = float(PLANS[plan]["price_toman"])
    return round((retail_toman / toman_per_usd) * dealer_discount, 3)
