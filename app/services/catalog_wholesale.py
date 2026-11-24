"""Admin-defined commercial wholesale formula, not inferred from dealer retail.

The old nine products keep exactly the previous dealer debit. Custom products
scale the one-month baseline by days and rolling monthly allowance. This is
a guarded interim cost matrix; the administrator can revise it before enabling
custom issuance in production.
"""
from decimal import Decimal, ROUND_HALF_UP

from app.config import settings
from app.utils.tariffs import get_dealer_debit_usd


def wholesale_usd(days: int, devices: int, traffic_gb: int) -> float:
    if not 1 <= days <= 365 or not 1 <= devices <= 20 or not 1 <= traffic_gb <= 10000:
        raise ValueError("Unsupported wholesale profile")
    # Exact historic cost for existing 30/60/90-day, 100-GB offers.
    if days in (30, 60, 90) and devices <= 3 and traffic_gb == 100:
        code = f"month{days // 30}" + (f"d{devices}" if devices > 1 else "")
        return get_dealer_debit_usd(code, settings.toman_per_usd, settings.dealer_discount)
    if devices == 1:
        device_multiplier = Decimal("1")
    elif devices == 2:
        device_multiplier = Decimal("1.5")
    else:
        device_multiplier = Decimal("1.7") + Decimal("0.2") * (devices - 3)
    raw = (device_multiplier * Decimal(days) / Decimal(30)
           * Decimal(traffic_gb) / Decimal(100)
           * Decimal(str(settings.dealer_discount)))
    # Round up to a mill so a paid debit never silently becomes zero.
    raw = raw.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return float(max(Decimal("0.001"), raw))


def minimum_toman(days: int, devices: int, traffic_gb: int) -> int:
    price = Decimal(str(wholesale_usd(days, devices, traffic_gb)))
    rate = Decimal(str(settings.toman_per_usd))
    if rate <= 0:
        raise ValueError("Invalid toman rate")
    return int((price * rate).to_integral_value(rounding="ROUND_CEILING"))
