"""Send the same Telegram content in preview and in a manual broadcast."""
import logging
from asyncio import sleep
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import LinkPreviewOptions, MessageEntity

log = logging.getLogger(__name__)
_SEND_INTERVAL = 0.08


@dataclass(frozen=True)
class BroadcastContent:
    text: str
    photo: str | None = None
    entities: list[MessageEntity] = field(default_factory=list)
    has_spoiler: bool | None = None
    show_caption_above_media: bool | None = None
    link_preview_options: LinkPreviewOptions | None = None

    async def send(self, bot: Bot, chat_id: int) -> None:
        # Preserve Telegram's formatting without interpreting user text as HTML.
        if self.photo:
            await bot.send_photo(
                chat_id, self.photo, caption=self.text or None,
                caption_entities=self.entities or None, parse_mode=None,
                has_spoiler=self.has_spoiler, show_caption_above_media=self.show_caption_above_media,
            )
        else:
            await bot.send_message(
                chat_id, self.text, entities=self.entities or None, parse_mode=None,
                link_preview_options=self.link_preview_options,
            )


async def deliver(bot: Bot, recipients: list[int], content: BroadcastContent) -> tuple[int, int]:
    """Deliver a campaign and return (sent, failed)."""
    sent = 0
    for i, chat_id in enumerate(recipients):
        if i and _SEND_INTERVAL:
            await sleep(_SEND_INTERVAL)
        for attempt in range(2):
            try:
                await content.send(bot, chat_id)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                await sleep(exc.retry_after)
                if attempt:
                    log.warning("Broadcast rate limited after retry user=%s", chat_id)
            except TelegramAPIError as exc:
                # Network errors can be ambiguous, so only retry an explicit flood wait.
                log.info("Broadcast delivery failed user=%s kind=%s", chat_id, type(exc).__name__)
                break
    return sent, len(recipients) - sent
