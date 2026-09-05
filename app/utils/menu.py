"""Единый экран бота с логотипом.

Первый экран отправляется новым сообщением. Дальнейшая навигация по inline-кнопкам
редактирует caption/текст текущего сообщения, чтобы не спамить чат.
"""
import logging
from pathlib import Path

from aiogram import types
from aiogram.exceptions import TelegramBadRequest

from app.keyboards.builders import main_menu_kb

log = logging.getLogger(__name__)

LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "logo.jpg"


def _edit_kwargs(kwargs: dict) -> dict:
    """Оставляет только параметры, совместимые с edit_text/edit_caption."""
    allowed = {
        "parse_mode",
        "entities",
        "link_preview_options",
        "show_caption_above_media",
    }
    return {key: value for key, value in kwargs.items() if key in allowed}


async def _edit_callback_screen(
    callback: types.CallbackQuery,
    text: str,
    reply_markup=None,
    **kwargs,
) -> bool:
    """Редактирует уже существующий экран. True = новое сообщение не нужно."""
    message = callback.message
    if message is None:
        return False

    edit_kwargs = _edit_kwargs(kwargs)
    try:
        if message.photo:
            # У фото нельзя edit_text: меняем caption и клавиатуру, само фото остаётся.
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                **edit_kwargs,
            )
        else:
            # Текстовый старый экран тоже поддерживаем, чтобы старые сообщения не ломались.
            edit_kwargs.pop("show_caption_above_media", None)
            await message.edit_text(
                text=text,
                reply_markup=reply_markup,
                **edit_kwargs,
            )
        return True
    except TelegramBadRequest as exc:
        # Повторный клик по уже открытому экрану — не ошибка.
        if "message is not modified" in str(exc).lower():
            return True
        log.warning("Не удалось отредактировать экран Telegram: %s", exc)
        return False
    except Exception:
        log.exception("Не удалось отредактировать экран Telegram")
        return False


async def send_with_logo(target, text: str, reply_markup=None, **kwargs) -> None:
    """Показывает экран: callback редактирует текущее сообщение, Message создаёт первое."""
    if isinstance(target, types.CallbackQuery):
        if await _edit_callback_screen(target, text, reply_markup, **kwargs):
            return

        # Редкий fallback: старое/не редактируемое сообщение.
        if target.message is not None:
            if LOGO_PATH.exists():
                await target.message.answer_photo(
                    types.FSInputFile(LOGO_PATH),
                    caption=text,
                    reply_markup=reply_markup,
                )
            else:
                await target.message.answer(text, reply_markup=reply_markup)
        return

    if isinstance(target, types.Message):
        if LOGO_PATH.exists():
            try:
                await target.answer_photo(
                    types.FSInputFile(LOGO_PATH),
                    caption=text,
                    reply_markup=reply_markup,
                )
                return
            except Exception:
                log.exception("Не удалось отправить экран с логотипом")
        await target.answer(text, reply_markup=reply_markup)
        return

    await target.answer(text, reply_markup=reply_markup)


async def send_main_menu(target, t) -> None:
    """Показывает главное меню в текущем экране."""
    await send_with_logo(
        target,
        t("main_menu_text"),
        reply_markup=main_menu_kb(t),
    )
