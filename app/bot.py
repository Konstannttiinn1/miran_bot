from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.config import settings

bot = Bot(
    token=settings.bot_token,
    default=DefaultBotProperties(parse_mode=None),  # None — для работы <tg-emoji> тегов
)
dp = Dispatcher()