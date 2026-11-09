"""One rounding policy for coupon previews and actual checkout amounts."""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP


def discounted_amount(amount: float | int, percent: int, currency: str) -> float | int:
    """Match invoice precision: Stars integer, Heleket cents, dealer USD mills."""
    if not 1 <= percent <= 99:
        raise ValueError("discount percent must be in 1..99")
    multiplier = Decimal(100 - percent) / Decimal(100)
    discounted = Decimal(str(amount)) * multiplier
    if currency.lower() == "xtr":
        return max(1, int(discounted.to_integral_value(rounding=ROUND_CEILING)))
    if currency.lower() not in {"usd", "usdt"}:
        raise ValueError("unsupported coupon currency")
    # Heleket bills to cents, while internal dealer USD balances use mills.
    precision = Decimal("0.01") if currency.lower() == "usdt" else Decimal("0.001")
    return float(max(
        precision,
        discounted.quantize(precision, rounding=ROUND_HALF_UP),
    ))


def discounted_toman(amount: int, percent: int) -> int:
    """Round the retail bank-transfer quote as the order repository does."""
    if not 1 <= percent <= 99:
        raise ValueError("discount percent must be in 1..99")
    multiplier = Decimal(100 - percent) / Decimal(100)
    return max(1, int(
        (Decimal(amount) * multiplier).to_integral_value(rounding=ROUND_HALF_UP)
    ))
