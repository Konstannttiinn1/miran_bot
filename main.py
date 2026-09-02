import asyncio
import logging

from app.bot import bot, dp
from app.database.engine import init_db
from app.handlers.admin import router as admin_router
from app.handlers.client import router as client_router
from app.handlers.dealer import router as dealer_router
from app.handlers.start import router as start_router
from app.services.payment_checker import payment_checker_loop
from app.services.reminder import reminder_loop

logging.basicConfig(level=logging.INFO)


async def main():
    await init_db()
    dp.include_router(start_router)
    dp.include_router(client_router)
    dp.include_router(dealer_router)
    dp.include_router(admin_router)
    asyncio.create_task(payment_checker_loop())
    asyncio.create_task(reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())