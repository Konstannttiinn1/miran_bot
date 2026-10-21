"""Retail prices in USD. Legacy products are kept only for existing orders."""
from math import ceil, floor
from app.config import settings

TEST_TARIFF = "test"
DEFAULT_PLAN = "month1"
TRAFFIC_TOPUP = "traffic50"
# Prepaid terms: $1 + $0.90 + $0.81; each next month costs 10% less.
PLANS: dict[str, dict] = {
    "test": {"days": 1, "price_usd": 0, "price_toman": 0, "traffic_gb": 1},
    **{
        f"month{months}": {
            "months": months, "days": months * 30, "price_usd": price_usd,
            "traffic_gb": 100, "monthly": True,
        }
        for months, price_usd in ((1, 1.0), (2, 1.90), (3, 2.71))
    },
}
TRAFFIC_PACKAGES = {
    TRAFFIC_TOPUP: {"days": 0, "price_usd": 0.5, "traffic_gb": 50},
}
RETIRED_PLANS = {
    f"{gb}gb": {"days": 30, "price_toman": price, "traffic_gb": gb}
    for gb, price in (
        (10, 39000), (15, 49000), (20, 59000), (25, 69000),
        (30, 79000), (40, 265000), (50, 119000), (100, 199000),
    )
}
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def get_tariff(plan: str) -> dict:
    for catalog in (PLANS, TRAFFIC_PACKAGES, RETIRED_PLANS):
        if plan in catalog:
            return catalog[plan]
    raise KeyError(plan)


def is_sale_product(plan: str) -> bool:
    return plan in PLANS or plan in TRAFFIC_PACKAGES


def _validate_toman_rate(rate: float) -> float:
    if float(rate) <= 0:
        raise ValueError("toman_per_usd must be positive")
    return float(rate)


def get_usd_price(plan: str, toman_per_usd: float | None = None) -> float:
    tariff = get_tariff(plan)
    if "price_usd" in tariff:
        return float(tariff["price_usd"])
    rate = _validate_toman_rate(settings.toman_per_usd if toman_per_usd is None else toman_per_usd)
    return float(tariff["price_toman"]) / rate


def get_toman_price(plan: str) -> int:
    tariff = get_tariff(plan)
    if "price_toman" in tariff:
        return int(tariff["price_toman"])
    return int(floor(get_usd_price(plan) * _validate_toman_rate(settings.toman_per_usd) + 0.5))


def get_rub_price(plan: str, rub_per_usd: float) -> int:
    if plan == TEST_TARIFF:
        return 0
    return max(10, int(floor(get_usd_price(plan) * rub_per_usd / 10 + 0.5) * 10))


def get_stars_price(plan: str, star_reward_usd: float) -> int:
    if plan == TEST_TARIFF:
        return 0
    if star_reward_usd <= 0:
        raise ValueError("star_reward_usd must be positive")
    return max(1, ceil(get_usd_price(plan) / star_reward_usd))


def get_price_display(plan: str, lang: str, rub_per_usd: float = 90.0) -> str:
    if plan in PLANS or plan in TRAFFIC_PACKAGES:
        amount = get_usd_price(plan)
        return "$" + (str(int(amount)) if amount.is_integer() else f"{amount:.2f}")
    if lang == "fa":
        amount = f"{get_toman_price(plan):,}".replace(",", "،").translate(_FA_DIGITS)
        return f"{amount} تومان"
    if lang == "ru":
        return f"{get_rub_price(plan, rub_per_usd)} ₽"
    return f"${get_usd_price(plan):.2f}"


def get_plan_button_text(plan: str, lang: str, rub_per_usd: float = 90.0) -> str:
    tariff = get_tariff(plan)
    months = tariff.get("months", 1)
    traffic = tariff["traffic_gb"]
    price = get_price_display(plan, lang, rub_per_usd)
    if lang == "fa":
        return f"🕒 {str(months).translate(_FA_DIGITS)} ماه | {str(traffic).translate(_FA_DIGITS)} گیگ در ماه — {price}"
    if lang == "ru":
        return f"🕒 {months} мес | {traffic} ГБ/мес — {price}"
    return f"🕒 {months} month{'s' if months != 1 else ''} | {traffic} GB/month — {price}"


def get_dealer_debit_usd(plan: str, toman_per_usd: float, dealer_discount: float = 0.5) -> float:
    if not 0 < dealer_discount <= 1:
        raise ValueError("dealer_discount must be in (0, 1]")
    return round(get_usd_price(plan, toman_per_usd) * dealer_discount, 3)
