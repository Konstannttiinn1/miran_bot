"""Отправка сообщений с логотипом."""
from pathlib import Path

from aiogram import types

from app.keyboards.builders import main_menu_kb

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "logo.jpg"


async def send_with_logo(target, text: str, reply_markup=None, **kwargs) -> None:
    """
    Универсальная функция отправки сообщения с логотипом.

    target: types.Message или types.CallbackQuery
    text: текст сообщения
    reply_markup: клавиатура
    """
    if LOGO_PATH.exists():
        try:
            if isinstance(target, types.CallbackQuery):
                # Это callback — отправляем новое фото
                await target.message.answer_photo(
                    types.FSInputFile(LOGO_PATH),
                    caption=text,
                    reply_markup=reply_markup,
                    **kwargs
                )
            elif isinstance(target, types.Message):
                # Это сообщение — отвечаем фото
                await target.answer_photo(
                    types.FSInputFile(LOGO_PATH),
                    caption=text,
                    reply_markup=reply_markup,
                    **kwargs
                )
            else:
                # Фолбэк
                await target.answer(text, reply_markup=reply_markup, **kwargs)
        except Exception:
            # Если не получилось отправить фото — отправляем текст
            if isinstance(target, types.CallbackQuery):
                await target.message.answer(text, reply_markup=reply_markup, **kwargs)
            else:
                await target.answer(text, reply_markup=reply_markup, **kwargs)
    else:
        # Логотипа нет — обычный текст
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(text, reply_markup=reply_markup, **kwargs)
        else:
            await target.answer(text, reply_markup=reply_markup, **kwargs)


async def send_main_menu(target, t) -> None:
    """Отправляет главное меню с логотипом."""
    kb = main_menu_kb(t)
    await send_with_logo(target, t("main_menu_text"), reply_markup=kb)