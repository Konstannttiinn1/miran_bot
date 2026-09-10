from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.utils.emojis import button_parts, strip_custom_emoji_tags
from app.utils.tariffs import PLANS, get_dealer_debit_usd, get_plan_button_text


def raw_btn(text: str, callback: str, **kw) -> InlineKeyboardButton:
    plain = strip_custom_emoji_tags(text)
    if settings.use_custom_emoji:
        label, icon = button_parts(plain, True)
        if icon:
            return InlineKeyboardButton(
                text=label or " ",
                callback_data=callback,
                icon_custom_emoji_id=icon,
                **kw,
            )
    return InlineKeyboardButton(text=plain, callback_data=callback, **kw)


def _btn(t, key: str, callback: str, **kw) -> InlineKeyboardButton:
    return raw_btn(t(key), callback, **kw)


def _detect_lang(t) -> str:
    back = strip_custom_emoji_tags(t("btn_back"))
    if "Назад" in back:
        return "ru"
    if "بازگشت" in back:
        return "fa"
    return "en"


LANG_BUTTONS = {
    "fa": ("🇮🇷 فارسی", "set_lang:fa"),
    "en": ("🇬🇧 English", "set_lang:en"),
    "ru": ("🇷🇺 Русский", "set_lang:ru"),
}


def language_kb(t=None, with_back: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for code in settings.langs_list:
        if code in LANG_BUTTONS:
            text, data = LANG_BUTTONS[code]
            rows.append([InlineKeyboardButton(text=text, callback_data=data)])
    if with_back and t is not None:
        rows.append([_btn(t, "btn_back", "back:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t, "btn_my_vpn", "menu:my_vpn")],
        [_btn(t, "btn_support", "menu:support")],
        [_btn(t, "btn_lang", "menu:lang")],
    ])


def plans_kb(t, with_test: bool = False, *, lang: str | None = None) -> InlineKeyboardMarkup:
    lang = lang or _detect_lang(t)
    rows = []
    for key in PLANS:
        if key == "test":
            continue
        rows.append([
            raw_btn(
                get_plan_button_text(key, lang, settings.rub_per_usd),
                f"plan:{key}",
            )
        ])
    if with_test:
        rows.append([_btn(t, "plan_test", "plan:test")])
    rows.append([_btn(t, "btn_back", "back:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t, "pay_stars", "pay:stars")],
        [_btn(t, "pay_heleket", "pay:heleket")],
        [_btn(t, "pay_dealer", "pay:dealer")],
        [_btn(t, "btn_back", "back:plans")],
    ])


def sub_link_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t, "btn_get_link", "menu:get_link")],
        [_btn(t, "btn_buy", "menu:buy")],
        [_btn(t, "btn_back", "back:main")],
    ])


def back_kb(t, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(t, "btn_back", callback_data)]])


def dealer_menu_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn("🎁 دریافت لینک تست", "dealer:test_link")],
        [raw_btn("🛒 خرید اشتراک", "dealer:buy_sub")],
        [raw_btn("📂 اشتراک‌های من", "dealer:subs")],
        [_btn(t, "btn_dealer_balance", "dealer:balance")],
        [_btn(t, "btn_dealer_history", "dealer:history")],
        [_btn(t, "btn_lang", "menu:lang")],
    ])


def _dealer_price(plan: str) -> float:
    return get_dealer_debit_usd(
        plan,
        settings.toman_per_usd,
        settings.dealer_discount,
    )


def dealer_buy_plans_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, plan in PLANS.items():
        if key == "test":
            continue
        traffic = int(plan["traffic_gb"])
        rows.append([
            raw_btn(
                f"🛒 {traffic} گیگ — ${_dealer_price(key):.3f}",
                f"dealer:buyplan:{key}",
            )
        ])
    rows.append([raw_btn("🔙 بازگشت", "back:dealer")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dealer_buy_confirm_kb(plan: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn("✅ تأیید خرید", f"dealer:buyconfirm:{plan}")],
        [raw_btn("🔙 بازگشت", "dealer:buy_sub")],
    ])


def _dealer_button_name(name: str, limit: int = 28) -> str:
    clean = " ".join(str(name).split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def dealer_subscriptions_kb(
    items: list[tuple[int, str, str]],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows = [
        [raw_btn(
            f"{status} #{sub_id} · {_dealer_button_name(name)}",
            f"dealer:sub:{sub_id}:{page}",
        )]
        for sub_id, name, status in items
    ]
    nav = []
    if page > 0:
        nav.append(raw_btn("⬅️", f"dealer:subs:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(raw_btn("➡️", f"dealer:subs:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([raw_btn("🔎 جستجو", "dealer:search")])
    rows.append([raw_btn("🔙 بازگشت", "back:dealer")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dealer_subscription_card_kb(
    sub_id: int,
    page: int,
    link: str | None,
) -> InlineKeyboardMarkup:
    rows = []
    if link and len(link) <= 256:
        rows.append([
            InlineKeyboardButton(
                text="📋 کپی لینک",
                copy_text=CopyTextButton(text=link),
            )
        ])
    rows.extend([
        [raw_btn("🛒 تمدید اشتراک", f"dealer:renew:{sub_id}:{page}")],
        [raw_btn("✏️ تغییر نام", f"dealer:rename:{sub_id}:{page}")],
        [raw_btn("🔙 بازگشت", f"dealer:subs:{page}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dealer_renew_plans_kb(sub_id: int, page: int) -> InlineKeyboardMarkup:
    rows = []
    for key, plan in PLANS.items():
        if key == "test":
            continue
        traffic = int(plan["traffic_gb"])
        rows.append([
            raw_btn(
                f"🕒 {traffic} گیگ — ${_dealer_price(key):.3f}",
                f"dealer:renewplan:{sub_id}:{key}:{page}",
            )
        ])
    rows.append([raw_btn("🔙 بازگشت", f"dealer:sub:{sub_id}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dealer_renew_confirm_kb(sub_id: int, plan: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn("✅ تأیید تمدید", f"dealer:renewconfirm:{sub_id}:{plan}:{page}")],
        [raw_btn("🔙 بازگشت", f"dealer:renew:{sub_id}:{page}")],
    ])


def dealer_created_name_kb(sub_id: int, link: str) -> InlineKeyboardMarkup:
    rows = []
    if len(link) <= 256:
        rows.append([
            InlineKeyboardButton(
                text="📋 کپی لینک",
                copy_text=CopyTextButton(text=link),
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dealer_search_results_kb(items: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [raw_btn(
            f"{status} #{sub_id} · {_dealer_button_name(name)}",
            f"dealer:sub:{sub_id}:0",
        )]
        for sub_id, name, status in items
    ]
    rows.append([raw_btn("🔙 بازگشت", "dealer:subs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dealer_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        raw_btn("✅", f"dealer_ok:{order_id}"),
        raw_btn("❌", f"dealer_no:{order_id}"),
    ]])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn("👥 Пользователи", "admin:users")],
        [raw_btn("🤝 Дилеры", "admin:dealers")],
        [raw_btn("📊 Логи", "admin:logs")],
    ])
