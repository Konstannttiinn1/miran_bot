"""Deterministic quote and legacy provisioning bridge for supported catalogue plans."""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP


def plan_code(days: int, devices: int, traffic_gb: int) -> str:
    """Map an editable offer to an existing monthly Remnawave issuance product."""
    if days not in (30, 60, 90) or devices not in (1, 2, 3) or traffic_gb != 100:
        raise ValueError("Commercial plan has unsupported issuance parameters")
    months = days // 30
    return f"month{months}" + (f"d{devices}" if devices != 1 else "")


def retail_amount(toman: int, currency: str, *, rate: float, stars_reward: float, dealer_discount: float):
    """Pre-discount amount, frozen when Order is created.

    USD is the internal dealer balance debit; USDT is the Heleket retail amount.
    """
    if not isinstance(toman, int) or isinstance(toman, bool) or toman <= 0:
        raise ValueError("Retail toman must be a positive integer")
    if rate <= 0 or stars_reward <= 0 or not (0 < dealer_discount <= 1):
        raise ValueError("Invalid currency configuration")
    usd = Decimal(toman) / Decimal(str(rate))
    if currency.lower() == "usd":
        dealer = (usd * Decimal(str(dealer_discount))).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        return float(max(Decimal("0.001"), dealer))
    if currency.lower() == "usdt":
        return float(max(
            Decimal("0.01"), usd.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ))
    if currency.lower() == "xtr":
        return max(1, int(
            (usd / Decimal(str(stars_reward))).to_integral_value(rounding=ROUND_CEILING)
        ))
    raise ValueError("Unsupported catalogue payment currency")
