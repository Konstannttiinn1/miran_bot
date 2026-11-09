import logging
from datetime import timedelta

from app.config import settings
from app.database.models import utcnow
from app.repositories import db_repo, customer_subscriptions_repo
from app.services.vpn_provider import (
    get_vpn_provider,
    reset_subscription_link,
    subscription_link,
)
from app.utils.tariffs import TEST_TARIFF, TRAFFIC_TOPUP, get_tariff
from app.services.monthly_traffic import grant_monthly, is_monthly, parse_time

log = logging.getLogger(__name__)


class TrialUnavailableError(Exception):
    """Тест уже использован/зарезервирован или есть активная подписка."""


async def grant_vpn(
    user_id: int, telegram_id: int, plan: str, *, bonus_days: int = 0,
) -> tuple[str, object]:
    if not 0 <= bonus_days <= 365:
        raise ValueError("Invalid bonus days")
    if plan == TRAFFIC_TOPUP:
        if bonus_days:
            raise ValueError("Traffic top-up cannot add days")
        return await grant_traffic_topup(user_id)
    if plan != TEST_TARIFF:
        return await _grant_vpn(user_id, telegram_id, plan, bonus_days=bonus_days)
    if bonus_days:
        raise ValueError("Trial cannot add bonus days")

    order = await db_repo.claim_test_order(user_id, telegram_id)
    if order is None:
        raise TrialUnavailableError
    try:
        result = await _grant_vpn(user_id, telegram_id, plan)
        await db_repo.update_order(order.id, status="paid")
    except Exception:
        await db_repo.update_order(order.id, status="failed")
        raise
    return result


async def _grant_vpn(
    user_id: int, telegram_id: int, plan: str, *, bonus_days: int = 0,
) -> tuple[str, object]:
    tariff = get_tariff(plan)
    days = tariff["days"] + bonus_days
    traffic_gb = tariff["traffic_gb"]
    email = str(telegram_id)

    provider = get_vpn_provider()
    panel_expiry = None
    if tariff.get("monthly"):
        panel_expiry = await grant_monthly(
            provider, email, tariff["days"], limit_ip=tariff["devices"],
            bonus_days=bonus_days,
        )
    elif plan != TEST_TARIFF and await is_monthly(email):
        # Honor pending legacy payments without erasing a monthly allowance.
        await provider.extend_client(email, days)
        await provider.add_traffic(email, traffic_gb)
        panel_expiry = parse_time((await provider.get_client(email) or {}).get("expireAt"))
    else:
        await provider.add_client(
            email=email, days=days, limit_ip=1, traffic_gb=traffic_gb,
        )
    link = await subscription_link(email)

    sub = await db_repo.get_subscription(user_id)
    base = sub.expire_at if sub and sub.expire_at > utcnow() else utcnow()
    expire_at = panel_expiry or base + timedelta(days=days)

    # Renewing time keeps a current-cycle traffic bonus.
    current_limit = None if sub and await is_monthly(email) else traffic_gb
    saved = await db_repo.upsert_subscription(
        user_id, email, expire_at, traffic_limit_gb=current_limit, only_extend=True,
    )
    expire_at = saved.expire_at
    log.info("VPN выдан: user=%s plan=%s till=%s traffic=%sGB", user_id, plan, expire_at, traffic_gb)
    return link, expire_at


async def grant_paid_order(user_id: int, telegram_id: int, order) -> tuple[str, object]:
    """Apply only the selected order's VPN target and snapshotted promo bonus."""
    bonus_days = await db_repo.get_order_promo_bonus_days(order.id)
    sub_id = order.subscription_id
    if order.plan == TRAFFIC_TOPUP:
        return await grant_traffic_topup(user_id, sub_id)
    if sub_id is not None:
        sub = await customer_subscriptions_repo.get_subscription(user_id, sub_id)
        if sub is None or sub.status not in {"active", "pending"}:
            raise ValueError("Order subscription is missing or belongs to another account")
        tariff = get_tariff(order.plan)
        if not tariff.get("monthly"):
            raise ValueError("Targeted purchases require a monthly subscription")
        provider = get_vpn_provider()
        expire_at = await grant_monthly(
            provider, sub.xui_email, int(tariff["days"]),
            limit_ip=int(tariff["devices"]), bonus_days=bonus_days,
        )
        link = await subscription_link(sub.xui_email)
        updated = await customer_subscriptions_repo.update_subscription(
            user_id, sub_id, expire_at=expire_at, plan=order.plan,
            status="active", notified_3d=False, notified_1d=False,
            **({"traffic_limit_gb": int(tariff["traffic_gb"])} if sub.status == "pending" else {}),
        )
        if not updated:
            raise RuntimeError("Panel confirmed a purchase but the target could not be saved")
        return link, expire_at
    link, expiry = await grant_vpn(user_id, telegram_id, order.plan, bonus_days=bonus_days)
    if order.plan != "test":
        primary = await db_repo.get_subscription(user_id)
        if primary is not None:
            await customer_subscriptions_repo.update_subscription(
                user_id, primary.id, plan=order.plan,
            )
    return link, expiry


