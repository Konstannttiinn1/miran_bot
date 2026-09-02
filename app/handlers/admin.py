import logging
from html import escape as h

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.keyboards.builders import admin_menu_kb
from app.middlewares.i18n import I18nMiddleware
from app.repositories import db_repo
from app.services import subscription as sub_service
from app.services.xui_api import XuiClient

log = logging.getLogger(__name__)

router = Router()
router.message.middleware(I18nMiddleware())
router.callback_query.middleware(I18nMiddleware())

PER_PAGE = 10
BACK_ADMIN = "🔙 Назад"


class Admin(StatesGroup):
    waiting_user_id = State()
    waiting_topup_amount = State()


async def user_card_text(user) -> str:
    sub = await db_repo.get_subscription(user.id)
    lines = [
        f"👤 {user.username or '-'}",
        f"🆔 {user.telegram_id}",
        f"🎭 Роль: {user.role}",
        f"🌐 Язык: {user.lang}",
        f"🚫 Блок: {'да' if user.is_blocked else 'нет'}",
    ]
    if sub:
        lines.append(f"📅 До: {sub.expire_at:%d.%m.%Y} | 📊 {sub.traffic_limit_gb} ГБ")
    else:
        lines.append("📅 Подписки нет")
    if user.role == "dealer":
        lines.append(f"💳 Баланс: {float(user.dealer_balance)} кр.")
    return "\n".join(lines)


def user_actions_kb(tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕7 дней", callback_data=f"admin_act:add7:{tg_id}"),
         InlineKeyboardButton(text="➖7 дней", callback_data=f"admin_act:sub7:{tg_id}")],
        [InlineKeyboardButton(text="🔄 Сбросить ссылку", callback_data=f"admin_act:reset:{tg_id}")],
        [InlineKeyboardButton(text="🚫 Блок", callback_data=f"admin_act:block:{tg_id}"),
         InlineKeyboardButton(text="✅ Разблок", callback_data=f"admin_act:unblock:{tg_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin_act:del:{tg_id}")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data="admin:users")],
    ])


