"""Persian-first dealer commercial catalogue, phase 1 (not connected to checkout)."""
import asyncio
import logging
from html import escape as h
from secrets import token_hex
from weakref import WeakValueDictionary

from aiogram import F, Router, types
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import InlineKeyboardMarkup

from app.config import settings
from app.keyboards.builders import admin_menu_kb, dealer_menu_kb, raw_btn
from app.middlewares.i18n import I18nMiddleware
from app.repositories import commercial_catalog_repo as catalog
from app.utils.menu import send_with_logo

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
router.message.middleware(I18nMiddleware())
router.callback_query.middleware(I18nMiddleware())

_locks: WeakValueDictionary[StorageKey, asyncio.Lock] = WeakValueDictionary()
FIELD_KEYS = {
    "name_fa": "catalog_field_name",
    "retail_toman": "catalog_field_price",
    "period_days": "catalog_field_days",
    "devices": "catalog_field_devices",
    "traffic_gb": "catalog_field_traffic",
}
FIELD_NAMES = {
    "name": "name_fa",
    "price": "retail_toman",
    "days": "period_days",
    "devices": "devices",
    "traffic": "traffic_gb",
}
STEPS = [
    ("name_fa", "catalog_new_name"),
    ("period_days", "catalog_new_days"),
    ("devices", "catalog_new_devices"),
    ("traffic_gb", "catalog_new_traffic"),
    ("retail_toman", "catalog_new_price"),
]


class CatalogNew(StatesGroup):
    name_fa = State()
    period_days = State()
    devices = State()
    traffic_gb = State()
    retail_toman = State()
    preview = State()


class CatalogEdit(StatesGroup):
    value = State()


def _guard(db_user) -> bool:
    return db_user.telegram_id in settings.admin_list or (
        db_user.role == "dealer" and not db_user.is_blocked
    )


def _lock(state: FSMContext) -> asyncio.Lock:
    lock = _locks.get(state.key)
    if lock is None:
        lock = _locks[state.key] = asyncio.Lock()
    return lock


async def clear_catalog_draft(state: FSMContext) -> None:
    async with _lock(state):
        if await state.get_state() in (CatalogNew.__all_states_names__ + CatalogEdit.__all_states_names__):
            await state.clear()


async def _denied(callback: types.CallbackQuery, t, db_user) -> bool:
    if _guard(db_user):
        return False
    await callback.answer(t("catalog_denied"), show_alert=True)
    return True