async def can_buy_traffic(user_id: int, subscription_id: int | None = None) -> bool:
    user = await db_repo.get_user_by_id(user_id)
    sub = (
        await customer_subscriptions_repo.get_subscription(user_id, subscription_id)
        if subscription_id is not None else await db_repo.get_subscription(user_id)
    )
    return bool(
        user and not user.is_blocked and sub and sub.status == "active"
        and sub.expire_at > utcnow()
    )


async def grant_traffic_topup(
    user_id: int, subscription_id: int | None = None,
) -> tuple[str, object]:
    if not await can_buy_traffic(user_id, subscription_id):
        raise ValueError("Traffic top-up requires an active subscription")
    sub = (
        await customer_subscriptions_repo.get_subscription(user_id, subscription_id)
        if subscription_id is not None else await db_repo.get_subscription(user_id)
    )
    provider = get_vpn_provider()
    current = await provider.get_client(sub.xui_email)
    if current is None or current.get("status") in {"DISABLED", "EXPIRED"}:
        raise ValueError("VPN subscription is unavailable for traffic credit")
    if "expireAt" in current and (parse_time(current["expireAt"]) or utcnow()) <= utcnow():
        raise ValueError("Cannot add traffic to an expired VPN client")
    if "trafficLimitBytes" in current and not current["trafficLimitBytes"]:
        raise ValueError("Cannot sell traffic for an unlimited VPN client")
    # Resolve the link before changing the allowance, so a link lookup error
    # cannot turn an already credited payment into a retryable failure.
    link = await subscription_link(sub.xui_email)
    extra = int(get_tariff(TRAFFIC_TOPUP)["traffic_gb"])
    new_limit = await provider.add_traffic(sub.xui_email, extra)
    if not await is_monthly(sub.xui_email):
        fields = {
            "traffic_limit_gb": new_limit if isinstance(new_limit, int) else sub.traffic_limit_gb + extra,
        }
        if subscription_id is None:
            await db_repo.update_subscription(user_id, **fields)
        else:
            await customer_subscriptions_repo.update_subscription(user_id, subscription_id, **fields)
    return link, sub.expire_at


async def extend_subscription(user, days: int) -> None:
    email = str(user.telegram_id)
    provider = get_vpn_provider()
    await provider.extend_client(email, days)

    sub = await db_repo.get_subscription(user.id)
    if sub is None:
        return
    base = sub.expire_at if sub.expire_at > utcnow() else utcnow()
    await db_repo.upsert_subscription(user.id, email, base + timedelta(days=days),
                                      traffic_limit_gb=sub.traffic_limit_gb)
    log.info("Продление %s на %s дн.", email, days)


async def reset_link(user) -> str:
    return await reset_subscription_link(str(user.telegram_id))


async def set_blocked(user, blocked: bool) -> None:
    """Apply a customer block to every active personal VPN, not only the primary."""
    await db_repo.set_user_blocked(user.telegram_id, blocked)
    provider = get_vpn_provider()
    subs = await customer_subscriptions_repo.list_subscriptions(user.id)
    if not subs:
        await provider.set_enabled(str(user.telegram_id), enabled=not blocked)
        return
    failures = []
    for sub in subs:
        if sub.status != "active" or sub.expire_at <= utcnow():
            continue
        try:
            await provider.set_enabled(sub.xui_email, enabled=not blocked)
        except Exception:
            log.exception("VPN status change failed: user=%s sub=%s", user.id, sub.id)
            failures.append(sub.id)
    if failures:
        raise RuntimeError(f"VPN status was not updated for subscription IDs {failures}")
