from html import escape as h

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.database.models import utcnow
from app.handlers.states import Purchase
from app.keyboards.builders import (admin_menu_kb, dealer_menu_kb, language_kb,
                                    plans_kb, sub_link_kb)
from app.middlewares.i18n import I18nMiddleware, get_text
from app.repositories import db_repo
from app.services.subscription import grant_vpn
from app.services.xui_api import XuiClient
from app.utils.menu import send_main_menu, send_with_logo

router = Router()
router.message.middleware(I18nMiddleware())
router.callback_query.middleware(I18nMiddleware())

EMPTY_KB = InlineKeyboardMarkup(inline_keyboard=[])


@router.message(CommandStart())
async def cmd_start(message: types.Message, t, lang, db_user):
    if message.from_user.id in settings.admin_list:
        await message.answer(
            " Админ-меню:\n🔧 /testvpn | /setdealer | /topup",
            reply_markup=admin_menu_kb(),
        )
        return

    if db_user.role == "dealer":
        await send_with_logo(message, t("dealer_menu_text"), reply_markup=dealer_menu_kb(t))
        return

    if not db_user.lang_selected:
        await message.answer(t("start_msg"), reply_markup=language_kb())
        return

    await send_main_menu(message, t)


@router.message(Command("testvpn"))
async def test_vpn(message: types.Message, t, lang, db_user):
    if message.from_user.id not in settings.admin_list:
        return
    try:
        link, expire_at = await grant_vpn(db_user.id, db_user.telegram_id, "30gb")
        await message.answer(f"🔧 TEST VPN выдан:\n{link}\n📅 до {expire_at.strftime('%d.%m.%Y')}")
    except Exception as e:
        await message.answer(f"❌ Ошибка 3x-UI:\n{type(e).__name__}: {e}")


@router.message(Command("setdealer"))
async def set_dealer(message: types.Message, t, lang, db_user):
    if message.from_user.id not in settings.admin_list:
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: /setdealer 123456789")
        return
    await db_repo.get_or_create_user(int(parts[1]))
    await db_repo.set_user_role(int(parts[1]), "dealer")
    await message.answer(f"✅ {parts[1]} теперь дилер.")


@router.message(Command("topup"))
async def topup(message: types.Message, t, lang, db_user):
    if message.from_user.id not in settings.admin_list:
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Формат: /topup 123456789 100")
        return
    tg_id, amount = int(parts[1]), float(parts[2])
    user = await db_repo.get_user_by_tg(tg_id)
    if user is None:
        await message.answer("Юзер не найден.")
        return
    await db_repo.change_dealer_balance(user.id, amount)
    await db_repo.create_dealer_log(user.id, "topup", None, {"amount": amount})
    await message.answer(f"✅ Дилеру {tg_id} начислено {amount} кредитов.")


@router.callback_query(F.data.startswith("set_lang:"))
async def set_lang(callback: types.CallbackQuery, t, lang, db_user):
    new_lang = callback.data.split(":")[1]
    await db_repo.set_user_lang(callback.from_user.id, new_lang)

    nt = lambda key, **kw: get_text(new_lang, key, **kw)
    await callback.answer(nt("lang_saved"))
    try:
        await callback.message.edit_reply_markup(reply_markup=EMPTY_KB)
    except Exception:
        pass
    if db_user.role == "dealer":
        await send_with_logo(callback, nt("dealer_menu_text"), reply_markup=dealer_menu_kb(nt))
    else:
        await send_main_menu(callback.message, nt)


@router.callback_query(F.data == "menu:lang")
async def change_lang(callback: types.CallbackQuery, t, lang, db_user):
    await callback.answer()
    await callback.message.answer(t("start_msg"), reply_markup=language_kb())


@router.callback_query(F.data == "back:main")
async def back_main(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=EMPTY_KB)
    except Exception:
        pass
    await send_main_menu(callback.message, t)


@router.callback_query(F.data == "menu:my_vpn")
async def my_vpn(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    await callback.answer()
    sub = await db_repo.get_subscription(db_user.id)
    now = utcnow()

    if sub is None or sub.expire_at <= now:
        if sub is not None:
            await send_with_logo(
                callback,
                t("subscription_expired", expire_date=sub.expire_at.strftime("%d.%m.%Y"))
            )
        used_test = await db_repo.user_has_order(db_user.id, "test")
        await state.set_state(Purchase.choosing_plan)
        await send_with_logo(
            callback,
            t("select_plan"),
            reply_markup=plans_kb(t, with_test=not used_test)
        )
        return

    await send_with_logo(
        callback,
        t("active_subscription",
          expire_date=sub.expire_at.strftime("%d.%m.%Y"),
          traffic=sub.traffic_limit_gb),
        reply_markup=sub_link_kb(t),
    )


@router.callback_query(F.data == "menu:buy")
async def buy(callback: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    await state.set_state(Purchase.choosing_plan)
    await callback.answer()
    await send_with_logo(callback, t("select_plan"), reply_markup=plans_kb(t, with_test=False))


@router.callback_query(F.data == "menu:get_link")
async def get_link(callback: types.CallbackQuery, t, lang, db_user):
    sub = await db_repo.get_subscription(db_user.id)
    if sub is None:
        await callback.answer()
        return
    try:
        client = await XuiClient().get_client(sub.xui_email)
    except Exception:
        client = None
    await callback.answer()

    sub_id = (client or {}).get("subId")
    if sub_id:
        link = f"{settings.xui_sub_url.rstrip('/')}/{sub_id}"
        await send_with_logo(callback, t("connection_link", link=h(link)))
    else:
        await send_with_logo(callback, t("support_msg", support=settings.support_username))


@router.callback_query(F.data == "menu:support")
async def support(callback: types.CallbackQuery, t, lang, db_user):
    await callback.answer()
    await send_with_logo(callback, t("support_msg", support=settings.support_username))