from html import escape as h

from aiogram import F, Router, types

from app.config import settings
from app.keyboards.builders import back_kb
from app.middlewares.i18n import I18nMiddleware
from app.repositories import db_repo
from app.services.vpn_provider import subscription_link
from app.services.xui_api import XuiClient
from app.utils.menu import send_with_logo

router = Router()
router.callback_query.middleware(I18nMiddleware())


async def _old_xui_link(email: str) -> str | None:
    """Старая ссылка 3x-ui — только на время миграции."""
    if not settings.xui_sub_url.strip():
        return None

    client = XuiClient()
    try:
        data = await client.get_client(email)
        sub_id = (data or {}).get("subId")
        if not sub_id:
            return None
        return f"{settings.xui_sub_url.rstrip('/')}/{sub_id}"
    except Exception:
        return None
    finally:
        if client._http is not None:
            await client._http.aclose()


async def _send_key(
    callback: types.CallbackQuery,
    t,
    *,
    email: str,
    old: bool,
    back_callback: str,
) -> None:
    try:
        link = await _old_xui_link(email) if old else await subscription_link(email)
    except Exception:
        link = None

    await callback.answer()
    if not link:
        await send_with_logo(
            callback,
            t("support_msg", support=settings.support_username),
            reply_markup=back_kb(t, back_callback),
        )
        return

    title = "↩️ <b>کلید قدیمی</b>" if old else "🆕 <b>کلید جدید</b>"
    await send_with_logo(
        callback,
        f"{title}\n\n<code>{h(link)}</code>\n\n"
        "این لینک را در برنامه VPN اضافه کنید و با کسی به اشتراک نگذارید.",
        reply_markup=back_kb(t, back_callback),
    )


@router.callback_query(F.data.in_({"migration:new", "migration:old"}))
async def migration_client_key(callback: types.CallbackQuery, t, lang, db_user):
    sub = await db_repo.get_subscription(db_user.id)
    if sub is None:
        await callback.answer("Subscription not found", show_alert=True)
        return

    await _send_key(
        callback,
        t,
        email=sub.xui_email,
        old=callback.data == "migration:old",
        back_callback="back:main",
    )


@router.callback_query(
    F.data.in_({"migration:dealer:new", "migration:dealer:old"})
)
async def migration_dealer_keys(callback: types.CallbackQuery, t, lang, db_user):
    if db_user.role != "dealer":
        await callback.answer("Dealer access only", show_alert=True)
        return

    old = callback.data == "migration:dealer:old"
    items, _ = await db_repo.list_dealer_subscriptions(
        db_user.id,
        page=0,
        per_page=100,
    )
    if not items:
        await callback.answer("No dealer subscriptions", show_alert=True)
        return

    lines = [
        "↩️ <b>کلیدهای قدیمی شما</b>" if old
        else "🆕 <b>کلیدهای جدید شما</b>",
        "",
    ]
    for sub in items:
        try:
            link = (
                await _old_xui_link(sub.xui_email)
                if old
                else await subscription_link(sub.xui_email)
            )
        except Exception:
            link = None
        if not link:
            continue

        name = sub.client_name or f"اشتراک #{sub.id}"
        lines.append(f"📦 <b>{h(name)}</b>\n<code>{h(link)}</code>")

    if len(lines) == 2:
        await callback.answer()
        await send_with_logo(
            callback,
            t("support_msg", support=settings.support_username),
            reply_markup=back_kb(t, "back:dealer"),
        )
        return

    await callback.answer()
    await send_with_logo(
        callback,
        "\n\n".join(lines),
        reply_markup=back_kb(t, "back:dealer"),
    )
