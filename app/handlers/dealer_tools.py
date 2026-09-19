import logging
import re
from datetime import timedelta
from html import escape as h
from uuid import uuid4

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.database.models import utcnow
from app.keyboards.builders import back_kb, dealer_menu_kb, main_menu_kb, raw_btn
from app.middlewares.i18n import I18nMiddleware
from app.repositories import db_repo
from app.repositories import dealer_tools_repo
from app.services.vpn_provider import get_vpn_provider, subscription_link
from app.utils.menu import send_main_menu, send_with_logo

log = logging.getLogger(__name__)
router = Router()
router.callback_query.middleware(I18nMiddleware())
router.message.middleware(I18nMiddleware())

MAX_TEST_DAYS = 30
MAX_TEST_TRAFFIC_GB = 10
MAX_PROMO_DAYS = 365
MAX_PROMO_USES = 1000
CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")


class DealerTestForm(StatesGroup):
    name = State()
    days = State()
    traffic = State()
    confirm = State()


class DealerTestManage(StatesGroup):
    extend_days = State()


class DealerPromoForm(StatesGroup):
    code = State()
    kind = State()
    days = State()
    traffic = State()
    validity = State()
    uses = State()
    confirm = State()


class PromoRedeem(StatesGroup):
    code = State()


async def _dealer(callback: types.CallbackQuery, user) -> bool:
    if user.role == "dealer":
        return True
    await callback.answer("Dealer access only", show_alert=True)
    return False


def _back(t, callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[raw_btn(t("btn_back"), callback)]]
    )


def _test_confirm_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn(t("dealer_confirm"), "dealer:test:confirm")],
        [raw_btn(t("btn_back"), "dealer:test:back:traffic")],
        [raw_btn(t("btn_cancel"), "back:dealer")],
    ])


def _test_card_kb(t, test, page: int, link: str | None) -> InlineKeyboardMarkup:
    rows = []
    if link and len(link) <= 256:
        rows.append([InlineKeyboardButton(
            text=t("btn_copy_link"), copy_text=CopyTextButton(text=link)
        )])
    if test.status == "active":
        rows.append([raw_btn(t("dealer_test_extend"), f"dealer:test:extend:{test.id}:{page}")])
        rows.append([raw_btn(t("dealer_test_disable"), f"dealer:test:disable:{test.id}:{page}")])
    rows.append([raw_btn(t("dealer_test_delete"), f"dealer:test:delete:{test.id}:{page}")])
    rows.append([raw_btn(t("btn_back"), f"dealer:tests:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _promo_kind_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn(t("promo_kind_days"), "dealer:promo:kind:days")],
        [raw_btn(t("promo_kind_days_traffic"), "dealer:promo:kind:days_traffic")],
        [raw_btn(t("btn_back"), "dealer:promos")],
    ])


def _promo_confirm_kb(t) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [raw_btn(t("dealer_confirm"), "dealer:promo:confirm")],
        [raw_btn(t("btn_back"), "dealer:promo:back:uses")],
        [raw_btn(t("btn_cancel"), "dealer:promos")],
    ])


