import random
import string
import time
import uuid as uuid_lib

from app.config import settings
from app.services.remnawave_api import RemnawaveClient
from app.services.xui_api import XuiClient


class VpnProviderError(RuntimeError):
    """VPN provider is unavailable or configured incorrectly."""


def provider_name() -> str:
    return (settings.vpn_provider or "xui").strip().lower()


def get_vpn_provider():
    name = provider_name()
    if name == "xui":
        return XuiClient()
    if name == "remnawave":
        return RemnawaveClient()
    raise VpnProviderError(
        f"Unsupported VPN_PROVIDER={name!r}; allowed: xui, remnawave"
    )


async def subscription_link(email: str) -> str:
    provider = get_vpn_provider()
    if isinstance(provider, RemnawaveClient):
        return await provider.subscription_link(email)

    client = await provider.get_client(email)
    sub_id = (client or {}).get("subId")
    if not sub_id:
        raise VpnProviderError(f"Subscription token not found for {email}")
    if not settings.xui_sub_url.strip():
        raise VpnProviderError("XUI_SUB_URL is not configured")
    return f"{settings.xui_sub_url.rstrip('/')}/{sub_id}"


async def reset_subscription_link(email: str) -> str:
    provider = get_vpn_provider()
    if isinstance(provider, RemnawaveClient):
        return await provider.reset_subscription_link(email)

    new_sub = "".join(
        random.choices(string.ascii_lowercase + string.digits, k=16)
    )
    await provider.update_client(
        email,
        id=str(uuid_lib.uuid4()),
        subId=new_sub,
    )
    return await subscription_link(email)


async def create_test_client(
    dealer_id: int,
    days: int,
    traffic_gb: int,
) -> tuple[str, str]:
    if days <= 0 or traffic_gb <= 0:
        raise ValueError("test days and traffic_gb must be positive")

    email = f"dtest-{dealer_id}-{int(time.time())}-{uuid_lib.uuid4().hex[:8]}"
    await get_vpn_provider().add_client(
        email=email,
        days=days,
        limit_ip=1,
        traffic_gb=traffic_gb,
    )
    return email, await subscription_link(email)
