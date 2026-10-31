import logging

from aiogram.types import InlineKeyboardMarkup

from app.bot import bot
from app.config import settings

log = logging.getLogger(__name__)


async def notify_admins(
    text: str,
    *,
    photo: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send every admin a summary, optionally as a receipt photo caption."""
    for admin_id in settings.admin_list:
        if photo is not None:
            try:
                await bot.send_photo(admin_id, photo, caption=text, reply_markup=reply_markup)
                continue
            except Exception:
                log.exception("notify admin %s photo failed; sending text", admin_id)
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception:
            log.exception("notify admin %s failed", admin_id)
