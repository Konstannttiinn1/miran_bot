#!/usr/bin/env python3
"""Одноразовая рассылка о переезде на Remnawave.

По умолчанию это dry-run.
Для отправки: --apply --confirm SEND_REMNAWAVE_MIGRATION
"""

import argparse
import asyncio

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import text

from app.bot import bot
from app.database.engine import async_session_factory


def buttons(dealer: bool) -> InlineKeyboardMarkup:
    prefix = "migration:dealer" if dealer else "migration"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🆕 دریافت کلید جدید",
            callback_data=f"{prefix}:new",
        )],
        [InlineKeyboardButton(
            text="↩️ دریافت کلید قدیمی",
            callback_data=f"{prefix}:old",
        )],
    ])


def message(lang: str, dealer: bool) -> str:
    if lang == "ru":
        subject = "дилерские ключи" if dealer else "ключ подключения"
        return (
            "🔄 <b>ORION_VPN переехал на новую инфраструктуру</b>\n\n"
            f"Мы перенесли {subject} в новую панель. Нажмите «Новый ключ», "
            "добавьте его в приложение VPN и подключитесь заново.\n\n"
            "Старый ключ временно остаётся доступным для плавного перехода."
        )
    if lang == "en":
        subject = "dealer keys" if dealer else "connection key"
        return (
            "🔄 <b>ORION_VPN has moved to new infrastructure</b>\n\n"
            f"Your {subject} have been migrated. Tap “New key”, add it to "
            "your VPN app and reconnect.\n\n"
            "The old key remains available temporarily."
        )
    subject = "کلیدهای نمایندگی شما" if dealer else "کلید اتصال شما"
    return (
        "🔄 <b>ORION_VPN به زیرساخت جدید منتقل شد</b>\n\n"
        f"{subject} به پنل جدید منتقل شده است. روی «دریافت کلید جدید» بزنید، "
        "آن را به برنامه VPN اضافه کنید و دوباره متصل شوید.\n\n"
        "کلید قدیمی موقتاً برای انتقال بدون قطعی فعال می‌ماند."
    )


async def recipients() -> tuple[list[dict], list[dict]]:
    async with async_session_factory() as session:
        normal = (await session.execute(text("""
            SELECT DISTINCT u.telegram_id, u.lang
            FROM users u
            JOIN subscriptions s ON s.user_id = u.id
            JOIN remnawave_migrations m ON m.legacy_email = s.xui_email
            WHERE u.role <> 'dealer' AND u.is_blocked = FALSE
            ORDER BY u.telegram_id
        """))).mappings().all()

        dealers = (await session.execute(text("""
            SELECT DISTINCT u.telegram_id, u.lang
            FROM users u
            JOIN dealer_subscriptions ds ON ds.dealer_id = u.id
            JOIN remnawave_migrations m ON m.legacy_email = ds.xui_email
            WHERE u.role = 'dealer' AND u.is_blocked = FALSE
            ORDER BY u.telegram_id
        """))).mappings().all()

    return [dict(row) for row in normal], [dict(row) for row in dealers]


async def main(apply: bool) -> None:
    normal, dealers = await recipients()

    print("===== DELIVERY PLAN =====")
    print("normal users =", len(normal))
    print("dealers =", len(dealers))
    print("total =", len(normal) + len(dealers))

    if not apply:
        print("\nDry-run complete. Nothing was sent.")
        return

    sent = failed = 0
    for dealer, rows in ((False, normal), (True, dealers)):
        for row in rows:
            try:
                await bot.send_message(
                    row["telegram_id"],
                    message(row["lang"] or "fa", dealer),
                    reply_markup=buttons(dealer),
                )
                sent += 1
                print("sent:", "dealer" if dealer else "user", row["telegram_id"])
            except Exception as exc:
                failed += 1
                print(
                    "failed:",
                    "dealer" if dealer else "user",
                    row["telegram_id"],
                    type(exc).__name__,
                )
            await asyncio.sleep(0.08)

    print(f"\nDone. Sent: {sent}; failed: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.apply and args.confirm != "SEND_REMNAWAVE_MIGRATION":
        raise SystemExit(
            "Use --apply --confirm SEND_REMNAWAVE_MIGRATION"
        )

    asyncio.run(main(args.apply))
