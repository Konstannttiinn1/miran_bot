import json
import re
from pathlib import Path

from aiogram import BaseMiddleware, types

from app.config import settings
from app.repositories.db_repo import get_or_create_user
from app.utils.emojis import apply_emoji

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

_translations: dict[str, dict] = {}

_TAG_RE = re.compile(r"</?tg-emoji[^>]*>")


def _plain(s: str) -> str:
    """Убирает tg-emoji теги (всплывашки не умеют HTML)."""
    return _TAG_RE.sub("", s)


def _load_locales() -> None:
    for lang in ("fa", "en", "ru"):
        with open(LOCALES_DIR / f"{lang}.json", encoding="utf-8") as f:
            _translations[lang] = json.load(f)


_load_locales()


def get_text(lang: str, key: str, **kwargs) -> str:
    data = _translations.get(lang, _translations["fa"])
    text = data.get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return apply_emoji(text, settings.use_custom_emoji)


class I18nMiddleware(BaseMiddleware):
    """Кладёт в хендлер: db_user, lang, t(). Блокирует забаненных."""

    async def __call__(self, handler, event, data):
        tg_user = event.from_user
        user = await get_or_create_user(tg_user.id, tg_user.username)

        if user.is_blocked and tg_user.id not in settings.admin_list:
            msg = get_text(user.lang or "fa", "blocked_msg")
            if isinstance(event, types.Message):
                await event.answer(msg)
            else:
                await event.answer(_plain(msg), show_alert=True)
            return

        lang = "ru" if tg_user.id in settings.admin_list else (user.lang or "fa")

        data["db_user"] = user
        data["lang"] = lang
        data["t"] = lambda key, **kw: get_text(lang, key, **kw)
        return await handler(event, data)