def _promo_card_kb(t, promo, page: int) -> InlineKeyboardMarkup:
    rows = []
    if promo.is_active:
        rows.append([raw_btn(t("promo_deactivate"), f"dealer:promo:disable:{promo.id}:{page}")])
    rows.append([raw_btn(t("btn_back"), f"dealer:promos:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_dealer_menu(callback, t) -> None:
    await send_with_logo(callback, t("dealer_menu_text"), reply_markup=dealer_menu_kb(t))


async def _ask_test_name(target, t) -> None:
    await send_with_logo(target, t("dealer_test_ask_name"), reply_markup=_back(t, "back:dealer"))


async def _ask_test_days(target, t) -> None:
    await send_with_logo(target, t("dealer_test_ask_days", maximum=MAX_TEST_DAYS), reply_markup=_back(t, "dealer:test:back:name"))


async def _ask_test_traffic(target, t) -> None:
    await send_with_logo(target, t("dealer_test_ask_traffic", maximum=MAX_TEST_TRAFFIC_GB), reply_markup=_back(t, "dealer:test:back:days"))


@router.callback_query(F.data == "dealer:test_link")
async def test_start(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    await state.clear()
    await state.set_state(DealerTestForm.name)
    await callback.answer()
    await _ask_test_name(callback, t)


@router.callback_query(F.data.startswith("dealer:test:back:"))
async def test_back(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    step = callback.data.rsplit(":", 1)[-1]
    await callback.answer()
    if step == "name":
        await state.set_state(DealerTestForm.name)
        await _ask_test_name(callback, t)
    elif step == "days":
        await state.set_state(DealerTestForm.days)
        await _ask_test_days(callback, t)
    elif step == "traffic":
        await state.set_state(DealerTestForm.traffic)
        await _ask_test_traffic(callback, t)


@router.message(DealerTestForm.name)
async def test_name(message: types.Message, t, db_user, state: FSMContext):
    if db_user.role != "dealer":
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name or len(name) > 64:
        await message.answer(t("dealer_test_name_invalid"))
        return
    await state.update_data(test_name=name)
    await state.set_state(DealerTestForm.days)
    await _ask_test_days(message, t)


@router.message(DealerTestForm.days)
async def test_days(message: types.Message, t, db_user, state: FSMContext):
    try:
        days = int((message.text or "").strip())
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_TEST_DAYS:
        await message.answer(t("dealer_number_range", minimum=1, maximum=MAX_TEST_DAYS))
        return
    await state.update_data(test_days=days)
    await state.set_state(DealerTestForm.traffic)
    await _ask_test_traffic(message, t)


@router.message(DealerTestForm.traffic)
async def test_traffic(message: types.Message, t, db_user, state: FSMContext):
    try:
        traffic = int((message.text or "").strip())
    except ValueError:
        traffic = 0
    if not 1 <= traffic <= MAX_TEST_TRAFFIC_GB:
        await message.answer(t("dealer_number_range", minimum=1, maximum=MAX_TEST_TRAFFIC_GB))
        return
    await state.update_data(test_traffic=traffic)
    data = await state.get_data()
    await state.set_state(DealerTestForm.confirm)
    await message.answer(
        t("dealer_test_confirm", name=h(data["test_name"]), days=data["test_days"], traffic=traffic),
        reply_markup=_test_confirm_kb(t),
    )


@router.callback_query(F.data == "dealer:test:confirm")
async def test_confirm(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    data = await state.get_data()
    try:
        name = data["test_name"]
        days = int(data["test_days"])
        traffic = int(data["test_traffic"])
    except (KeyError, ValueError):
        await state.clear()
        await callback.answer(t("dealer_flow_expired"), show_alert=True)
        return

    status, slot_id, used = await db_repo.reserve_dealer_test_slot(
        dealer_id=db_user.id,
        daily_limit=settings.dealer_test_daily_limit,
        tz_name=settings.dealer_test_timezone,
        days=days,
        traffic_gb=traffic,
    )
    if status == "limit":
        await callback.answer(t("dealer_test_daily_limit", limit=settings.dealer_test_daily_limit), show_alert=True)
        return
    if status != "reserved" or slot_id is None:
        await callback.answer(t("dealer_action_failed"), show_alert=True)
        return

    email = f"dtest-{db_user.id}-{uuid4().hex[:16]}"
    await callback.answer(t("dealer_processing"))
    try:
        provider = get_vpn_provider()
        await provider.add_client(email=email, days=days, limit_ip=1, traffic_gb=traffic)
        link = await subscription_link(email)
        test = await dealer_tools_repo.create_dealer_test(
            db_user.id, name, email, utcnow() + timedelta(days=days), traffic
        )
        await db_repo.complete_dealer_test_slot(slot_id, email)
    except Exception as exc:
        await db_repo.fail_dealer_test_slot(slot_id, str(exc))
        try:
            await get_vpn_provider().delete_client(email)
        except Exception:
            pass
        log.exception("Dealer test creation failed")
        await callback.message.answer(t("dealer_action_failed"))
        return

    await state.clear()
    remaining = max(0, settings.dealer_test_daily_limit - used)
    await callback.message.answer(
        t("dealer_test_created", name=h(test.client_name), days=days, traffic=traffic, link=h(link), remaining=remaining, limit=settings.dealer_test_daily_limit),
        reply_markup=_test_card_kb(t, test, 0, link),
    )


async def _show_tests(callback, t, dealer_id: int, page: int) -> None:
    page = max(0, page)
    items, total = await dealer_tools_repo.list_dealer_tests(dealer_id, page)
    pages = max(1, (total + 4) // 5)
    if page >= pages:
        page = pages - 1
        items, total = await dealer_tools_repo.list_dealer_tests(dealer_id, page)
    rows = [[raw_btn(
        f"{'🟢' if item.status == 'active' else '🔴'} #{item.id} · {item.client_name[:24]}",
        f"dealer:testcard:{item.id}:{page}",
    )] for item in items]
    nav = []
    if page:
        nav.append(raw_btn("⬅️", f"dealer:tests:{page - 1}"))
    if page + 1 < pages:
        nav.append(raw_btn("➡️", f"dealer:tests:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([raw_btn(t("dealer_test_create"), "dealer:test_link")])
    rows.append([raw_btn(t("btn_back"), "back:dealer")])
    await callback.answer()
    await send_with_logo(callback, t("dealer_tests_title", total=total, page=page + 1, pages=pages), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "dealer:tests")
async def tests_first(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    await state.clear()
    await _show_tests(callback, t, db_user.id, 0)


@router.callback_query(F.data.startswith("dealer:tests:"))
async def tests_page(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    try:
        page = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _show_tests(callback, t, db_user.id, page)


@router.callback_query(F.data.startswith("dealer:testcard:"))
async def test_card(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    _, _, raw_id, raw_page = callback.data.split(":")
    test = await dealer_tools_repo.get_dealer_test(db_user.id, int(raw_id))
    if test is None:
        await callback.answer(t("dealer_not_found"), show_alert=True)
        return
    link = None
    try:
        link = await subscription_link(test.xui_email)
    except Exception:
        pass
    await callback.answer()
    await send_with_logo(
        callback,
        t("dealer_test_card", name=h(test.client_name), traffic=test.traffic_limit_gb, expire=test.expire_at.strftime("%d.%m.%Y"), status=t("dealer_test_status_" + test.status), link=h(link or "—")),
        reply_markup=_test_card_kb(t, test, int(raw_page), link),
    )


@router.callback_query(F.data.startswith("dealer:test:disable:"))
async def test_disable(callback: types.CallbackQuery, t, db_user):
    if not await _dealer(callback, db_user):
        return
    _, _, _, raw_id, raw_page = callback.data.split(":")
    test = await dealer_tools_repo.get_dealer_test(db_user.id, int(raw_id))
    if test is None:
        await callback.answer(t("dealer_not_found"), show_alert=True)
        return
    try:
        await get_vpn_provider().set_enabled(test.xui_email, False)
        await dealer_tools_repo.set_dealer_test_status(db_user.id, test.id, "disabled")
    except Exception:
        log.exception("Dealer test disable failed")
        await callback.answer(t("dealer_action_failed"), show_alert=True)
        return
    await callback.answer(t("dealer_test_disabled"))
    await _show_tests(callback, t, db_user.id, int(raw_page))


@router.callback_query(F.data.startswith("dealer:test:delete:"))
async def test_delete(callback: types.CallbackQuery, t, db_user):
    if not await _dealer(callback, db_user):
        return
    _, _, _, raw_id, raw_page = callback.data.split(":")
    test = await dealer_tools_repo.get_dealer_test(db_user.id, int(raw_id))
    if test is None:
        await callback.answer(t("dealer_not_found"), show_alert=True)
        return
    try:
        await get_vpn_provider().delete_client(test.xui_email)
        await dealer_tools_repo.set_dealer_test_status(db_user.id, test.id, "deleted")
    except Exception:
        log.exception("Dealer test delete failed")
        await callback.answer(t("dealer_action_failed"), show_alert=True)
        return
    await callback.answer(t("dealer_test_deleted"))
    await _show_tests(callback, t, db_user.id, int(raw_page))


@router.callback_query(F.data.startswith("dealer:test:extend:"))
async def test_extend_start(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    _, _, _, raw_id, raw_page = callback.data.split(":")
    await state.set_state(DealerTestManage.extend_days)
    await state.update_data(managed_test_id=int(raw_id), managed_test_page=int(raw_page))
    await callback.answer()
    await send_with_logo(callback, t("dealer_test_extend_ask", maximum=MAX_TEST_DAYS), reply_markup=_back(t, f"dealer:testcard:{raw_id}:{raw_page}"))


@router.message(DealerTestManage.extend_days)
async def test_extend_days(message: types.Message, t, db_user, state: FSMContext):
    try:
        days = int((message.text or "").strip())
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_TEST_DAYS:
        await message.answer(t("dealer_number_range", minimum=1, maximum=MAX_TEST_DAYS))
        return
    data = await state.get_data()
    test = await dealer_tools_repo.get_dealer_test(db_user.id, int(data.get("managed_test_id", 0)))
    if test is None or test.status != "active":
        await state.clear()
        await message.answer(t("dealer_not_found"))
        return
    try:
        await get_vpn_provider().extend_client(test.xui_email, days)
        new_expire = max(test.expire_at, utcnow()) + timedelta(days=days)
        await dealer_tools_repo.update_dealer_test_expiry(db_user.id, test.id, new_expire)
    except Exception:
        log.exception("Dealer test extend failed")
        await message.answer(t("dealer_action_failed"))
        return
    await state.clear()
    await message.answer(t("dealer_test_extended", days=days, expire=new_expire.strftime("%d.%m.%Y")))


async def _show_promos(callback, t, dealer_id: int, page: int) -> None:
    page = max(0, page)
    items, total = await dealer_tools_repo.list_dealer_promos(dealer_id, page)
    pages = max(1, (total + 4) // 5)
    rows = [[raw_btn(
        f"{'🟢' if promo.is_active else '🔴'} {promo.code} · {promo.uses_count}/{promo.max_uses}",
        f"dealer:promocard:{promo.id}:{page}",
    )] for promo in items]
    rows.append([raw_btn(t("promo_create"), "dealer:promo:new")])
    rows.append([raw_btn(t("btn_back"), "back:dealer")])
    await callback.answer()
    await send_with_logo(callback, t("promo_list_title", total=total, page=page + 1, pages=pages), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "dealer:promos")
async def promos_first(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    await state.clear()
    await _show_promos(callback, t, db_user.id, 0)


@router.callback_query(F.data.startswith("dealer:promos:"))
async def promos_page(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    try:
        page = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _show_promos(callback, t, db_user.id, page)


@router.callback_query(F.data == "dealer:promo:new")
async def promo_new(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    await state.clear()
    await state.set_state(DealerPromoForm.code)
    await callback.answer()
    await send_with_logo(callback, t("promo_ask_code"), reply_markup=_back(t, "dealer:promos"))


@router.message(DealerPromoForm.code)
async def promo_code(message: types.Message, t, db_user, state: FSMContext):
    code = (message.text or "").strip().upper()
    if not CODE_RE.fullmatch(code):
        await message.answer(t("promo_code_invalid"))
        return
    await state.update_data(promo_code=code)
    await state.set_state(DealerPromoForm.kind)
    await message.answer(t("promo_ask_kind"), reply_markup=_promo_kind_kb(t))


@router.callback_query(F.data.startswith("dealer:promo:kind:"))
async def promo_kind(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    kind = callback.data.rsplit(":", 1)[-1]
    if kind not in {"days", "days_traffic"}:
        return
    await state.update_data(promo_kind=kind)
    await state.set_state(DealerPromoForm.days)
    await callback.answer()
    await send_with_logo(callback, t("promo_ask_days", maximum=MAX_PROMO_DAYS), reply_markup=_back(t, "dealer:promo:new"))


@router.message(DealerPromoForm.days)
async def promo_days(message: types.Message, t, db_user, state: FSMContext):
    try:
        days = int((message.text or "").strip())
    except ValueError:
        days = 0
    if not 1 <= days <= MAX_PROMO_DAYS:
        await message.answer(t("dealer_number_range", minimum=1, maximum=MAX_PROMO_DAYS))
        return
    await state.update_data(promo_days=days)
    data = await state.get_data()
    if data["promo_kind"] == "days_traffic":
        await state.set_state(DealerPromoForm.traffic)
        await message.answer(t("promo_ask_traffic", maximum=MAX_TEST_TRAFFIC_GB), reply_markup=_back(t, "dealer:promo:back:days"))
        return
    await state.update_data(promo_traffic=0)
    await state.set_state(DealerPromoForm.validity)
    await message.answer(t("promo_ask_validity", maximum=MAX_PROMO_DAYS), reply_markup=_back(t, "dealer:promo:back:days"))


@router.message(DealerPromoForm.traffic)
async def promo_traffic(message: types.Message, t, db_user, state: FSMContext):
    try:
        traffic = int((message.text or "").strip())
    except ValueError:
        traffic = 0
    if not 1 <= traffic <= MAX_TEST_TRAFFIC_GB:
        await message.answer(t("dealer_number_range", minimum=1, maximum=MAX_TEST_TRAFFIC_GB))
        return
    await state.update_data(promo_traffic=traffic)
    await state.set_state(DealerPromoForm.validity)
    await message.answer(t("promo_ask_validity", maximum=MAX_PROMO_DAYS), reply_markup=_back(t, "dealer:promo:back:traffic"))


@router.message(DealerPromoForm.validity)
async def promo_validity(message: types.Message, t, db_user, state: FSMContext):
    try:
        validity = int((message.text or "").strip())
    except ValueError:
        validity = 0
    if not 1 <= validity <= MAX_PROMO_DAYS:
        await message.answer(t("dealer_number_range", minimum=1, maximum=MAX_PROMO_DAYS))
        return
    await state.update_data(promo_validity=validity)
    await state.set_state(DealerPromoForm.uses)
    await message.answer(t("promo_ask_uses", maximum=MAX_PROMO_USES), reply_markup=_back(t, "dealer:promo:back:validity"))


@router.message(DealerPromoForm.uses)
async def promo_uses(message: types.Message, t, db_user, state: FSMContext):
    try:
        uses = int((message.text or "").strip())
    except ValueError:
        uses = 0
    if not 1 <= uses <= MAX_PROMO_USES:
        await message.answer(t("dealer_number_range", minimum=1, maximum=MAX_PROMO_USES))
        return
    await state.update_data(promo_uses=uses)
    data = await state.get_data()
    await state.set_state(DealerPromoForm.confirm)
    await message.answer(t("promo_confirm", code=h(data["promo_code"]), days=data["promo_days"], traffic=data["promo_traffic"], validity=data["promo_validity"], uses=uses), reply_markup=_promo_confirm_kb(t))


@router.callback_query(F.data.startswith("dealer:promo:back:"))
async def promo_back(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    step = callback.data.rsplit(":", 1)[-1]
    await callback.answer()
    if step == "days":
        await state.set_state(DealerPromoForm.days)
        await send_with_logo(callback, t("promo_ask_days", maximum=MAX_PROMO_DAYS), reply_markup=_back(t, "dealer:promo:new"))
    elif step == "traffic":
        await state.set_state(DealerPromoForm.traffic)
        await send_with_logo(callback, t("promo_ask_traffic", maximum=MAX_TEST_TRAFFIC_GB), reply_markup=_back(t, "dealer:promo:back:days"))
    elif step == "validity":
        await state.set_state(DealerPromoForm.validity)
        await send_with_logo(callback, t("promo_ask_validity", maximum=MAX_PROMO_DAYS), reply_markup=_back(t, "dealer:promo:back:days"))
    elif step == "uses":
        await state.set_state(DealerPromoForm.uses)
        await send_with_logo(callback, t("promo_ask_uses", maximum=MAX_PROMO_USES), reply_markup=_back(t, "dealer:promo:back:validity"))


@router.callback_query(F.data == "dealer:promo:confirm")
async def promo_confirm(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    if not await _dealer(callback, db_user):
        return
    data = await state.get_data()
    try:
        promo = await dealer_tools_repo.create_promo(
            db_user.id, data["promo_code"], data["promo_kind"], int(data["promo_days"]),
            int(data["promo_traffic"]), utcnow() + timedelta(days=int(data["promo_validity"])),
            int(data["promo_uses"]),
        )
    except ValueError:
        await callback.answer(t("promo_code_taken"), show_alert=True)
        return
    await state.clear()
    await callback.answer()
    await send_with_logo(callback, t("promo_created", code=h(promo.code)), reply_markup=_promo_card_kb(t, promo, 0))


@router.callback_query(F.data.startswith("dealer:promocard:"))
async def promo_card(callback: types.CallbackQuery, t, db_user):
    if not await _dealer(callback, db_user):
        return
    _, _, raw_id, raw_page = callback.data.split(":")
    promo = await dealer_tools_repo.get_dealer_promo(db_user.id, int(raw_id))
    if promo is None:
        await callback.answer(t("dealer_not_found"), show_alert=True)
        return
    await callback.answer()
    await send_with_logo(callback, t("promo_card", code=h(promo.code), days=promo.days, traffic=promo.traffic_gb, expire=promo.expire_at.strftime("%d.%m.%Y"), uses=promo.uses_count, maximum=promo.max_uses, status=t("promo_status_active" if promo.is_active else "promo_status_disabled")), reply_markup=_promo_card_kb(t, promo, int(raw_page)))


@router.callback_query(F.data.startswith("dealer:promo:disable:"))
async def promo_disable(callback: types.CallbackQuery, t, db_user):
    if not await _dealer(callback, db_user):
        return
    _, _, _, raw_id, raw_page = callback.data.split(":")
    if not await dealer_tools_repo.deactivate_promo(db_user.id, int(raw_id)):
        await callback.answer(t("dealer_action_failed"), show_alert=True)
        return
    await callback.answer(t("promo_disabled"))
    await _show_promos(callback, t, db_user.id, int(raw_page))


@router.callback_query(F.data == "menu:promo")
async def promo_redeem_start(callback: types.CallbackQuery, t, db_user, state: FSMContext):
    await state.clear()
    await state.set_state(PromoRedeem.code)
    await callback.answer()
    await send_with_logo(callback, t("promo_redeem_ask"), reply_markup=back_kb(t, "back:main"))


@router.message(PromoRedeem.code)
async def promo_redeem(message: types.Message, t, db_user, state: FSMContext):
    code = (message.text or "").strip().upper()
    status, promo = await dealer_tools_repo.reserve_promo_redemption(db_user.id, code)
    if status != "reserved" or promo is None:
        await state.clear()
        await message.answer(t("promo_redeem_" + status), reply_markup=main_menu_kb(t))
        return
    sub = await db_repo.get_subscription(db_user.id)
    if sub is None:
        await dealer_tools_repo.rollback_promo_redemption(db_user.id, promo.id)
        await state.clear()
        await message.answer(t("promo_redeem_no_subscription"), reply_markup=main_menu_kb(t))
        return
    try:
        provider = get_vpn_provider()
        await provider.extend_client(sub.xui_email, promo.days)
        if promo.traffic_gb:
            await provider.add_traffic(sub.xui_email, promo.traffic_gb)
        new_expire = max(sub.expire_at, utcnow()) + timedelta(days=promo.days)
        traffic = sub.traffic_limit_gb if sub.traffic_limit_gb == 0 else sub.traffic_limit_gb + promo.traffic_gb
        await db_repo.update_subscription(db_user.id, expire_at=new_expire, traffic_limit_gb=traffic)
    except Exception:
        await dealer_tools_repo.rollback_promo_redemption(db_user.id, promo.id)
        log.exception("Promo redemption provider failure")
        await state.clear()
        await message.answer(t("promo_redeem_failed"), reply_markup=main_menu_kb(t))
        return
    await state.clear()
    await message.answer(t("promo_redeem_success", days=promo.days, traffic=promo.traffic_gb, expire=new_expire.strftime("%d.%m.%Y")), reply_markup=main_menu_kb(t))
