from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings

LANG_BUTTONS = {
    "fa": ("🇮🇷 فارسی", "set_lang:fa"),
    "en": ("🇬 English", "set_lang:en"),
    "ru": ("🇷 Русский", "set_lang:ru"),
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
        [InlineKeyboardButton(text=t("btn_my_vpn"), callback_data="menu:my_vpn")],
        [InlineKeyboardButton(text=t("btn_support"), callback_data="menu:support")],
        [InlineKeyboardButton(text=t("btn_lang"), callback_data="menu:lang")],
    ])


def plans_kb(t, with_test: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t("plan_1m"), callback_data="plan:1m")],
        [InlineKeyboardButton(text=t("plan_3m"), callback_data="plan:3m")],
        [InlineKeyboardButton(text=t("plan_6m"), callback_data="plan:6m")],
    ]
    if with_test:
        rows.append([InlineKeyboardButton(text=t("plan_test"), callback_data="plan:test")])
    rows.append([InlineKeyboardButton(text=t("btn_back"), callback_data="back:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("pay_heleket"), callback_data="pay:heleket")],
        [InlineKeyboardButton(text=t("pay_dealer"), callback_data="pay:dealer")],
        [InlineKeyboardButton(text=t("pay_stars"), callback_data="pay:stars")],
        [InlineKeyboardButton(text=t("btn_back"), callback_data="back:main")],
    ])


def sub_link_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_get_link"), callback_data="menu:get_link")],
        [InlineKeyboardButton(text=t("btn_buy"), callback_data="menu:buy")],
        [InlineKeyboardButton(text=t("btn_back"), callback_data="back:main")],
    ])


def back_kb(t, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_back"), callback_data=callback_data)],
    ])


def dealer_menu_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_dealer_balance"), callback_data="dealer:balance")],
        [InlineKeyboardButton(text=t("btn_dealer_history"), callback_data="dealer:history")],
        [InlineKeyboardButton(text=t("btn_lang"), callback_data="menu:lang")],
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
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users")],
        [InlineKeyboardButton(text="🤝 Дилеры", callback_data="admin:dealers")],
        [InlineKeyboardButton(text="📊 Логи", callback_data="admin:logs")],
    ])