"""Отправка главного меню (с логотипом, если он есть)."""
from pathlib import Path

from aiogram import types

from app.keyboards.builders import main_menu_kb

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "logo.jpg"


async def send_main_menu(target, t) -> None:
    kb = main_menu_kb(t)
    if LOGO_PATH.exists():
        await target.answer_photo(
            types.FSInputFile(LOGO_PATH),
            caption=t("main_menu_text"),
            reply_markup=kb,
        )
    else:
        await target.answer(t("main_menu_text"), reply_markup=kb)