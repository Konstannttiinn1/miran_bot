from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.utils.emojis import button_parts, strip_custom_emoji_tags
from app.utils.tariffs import PLANS, get_plan_button_text


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


def language_kb() -> InlineKeyboardMarkup:
    rows = []
    for code in settings.langs_list:
        if code in LANG_BUTTONS:
            text, data = LANG_BUTTONS[code]
            rows.append([InlineKeyboardButton(text=text, callback_data=data)])
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
        [_btn(t, "btn_back", "back:main")],
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
        [_btn(t, "btn_dealer_balance", "dealer:balance")],
        [_btn(t, "btn_dealer_history", "dealer:history")],
        [_btn(t, "btn_lang", "menu:lang")],
    ])


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
