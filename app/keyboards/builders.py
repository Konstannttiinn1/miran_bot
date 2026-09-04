import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.utils.emojis import button_parts
from app.utils.tariffs import PLANS

_TAG_RE = re.compile(r"</?tg-emoji[^>]*>")


def _p(text: str) -> str:
    return _TAG_RE.sub("", text)


def raw_btn(text: str, callback: str, **kw) -> InlineKeyboardButton:
    if settings.use_custom_emoji:
        label, icon = button_parts(text, True)
        if icon and label:
            return InlineKeyboardButton(text=label, callback_data=callback,
                                        icon_custom_emoji_id=icon, **kw)
    return InlineKeyboardButton(text=_p(text), callback_data=callback, **kw)


def _btn(t, key: str, callback: str, **kw) -> InlineKeyboardButton:
    return raw_btn(_p(t(key)), callback, **kw)


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


def plans_kb(t, with_test: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for key in PLANS:
        if key == "test":
            continue
        rows.append([_btn(t, f"plan_{key}", f"plan:{key}")])
    if with_test:
        rows.append([_btn(t, "plan_test", "plan:test")])
    rows.append([_btn(t, "btn_back", "back:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t, "pay_heleket", "pay:heleket")],
        [_btn(t, "pay_dealer", "pay:dealer")],
        [_btn(t, "pay_stars", "pay:stars")],
        [_btn(t, "btn_back", "back:main")],
    ])


def sub_link_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t, "btn_get_link", "menu:get_link")],
        [_btn(t, "btn_buy", "menu:buy")],
        [_btn(t, "btn_back", "back:main")],
    ])


def back_kb(t, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t, "btn_back", callback_data)],
    ])


def dealer_menu_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(t, "btn_dealer_balance", "dealer:balance")],
        [_btn(t, "btn_dealer_history", "dealer:history")],
        [_btn(t, "btn_lang", "menu:lang")],
    ])


def dealer_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅", callback_data=f"dealer_ok:{order_id}"),
            InlineKeyboardButton(text="❌", callback_data=f"dealer_no:{order_id}"),
        ],
    ])


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn("👥 Пользователи", "admin:users")],
        [raw_btn("🤝 Дилеры", "admin:dealers")],
        [raw_btn("📊 Логи", "admin:logs")],
    ])