def _keyboard(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _cancel(t, data: str = "cat:home"):
    return _keyboard([[raw_btn(t("catalog_cancel"), data)]])


def _back_to(t, data: str = "cat:home"):
    return _keyboard([[raw_btn(t("btn_back"), data)]])


def _price(value: int) -> str:
    return f"{value:,}".replace(",", "٬") + " تومان"


def _number(raw: str | None) -> int:
    clean = (raw or "").strip().replace("٬", "").replace(",", "").replace(" ", "")
    if not clean.isdecimal():
        raise catalog.CatalogInvalid("Expected a positive whole number")
    return int(clean)


def _validate(field: str, raw: str | None):
    result = raw if field == "name_fa" else _number(raw)
    return catalog._validate_fields({field: result})[field]


def _status(t, item) -> str:
    return t("catalog_status_" + item.status)


async def _home(target, t, user):
    numbers = await catalog.counts(user.telegram_id)
    rows = [
        [raw_btn(t("catalog_tariffs"), "cat:list:0")],
        [raw_btn(t("catalog_new"), "cat:new")],
        [raw_btn(t("catalog_import"), "cat:import")],
        [raw_btn(t("catalog_server_locked"), "cat:server")],
        [raw_btn(t("btn_back"), "cat:exit")],
    ]
    await send_with_logo(
        target,
        t("catalog_heading", total=numbers["all"], published=numbers["published"],
          draft=numbers["draft"], paused=numbers["paused"], archived=numbers["archived"]),
        reply_markup=_keyboard(rows),
    )


async def _list(target, t, actor: int, page: int):
    page = max(0, page)
    items, total = await catalog.list_tariffs(actor, page=page)
    pages = max(1, (total + 5) // 6)
    if page >= pages:
        page = pages - 1
        items, total = await catalog.list_tariffs(actor, page=page)
    rows = [
        [raw_btn(f"{_status(t, item).split()[0]} #{item.id} · {item.name_fa[:35]}",
                 f"cat:card:{item.id}:{page}")]
        for item in items
    ]
    navigation = []
    if page:
        navigation.append(raw_btn("⬅️", f"cat:list:{page-1}"))
    if page + 1 < pages:
        navigation.append(raw_btn("➡️", f"cat:list:{page+1}"))
    if navigation:
        rows.append(navigation)
    rows.extend([
        [raw_btn(t("catalog_new"), "cat:new")],
        [raw_btn(t("btn_back"), "cat:home")],
    ])
    await send_with_logo(
        target, t("catalog_list", total=total, page=page + 1, pages=pages),
        reply_markup=_keyboard(rows),
    )


async def _card(target, t, actor: int, item_id: int, page: int):
    item = await catalog.get_tariff(actor, item_id)
    if item is None:
        await send_with_logo(target, t("catalog_missing"), reply_markup=_back_to(t, f"cat:list:{page}"))
        return
    text = t(
        "catalog_card", name=h(item.name_fa), sku=h(item.sku),
        status=_status(t, item), days=item.period_days, devices=item.devices,
        traffic=item.traffic_gb, price=_price(item.retail_toman), revision=item.revision,
    ) + "\n\n" + t("catalog_not_live")
    if item.status != "archived" and not catalog.supports_issuance(item):
        text += "\n\n" + t("catalog_unsupported")
    rows = []
    if item.status != "archived":
        rows.extend([
            [raw_btn(t("catalog_price"), f"cat:edit:price:{item_id}:{item.revision}:{page}"),
             raw_btn(t("catalog_name"), f"cat:edit:name:{item_id}:{item.revision}:{page}")],
        ])
        if item.status != "published":
            rows.extend([
                [raw_btn(t("catalog_days"), f"cat:edit:days:{item_id}:{item.revision}:{page}"),
                 raw_btn(t("catalog_devices"), f"cat:edit:devices:{item_id}:{item.revision}:{page}")],
                [raw_btn(t("catalog_traffic"), f"cat:edit:traffic:{item_id}:{item.revision}:{page}")],
            ])
        if item.status == "draft" and catalog.supports_issuance(item):
            rows.append([raw_btn(t("catalog_publish"), f"cat:do:publish:{item_id}:{item.revision}:{page}")])
        elif item.status == "published":
            rows.append([raw_btn(t("catalog_pause"), f"cat:do:pause:{item_id}:{item.revision}:{page}")])
        elif item.status == "paused" and catalog.supports_issuance(item):
            rows.append([raw_btn(t("catalog_resume"), f"cat:do:resume:{item_id}:{item.revision}:{page}")])
        rows.append([raw_btn(t("catalog_archive"), f"cat:ask_archive:{item_id}:{item.revision}:{page}")])
    rows.append([raw_btn(t("catalog_clone"), f"cat:clone:{item_id}:{page}")])
    rows.append([raw_btn(t("btn_back"), f"cat:list:{page}")])
    await send_with_logo(target, text, reply_markup=_keyboard(rows))


@router.callback_query(F.data.in_({"cat:open", "cat:home"}))
async def catalog_home(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if await _denied(callback, t, db_user):
        return
    await clear_catalog_draft(state)
    await callback.answer()
    await _home(callback, t, db_user)


@router.callback_query(F.data == "cat:exit")
async def exit_catalog(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if await _denied(callback, t, db_user):
        return
    await clear_catalog_draft(state)
    await callback.answer()
    markup = admin_menu_kb() if db_user.telegram_id in settings.admin_list else dealer_menu_kb(t)
    label = ("👑 Админ-меню:\n🔧 /testvpn | /setdealer | /topup"
             if db_user.telegram_id in settings.admin_list else t("dealer_menu_text"))
    await send_with_logo(callback, label, reply_markup=markup)


@router.callback_query(F.data == "cat:server")
async def server_locked(callback: types.CallbackQuery, t, db_user):
    if await _denied(callback, t, db_user):
        return
    await callback.answer(t("catalog_server_locked"), show_alert=True)


@router.callback_query(F.data.startswith("cat:list:"))
async def catalog_list(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if await _denied(callback, t, db_user):
        return
    await clear_catalog_draft(state)
    try:
        page = int(callback.data.split(":")[2])
    except ValueError:
        page = 0
    await callback.answer()
    await _list(callback, t, db_user.telegram_id, page)


@router.callback_query(F.data.startswith("cat:card:"))
async def catalog_card(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if await _denied(callback, t, db_user):
        return
    await clear_catalog_draft(state)
    try:
        _, _, raw_id, raw_page = callback.data.split(":")
        item_id, page = int(raw_id), int(raw_page)
    except (ValueError, TypeError):
        await callback.answer(t("catalog_missing"), show_alert=True)
        return
    await callback.answer()
    await _card(callback, t, db_user.telegram_id, item_id, page)


@router.callback_query(F.data == "cat:import")
async def catalog_import(callback: types.CallbackQuery, t, db_user):
    if await _denied(callback, t, db_user):
        return
    await callback.answer()
    count = await catalog.import_legacy(db_user.telegram_id)
    await send_with_logo(
        callback, t("catalog_imported", count=count),
        reply_markup=_back_to(t, "cat:list:0"),
    )


@router.callback_query(F.data == "cat:new")
async def catalog_new(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if await _denied(callback, t, db_user):
        return
    await clear_catalog_draft(state)
    await state.set_data({"catalog_nonce": token_hex(6)})
    await state.set_state(CatalogNew.name_fa)
    await callback.answer()
    await send_with_logo(callback, t("catalog_new_name"), reply_markup=_cancel(t))


async def _next_step(message: types.Message, state: FSMContext, t, field: str):
    try:
        value = _validate(field, message.text)
    except catalog.CatalogInvalid:
        await message.answer(t("catalog_invalid"))
        return
    await state.update_data(**{field: value})
    i = next(i for i, (key, _) in enumerate(STEPS) if key == field)
    if i + 1 < len(STEPS):
        next_field, prompt = STEPS[i + 1]
        await state.set_state(getattr(CatalogNew, next_field))
        await message.answer(t(prompt), reply_markup=_cancel(t))
        return
    data = await state.get_data()
    await state.set_state(CatalogNew.preview)
    await message.answer(
        t("catalog_new_preview",
          name=h(data["name_fa"]), days=data["period_days"], devices=data["devices"],
          traffic=data["traffic_gb"], price=_price(data["retail_toman"])),
        reply_markup=_keyboard([
            [raw_btn(t("catalog_confirm"), f"cat:save:{data['catalog_nonce']}")],
            [raw_btn(t("catalog_cancel"), "cat:home")],
        ]),
    )


@router.message(CatalogNew.name_fa)
async def new_name(message: types.Message, state: FSMContext, t, db_user):
    if _guard(db_user):
        await _next_step(message, state, t, "name_fa")


@router.message(CatalogNew.period_days)
async def new_days(message: types.Message, state: FSMContext, t, db_user):
    if _guard(db_user):
        await _next_step(message, state, t, "period_days")


@router.message(CatalogNew.devices)
async def new_devices(message: types.Message, state: FSMContext, t, db_user):
    if _guard(db_user):
        await _next_step(message, state, t, "devices")


@router.message(CatalogNew.traffic_gb)
async def new_traffic(message: types.Message, state: FSMContext, t, db_user):
    if _guard(db_user):
        await _next_step(message, state, t, "traffic_gb")


@router.message(CatalogNew.retail_toman)
async def new_price(message: types.Message, state: FSMContext, t, db_user):
    if _guard(db_user):
        await _next_step(message, state, t, "retail_toman")


@router.callback_query(F.data.startswith("cat:save:"))
async def catalog_save(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if await _denied(callback, t, db_user):
        return
    async with _lock(state):
        data = await state.get_data()
        if (await state.get_state() != CatalogNew.preview.state
            or callback.data != f"cat:save:{data.get('catalog_nonce')}"):
            await callback.answer(t("catalog_missing"), show_alert=True)
            return
        try:
            item = await catalog.create_tariff(
                db_user.telegram_id, **{field: data[field] for field, _ in STEPS},
            )
        except catalog.CatalogInvalid:
            await callback.answer(t("catalog_invalid"), show_alert=True)
            return
        await state.clear()
    await callback.answer()
    await _card(callback, t, db_user.telegram_id, item.id, 0)


@router.callback_query(F.data.startswith("cat:edit:"))
async def catalog_edit(callback: types.CallbackQuery, state: FSMContext, t, db_user):
    if await _denied(callback, t, db_user):
        return
    try:
        _, _, key, raw_id, raw_rev, raw_page = callback.data.split(":")
        field = FIELD_NAMES[key]
        item_id, revision, page = int(raw_id), int(raw_rev), int(raw_page)
    except (KeyError, ValueError):
        await callback.answer(t("catalog_missing"), show_alert=True)
        return
    item = await catalog.get_tariff(db_user.telegram_id, item_id)
    if (item is None or item.status == "archived" or revision != item.revision
        or (item.status == "published" and field not in {"retail_toman", "name_fa"})):
        await callback.answer(t("catalog_conflict"), show_alert=True)
        return
    await clear_catalog_draft(state)
    await state.set_data({
        "catalog_item_id": item_id, "catalog_revision": revision,
        "catalog_page": page, "catalog_field": field,
    })
    await state.set_state(CatalogEdit.value)
    await callback.answer()
    await send_with_logo(callback,
        t("catalog_edit_prompt", field=t(FIELD_KEYS[field])),
        reply_markup=_cancel(t, f"cat:card:{item_id}:{page}"),
    )


@router.message(CatalogEdit.value)
async def catalog_edit_value(message: types.Message, state: FSMContext, t, db_user):
    if not _guard(db_user):
        return
    async with _lock(state):
        data = await state.get_data()
        field = data.get("catalog_field")
        if field not in FIELD_KEYS:
            await state.clear()
            await message.answer(t("catalog_missing"))
            return
        try:
            value = _validate(field, message.text)
            item = await catalog.update_tariff(
                db_user.telegram_id, data["catalog_item_id"], data["catalog_revision"],
                action="edit", changes={field: value},
            )
        except catalog.CatalogInvalid:
            await message.answer(t("catalog_invalid"))
            return
        except catalog.CatalogConflict:
            await state.clear()
            await message.answer(t("catalog_conflict"))
            return
        await state.clear()
    await message.answer(
        t("catalog_updated"),
        reply_markup=_back_to(t, f"cat:card:{item.id}:{data['catalog_page']}"),
    )


@router.callback_query(F.data.startswith("cat:do:"))
async def catalog_action(callback: types.CallbackQuery, t, db_user):
    if await _denied(callback, t, db_user):
        return
    try:
        _, _, action, raw_id, raw_revision, raw_page = callback.data.split(":")
        if action not in {"publish", "pause", "resume", "archive"}:
            raise ValueError
        item_id, revision, page = int(raw_id), int(raw_revision), int(raw_page)
    except ValueError:
        await callback.answer(t("catalog_missing"), show_alert=True)
        return
    try:
        await catalog.update_tariff(
            db_user.telegram_id, item_id, revision, action=action, changes={},
        )
    except catalog.CatalogConflict:
        await callback.answer(t("catalog_conflict"), show_alert=True)
        return
    except catalog.CatalogInvalid:
        await callback.answer(t("catalog_invalid"), show_alert=True)
        return
    await callback.answer()
    await _card(callback, t, db_user.telegram_id, item_id, page)


@router.callback_query(F.data.startswith("cat:ask_archive:"))
async def catalog_archive_prompt(callback: types.CallbackQuery, t, db_user):
    if await _denied(callback, t, db_user):
        return
    try:
        _, _, raw_id, raw_revision, raw_page = callback.data.split(":")
        item_id, revision, page = int(raw_id), int(raw_revision), int(raw_page)
    except ValueError:
        await callback.answer(t("catalog_missing"), show_alert=True)
        return
    item = await catalog.get_tariff(db_user.telegram_id, item_id)
    if item is None or item.revision != revision or item.status == "archived":
        await callback.answer(t("catalog_conflict"), show_alert=True)
        return
    await callback.answer()
    await send_with_logo(
        callback, t("catalog_confirm_archive", name=h(item.name_fa)),
        reply_markup=_keyboard([
            [raw_btn(t("catalog_confirm"), f"cat:do:archive:{item_id}:{revision}:{page}")],
            [raw_btn(t("btn_back"), f"cat:card:{item_id}:{page}")],
        ]),
    )


@router.callback_query(F.data.startswith("cat:clone:"))
async def catalog_clone(callback: types.CallbackQuery, t, db_user):
    if await _denied(callback, t, db_user):
        return
    try:
        _, _, raw_id, raw_page = callback.data.split(":")
        item_id, page = int(raw_id), int(raw_page)
    except ValueError:
        await callback.answer(t("catalog_missing"), show_alert=True)
        return
    try:
        item = await catalog.clone_tariff(db_user.telegram_id, item_id)
    except catalog.CatalogInvalid:
        await callback.answer(t("catalog_invalid"), show_alert=True)
        return
    await callback.answer()
    await _card(callback, t, db_user.telegram_id, item.id, page)