async def users_page_kb(page: int) -> InlineKeyboardMarkup:
    total = await db_repo.count_users()
    users = await db_repo.list_users_page(page, PER_PAGE)
    pages = (total + PER_PAGE - 1) // PER_PAGE

    rows = [
        [InlineKeyboardButton(
            text=f"{u.username or u.telegram_id} | {u.role}",
            callback_data=f"admin_user:{u.telegram_id}")]
        for u in users
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_users:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{max(pages, 1)}", callback_data="admin:noop"))
    if (page + 1) * PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_users:{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔎 Поиск по ID", callback_data="admin:search")])
    rows.append([InlineKeyboardButton(text=BACK_ADMIN, callback_data="back:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "back:admin")
async def back_admin(cb: types.CallbackQuery, t, lang, db_user):
    await cb.answer()
    await cb.message.edit_text(
        "👑 Админ-меню:\n🔧 /testvpn | /setdealer | /topup",
        reply_markup=admin_menu_kb(),
    )


@router.callback_query(F.data == "admin:noop")
async def noop(cb: types.CallbackQuery):
    await cb.answer()


@router.callback_query(F.data == "admin:users")
async def users_list(cb: types.CallbackQuery, t, lang, db_user):
    total = await db_repo.count_users()
    await cb.answer()
    await cb.message.edit_text(f"👥 Всего пользователей: {total}",
                               reply_markup=await users_page_kb(0))


@router.callback_query(F.data.startswith("admin_users:"))
async def users_page(cb: types.CallbackQuery, t, lang, db_user):
    page = int(cb.data.split(":")[1])
    total = await db_repo.count_users()
    await cb.answer()
    await cb.message.edit_text(f"👥 Всего пользователей: {total}",
                               reply_markup=await users_page_kb(page))


@router.callback_query(F.data == "admin:search")
async def ask_search(cb: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    await state.set_state(Admin.waiting_user_id)
    await cb.answer()
    await cb.message.answer("🔎 Введи telegram_id пользователя:")


@router.message(Admin.waiting_user_id)
async def show_user(msg: types.Message, t, lang, db_user, state: FSMContext):
    await state.clear()
    raw = (msg.text or "").strip()
    if not raw.isdigit():
        await msg.answer("Это не число.")
        return
    user = await db_repo.get_user_by_tg(int(raw))
    if user is None:
        await msg.answer("Пользователь не найден.")
        return
    await msg.answer(await user_card_text(user), reply_markup=user_actions_kb(user.telegram_id))


@router.callback_query(F.data.startswith("admin_user:"))
async def open_user(cb: types.CallbackQuery, t, lang, db_user):
    tg_id = int(cb.data.split(":")[1])
    user = await db_repo.get_user_by_tg(tg_id)
    if user is None:
        await cb.answer("Не найден", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer(await user_card_text(user), reply_markup=user_actions_kb(tg_id))


@router.callback_query(F.data.startswith("admin_act:"))
async def admin_action(cb: types.CallbackQuery, t, lang, db_user):
    _, action, tg_id_s = cb.data.split(":")
    tg_id = int(tg_id_s)
    user = await db_repo.get_user_by_tg(tg_id)
    if user is None:
        await cb.answer("Не найден", show_alert=True)
        return

    try:
        if action == "add7":
            await sub_service.extend_subscription(user, 7)
            text = "✅ +7 дней"
        elif action == "sub7":
            await sub_service.extend_subscription(user, -7)
            text = "✅ -7 дней"
        elif action == "reset":
            new_sub = await sub_service.reset_link(user)
            text = f"✅ Новая ссылка: {settings.xui_sub_url.rstrip('/')}/{new_sub}"
        elif action == "block":
            await sub_service.set_blocked(user, True)
            text = "🚫 Заблокирован"
        elif action == "unblock":
            await sub_service.set_blocked(user, False)
            text = "✅ Разблокирован"
        elif action == "del":
            try:
                await XuiClient().delete_client(str(tg_id))
            except Exception:
                log.warning("panel delete failed for %s", tg_id)
            await db_repo.delete_user_full(tg_id)
            await cb.answer("🗑 Удалён из базы и панели", show_alert=True)
            return
        else:
            text = "❓"
    except Exception as e:
        log.exception("admin action failed")
        await cb.answer("❌")
        await cb.message.answer(f"Ошибка: {h(str(e))}")
        return

    await cb.answer(text, show_alert=True)
    fresh = await db_repo.get_user_by_tg(tg_id)
    await cb.message.answer(await user_card_text(fresh), reply_markup=user_actions_kb(tg_id))


@router.callback_query(F.data == "admin:dealers")
async def dealers_list(cb: types.CallbackQuery, t, lang, db_user):
    dealers = await db_repo.list_dealers()
    await cb.answer()
    if not dealers:
        await cb.message.answer("Дилеров нет. Назначь: /setdealer ID",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text=BACK_ADMIN, callback_data="back:admin")]]))
        return
    text = "\n".join(
        f"🤝 {d.username or d.telegram_id} — {float(d.dealer_balance)} кр." for d in dealers
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"➕ {d.username or d.telegram_id}",
                              callback_data=f"admin_topup:{d.telegram_id}")]
        for d in dealers
    ] + [[InlineKeyboardButton(text=BACK_ADMIN, callback_data="back:admin")]])
    await cb.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("admin_topup:"))
async def ask_topup(cb: types.CallbackQuery, t, lang, db_user, state: FSMContext):
    tg_id = int(cb.data.split(":")[1])
    await state.update_data(dealer_tg=tg_id)
    await state.set_state(Admin.waiting_topup_amount)
    await cb.answer()
    await cb.message.answer(f"Введи сумму для {tg_id}:")


@router.message(Admin.waiting_topup_amount)
async def do_topup(msg: types.Message, t, lang, db_user, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        amount = float((msg.text or "").strip())
    except ValueError:
        await msg.answer("Это не число.")
        return
    dealer = await db_repo.get_user_by_tg(data["dealer_tg"])
    if dealer is None:
        await msg.answer("Дилер не найден.")
        return
    await db_repo.change_dealer_balance(dealer.id, amount)
    await db_repo.create_dealer_log(dealer.id, "topup", None,
                                    {"amount": amount, "by": db_user.telegram_id})
    await msg.answer(f"✅ Начислено {amount} → {dealer.username or dealer.telegram_id}")


@router.callback_query(F.data == "admin:logs")
async def show_logs(cb: types.CallbackQuery, t, lang, db_user):
    logs = await db_repo.list_recent_logs(20)
    await cb.answer()
    back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BACK_ADMIN, callback_data="back:admin")]])
    if not logs:
        await cb.message.answer("Логи пусты.", reply_markup=back)
        return
    lines = [
        f"• {lg.created_at:%d.%m %H:%M} | {lg.action} | дилер {lg.dealer_id}"
        + (f" | заказ #{lg.order_id}" if lg.order_id else "")
        for lg in logs
    ]
    await cb.message.answer("\n".join(lines), reply_markup=back)