import logging

from app.bot import bot
from app.config import settings

log = logging.getLogger(__name__)


async def notify_admins(text: str) -> None:
    """Шлёт сообщение всем супер-админам."""
    for admin_id in settings.admin_list:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            log.exception("notify admin %s failed", admin_id)


async def notify_admins_photo(photo: str, caption: str) -> None:
    """Шлёт фото с подписью всем супер-админам."""
    for admin_id in settings.admin_list:
        try:
            await bot.send_photo(admin_id, photo, caption=caption)
        except Exception:
            log.exception("notify admin photo %s failed", admin_id)
