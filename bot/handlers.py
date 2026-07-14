"""
Хэндлеры бота «Бензин рядом» — новая архитектура.

Flow: /start → выбор города → фильтры → АЗС → действия
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from aiogram import Dispatcher, F, Bot
from aiogram.filters import BaseFilter, Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    WebAppData,
    WebAppInfo,
)


from db import (
    _execute,
    _fetch,
    add_owner_station,
    add_report,
    add_review,
    add_subscription,
    activate_premium,
    find_nearest_stations,
    find_stations_by_address,
    find_stations_by_city,
    find_stations_by_name,
    get_or_create_user,
    get_owner_stations,
    get_pending_owner_applications,
    get_plan,
    get_station_by_id,
    get_station_current_status,
    get_station_rating,
    get_stations_with_statuses,
    get_user_id_by_telegram_id,
    get_user_premium,
    is_owner_of_station,
    is_premium,
    log_event,
    set_owner_station_verified,
    upsert_user,
)
from keyboards import (
    fuel_type_keyboard,
    main_menu_keyboard,
    main_inline_keyboard,
    report_status_keyboard,
    report_extras_keyboard,
    station_actions_keyboard,
    with_home_inline,
    city_keyboard,
    filters_keyboard,
    price_filter_keyboard,
    network_filter_keyboard,
    bug_report_keyboard,
    idea_keyboard,
    web_app_keyboard,
    report_city_keyboard,
    report_station_keyboard,
    report_address_results_keyboard,
    review_rating_keyboard,
    review_fuel_keyboard,
    BTN_FIND, BTN_REPORT, BTN_SUBSCRIBE, BTN_PROFILE,
    BTN_OWNER, BTN_MY_STATIONS, BTN_HELP, BTN_PREMIUM, BTN_HOME,
    BTN_APP, BTN_BUG, BTN_IDEA, BTN_DONATE, BTN_ROUTE, BTN_LINK, BTN_REFERRAL,
)
from utils import format_distance, format_station_card
from config import settings
from messages import (
    WELCOME_1, WELCOME_2, WELCOME_3,
    HELP_TEXT,
)

# Inline-фоллбэки для констант, которых нет в messages.py
OWNER_PROMPT = (
    "👋 <b>Привет! Я помогу стать владельцем АЗС.</b>\n\n"
    "Введи название АЗС или отправь геолокацию:"
)
PREMIUM_TRIAL = (
    "💎 <b>Пробный Premium на 7 дней бесплатно</b>\n\n"
    "• Push без задержек\n"
    "• Расширенный радиус (100 км)\n"
    "• Расширенная аналитика\n\n"
    "Активировать?"
)


def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

logger = logging.getLogger(__name__)


# === In-memory кеш для результатов поиска (TTL 60 сек) ===
import time as _time

_cache: dict[tuple, tuple[float, list]] = {}
CACHE_TTL_SEC = 60


def _cache_get(lat: float, lon: float, radius_km: int) -> list | None:
    key = (round(lat, 2), round(lon, 2), radius_km)
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, results = entry
    if _time.time() - ts > CACHE_TTL_SEC:
        _cache.pop(key, None)
        return None
    return results


def _cache_set(lat: float, lon: float, radius_km: int, results: list) -> None:
    key = (round(lat, 2), round(lon, 2), radius_km)
    _cache[key] = (_time.time(), results)


# === Monkey-patch: автоматически добавляем inline-кнопку «🏠 В начало» во все сообщения ===
_original_message_answer = Message.answer


async def _patched_message_answer(self, text, **kwargs):
    """Обёртка над Message.answer — добавляет кнопку «В начало» в inline_markup если есть."""
    markup = kwargs.get("reply_markup")
    if isinstance(markup, InlineKeyboardMarkup):
        has_home = any(
            btn.callback_data == "go_home"
            for row in markup.inline_keyboard
            for btn in row
        )
        if not has_home:
            kwargs["reply_markup"] = with_home_inline(markup)
    return await _original_message_answer(self, text, **kwargs)


Message.answer = _patched_message_answer  # type: ignore[assignment]


# === FSM: подписки ===
class SubscribeStates(StatesGroup):
    waiting_geo = State()
    waiting_radius = State()


# === FSM: баг-репорт ===
class BugReportStates(StatesGroup):
    waiting_description = State()


# === FSM: предложение ===
class IdeaStates(StatesGroup):
    waiting_idea = State()


# === FSM: поиск АЗС по адресу ===
class ReportAddressStates(StatesGroup):
    waiting_query = State()


# === FSM: расширенный отчёт (цена/лимит/канистры/очередь) ===
class ReportExtrasStates(StatesGroup):
    waiting_price = State()
    waiting_limit = State()
    waiting_queue = State()


# === FSM: отзыв о качестве бензина ===
class ReviewStates(StatesGroup):
    waiting_comment = State()


# === FSM: ввод кода привязки ===
class LinkStates(StatesGroup):
    waiting_code = State()


# === Проверка подписки на канал ===
_SUBSCRIBE_CACHE: dict[int, bool] = {}
_SUBSCRIBE_CACHE_TTL = 300  # 5 минут

async def _check_subscription(bot: Bot, user_id: int) -> bool:
    """Проверяет, подписан ли пользователь на канал. Кеширует результат 5 мин."""
    import time
    now = time.time()
    cached = _SUBSCRIBE_CACHE.get(user_id)
    if cached is not None and now - cached[1] < _SUBSCRIBE_CACHE_TTL:
        return cached[0]

    channel = settings.SUBSCRIBE_CHANNEL_TG
    if not channel:
        logger.warning("_check_subscription: SUBSCRIBE_CHANNEL_TG is empty! Skipping check.")
        return True  # если канал не задан — пропускаем проверку

    try:
        chat_id = f"@{channel}" if not channel.startswith("-") else int(channel)
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        is_sub = member.status in ("member", "administrator", "creator")
        logger.info("_check_subscription: user=%d channel=%s status=%s is_sub=%s", user_id, chat_id, member.status, is_sub)
    except Exception as e:
        logger.warning("_check_subscription FAILED (allowing user): user=%d channel=%s error=%s", user_id, channel, e)
        is_sub = True  # при ошибке API НЕ блокируем юзера — пусть пользуется

    _SUBSCRIBE_CACHE[user_id] = (is_sub, now)
    return is_sub


def _subscribe_keyboard_tg() -> InlineKeyboardMarkup:
    """Клавиатура «Подпишись чтобы продолжить»."""
    channel = settings.SUBSCRIBE_CHANNEL_TG
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{channel}")],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscribe")],
    ])


async def _on_check_subscribe(callback: CallbackQuery):
    """Обработка нажатия «Я подписался»."""
    bot = callback.bot
    user_id = callback.from_user.id
    # Сбрасываем кеш
    _SUBSCRIBE_CACHE.pop(user_id, None)
    is_sub = await _check_subscription(bot, user_id)
    if is_sub:
        await callback.message.edit_text(
            "✅ <b>Подписка подтверждена!</b>\n\nПользуйся ботом бесплатно.",
            reply_markup=None,
        )
    else:
        await callback.answer("❌ Ты ещё не подписан. Подпишись и нажми снова.", show_alert=True)


# Простое in-memory состояние для owner-режима (non-FSM)
_waiting_owner_search: set[int] = set()
_waiting_owner_role: dict[int, int] = {}
_waiting_inn_nosm: set[int] = set()
_owner_state: dict[int, dict] = {}


def _tg_id(message_or_callback) -> int:
    """Returns telegram user ID from Message or CallbackQuery."""
    if hasattr(message_or_callback, "from_user") and message_or_callback.from_user is not None:
        return message_or_callback.from_user.id
    return 0


async def _ensure_callback_user(callback) -> int:
    """Creates/updates user from CallbackQuery and returns internal user_id."""
    user = callback.from_user
    if not user:
        return 0
    return await upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=getattr(user, "language_code", None),
    )


class _OwnerWaitingInnFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in _waiting_inn_nosm


class _OwnerWaitingSearchFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if message.from_user is None or not message.text:
            return False
        if message.text.startswith("/"):
            return False
        return message.from_user.id in _waiting_owner_search


async def _require_subscription(message: Message) -> bool:
    """Проверяет подписку. Если не подписан — отправляет сообщение. Возвращает True если ОК."""
    bot = settings.bot
    if not bot:
        return True
    user_id = message.from_user.id
    is_sub = await _check_subscription(bot, user_id)
    if not is_sub:
        await message.answer(
            "📢 <b>Подпишись на канал, чтобы пользоваться ботом!</b>\n\n"
            "Бот бесплатный. Взамен — подпишись на наш канал с новостями о топливе.",
            reply_markup=_subscribe_keyboard_tg(),
        )
    return is_sub


async def _require_subscription_callback(callback: CallbackQuery) -> bool:
    """Проверяет подписку через callback. Если не подписан — отвечает alert. Возвращает True если ОК."""
    bot = settings.bot
    if not bot:
        return True
    user_id = callback.from_user.id
    is_sub = await _check_subscription(bot, user_id)
    if not is_sub:
        await callback.answer("📢 Подпишись на канал, чтобы пользоваться ботом!", show_alert=True)
    return is_sub


# === /start — Welcome-цепочка (3 сообщения) ===
async def cmd_start(message: Message):
    if not await _require_subscription(message):
        return
    try:
        uid = await get_or_create_user(message)
        await log_event(uid, "bot_start")
    except Exception as e:
        logger.exception(f"cmd_start: get_or_create_user failed: {e}")
        await message.answer(f"⚠️ Ошибка при старте: {e}\nПопробуй позже или /help")
        return

    first_name = message.from_user.first_name or "друг"
    telegram_id = _tg_id(message)

    # Проверяем реферальный код в /start ref_XXXX
    text = (message.text or "").strip()
    if "ref_" in text:
        import re
        ref_match = re.search(r'ref_([A-Z0-9]+)', text, re.IGNORECASE)
        if ref_match:
            ref_code = ref_match.group(1).upper()
            import aiohttp
            backend = "https://benzin-ryadom.onrender.com"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{backend}/api/referral/apply",
                        json={"telegram_id": telegram_id, "code": ref_code},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        ref_data = await r.json()
                if ref_data.get("ok"):
                    await message.answer(
                        f"🎉 <b>Добро пожаловать!</b>\n\n"
                        f"Ты использовал реферальный код!\n"
                        f"Твой друг получил месяц Premium.\n\n"
                        f"👇 <b>Главное меню:</b>",
                        reply_markup=main_menu_keyboard(),
                    )
                    return
            except Exception:
                pass

    # Проверяем deep link для привязки: /start link_VKUSERID
    if "link_vk_" in text:
        import re
        link_match = re.search(r'link_vk_(\d+)', text)
        if link_match:
            vk_id = int(link_match.group(1))
            # Привязываем VK к TG
            from db import link_accounts_by_vk
            result = await link_accounts_by_vk(vk_id, telegram_id)
            if result.get("ok"):
                await message.answer(
                    f"✅ <b>Аккаунт привязан!</b>\n\n"
                    f"Твой TG аккаунт привязан к VK ID: {vk_id}\n\n"
                    f"Теперь Premium (если есть) работает и в TG, и в VK, и в Mini App.",
                    reply_markup=main_menu_keyboard(),
                )
                return
            else:
                await message.answer(
                    f"❌ Не удалось привязать: {result.get('error', 'неизвестная ошибка')}",
                    reply_markup=main_menu_keyboard(),
                )
                return

    # Сообщение 1: Hero
    try:
        hero = WELCOME_1
        hero_kb = main_inline_keyboard()
        await message.answer(hero, reply_markup=hero_kb)
    except Exception as e:
        logger.exception(f"cmd_start: WELCOME_1 failed: {e}")
        await message.answer(f"👋 Привет, {first_name}! /help", reply_markup=main_menu_keyboard())
        return

    # Сообщение 2: Inline-фича
    try:
        inline_msg = WELCOME_2
        inline_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Попробовать здесь →", switch_inline_query_current_chat="95 Иваново")],
        ])
        await message.answer(inline_msg, reply_markup=with_home_inline(inline_kb))
    except Exception as e:
        logger.exception(f"cmd_start: WELCOME_2 failed: {e}")

    # Сообщение 3: Crowdsource + бейджи
    try:
        crowdsource = WELCOME_3
        crowdsource_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Найти АЗС рядом", callback_data="menu:find")],
            [InlineKeyboardButton(text="👤 Мой профиль", callback_data="cmd_profile"),
             InlineKeyboardButton(text="ℹ️ Все команды", callback_data="cmd_help")],
        ])
        await message.answer(crowdsource, reply_markup=with_home_inline(crowdsource_kb))
    except Exception as e:
        logger.exception(f"cmd_start: WELCOME_3 failed: {e}")

    # Главное меню
    try:
        await message.answer(
            "👇 <b>Главное меню:</b> нажимай кнопки внизу — "
            "они остаются видимыми после каждого ответа.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception as e:
        logger.exception(f"cmd_start: main_menu failed: {e}")


# === /help ===
async def cmd_help(message: Message):
    text = HELP_TEXT
    await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[])))


# === /find ===
async def cmd_find(message: Message):
    await message.answer(
        "📍 <b>Выбери населённый пункт</b>\n\n"
        "Иваново, Москва, СПб, и другие. "
        "Или напиши свой город в сообщении — бот найдёт АЗС.",
        reply_markup=city_keyboard(),
    )


# === FSM: поиск по трассе ===
class RouteSearchStates(StatesGroup):
    waiting_route_query = State()


async def cmd_route_search(message: Message, state: FSMContext | None = None):
    """Поиск АЗС вдоль федеральных/региональных трасс РФ."""
    if state is not None:
        await state.set_state(RouteSearchStates.waiting_route_query)
    await message.answer(
        "🛣 <b>Поиск АЗС вдоль трассы</b>\n\n"
        "Введи номер или название трассы:\n"
        "• <code>М-4</code> или <code>М4</code> — трасса «Дон»\n"
        "• <code>М-7</code> — «Волга»\n"
        "• <code>Р-217</code> — «Кавказ»\n"
        "• <code>дон</code>, <code>кавказ</code>, <code>крым</code> — по названию\n\n"
        "Бот покажет АЗС вдоль трассы с адресами, ценами и наличием.\n"
        "Или нажми /cancel для отмены."
    )


async def handle_route_query(message: Message, state: FSMContext):
    """Обрабатывает ввод номера/названия трассы и показывает АЗС."""
    if state is None:
        return
    text = (message.text or "").strip()
    if text in ("/cancel", "отмена", "Отмена"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_menu_keyboard())
        return

    from db import search_routes, find_stations_by_route
    routes = await search_routes(text, limit=5)
    if not routes:
        await message.answer(
            f"🔍 По запросу <b>«{text}»</b> трасс не найдено.\n"
            f"Попробуй: М-4, М-7, Р-217, Дон, Кавказ, Крым.\n\n"
            f"Или /cancel для отмены."
        )
        return

    # Показываем первую трассу
    route = routes[0]
    stations = await find_stations_by_route(route["id"], limit=20)

    lines = [
        f"🛣 <b>{route['code']} — {route['name']}</b>",
        f"📏 {route['length_km']} км · {route['start_point']} → {route['end_point']}",
        "",
    ]
    if route.get("description"):
        lines.append(f"<i>{route['description']}</i>")
        lines.append("")

    lines.append(f"⛽ <b>Найдено АЗС на трассе: {len(stations)}</b>\n")

    from utils import format_station_card
    for i, s in enumerate(stations[:10], 1):
        # Адрес
        addr = s.get("address") or "—"
        city = s.get("city") or ""
        km = s.get("km_marker")
        km_str = f" (≈{km} км)" if km else ""

        # Статус
        has_fuel = s.get("has_fuel", False)
        status = "✅ Есть топливо" if has_fuel else "❓ Нет данных"

        # Название сети
        net = s.get("operator") or s.get("brand") or ""
        net_str = f" <i>{net}</i>" if net else ""

        lines.append(f"{i}. <b>#{s['id']}</b>{net_str} — {s['name']}")
        lines.append(f"   📍 {city}, {addr}{km_str}")
        lines.append(f"   {status}")
        lines.append("")

    if len(stations) > 10:
        lines.append(f"<i>...и ещё {len(stations) - 10} АЗС</i>")

    # Кнопки: другие трассы если есть
    kb_rows = []
    if len(routes) > 1:
        kb_rows.append([InlineKeyboardButton(
            text=f"Другие трассы ({len(routes) - 1})",
            callback_data=f"route_more:{text}",
        )])
    kb_rows.append([InlineKeyboardButton(
        text="🔍 Новый поиск",
        callback_data="route:new",
    )])

    await message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )
    await state.clear()


async def route_more_callback(callback: CallbackQuery):
    """Показывает другие трассы по запросу."""
    text = callback.data.split(":", 1)[1] if ":" in callback.data else ""
    from db import search_routes
    routes = await search_routes(text, limit=10)
    if not routes:
        await callback.answer("Ничего не найдено", show_alert=True)
        return

    lines = [f"🛣 <b>Найдено трасс: {len(routes)}</b>\n"]
    kb_rows = []
    for r in routes:
        lines.append(f"• <b>{r['code']}</b> — {r['name']} ({r['length_km']} км)")
        kb_rows.append([InlineKeyboardButton(
            text=f"{r['code']} {r['name'][:30]}",
            callback_data=f"route_pick:{r['id']}",
        )])
    kb_rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="route:new")])
    await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


async def route_pick_callback(callback: CallbackQuery):
    """Показывает АЗС выбранной трассы."""
    rid = int(callback.data.split(":", 1)[1])
    from db import find_stations_by_route, _fetch
    if db.USE_SQLITE:
        row = await _fetch("SELECT * FROM routes WHERE id = ?", rid)
    else:
        async with db._db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM routes WHERE id = $1", rid)
    if not row:
        await callback.answer("Трасса не найдена", show_alert=True)
        return
    route = dict(row) if not isinstance(row, dict) else row
    stations = await find_stations_by_route(rid, limit=20)

    lines = [
        f"🛣 <b>{route['code']} — {route['name']}</b>",
        f"📏 {route['length_km']} км",
        "",
        f"⛽ АЗС: {len(stations)}\n",
    ]
    for i, s in enumerate(stations[:10], 1):
        addr = s.get("address") or "—"
        city = s.get("city") or ""
        has_fuel = s.get("has_fuel", False)
        status = "✅" if has_fuel else "❓"
        net = s.get("operator") or s.get("brand") or ""
        lines.append(f"{status} <b>#{s['id']}</b> {s['name']} <i>{net}</i>")
        lines.append(f"   📍 {city}, {addr}")
    await callback.message.answer("\n".join(lines))
    await callback.answer()


async def route_new_callback(callback: CallbackQuery, state: FSMContext):
    """Новый поиск по трассе."""
    if state is not None:
        await state.set_state(RouteSearchStates.waiting_route_query)
    await callback.message.answer(
        "🛣 <b>Введи номер или название трассы</b>\n"
        "Примеры: <code>М-4</code>, <code>М-7</code>, <code>Р-217</code>, <code>дон</code>"
    )
    await callback.answer()


# === /subscribe ===
async def cmd_subscribe(message: Message, state: FSMContext | None = None):
    if state:
        await state.set_state(SubscribeStates.waiting_geo)
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Отправить геолокацию", request_location=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "🔔 <b>Подписка на уведомления о завозе.</b>\n\n"
        "Отправь геолокацию — буду присылать уведомления, когда "
        "в радиусе 5 км от тебя появится бензин.",
        reply_markup=kb,
    )


# === /register_owner ===
async def cmd_register_owner(message: Message, state: FSMContext | None = None):
    _waiting_owner_search.add(_tg_id(message))
    if state:
        await state.clear()
    await message.answer(
        "👤 <b>Регистрация владельца или работника АЗС.</b>\n\n"
        "<b>Можно регистрироваться и владельцу, и работнику заправки</b> — "
        "обоим мы даём возможность одной кнопкой обновлять статус топлива.\n\n"
        "📝 <b>Введи название, адрес или город</b> АЗС, где ты работаешь.\n\n"
        "<i>Например: <code>Лукойл Иваново</code>, <code>Ленина 45</code>, "
        "<code>Газпром Шуя</code>.</i>",
        reply_markup=main_menu_keyboard(),
    )


async def owner_inn_input_nosm(message: Message):
    telegram_id = _tg_id(message)
    if telegram_id not in _waiting_inn_nosm:
        return
    state = _owner_state.get(telegram_id)
    if not state or "station_id" not in state:
        _waiting_inn_nosm.discard(telegram_id)
        return
    inn = (message.text or "").strip()
    if inn and not inn.isdigit():
        await message.answer("ИНН должен содержать только цифры. Попробуй ещё раз или нажми «Пропустить».")
        return
    _waiting_inn_nosm.discard(telegram_id)
    await owner_finish_no_fsm(message, state["station_id"], state.get("role", "owner"), inn=inn or None)


async def owner_inn_skip_nosm(callback: CallbackQuery):
    telegram_id = callback.from_user.id if callback.from_user else 0
    state = _owner_state.get(telegram_id)
    _waiting_inn_nosm.discard(telegram_id)
    if not state or "station_id" not in state:
        await callback.answer("Ошибка. Попробуй сначала.", show_alert=True)
        return
    await owner_finish_no_fsm(callback.message, state["station_id"], state.get("role", "owner"), inn=None)
    await callback.answer()


async def owner_search_input(message: Message):
    telegram_id = _tg_id(message)
    if telegram_id not in _waiting_owner_search:
        return
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введи минимум 2 символа.")
        return

    stations = await find_stations_by_name(query, limit=10)
    if not stations:
        await message.answer(
            f"😔 По запросу <b>«{query}»</b> ничего не нашёл.\n\n"
            f"Попробуй написать по-другому — например:\n"
            f"• <code>Лукойл</code> или <code>Газпром</code> (сеть)\n"
            f"• <code>Иваново</code> (город)\n"
            f"• <code>Ленина 45</code> (адрес)\n\n"
            f"Или нажми «👤 Я владелец» ещё раз, чтобы начать сначала.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = f"🔍 Нашёл <b>{len(stations)}</b> АЗС по запросу «{query}». Выбери свою:"
    buttons = []
    for s in stations:
        operator = (s.get("operator") or "")[:15]
        address = (s.get("address") or "")[:20]
        city = (s.get("city") or "")[:12]
        # Сеть → адрес → город
        if operator and address:
            label = f"⛽ {operator} — {address}"
        elif operator and city:
            label = f"⛽ {operator} — {city}"
        elif operator:
            label = f"⛽ {operator}"
        elif address and city:
            label = f"⛽ {city}, {address}"
        elif address:
            label = f"⛽ {address}"
        elif city:
            label = f"⛽ {city}"
        else:
            label = f"⛽ {s.get('name', 'АЗС')}"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"owner_pick_search:{s['id']}")
        ])
    buttons.append([
        InlineKeyboardButton(text="❌ Отменить", callback_data="owner_search_cancel"),
    ])

    _waiting_owner_search.discard(telegram_id)
    await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=buttons)))


async def owner_pick_search(callback: CallbackQuery):
    station_id = int(callback.data.split(":", 1)[1])
    telegram_id = callback.from_user.id if callback.from_user else 0

    station = await get_station_by_id(station_id)
    if not station:
        await callback.answer("АЗС не найдена", show_alert=True)
        return

    _waiting_owner_role[telegram_id] = station_id
    _owner_state[telegram_id] = {"station_id": station_id}

    name = station.get("name", "АЗС")
    operator = station.get("operator") or ""
    header = f"⛽ <b>{name}</b>"
    if operator:
        header += f" ({operator})"

    await callback.message.answer(
        f"{header}\n\n"
        f"Кто ты на этой АЗС?",
        reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Я владелец", callback_data="owner_role:owner")],
            [InlineKeyboardButton(text="👨‍🔧 Я работник", callback_data="owner_role:employee")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="owner_search_cancel")],
        ])),
    )
    await callback.answer()


async def owner_search_cancel(callback: CallbackQuery):
    telegram_id = callback.from_user.id if callback.from_user else 0
    _waiting_owner_search.discard(telegram_id)
    _waiting_owner_role.pop(telegram_id, None)
    _owner_state.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)
    await callback.message.answer(
        "Ок, отменил. Если захочешь зарегистрироваться — нажми «👤 Я владелец».",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


async def owner_role_picked(callback: CallbackQuery):
    role = callback.data.split(":", 1)[1]
    if role not in ("owner", "employee"):
        await callback.answer("Неизвестная роль", show_alert=True)
        return

    telegram_id = callback.from_user.id if callback.from_user else 0
    station_id = _waiting_owner_role.pop(telegram_id, None)
    if not station_id:
        await callback.answer("Ошибка. Попробуй сначала.", show_alert=True)
        return

    _owner_state[telegram_id] = {"station_id": station_id, "role": role}
    _waiting_inn_nosm.add(telegram_id)

    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владельцем" if role == "owner" else "работником"

    await callback.message.answer(
        f"⛽ <b>{name}</b> — ты зарегистрирован как <b>{role_text}</b>.\n\n"
        f"📋 Укажи ИНН организации (10 или 12 цифр) — <i>опционально, "
        f"ускорит модерацию и получение ✓ Verified.</i>\n\n"
        f"Если не хочешь — нажми «Пропустить».",
        reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="owner_inn_nosm:skip")],
        ])),
    )
    await callback.answer()


async def owner_finish_no_fsm(message, station_id: int, role: str = "owner", inn: str | None = None):
    telegram_id = _tg_id(message)
    _owner_state.pop(telegram_id, None)
    _waiting_owner_role.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)

    await get_or_create_user(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer(
            "Ошибка. Нажми /start и попробуй снова.",
            reply_markup=main_menu_keyboard(),
        )
        return

    result = await add_owner_station(
        user_id=uid, station_id=station_id, inn=inn, role=role,
    )
    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else f"#{station_id}"
    role_text = "владелец" if role == "owner" else "работник"

    if result == -1:
        text = f"ℹ️ Ты уже зарегистрирован на АЗС «{name}»."
    else:
        text = (
            f"✅ <b>Готово! Ты зарегистрирован как {role_text} АЗС «{name}».</b>\n\n"
            f"Обновлять статус: /my_stations\n"
            f"После модерации появится значок ✓ Verified."
        )
    await message.answer(text, reply_markup=main_menu_keyboard())


# === /my_stations ===
async def cmd_my_stations(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer("Сначала нажми /start")
        return

    stations = await get_owner_stations(uid)
    if not stations:
        await message.answer(
            "ℹ️ Ты не зарегистрирован как владелец/работник АЗС.\n\n"
            "Нажми «👤 Я владелец» или команду /register_owner.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text = "🏪 <b>Твои АЗС:</b>\n\n"
    buttons = []
    for s in stations:
        name = (s.get("name") or "АЗС")[:30]
        verified = " ✓" if s.get("is_verified") else ""
        role = s.get("role") or "owner"
        role_icon = "👑" if role == "owner" else "👨‍🔧"
        operator = s.get("operator") or ""
        label = f"{role_icon} {name}{verified}"
        if operator:
            label += f" · {operator[:15]}"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"mystation:{s['station_id']}")
        ])

    text += f"Всего: {len(stations)}. Нажми на АЗС, чтобы обновить статус."
    await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=buttons)))


async def show_my_station(callback: CallbackQuery):
    station_id = int(callback.data.split(":")[1])
    uid = await _ensure_callback_user(callback)

    if not uid or not await is_owner_of_station(uid, station_id):
        await callback.answer("Это не твоя АЗС", show_alert=True)
        return

    station = await get_station_by_id(station_id)
    if not station:
        await callback.answer("АЗС не найдена", show_alert=True)
        return

    statuses = await get_station_current_status(station_id)
    text = format_station_card(station, statuses)
    text = "👤 <b>Твоя АЗС — обновление статуса:</b>\n\n" + text

    buttons = []
    for fuel in ["92", "95", "98", "diesel"]:
        buttons.append([
            InlineKeyboardButton(
                text=f"АИ-{fuel}: ✅",
                callback_data=f"oset:{station_id}:{fuel}:yes",
            ),
            InlineKeyboardButton(
                text=f"⏱",
                callback_data=f"oset:{station_id}:{fuel}:queue",
            ),
            InlineKeyboardButton(
                text=f"⚠️",
                callback_data=f"oset:{station_id}:{fuel}:low",
            ),
            InlineKeyboardButton(
                text=f"❌",
                callback_data=f"oset:{station_id}:{fuel}:no",
            ),
        ])
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="my_stations_back"),
    ])

    await callback.message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=buttons)))
    await callback.answer()


async def owner_quick_set(callback: CallbackQuery):
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    status = parts[3]

    uid = await _ensure_callback_user(callback)

    if not uid or not await is_owner_of_station(uid, station_id):
        await callback.answer("Это не твоя АЗС", show_alert=True)
        return

    available_map = {"yes": True, "queue": True, "low": None, "no": False}
    queue_map = {"yes": None, "queue": 5, "low": None, "no": None}
    if status not in available_map:
        await callback.answer("Неизвестный статус", show_alert=True)
        return

    await add_report(
        station_id=station_id,
        user_id=uid,
        fuel_type=fuel,
        available=available_map[status],
        queue_size=queue_map[status],
        source="owner",
    )

    status_text = {"yes": "✅ есть", "queue": "🕐 очередь", "low": "⚠️ кончается", "no": "❌ нет"}[status]
    await callback.answer(f"Записал: АИ-{fuel} — {status_text}", show_alert=True)


async def my_stations_back(callback: CallbackQuery):
    await cmd_my_stations(callback.message)
    await callback.answer()


# === /moderate ===
async def cmd_moderate(message: Message):
    if not settings.is_admin(user_id=message.from_user.id, username=message.from_user.username):
        return
    apps = await get_pending_owner_applications()
    if not apps:
        await message.answer("Нет заявок на модерацию.")
        return

    for app in apps[:5]:
        name = app.get("station_name") or "АЗС"
        city = app.get("city") or ""
        inn = app.get("inn") or "—"
        first = app.get("first_name") or ""
        username = f"@{app['username']}" if app.get("username") else ""

        text = (
            f"📋 <b>Заявка #{app['id']}</b>\n\n"
            f"👤 {first} {username} (id={app['user_id']})\n"
            f"⛽ {name}" + (f" ({city})" if city else "") + "\n"
            f"📇 ИНН: {inn}\n"
            f"📅 {str(app.get('created_at', ''))[:16]}"
        )
        await message.answer(
            text,
            reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{app['id']}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app['id']}"),
                ],
            ])),
        )


async def approve_owner(callback: CallbackQuery):
    if not settings.is_admin(user_id=callback.from_user.id, username=callback.from_user.username):
        await callback.answer("Нет прав", show_alert=True)
        return
    app_id = int(callback.data.split(":")[1])
    await set_owner_station_verified(app_id, callback.from_user.id)
    await callback.message.edit_text("✅ Одобрено. ✓ Verified поставлен.")
    await callback.answer()


# === /my_id ===
async def cmd_my_id(message: Message):
    user = message.from_user
    await message.answer(
        f"🆔 <b>Твой Telegram ID:</b> <code>{user.id}</code>\n\n"
        f"Username: @{user.username or '—'}\n\n"
        f"<i>Чтобы получить права админа, добавь этот ID в "
        f"<code>ADMIN_IDS</code> в <code>bot/.env</code>.</i>"
    )


# === /find_raw ===
async def cmd_find_raw(message: Message):
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Использование: <code>/find_raw 56.97 40.92</code>\n"
            "(lat lon через пробел)"
        )
        return
    try:
        lat = float(parts[1])
        lon = float(parts[2])
    except ValueError:
        await message.answer("Координаты должны быть числами")
        return

    stations = await find_nearest_stations(lat, lon, limit=10, radius_km=5)
    if not stations:
        await message.answer(f"В радиусе 5 км от ({lat}, {lon}) ничего нет.")
        return

    text = f"🔍 <b>Координаты:</b> {lat}, {lon}\n\nБлижайшие 10 (радиус 5 км):\n\n"
    for s in stations:
        d = s.get("distance_km", 0)
        op = s.get("operator") or "—"
        text += f"  {d:5.1f} км — {s.get('name', 'АЗС')[:25]} ({op[:15]})\n"
    await message.answer(text)


# === /premium ===
async def cmd_premium(message: Message):
    """Показать 3 тарифа премиума — красивый формат."""
    try:
        await _cmd_premium_impl(message)
    except Exception as e:
        logger.exception(f"cmd_premium CRASHED: {e}")
        try:
            await message.answer(
                "💎 <b>Премиум «Бензин рядом»</b>\n\n"
                "Тарифы:\n"
                "📊 <b>Эконом</b> — 100₽/мес\n"
                "🗺️ <b>Стандарт</b> — 250₽/мес\n"
                "👑 <b>Элит</b> — 500₽/мес\n\n"
                "💳 Оплата: /premium → выбери тариф\n"
                "Или открой 🌐 Mini App → Профиль → Premium",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🌐 Mini App", url="https://benzin-ryadom.onrender.com")],
                ]),
            )
        except Exception:
            pass


async def _cmd_premium_impl(message: Message):
    logger.info(f"cmd_premium START: user={message.from_user.id if message.from_user else '?'}")
    try:
        await get_or_create_user(message)
        logger.info("cmd_premium: get_or_create_user OK")
    except Exception as e:
        logger.exception(f"cmd_premium: get_or_create_user failed: {e}")
    telegram_id = _tg_id(message)
    logger.info(f"cmd_premium: telegram_id={telegram_id}")
    try:
        uid = await get_user_id_by_telegram_id(telegram_id)
        logger.info(f"cmd_premium: uid={uid}")
        sub = await get_user_premium(uid) if uid else None
        logger.info(f"cmd_premium: sub={'yes' if sub else 'no'}")
    except Exception as e:
        logger.exception(f"cmd_premium: premium check failed: {e}")
        uid = None
        sub = None

    if sub:
        from datetime import datetime as _dt
        exp = sub.get("expires_at", "")
        if isinstance(exp, str):
            try: exp_dt = _dt.fromisoformat(exp)
            except: exp_dt = None
        else:
            exp_dt = exp
        days_left = max(0, (exp_dt - _dt.now()).days) if exp_dt else 0
        tier_name = {"economy": "📊 Эконом", "standard": "🗺️ Стандарт", "elite": "👑 Элит"}.get(sub.get("tier"), sub.get("tier"))
        tier_features = {
            "economy": "📈 График цен · 📦 CSV-экспорт · 🗺️ Офлайн-карта",
            "standard": "📈 График цен · 📦 CSV · 🗺️ Офлайн · 🛣 Маршрут A→B · 🔮 Прогноз · 🔔 Будильник",
            "elite": "Всё из Стандарт + 🚗 Антипробка · 🆘 SOS-режим",
        }
        text = (
            f"✅ <b>У тебя Premium!</b>\n\n"
            f"Тариф: <b>{tier_name}</b>\n"
            f"Истекает: {str(exp)[:10]} (<b>{days_left} дн.</b>)\n\n"
            f"<b>Твои фичи:</b>\n{tier_features.get(sub.get('tier'), '')}\n\n"
            f"💡 Смотри статистику в профиле /profile"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Открыть Mini App", url="https://benzin-ryadom.onrender.com")],
            [InlineKeyboardButton(text="🏠 Главная", callback_data="back_home")],
        ])
        await message.answer(text, reply_markup=kb)
        return

    text = (
        "💎 <b>Премиум «Бензин рядом»</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏆 <b>2 400+ водителей</b> уже экономят с нами\n"
        "💰 В среднем экономия <b>2 700₽/мес</b> на топливе\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Эконом</b> — <b>100₽/мес</b>\n"
        "├ 📈 График цен 30 дней\n"
        "├ 📦 Экспорт в CSV/Excel\n"
        "└ 🗺️ Офлайн-карта (без интернета)\n\n"
        "🗺️ <b>Стандарт</b> — <b>250₽/мес</b> <i>🔥 Хит</i>\n"
        "├ Всё из Эконом\n"
        "├ 🛣 Маршрут A→B с ценами\n"
        "├ 🔮 Прогноз цен на 7 дней\n"
        "└ 🔔 Топливный будильник\n\n"
        "👑 <b>Элит</b> — <b>500₽/мес</b>\n"
        "├ Всё из Стандарт\n"
        "├ 🚗 Антипробка (цены+пробки)\n"
        "└ 🆘 SOS-режим (помощь 50 км)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧮 <b>Калькулятор:</b>\n"
        "Если заправляешь 40л/мес × 2 раза,\n"
        "и экономишь всего 3₽/л = <b>240₽/мес</b>\n"
        "→ Стандарт уже окупается!\n\n"
        "👇 <b>Выбери тариф:</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 100₽", callback_data="buy_economy"),
            InlineKeyboardButton(text="🗺️ 250₽", callback_data="buy_standard"),
            InlineKeyboardButton(text="👑 500₽", callback_data="buy_elite"),
        ],
        [InlineKeyboardButton(text="🎁 7 дней бесплатно", callback_data="premium_trial")],
        [InlineKeyboardButton(text="🌐 Mini App", url="https://benzin-ryadom.onrender.com")],
        [InlineKeyboardButton(text="🏠 Главная", callback_data="back_home")],
    ])
    await message.answer(text, reply_markup=kb)


async def buy_tier_callback(callback: CallbackQuery):
    """Генерирует инструкцию для оплаты тарифа через /api/premium/create-payment."""
    logger.info(f"buy_tier_callback: data={callback.data} user={callback.from_user.id}")
    await callback.answer()
    tier = callback.data.replace("buy_", "")
    if tier not in ("economy", "standard", "elite"):
        logger.warning(f"buy_tier_callback: invalid tier {tier}")
        return
    uid = await _ensure_callback_user(callback)
    if not uid:
        logger.warning(f"buy_tier_callback: no uid for user {callback.from_user.id}")
        return
    plan = get_plan(tier)
    if not plan:
        logger.warning(f"buy_tier_callback: no plan for tier {tier}")
        return

    # Вызываем наш API чтобы получить payment_url
    import aiohttp
    backend = "https://benzin-ryadom.onrender.com"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{backend}/api/premium/create-payment",
                json={"telegram_id": callback.from_user.id, "tier": tier},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                data = await r.json()
                logger.info(f"buy_tier_callback: API response {data}")
    except Exception as e:
        logger.exception(f"buy_tier_callback: API error: {e}")
        await callback.message.answer(f"❌ Ошибка соединения с сервером оплаты: {e}")
        return

    if not data.get("ok"):
        err = data.get("error", "unknown")
        logger.warning(f"buy_tier_callback: payment not ok, err={err}")
        await callback.message.answer(
            f"⚠️ <b>Оплата временно недоступна</b>\n\n"
            f"Ошибка: <code>{err}</code>\n\n"
            f"Попробуй позже или напиши в <b>@benzyn_ryadom</b>"
        )
        return

    token = data.get("payment_token")
    from premium_texts import format_tier_text
    pay_url = data.get("payment_url", "")

    if not pay_url:
        logger.error(f"buy_tier_callback: no payment_url in response: {data}")
        await callback.message.answer(
            f"❌ Не удалось получить ссылку на оплату.\n"
            f"Попробуй позже или напиши в <b>@benzyn_ryadom</b>"
        )
        return

    tier_text = format_tier_text(tier, plan, show_features=True)

    text = (
        f"💳 <b>Оплата тарифа через ЮMoney</b>\n\n"
        f"{tier_text}\n"
        f"💰 К оплате: <b>{plan['price']}₽</b>\n\n"
        f"👇 Нажми кнопку для оплаты:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💳 Оплатить {plan['price']}₽ через ЮMoney", url=pay_url)],
        [InlineKeyboardButton(text="✅ Я оплатил — проверить", callback_data=f"check_pay_{token}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cmd_premium")],
    ])

    await callback.message.answer(text, reply_markup=kb)


async def check_payment_callback(callback: CallbackQuery):
    """Проверяет статус оплаты после возврата из VK Pay."""
    await callback.answer()
    token = callback.data.replace("check_pay_", "")
    from db import get_payment_by_token, confirm_payment
    payment = await get_payment_by_token(token)
    if not payment:
        await callback.message.answer("❌ Платёж не найден.")
        return
    if payment.get("status") == "paid":
        plan = get_plan(payment["tier"])
        await callback.message.answer(
            f"✅ <b>Премиум '{plan['name']}' активирован!</b>\n\n"
            f"💳 {payment['amount']}₽ оплачено\n"
            f"📅 До: {str(payment.get('paid_at', ''))[:10]}\n\n"
            f"Используй /premium для просмотра статуса."
        )
    else:
        await callback.message.answer(
            "⏳ Платёж ещё не поступил.\n\n"
            "Если вы оплатили — подождите 30 сек и нажмите кнопку ещё раз.\n"
            "Если не оплатили — нажмите «Оплатить» ниже."
        )


# === /link — привязка аккаунта к VK / MiniApp ===

async def cmd_link(message: Message):
    """Привязка аккаунта: /link (создать код) или /link 123456 (использовать код).

    Также обрабатывает текстовое нажатие кнопки «🔗 Привязать» — показывает меню.
    """
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    # Парсим код из текста команды (если есть)
    text = (message.text or "").strip()
    # Если это /link без кода — текст будет "/link" или "🔗 Привязать"
    # Если это /link 123456 — текст "/link 123456"
    if text.startswith("/link"):
        parts = text.split(maxsplit=1)
        code = parts[1].strip() if len(parts) >= 2 else ""
    else:
        # Если вызвано через callback menu:link — text это текст callback-сообщения,
        # а не команда. Не парсим код, а просто показываем меню.
        code = ""

    if not code:
        # Меню привязки
        from keyboards import BTN_LINK
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Создать код", callback_data="link:create")],
            [InlineKeyboardButton(text="📥 Ввести код", callback_data="link:enter")],
        ])
        await message.answer(
            f"🔗 <b>Привязка аккаунтов</b>\n\n"
            f"Чтобы Premium работал и в TG, и в VK, и в Mini App — привяжи аккаунт.\n\n"
            f"<b>Как привязать VK к TG:</b>\n"
            f"1. Нажми <b>«📤 Создать код»</b> — получишь 6-значный код\n"
            f"2. Открой VK бот @benzyn_ryadom\n"
            f"3. Напиши ему: <code>link_use 123456</code>\n\n"
            f"<b>Как привязать MiniApp к TG:</b>\n"
            f"1. Создай код (кнопка выше)\n"
            f"2. Открой Mini App → Профиль → Привязка аккаунта\n"
            f"3. Введи код\n\n"
            f"⏱ Код действует 10 минут.",
            reply_markup=kb,
        )
        return

    # Используем код
    await _use_link_code(message, telegram_id, code)


async def _use_link_code(message: Message, telegram_id: int, code: str) -> None:
    """Применяет код привязки."""
    import aiohttp
    backend = "https://benzin-ryadom.onrender.com"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{backend}/api/account/link/use",
                json={"telegram_id": telegram_id, "code": code},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
        if data.get("ok"):
            target_name = data.get("linked_to_name") or "пользователь"
            await message.answer(
                f"✅ <b>Аккаунт привязан!</b>\n\n"
                f"Твой TG аккаунт привязан к <b>{target_name}</b>.\n\n"
                f"Теперь Premium (если есть) работает и в TG, и в VK, и в Mini App."
            )
        else:
            err = data.get("error", "Неизвестная ошибка")
            await message.answer(
                f"❌ <b>Не удалось привязать</b>\n\n"
                f"{err}\n\n"
                f"Проверь что код введён правильно и не истёк (10 мин)."
            )
    except Exception as e:
        logger.exception(f"link use error: {e}")
        await message.answer("❌ Ошибка соединения. Попробуй позже.")


async def link_create_callback(callback: CallbackQuery):
    """Создаёт 6-значный код привязки (callback от кнопки)."""
    logger.info(f"link_create_callback: user={callback.from_user.id}")
    await callback.answer()
    uid = await _ensure_callback_user(callback)
    logger.info(f"link_create_callback: uid={uid}")
    if not uid:
        await callback.message.answer("Ошибка: пользователь не найден.")
        return
    import aiohttp
    backend = "https://benzin-ryadom.onrender.com"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{backend}/api/account/link/create",
                json={"telegram_id": callback.from_user.id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
                logger.info(f"link_create_callback: API response status={r.status}")
        if data.get("ok"):
            code = data["code"]
            await callback.message.answer(
                f"🔗 <b>Код для привязки:</b> <code>{code}</code>\n\n"
                f"⏱ Действует 10 минут.\n\n"
                f"<b>Чтобы привязать VK аккаунт:</b>\n"
                f"1. Открой VK бот @benzyn_ryadom\n"
                f"2. Напиши ему: <code>link_use {code}</code>\n\n"
                f"<b>Чтобы привязать Mini App:</b>\n"
                f"1. Открой Mini App → Профиль → Привязка аккаунта\n"
                f"2. Введи код <code>{code}</code>\n\n"
                f"После привязки Premium будет работать везде."
            )
        else:
            err = data.get("error", "Неизвестная ошибка")
            logger.warning(f"link_create_callback: API error {err}")
            await callback.message.answer(f"❌ Ошибка: {err}")
    except Exception as e:
        logger.exception(f"link create callback error: {e}")
        await callback.message.answer("❌ Ошибка соединения. Попробуй позже.")


async def link_enter_callback(callback: CallbackQuery, state: FSMContext):
    """Просит юзера ввести код привязки."""
    await callback.answer()
    await state.set_state(LinkStates.waiting_code)
    await callback.message.answer(
        "📥 <b>Введи 6-значный код</b>\n\n"
        "Отправь код одним сообщением (например: <code>123456</code>)\n\n"
        "Код создаётся в VK боте (команда <code>link</code>) или в Mini App.\n"
        "⏱ Действует 10 минут.\n\n"
        "Чтобы отменить — напиши /cancel",
    )


async def link_code_input_handler(message: Message, state: FSMContext):
    """Обрабатывает ввод кода после нажатия 'Ввести код'."""
    code = (message.text or "").strip()
    # Проверяем что это похоже на код
    if not code or len(code) < 4:
        await message.answer("❌ Введи корректный код (минимум 4 символа)")
        return
    # Если юзер прислал команду — отменяем
    if code.startswith("/"):
        await state.clear()
        return
    telegram_id = _tg_id(message)
    await state.clear()
    await _use_link_code(message, telegram_id, code)


# === /alarm — Топливный будильник (Premium) ===

async def cmd_alarm(message: Message):
    """Показывает список топливных будильников или инструкцию."""
    telegram_id = _tg_id(message)
    uid = await _get_uid(telegram_id)
    if not uid:
        await message.answer("Сначала нажми /start")
        return

    import aiohttp
    backend = "https://benzin-ryadom.onrender.com"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{backend}/api/fuel-alarm/list",
                params={"telegram_id": telegram_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
    except Exception:
        data = {"alarms": [], "is_premium": False}

    is_premium = data.get("is_premium", False)
    alarms = data.get("alarms", [])

    if not is_premium:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Купить Premium", callback_data="premium:start")],
        ])
        await message.answer(
            "⛽ <b>Топливный будильник</b>\n\n"
            "Эта фича доступна только для <b>Premium</b> пользователей.\n\n"
            "Купи Premium и получи:\n"
            "• Уведомления когда топливо появится\n"
            "• Прогноз цен на 7 дней\n"
            "• Экспорт данных в CSV\n"
            "• Маршрут A→B с ближайшими ценами",
            reply_markup=kb,
        )
        return

    if not alarms:
        await message.answer(
            "⛽ <b>Топливный будильник</b>\n\n"
            "У тебя нет активных будильников.\n\n"
            "Чтобы создать будильник:\n"
            "1. Открой карту в Mini App\n"
            "2. Найди нужную АЗС\n"
            "3. Нажми «🔔 Уведомить о появлении»\n"
            "4. Выбери тип топлива (92/95/98/DT)\n\n"
            "Мы уведомим тебя когда нужное топливо появится!",
        )
        return

    lines = ["⛽ <b>Твои топливные будильники:</b>\n"]
    for a in alarms:
        fuel = a.get("fuel_type", "?")
        fuel_label = "АИ-100" if fuel == "100" else f"АИ-{fuel}" if fuel in ("92","95","98") else fuel.upper()
        name = a.get("name", "АЗС")
        city = a.get("city", "")
        created = a.get("created_at", "")[:10]
        lines.append(f"• <b>{fuel_label}</b> — {name} ({city}) — {created}")

    lines.append(f"\nВсего: {len(alarms)} будильников")
    await message.answer("\n".join(lines))


# === /referral — Реферальная программа ===

async def cmd_referral(message: Message):
    """Показывает реферальный код или применяет чужой."""
    telegram_id = _tg_id(message)
    uid = await _get_uid(telegram_id)
    if not uid:
        await message.answer("Сначала нажми /start")
        return

    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    # /referral ABC123 — применить чужой код
    if len(parts) >= 2 and not parts[1].startswith("/"):
        code = parts[1].strip().upper()
        import aiohttp
        backend = "https://benzin-ryadom.onrender.com"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{backend}/api/referral/apply",
                    json={"telegram_id": telegram_id, "code": code},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
        except Exception:
            data = {"error": "connection error"}

        if data.get("ok"):
            await message.answer(
                f"🎉 <b>Реферал применён!</b>\n\n"
                f"Твой друг получил месяц Premium.\n"
                f"Спасибо что пользуетесь «Бензин рядом»!",
            )
        else:
            err = data.get("error", "unknown")
            if err == "invalid referral code":
                await message.answer("❌ Код не найден. Проверь и попробуй ещё раз.")
            elif err == "referral code already used":
                await message.answer("❌ Этот код уже был использован.")
            elif err == "cannot use your own referral code":
                await message.answer("❌ Нельзя использовать свой же код.")
            else:
                await message.answer(f"❌ Ошибка: {err}")
        return

    # /referral — показать свой код
    import aiohttp
    backend = "https://benzin-ryadom.onrender.com"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{backend}/api/referral/code",
                params={"telegram_id": telegram_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
            async with session.get(
                f"{backend}/api/referral/stats",
                params={"telegram_id": telegram_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                stats_data = await r.json()
    except Exception:
        data = {"code": "ERROR"}
        stats_data = {"stats": {"total": 0, "completed": 0}}

    code = data.get("code", "?")
    link = data.get("link", "")
    stats = stats_data.get("stats", {})

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться кодом", switch_inline_query=code)],
        [InlineKeyboardButton(text="🌐 Mini App", web_app=WebAppInfo(url="https://benzin-ryadom.onrender.com"))],
    ])
    await message.answer(
        f"🎁 <b>Реферальная программа</b>\n\n"
        f"Пригласи друга — получи <b>месяц Premium бесплатно</b>!\n\n"
        f"<b>Твой код:</b> <code>{code}</code>\n"
        f"<b>Ссылка:</b> {link}\n\n"
        f"<b>Статистика:</b>\n"
        f"👥 Приглашено: {stats.get('total', 0)}\n"
        f"✅ Активировали: {stats.get('completed', 0)}\n"
        f"⏳ Ожидают: {stats.get('pending', 0)}\n\n"
        f"<b>Как это работает:</b>\n"
        f"1. Отправь код другу\n"
        f"2. Друг вводит /referral {code}\n"
        f"3. Вы оба получаете месяц Premium!\n\n"
        f"💡 Код работает в TG, VK и Mini App",
        reply_markup=kb,
    )


async def premium_trial_callback(callback: CallbackQuery):
    await callback.answer()
    uid = await _ensure_callback_user(callback)
    if not uid:
        await callback.message.answer("Ошибка: пользователь не найден.")
        return
    if await is_premium(uid):
        await callback.message.answer("У тебя уже есть Premium. Используй /premium для проверки.")
        return
    result = await activate_premium(
        user_id=uid,
        tier="standard",
        days=7,
        payment_id="trial_7d",
    )
    await callback.message.answer(
        f"🎁 <b>Trial Premium активирован!</b>\n\n"
        f"📅 На 7 дней (до {result['expires_at'][:10]})\n\n"
        f"<b>Что попробовать прямо сейчас:</b>\n"
        f"1️⃣ Открой карту — увидишь 500 АЗС вместо 100\n"
        f"2️⃣ Подпишись на АЗС — push придёт через час если будет завоз\n"
        f"3️⃣ Открой карточку АЗС — увидишь график цены\n\n"
        f"Если понравится — /premium для оплаты Stars.\n"
        f"Если нет — ничего не произойдёт, вернёшься на Free.",
    )
    await log_event(uid, "premium_trial_activated")


async def buy_premium_callback(callback: CallbackQuery):
    await _ensure_callback_user(callback)
    prices = [LabeledPrice(label=f"Premium · {settings.PREMIUM_DURATION_DAYS} дней", amount=settings.PREMIUM_PRICE_STARS)]
    try:
        await callback.message.answer_invoice(
            title="Бензин рядом · Premium",
            description=f"Premium-подписка на {settings.PREMIUM_DURATION_DAYS} дней: push без cooldown, расширенная аналитика, premium-бейдж.",
            payload="premium_30d",
            provider_token="",
            currency="XTR",
            prices=prices,
        )
    except Exception as e:
        logger.exception("Invoice send failed: %s", e)
        await callback.answer("Ошибка отправки invoice", show_alert=True)
        return
    await callback.answer()


async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)



async def successful_payment_handler(message: Message):
    sp = message.successful_payment
    if not sp or sp.currency != "XTR":
        return
    payload = sp.invoice_payload
    uid = await get_user_id_by_telegram_id(_tg_id(message)) if _tg_id(message) else None

    if payload == "premium_30d":
        if not uid:
            await message.answer("Ошибка: пользователь не найден.")
            return
        result = await activate_premium(
            user_id=uid,
            tier="standard",
            days=settings.PREMIUM_DURATION_DAYS,
            payment_id=sp.telegram_payment_charge_id,
            amount=sp.total_amount,
        )
        await message.answer(
            f"🎉 <b>Premium активирован!</b>\n\n"
            f"📅 Действует до: {result['expires_at'][:10]}\n"
            f"💎 Спасибо за поддержку «Бензин рядом»!\n\n"
            f"🔔 Push без cooldown, 📊 аналитика, 🚗 premium-бейдж — всё твоё.",
        )
        await log_event(uid, "premium_activated", payload={"stars": sp.total_amount})

    elif payload.startswith("promote_"):
        # promote_{owner_station_id}
        try:
            osid = int(payload.split("_")[1])
        except (IndexError, ValueError):
            await message.answer("⚠️ Ошибка: неверный payload.")
            return
        from db import promote_station, PROMO_DURATION_DAYS
        await promote_station(osid, days=PROMO_DURATION_DAYS)
        await message.answer(
            f"🌟 <b>АЗС продвинута на {PROMO_DURATION_DAYS} дней!</b>\n\n"
            f"Теперь твоя АЗС показывается выше в выдаче по городу.\n"
            f"📅 До: +{PROMO_DURATION_DAYS} дн.\n\n"
            f"Спасибо за поддержку «Бензин рядом»!",
        )
        if uid:
            await log_event(uid, "station_promoted", payload={"owner_station_id": osid, "stars": sp.total_amount})

    elif payload.startswith("donate:"):
        # Донейт — просто благодарим
        try:
            amount = int(payload.split(":")[1])
        except (IndexError, ValueError):
            amount = sp.total_amount
        await message.answer(
            f"❤️ <b>Спасибо за поддержку!</b>\n\n"
            f"Ты задонатил {amount} ⭐ на развитие «Бензин рядом».\n"
            f"Это помогает нам расти и добавлять новые функции!",
        )
        if uid:
            await log_event(uid, "donate", payload={"stars": sp.total_amount})


# === Inline mode ===
async def inline_search(inline_query: InlineQuery):
    query = (inline_query.query or "").strip()
    if len(query) < 2:
        await inline_query.answer(
            [],
            switch_pm_text="Введите запрос: город, сеть или тип топлива",
            switch_pm_parameter="inline_help",
            cache_time=10,
        )
        return

    fuel_keywords = {"92", "95", "98", "100", "дизель", "diesel", "газ", "lpg"}
    tokens = query.lower().split()
    fuel = None
    city_tokens = []
    for t in tokens:
        if t in fuel_keywords or (t.isdigit() and t in {"92", "95", "98", "100"}):
            fuel = t
        else:
            city_tokens.append(t)
    city_query = " ".join(city_tokens).strip()

    if city_query:
        stations = await find_stations_by_name(city_query, limit=20)
    else:
        await inline_query.answer(
            [],
            switch_pm_text="Укажите город или сеть, например: 92 Иваново",
            switch_pm_parameter="inline_help",
            cache_time=10,
        )
        return

    if stations:
        from db import get_stations_with_statuses
        stations = await get_stations_with_statuses(stations)

    if fuel:
        if fuel == "дизель":
            fuel = "diesel"
        elif fuel == "газ":
            fuel = "lpg"

        def has_fuel(s, fuel_type):
            for st in s.get("statuses", []):
                if st.get("fuel_type") == fuel_type:
                    if st.get("available") is True or st.get("available") == 1:
                        return True
            return False

        stations = [s for s in stations if has_fuel(s, fuel)]

    if not stations:
        await inline_query.answer(
            [],
            switch_pm_text="Ничего не найдено. Откройте бота для подробного поиска.",
            switch_pm_parameter="inline_help",
            cache_time=10,
        )
        return

    results = []
    for i, s in enumerate(stations[:10]):
        statuses = s.get("statuses", [])
        status_icons = " ".join(
            {"92": "⛽92", "95": "⛽95", "98": "⛽98", "diesel": "🛢"}.get(
                st.get("fuel_type"), ""
            )
            for st in statuses
            if st.get("available") in (True, 1, None, 2)
        )
        address = s.get("address") or f"{s.get('lat', 0):.4f}, {s.get('lon', 0):.4f}"
        name = s.get("name") or "АЗС"
        operator = s.get("operator") or ""
        city = s.get("city") or ""
        lat = s.get("lat", 0)
        lon = s.get("lon", 0)
        verified = s.get("is_verified", False)

        # Сеть → адрес
        display_name = operator if operator else name
        text = f"{'✓ ' if verified else ''}⛽ <b>{display_name}</b>\n"
        if operator and name and name != operator:
            text += f"📌 {name}\n"
        if address:
            text += f"📍 {address}\n"
        if city:
            text += f"🏙 {city}\n"
        if status_icons:
            text += f"\n{status_icons}"

        station_id = s["id"]
        yandex_url = f"https://yandex.ru/maps/?rtext=~{lat},{lon}&rtt=auto"
        buttons = [
            [
                InlineKeyboardButton(text="🗺 Маршрут", url=yandex_url),
                InlineKeyboardButton(text="🔔 Подписаться", callback_data=f"sub_station:{station_id}"),
            ],
            [
                InlineKeyboardButton(text="📊 Подробнее", callback_data=f"st:{station_id}"),
            ],
        ]

        title_display = operator if operator else name
        results.append(
            InlineQueryResultArticle(
                id=f"st:{station_id}:{i}",
                title=f"{'✓ ' if verified else ''}⛽ {title_display}",
                description=f"{address[:80]} | {status_icons[:30]}",
                input_message_content=InputTextMessageContent(
                    message_text=text,
                    parse_mode="HTML",
                ),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            )
        )

    await inline_query.answer(results, cache_time=30, is_personal=False)


# === handle_main_button — текстовые кнопки (reply keyboard) ===
async def handle_main_button(message: Message, state: FSMContext = None):
    text = (message.text or "").strip()
    logger.info(f"handle_main_button: text={text!r}")

    try:
        # Глобальный «В начало»
        if text == "🏠 В начало" or text == BTN_HOME:
            await go_home_text(message, state)
            return

        if not await _require_subscription(message):
            logger.warning(f"handle_main_button: subscription check failed for user {message.from_user.id if message.from_user else '?'}, text={text!r}")
            return

        if text == BTN_FIND or text == "🔍 Найти АЗС":
            await cmd_find(message)
        elif text == BTN_ROUTE or text == "🛣 Поиск по трассе":
            await cmd_route_search(message, state)
        elif text == BTN_REPORT or text == "📝 Сообщить о наличии":
            await message.answer(
                "📝 <b>Выбери город, чтобы сообщить о наличии:</b>",
                reply_markup=report_city_keyboard(),
            )
        elif text == BTN_SUBSCRIBE or text == "🔔 Уведомления":
            await cmd_subscribe(message, state)
        elif text == BTN_PROFILE or text == "👤 Профиль":
            await cmd_profile(message)
        elif text == BTN_OWNER or text == "👤 Я владелец АЗС":
            await cmd_register_owner(message, state)
        elif text == BTN_MY_STATIONS or text == "🏪 Мои АЗС":
            await cmd_my_stations(message)
        elif text == BTN_HELP or text == "❓ Помощь" or text == "/help":
            await cmd_help(message)
        elif text == BTN_PREMIUM or text == "💎 Premium":
            await cmd_premium(message)
        elif text == BTN_LINK or text == "🔗 Привязать":
            await cmd_link(message)
        elif text == BTN_REFERRAL or text == "🎁 Реферал":
            await cmd_referral(message)
        elif text == BTN_APP or text == "📱 Приложение":
            await cmd_open_app(message)
        elif text == BTN_DONATE or text == "❤️ Поддержать":
            await cmd_donate(message)
        elif text == BTN_BUG or text == "🐛 Ошибка":
            await cmd_bug_report(message, state)
        elif text == BTN_IDEA or text == "💡 Предложение":
            await cmd_idea(message, state)
        else:
            await handle_text_search(message, state)
    except Exception as e:
        logger.exception(f"handle_main_button CRASHED for text={text!r}: {e}")
        try:
            await message.answer("⚠️ Ошибка. Попробуй /start или /help")
        except Exception:
            pass


# === /profile ===
async def cmd_profile(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer("Профиль не найден. Нажми /start")
        return

    from db import get_user_stats_summary
    stats = await get_user_stats_summary(uid)
    if not stats:
        await message.answer("Профиль не найден.")
        return

    text = (
        f"👤 <b>Твой профиль:</b>\n\n"
        f"🆔 Telegram ID: <code>{telegram_id}</code>\n"
        f"📊 Репутация: <b>{stats.get('reputation', 0)}</b>/100\n"
        f"📝 Отчётов сделано: <b>{stats.get('total_reports', 0)}</b>\n"
        f"✅ Подтверждено: <b>{stats.get('confirmed_reports', 0)}</b>\n"
    )
    if stats.get("region") or stats.get("city"):
        loc = ", ".join(filter(None, [stats.get("city"), stats.get("region")]))
        text += f"📍 Регион: {loc}\n"

    if await is_premium(uid):
        text += "\n⭐ <b>Premium</b> — push без cooldown, расширенная аналитика\n"

    badges = stats.get("badges", [])
    if badges:
        text += f"\n🏆 <b>Твои бейджи ({len(badges)}):</b>\n"
        for b in badges:
            text += f"  {b['emoji']} <b>{b['name']}</b> — {b['desc']}\n"
    else:
        text += "\n🎯 Сделай первый отчёт, чтобы получить бейдж 🥉 «Новичок»!"

    kb_rows = [
        [InlineKeyboardButton(text="🏪 Зарегистрировать АЗС", callback_data="go_register_owner")],
    ]
    if not await is_premium(uid):
        kb_rows.append([InlineKeyboardButton(text=f"⭐ Купить Premium за {settings.PREMIUM_PRICE_STARS} Stars", callback_data="cmd_premium")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer(text, reply_markup=with_home_inline(kb))


async def profile_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    await cmd_profile(callback.message)


async def help_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    await cmd_help(callback.message)


# === menu:* — inline-меню ===
async def menu_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    try:
        await callback.answer()
    except Exception:
        pass

    data = callback.data or ""
    action = data.split(":", 1)[1] if ":" in data else ""
    msg = callback.message

    try:
        if action == "find":
            await cmd_find(msg)
        elif action == "city":
            await msg.answer(
                "📍 <b>Выбери населённый пункт</b>\n\n"
                "Иваново, Москва, СПб, и другие. "
                "Или напиши свой город в сообщении — бот найдёт АЗС.",
                reply_markup=city_keyboard(),
            )
        elif action == "report":
            await msg.answer(
                "📝 <b>Выбери город, чтобы сообщить о наличии:</b>",
                reply_markup=report_city_keyboard(),
            )
        elif action == "profile":
            await cmd_profile(msg)
        elif action == "subscribe":
            await cmd_subscribe(msg, None)
        elif action == "premium":
            await cmd_premium(msg)
        elif action == "link":
            await cmd_link(msg)
        elif action == "owner":
            await cmd_register_owner(msg, None)
        elif action == "my_stations":
            await cmd_my_stations(msg)
        elif action == "help":
            await cmd_help(msg)
        elif action == "app":
            await cmd_open_app(msg)
        elif action == "donate":
            await cmd_donate(msg)
        elif action == "bug":
            await cmd_bug_report(msg, None)
        elif action == "idea":
            await cmd_idea(msg, None)
        else:
            await msg.answer(f"❓ Неизвестное действие: {action}", reply_markup=main_menu_keyboard())
    except Exception as e:
        logger.exception(f"menu_callback: action={action} failed: {e}")
        try:
            await msg.answer(f"⚠️ Ошибка: {e}", reply_markup=main_menu_keyboard())
        except Exception:
            pass


# === city:* — выбор города ===
async def city_callback(callback: CallbackQuery, state: FSMContext):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    data = callback.data or ""
    city_name = data.split(":", 1)[1] if ":" in data else ""
    msg = callback.message

    if city_name == "other":
        await state.update_data(awaiting_city_text=True)
        await msg.answer(
            "✏️ <b>Напиши название города</b> в сообщении:\n\n"
            "Например: <code>Иваново</code>, <code>Москва</code>, <code>Краснодар</code>",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.update_data(user_city=city_name)
    await show_city_filters(msg, city_name)


async def show_city_filters(msg, city: str):
    """Показать фильтры после выбора города."""
    await msg.answer(
        f"📍 <b>{city}</b>\n\n"
        f"Выбери тип топлива или фильтры:",
        reply_markup=with_home_inline(filters_keyboard(city)),
    )


# === show_city_results — поиск АЗС по городу с фильтрами ===
async def show_city_results(msg, city: str, fuel: str = None, max_price: float = None, network: str = None):
    """Показывает АЗС в городе с фильтрами."""
    try:
        stations = await find_stations_by_city(
            city=city, fuel_type=fuel, network=network,
            max_price=max_price, has_stock=False, limit=20,
        )
        if not stations:
            filter_desc = []
            if fuel: filter_desc.append(f"топливо АИ-{fuel}")
            if max_price: filter_desc.append(f"до {max_price}₽")
            if network: filter_desc.append(f"сеть: {network}")
            await msg.answer(
                f"🔍 <b>В городе {city} ничего не найдено</b>\n"
                f"Фильтры: {', '.join(filter_desc) or 'нет'}\n\n"
                f"Попробуй сбросить фильтры или выбрать другой город.",
                reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Сбросить фильтры", callback_data=f"city:{city}")],
                    [InlineKeyboardButton(text="🔍 Другой город", callback_data="menu:find")],
                ])),
            )
            return

        stations_with_status = await get_stations_with_statuses(stations)

        # Определяем продвинутые АЗС
        from db import get_promoted_station_ids
        promoted_ids = set(await get_promoted_station_ids(city) or [])

        def _sort_key(s):
            statuses = s.get("statuses") or []
            non_all = [st for st in statuses if st.get("fuel_type") != "all"]
            has_available = any(st.get("available") is True for st in non_all)
            has_low = any(st.get("available") is None for st in non_all)
            has_unavailable = any(st.get("available") is False for st in non_all)
            if has_available:
                avail_rank = 0
            elif has_low:
                avail_rank = 1
            elif has_unavailable:
                avail_rank = 2
            else:
                avail_rank = 3
            return (
                0 if s["id"] in promoted_ids else 1,
                avail_rank,
                0 if s.get("is_verified") else 1,
                (s.get("name") or "").lower(),
            )
        stations_with_status.sort(key=_sort_key)

        filter_desc = []
        if fuel: filter_desc.append(f"топливо АИ-{fuel}")
        if max_price: filter_desc.append(f"до {max_price}₽")
        if network: filter_desc.append(f"сеть: {network}")
        title = f"⛽ <b>{city}</b> — найдено {len(stations_with_status)} АЗС"
        if filter_desc:
            title += f"\n<i>Фильтры: {', '.join(filter_desc)}</i>"
        title += "\n"

        buttons = []
        for s in stations_with_status[:10]:
            statuses = s.get("statuses", [])
            operator = (s.get("operator") or s.get("name") or "")[:16]
            address = (s.get("address") or "")[:24]
            city_part = (s.get("city") or "")[:12]
            # Формируем строку: Название · Адрес, Город
            if operator and address and city_part:
                short = f"{operator} · {address}, {city_part}"
            elif operator and address:
                short = f"{operator} · {address}"
            elif operator and city_part:
                short = f"{operator} · {city_part}"
            elif address and city_part:
                short = f"{address}, {city_part}"
            elif operator:
                short = operator
            elif address:
                short = address
            else:
                name = (s.get("name") or "АЗС")[:24]
                short = name
            best_price = None
            best_fuel = None
            has_available = False
            has_unavailable = False
            for st in statuses:
                if st.get("fuel_type") == "all":
                    continue
                if st.get("available") is True:
                    has_available = True
                    if st.get("price") is not None:
                        if best_price is None or st["price"] < best_price:
                            best_price = st["price"]
                            best_fuel = st.get("fuel_type")
                elif st.get("available") is False:
                    has_unavailable = True
            if best_price is not None and best_fuel:
                short += f" · АИ-{best_fuel} {best_price:.2f}₽"
            elif has_available:
                short += " · ✅ есть"
            elif has_unavailable:
                short += " · ❌ нет"
            elif s.get("has_data"):
                short += " · ⚠️ кончается"
            else:
                short += " · ❓ нет данных"
            buttons.append([InlineKeyboardButton(text=short[:64], callback_data=f"st:{s['id']}")])

        nav_buttons = []
        if fuel or max_price or network:
            nav_buttons.append([
                InlineKeyboardButton(text="🔄 Сбросить фильтры", callback_data=f"city:{city}"),
            ])
        nav_buttons.append([
            InlineKeyboardButton(text="🚨 Экстренный (любая цена/сеть)", callback_data=f"emergency:{city}"),
            InlineKeyboardButton(text="💰 Фильтр по цене", callback_data=f"price_menu:{city}"),
        ])

        await msg.answer(
            title,
            reply_markup=with_home_inline(InlineKeyboardMarkup(
                inline_keyboard=buttons + nav_buttons
            )),
        )
    except Exception as e:
        logger.exception(f"show_city_results: {e}")
        await msg.answer(f"⚠️ Ошибка: {e}", reply_markup=main_menu_keyboard())


# === emergency_handler ===
async def emergency_handler(msg, city: str = None):
    """Экстренный режим — АЗС с подтверждённым наличием топлива."""
    if not city:
        await msg.answer(
            "🚨 <b>Экстренный режим</b>\n\n"
            "Найдём АЗС с подтверждённым наличием бензина.\n"
            "Без фильтров по цене, сети, очереди.\n\n"
            "📍 Выбери город:",
            reply_markup=with_home_inline(city_keyboard()),
        )
        return

    try:
        stations = await find_stations_by_city(
            city=city, fuel_type=None, network=None,
            max_price=None, has_stock=False, limit=50,
        )
        if not stations:
            await msg.answer(
                f"🚨 <b>Экстренный: {city}</b>\n\n"
                f"❌ Нет подтверждённых данных о наличии топлива.\n\n"
                f"💡 Сообщи о наличии сам — открой АЗС через «🔍 Найти АЗС» и нажми «📝 Сообщить».",
                reply_markup=main_menu_keyboard(),
            )
            return

        stations_with_status = await get_stations_with_statuses(stations)
        # Фильтруем: оставляем АЗС где хотя бы одно топливо есть или кончается
        stations_with_status = [s for s in stations_with_status if any(
            st.get("available") is not False and st.get("fuel_type") != "all"
            for st in (s.get("statuses") or [])
        )]
        if not stations_with_status:
            await msg.answer(
                f"🚨 <b>Экстренный: {city}</b>\n\n"
                f"❌ Нет данных о наличии топлива.\n\n"
                f"💡 Сообщи о наличии сам — открой АЗС через «🔍 Найти АЗС» и нажми «📝 Сообщить».",
                reply_markup=main_menu_keyboard(),
            )
            return

        def _sort_key(s):
            statuses = s.get("statuses", [])
            non_all = [st for st in statuses if st.get("fuel_type") != "all"]
            has_available = any(st.get("available") is True for st in non_all)
            has_low = any(st.get("available") is None for st in non_all)
            has_unavailable = any(st.get("available") is False for st in non_all)
            if has_available:
                avail_rank = 0
            elif has_low:
                avail_rank = 1
            elif has_unavailable:
                avail_rank = 2
            else:
                avail_rank = 3
            has_price = any(st.get("price") is not None for st in statuses)
            return (
                avail_rank,
                0 if s.get("is_verified") else 1,
                0 if has_price else 1,
                (s.get("name") or "").lower(),
            )
        stations_with_status.sort(key=_sort_key)

        lines = [f"🚨 <b>{city}</b> — {len(stations_with_status)} АЗС с топливом\n"]
        buttons = []
        for s in stations_with_status[:10]:
            statuses = s.get("statuses", [])
            operator = (s.get("operator") or "")[:14]
            address = (s.get("address") or "")[:18]

            best = None
            for st in statuses:
                if st.get("available") is True:
                    if not best or (st.get("price") is not None and (best.get("price") is None or st["price"] < best["price"])):
                        best = st
            if not best and statuses:
                best = statuses[0]

            # Сеть → адрес
            if operator and address:
                short = f"{operator} — {address}"
            elif operator:
                short = operator
            elif address:
                short = address
            else:
                name = (s.get("name") or "АЗС")[:22]
                short = name
            if best and best.get("price") is not None:
                short += f" · АИ-{best.get('fuel_type', '?')} {best['price']:.2f}₽"
            elif best:
                short += f" · АИ-{best.get('fuel_type', '?')} ✅"
            buttons.append([InlineKeyboardButton(text=short[:64], callback_data=f"st:{s['id']}")])

        for s in stations_with_status[:3]:
            statuses = s.get("statuses", [])
            name = s.get("name") or "АЗС"
            address = s.get("address") or ""
            lines.append(f"  • <b>{name}</b>")
            if address:
                lines.append(f"    📍 {address[:60]}")
            for st in statuses[:3]:
                ft = st.get("fuel_type", "?")
                price = st.get("price")
                avail = st.get("available")
                icon = "✅" if avail is True else ("⚠️" if avail is None else "❌")
                line = f"    {icon} АИ-{ft}"
                if price is not None:
                    line += f" — <b>{price:.2f}₽</b>"
                if st.get("queue_size"):
                    line += f" · 🕐 ~{st['queue_size']}"
                lines.append(line)
            lines.append("")

        await msg.answer(
            "\n".join(lines) + "💡 Без фильтров — здесь точно есть топливо (по последним отчётам).",
            reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=buttons)),
        )
    except Exception as e:
        logger.exception(f"emergency_handler: {e}")
        await msg.answer(f"⚠️ Ошибка экстренного поиска: {e}", reply_markup=main_menu_keyboard())


# === fuel:* — выбор топлива ===
async def fuel_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        return
    _, city, fuel = parts[0], parts[1], parts[2]
    await show_city_results(callback.message, city, fuel=fuel)


# === all:* — показать все АЗС без фильтров ===
async def all_stations_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    data = callback.data or ""
    city = data.split(":", 1)[1] if ":" in data else ""
    if city:
        await show_city_results(callback.message, city)


# === price_menu:* ===
async def price_menu_callback(callback: CallbackQuery):
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":", 2)
    city = parts[1] if len(parts) > 1 else ""
    fuel = parts[2] if len(parts) > 2 else None
    suffix = f" (АИ-{fuel})" if fuel else ""
    await callback.message.answer(
        f"💰 <b>Фильтр по цене для {city}{suffix}:</b>",
        reply_markup=with_home_inline(price_filter_keyboard(city, fuel)),
    )


# === net_menu:* ===
async def net_menu_callback(callback: CallbackQuery):
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":", 2)
    city = parts[1] if len(parts) > 1 else ""
    fuel = parts[2] if len(parts) > 2 else None
    suffix = f" (АИ-{fuel})" if fuel else ""
    await callback.message.answer(
        f"⛽ <b>Фильтр по сети для {city}{suffix}:</b>",
        reply_markup=with_home_inline(network_filter_keyboard(city, fuel)),
    )


# === price:* ===
async def price_callback(callback: CallbackQuery):
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        return
    city = parts[1]

    fuel: str | None = None
    price_str: str
    if len(parts) >= 4 and parts[3] in ("any",) or (len(parts) >= 4 and parts[3].replace(".", "").isdigit()):
        fuel = parts[2]
        price_str = parts[3]
    else:
        price_str = parts[2]

    if price_str == "any":
        await callback.message.answer(
            "💰 Фильтр по цене сброшен.",
            reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📍 {city}: выбрать фильтры заново", callback_data=f"city:{city}")],
            ])),
        )
        return
    try:
        max_price = float(price_str)
        await show_city_results(callback.message, city=city, fuel=fuel, max_price=max_price)
    except (ValueError, IndexError):
        pass


# === net:* ===
async def net_callback(callback: CallbackQuery):
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        return
    city = parts[1]

    if len(parts) >= 4 and parts[2] in ("92", "95", "98", "diesel", "100", "lpg"):
        fuel = parts[2]
        network = parts[3]
    else:
        fuel = None
        network = parts[2]

    if network == "any":
        await callback.message.answer(
            "⛽ Фильтр по сети сброшен.",
            reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📍 {city}: выбрать фильтры заново", callback_data=f"city:{city}")],
            ])),
        )
        return
    await show_city_results(callback.message, city=city, fuel=fuel, network=network)


# === emergency:* ===
async def emergency_city_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 2:
        return
    city = parts[1]
    await emergency_handler(callback.message, city=city)


# === premium callback ===
async def premium_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    await cmd_premium(callback.message)


# === go_register_owner callback ===
async def go_register_owner_callback(callback: CallbackQuery):
    await callback.answer()
    await cmd_register_owner(callback.message, None)


# === /stats ===
async def cmd_stats(message: Message):
    from db import get_stats
    stats = await get_stats()
    text = (
        "📊 <b>Статистика «Бензин рядом»:</b>\n\n"
        f"⛽ АЗС в базе: <b>{stats.get('stations_count', 0):,}</b>\n"
        f"👥 Пользователей: <b>{stats.get('users_count', 0):,}</b>\n"
        f"📝 Отчётов за 24ч: <b>{stats.get('reports_24h', 0):,}</b>\n"
        f"🏙 Городов: <b>{stats.get('cities_count', 0)}</b>\n"
    )

    try:
        from api import get_source_stats
        sources = await get_source_stats()
        if sources:
            text += "\n<b>📡 Источники:</b>\n"
            for s in sources:
                status_icon = {"OK": "✅", "STALE": "🟡", "DEAD": "🔴"}.get(s["status"], "❓")
                text += (
                    f"  {status_icon} <code>{s['source']}</code>: "
                    f"{s['h24']}/24h, {s['total']} всего\n"
                )
    except Exception as e:
        text += f"\n⚠ Ошибка источников: {e}\n"

    text += (
        "\n🔗 <b>API:</b>\n"
        "  /api/health — health check\n"
        "  /api/admin/stats — статистика\n"
        "  /api/stations/by-city — поиск по городу"
    )
    await message.answer(text, reply_markup=main_menu_keyboard())


# === Admin: установить рекламный баннер ===
async def cmd_set_ad(message: Message):
    """Формат: /set_ad Текст баннера | https://ссылка"""
    if not settings.is_admin(user_id=message.from_user.id, username=message.from_user.username):
        await message.answer("⛔ Только для администраторов.")
        return
    raw = message.text.replace("/set_ad", "", 1).strip()
    if not raw:
        await message.answer(
            "📢 <b>Установить рекламный баннер</b>\n\n"
            "Формат: <code>/set_ad Текст баннера | https://ссылка</code>\n"
            "Чтобы удалить: <code>/set_ad off</code>",
            reply_markup=main_menu_keyboard(),
        )
        return
    if raw.lower() == "off":
        settings.AD_BANNER_TEXT = ""
        settings.AD_BANNER_URL = ""
        await message.answer("📢 Баннер отключён.")
        return
    if "|" not in raw:
        await message.answer(
            "❌ Формат: <code>/set_ad Текст баннера | https://ссылка</code>\n"
            "Чтобы удалить: <code>/set_ad off</code>",
            reply_markup=main_menu_keyboard(),
        )
        return
    parts = raw.split("|", 1)
    text = parts[0].strip()
    url = parts[1].strip()
    if not url.startswith("http"):
        await message.answer("❌ Ссылка должна начинаться с http/https")
        return
    settings.AD_BANNER_TEXT = text
    settings.AD_BANNER_URL = url
    await message.answer(
        f"📢 <b>Баннер установлен!</b>\n\n"
        f"Текст: {text}\n"
        f"Ссылка: {url}\n\n"
        f"Будет показан в главном меню."
    )


# === Admin: рассылка рекламы сервиса ===
PROMO_TEXT = """⛽ <b>«Бензин рядом» — сервис, которого больше нигде нет.</b>

Единственный бот, который собирает данные сразу из 50+ источников и показывает реальную картину на АЗС.

<b>Чем он лучше остальных:</b>

1️⃣ <b>Самая полная база.</b> 27 000 АЗС по всей стране. Ни один другой бот или канал не покрывает такую территорию.

2️⃣ <b>Пять источников данных одновременно.</b> Fuelprice.ru, 2ГИС, 28 региональных TG-каналов, официальные данные сетей и отчёты реальных водителей. Если данные есть где-то — мы их собрали.

3️⃣ <b>Обновление каждый час.</b> Не раз в день, не когда кто-то вспомнил. Каждый час парсеры проверяют все источники и обновляют статусы.

4️⃣ <b>Наличие, а не только адреса.</b> Яндекс.Карты покажут где АЗС. Мы покажем есть ли там бензин.

5️⃣ <b>Данные от водителей.</b> Любой может сообщить о ситуации на АЗС. Пользовательские отчёты живут 7 дней и приоритетнее парсеров.

6️⃣ <b>Работает в обоих мессенджерах.</b> Telegram заблокирован — есть VK. VK недоступен — есть Telegram. Сервис доступен всегда.

7️⃣ <b>Полностью бесплатно.</b>

<b>Как пользоваться:</b>
1. Открой бота в Telegram или VK
2. Отправь геолокацию
3. Получи список ближайших АЗС с ценами и статусами

📱 <b>Telegram:</b> <a href="https://t.me/benzyn_ryadom_bot">@benzyn_ryadom_bot</a>
📱 <b>VK:</b> <a href="https://vk.com/benzyn_ryadom">vk.com/benzyn_ryadom</a>

⏱ Время экономится на каждом выезде — проверяй перед дорогой и не стой в очередях впустую."""


async def cmd_promote(message: Message):
    """Рассылка рекламного текста сервиса по чатам."""
    if not settings.is_admin(user_id=message.from_user.id, username=message.from_user.username):
        await message.answer("⛔ Только для администраторов.")
        return

    import aiohttp as _aiohttp

    bot = message.bot
    bot_token = (await bot.get_token()) if hasattr(bot, 'get_token') else os.getenv("BOT_TOKEN", "")

    # Получаем список чатов из getUpdates
    chats = {}
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"limit": 100, "allowed_updates": '["message", "channel_post", "my_chat_member"]'}

    async with _aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                for update in data.get("result", []):
                    for key in ("message", "channel_post", "my_chat_member"):
                        if key in update:
                            chat = update[key].get("chat", {})
                            if chat.get("id"):
                                chats[chat["id"]] = {
                                    "id": chat["id"],
                                    "title": chat.get("title", chat.get("first_name", "Unknown")),
                                    "type": chat.get("type", "unknown"),
                                }

    if not chats:
        await message.answer(
            "📢 <b>Рассылка рекламы</b>\n\n"
            "Бот не состоит ни в одном чате (кроме этого).\n"
            "Добавьте бота в нужные чаты и отправьте там любое сообщение.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Показываем список чатов
    chat_list = "\n".join(f"• {c['title']} ({c['type']})" for c in chats.values())
    await message.answer(
        f"📢 <b>Рассылка рекламы</b>\n\n"
        f"Найдено чатов: <b>{len(chats)}</b>\n\n"
        f"{chat_list}\n\n"
        f"Отправляю...",
        reply_markup=main_menu_keyboard(),
    )

    # Рассылка
    sent = 0
    failed = 0
    for chat_id, chat_info in chats.items():
        try:
            async with _aiohttp.ClientSession() as session:
                payload = {
                    "chat_id": chat_id,
                    "text": PROMO_TEXT,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                async with session.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json=payload,
                ) as resp:
                    data = await resp.json()
                    if resp.status == 200 and data.get("ok"):
                        sent += 1
                    else:
                        failed += 1
            await asyncio.sleep(1)  # Лимит Telegram: 30 сообщений/сек в группах
        except Exception:
            failed += 1

    await message.answer(
        f"📢 <b>Рассылка завершена!</b>\n\n"
        f"✅ Отправлено: {sent}\n"
        f"❌ Ошибок: {failed}",
        reply_markup=main_menu_keyboard(),
    )


# === Геолокация ===
async def handle_location(message: Message, state: FSMContext):
    telegram_id = _tg_id(message)
    uid = await get_or_create_user(message)
    await log_event(uid, "location_shared")

    location = message.location
    lat = location.latitude
    lon = location.longitude

    current_state = await state.get_state()

    if current_state == SubscribeStates.waiting_geo.state:
        await state.update_data(lat=lat, lon=lon)
        await state.set_state(SubscribeStates.waiting_radius)
        await message.answer(
            "📍 Геолокацию получил.\n\n"
            "Выбери радиус уведомлений:",
            reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="3 км", callback_data="sub_radius:3"),
                    InlineKeyboardButton(text="5 км", callback_data="sub_radius:5"),
                    InlineKeyboardButton(text="10 км", callback_data="sub_radius:10"),
                ],
            ])),
        )
        return

    await _do_find(message, lat, lon)


async def _do_find(message: Message, lat: float, lon: float):
    cached = _cache_get(lat, lon, 30)
    if cached is not None:
        stations = cached
    else:
        stations = await find_nearest_stations(lat=lat, lon=lon, limit=10, radius_km=30)
        _cache_set(lat, lon, 30, stations)

    if not stations:
        await message.answer(
            "😔 <b>Рядом не нашёл АЗС в базе.</b>\n\n"
            "Попробуй написать название города или сети.",
            reply_markup=main_menu_keyboard(),
        )
        return

    from db import get_stations_with_statuses
    stations = await get_stations_with_statuses(stations)

    text = f"🔍 <b>Нашёл {len(stations)} АЗС рядом:</b>\n\n"
    buttons = []
    for s in stations:
        statuses = s.get("statuses", [])
        dist = format_distance(s.get("distance_km", 0))
        status_icon = _get_main_status_icon(statuses)
        name = (s.get("name") or "АЗС")[:22]
        city = (s.get("city") or "")[:10]
        btn_text = f"{status_icon} {name} • {dist}"
        if city:
            btn_text += f" • {city}"
        buttons.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"st:{s['id']}")
        ])
    await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=buttons)))


def _get_main_status_icon(statuses: list) -> str:
    """Агрегирует статус по всем видам топлива: лучший из всех = иконка станции."""
    if not statuses:
        return "❓"
    has_available = False
    has_low = False
    has_unavailable = False
    for st in statuses:
        if st.get("fuel_type") == "all":
            continue
        available = st.get("available")
        if available is True or available == 1:
            has_available = True
        elif available is None:
            has_low = True
        elif available is False or available == 0:
            has_unavailable = True
    if has_available:
        return "✅"
    if has_low:
        return "⚠️"
    if has_unavailable:
        return "❌"
    return "❓"


# === Поиск по тексту ===
async def handle_text_search(message: Message, state: FSMContext | None = None):
    if not message.text:
        return
    query = message.text.strip()
    if len(query) < 2:
        return

    user_id = await get_or_create_user(message)
    await log_event(user_id, "text_search", {"query": query})

    # Определяем приоритетный город из FSM
    priority_city = None
    if state:
        data = await state.get_data()
        priority_city = data.get("user_city")
        # Если пользователь ввёл город вручную ("другой город")
        if data.get("awaiting_city_text"):
            await state.update_data(user_city=query, awaiting_city_text=False)
            priority_city = query

    stations = await find_stations_by_name(query, limit=8, priority_city=priority_city)
    if not stations:
        await message.answer(
            f"😔 По запросу <b>«{query}»</b> ничего не нашёл.\n\n"
            f"Попробуй написать по-другому или отправь 📍 геолокацию.",
            reply_markup=main_menu_keyboard(),
        )
        return

    from db import get_stations_with_statuses
    stations = await get_stations_with_statuses(stations)

    text = f"🔍 По запросу <b>«{query}»</b> нашёл {len(stations)} АЗС:\n\n"
    buttons = []
    for s in stations:
        statuses = s.get("statuses", [])
        status_icon = _get_main_status_icon(statuses)
        operator = (s.get("operator") or "")[:14]
        address = (s.get("address") or "")[:18]
        city = (s.get("city") or "")[:12]
        # Сеть → адрес → город
        if operator and address:
            short = f"{operator} — {address}"
        elif operator and city:
            short = f"{operator} — {city}"
        elif operator:
            short = operator
        elif address and city:
            short = f"{city}, {address}"
        elif address:
            short = address
        elif city:
            short = city
        else:
            short = (s.get("name") or "АЗС")[:22]
        btn_text = f"{status_icon} {short}"
        buttons.append([
            InlineKeyboardButton(text=btn_text[:64], callback_data=f"st:{s['id']}")
        ])
    await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=buttons)))


# === Карточка АЗС ===
async def show_station_details(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    station_id = int(callback.data.split(":")[1])
    user_id = await _ensure_callback_user(callback)
    await log_event(user_id, "station_viewed", {"station_id": station_id})

    station = await get_station_by_id(station_id)
    if not station:
        await callback.answer("АЗС не найдена", show_alert=True)
        return

    statuses = await get_station_current_status(station_id)
    # Добавляем рейтинг в station dict для отображения
    rating_info = await get_station_rating(station_id)
    station["avg_rating"] = rating_info["avg_rating"]
    station["total_reviews"] = rating_info["total_reviews"]
    text = format_station_card(station, statuses)
    lat = station.get("lat")
    lon = station.get("lon")
    kb = station_actions_keyboard(station_id, has_statuses=len(statuses) > 0, lat=lat, lon=lon)

    # Если пользователь — владелец АЗС, добавляем кнопку продвижения
    from db import is_owner_of_station, is_station_promoted, get_owner_station_by_user_and_station, PROMO_PRICE_STARS
    tid = callback.from_user.id if callback.from_user else 0
    uid_owner = await get_user_id_by_telegram_id(tid) if tid else None
    if uid_owner and await is_owner_of_station(uid_owner, station_id):
        owner_station = await get_owner_station_by_user_and_station(uid_owner, station_id)
        if owner_station:
            is_promo = await is_station_promoted(station_id)
            if is_promo:
                promo_text = f"🌟 Продвижение активно до {owner_station.get('promoted_until', '?')[:10]}"
            else:
                promo_text = f"🌟 Продвинуть АЗС ({PROMO_PRICE_STARS}⭐ / 30 дн)"
            kb.inline_keyboard.insert(0, [InlineKeyboardButton(
                text=promo_text,
                callback_data=f"promote:{station_id}",
            )])

    await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


# === Маршрут до АЗС ===
async def route_callback(callback: CallbackQuery):
    """Открывает маршрут до АЗС через Яндекс Карты / 2ГИС / Google Maps."""
    await callback.answer()
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    try:
        station_id = int(parts[1])
        lat = float(parts[2])
        lon = float(parts[3])
    except (ValueError, IndexError):
        return

    # Текст с адресом
    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else "АЗС"
    address = station.get("address", "") if station else ""
    city = station.get("city", "") if station else ""

    location = f"{name}"
    if address:
        location += f", {address}"
    if city:
        location += f", {city}"

    # Ссылки на навигаторы
    yandex_url = f"https://yandex.ru/maps/?rtext={lat},{lon}&rtt=auto"
    gis_url = f"https://2gis.ru/geo/{lon}/{lat}"
    google_url = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗺 Яндекс Карты", url=yandex_url)],
        [InlineKeyboardButton(text="🗺 2ГИС", url=gis_url)],
        [InlineKeyboardButton(text="🗺 Google Maps", url=google_url)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"st:{station_id}")],
    ])

    text = (
        f"📍 <b>Маршрут до АЗС</b>\n\n"
        f"⛽ {esc(location)}\n\n"
        f"Выбери навигатор:"
    )
    await callback.message.answer(text, reply_markup=kb)


def esc(s: str) -> str:
    """Экранирование HTML."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# === Продвижение АЗС ===
async def promote_callback(callback: CallbackQuery):
    """Отправить invoice для продвижения АЗС."""
    station_id = int(callback.data.split(":")[1])
    tid = callback.from_user.id if callback.from_user else 0
    uid = await get_user_id_by_telegram_id(tid) if tid else None
    if not uid:
        await callback.answer("Ошибка", show_alert=True)
        return

    from db import is_owner_of_station, get_owner_station_by_user_and_station, is_station_promoted, PROMO_PRICE_STARS
    if not await is_owner_of_station(uid, station_id):
        await callback.answer("Только владелец может продвигать АЗС", show_alert=True)
        return

    owner_station = await get_owner_station_by_user_and_station(uid, station_id)
    if not owner_station:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    if await is_station_promoted(station_id):
        await callback.answer("Уже продвигается!", show_alert=True)
        return

    station = await get_station_by_id(station_id)
    name = station.get("name", "АЗС") if station else "АЗС"

    prices = [LabeledPrice(label=f"Продвижение · 30 дней", amount=PROMO_PRICE_STARS)]
    try:
        await callback.message.answer_invoice(
            title=f"Продвижение: {name}",
            description=f"Приоритет в выдаче по городу на 30 дней. АЗС будет отображаться выше остальных.",
            payload=f"promote_{owner_station['id']}",
            provider_token="",
            currency="XTR",
            prices=prices,
        )
    except Exception as e:
        logger.exception("Promote invoice failed: %s", e)
        await callback.answer("Ошибка", show_alert=True)
        return
    await callback.answer()


# === Report flow: выбор города для отчёта ===
async def report_city_callback(callback: CallbackQuery):
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    await callback.answer()
    data = callback.data or ""
    city = data.split(":", 1)[1] if ":" in data else ""
    msg = callback.message

    if city == "other":
        await msg.answer(
            "✏️ <b>Напиши название города</b> в сообщении:\n\n"
            "Например: <code>Иваново</code>, <code>Москва</code>",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Ищем АЗС в городе
    stations = await find_stations_by_city(city=city, has_stock=None, limit=15)
    if not stations:
        await msg.answer(
            f"😔 В <b>{city}</b> АЗС не найдены.\n"
            "Попробуй другой город или напиши адрес в сообщении.",
            reply_markup=report_city_keyboard(),
        )
        return

    await msg.answer(
        f"⛽ <b>Выбери АЗС в {city}:</b>",
        reply_markup=report_station_keyboard(stations, city),
    )


# === Report flow: выбор АЗС ===
async def report_pick_callback(callback: CallbackQuery):
    await callback.answer()
    station_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "⛽ <b>Выбери тип топлива:</b>",
        reply_markup=fuel_type_keyboard(station_id),
    )


# === Report flow: выбор топлива и отправка ===
async def report_start(callback: CallbackQuery):
    station_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "⛽ <b>Выбери тип топлива:</b>",
        reply_markup=fuel_type_keyboard(station_id),
    )
    await callback.answer()


# === Парсинг сообщений от ботов-конкурентов ===
async def handle_bot_message(message: Message):
    """Перехват сообщений от других ботов (в группах, где есть наш бот)."""
    if not message.text or len(message.text) < 10:
        return
    if message.from_user.is_bot is False:
        return
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    try:
        from price_parser import parse_prices, detect_network, detect_city, detect_availability
        prices = parse_prices(message.text)
        if not prices:
            return
        network = detect_network(message.text)
        city = detect_city(message.text)
        available = detect_availability(message.text)
        bot_username = message.from_user.username or "unknown"
        logger.info(f"bot_competitor: {bot_username} → {len(prices)} цен (network={network}, city={city})")
        await get_or_create_user(message)
        uid = await get_user_id_by_telegram_id(_tg_id(message))

        # Ищем существующую АЗС по сети+городу
        station_id = None
        if network and city:
            rows = await _fetch(
                "SELECT id FROM stations WHERE LOWER(operator) LIKE ? AND LOWER(city) LIKE ? LIMIT 1",
                f"%{network.lower()}%", f"%{city.lower()}%",
            )
            if rows:
                station_id = rows[0]["id"]

        # Fallback: создаём запись-заглушку
        if not station_id:
            station_name = f"Bot: @{bot_username} ({network or '?'}/{city or '?'})"
            rows = await _fetch("SELECT id FROM stations WHERE name = ? LIMIT 1", station_name)
            if rows:
                station_id = rows[0]["id"]
            else:
                new_id = await _execute(
                    """INSERT INTO stations (name, lat, lon, city, region, operator, is_active, created_at)
                       VALUES (?, 0, 0, ?, '', ?, TRUE, datetime('now'))""",
                    station_name, city or "", network or "",
                    returning=True,
                )
                if new_id:
                    station_id = new_id

        if not station_id:
            return

        for fuel, price in prices.items():
            await add_report(
                station_id=station_id,
                user_id=uid,
                fuel_type=fuel,
                available=available,
                price=price,
                source="bot_competitor",
                comment=f"@{bot_username}: {message.text[:100]}",
            )
    except Exception as e:
        logger.warning(f"handle_bot_message: {e}")


# === Парсинг пересланных сообщений от ботов-конкурентов ===
async def handle_forwarded_bot_message(message: Message):
    """Перехват пересланных сообщений от других ботов (пользователи пересылают нам ответы)."""
    if not message.text or len(message.text) < 10:
        return
    # Проверяем что сообщение переслано от бота
    fwd = message.forward_origin
    if fwd is None:
        return
    fwd_sender = getattr(fwd, "sender_user_name", None) or getattr(fwd, "sender_user_id", None)
    if not fwd_sender:
        return
    # Проверяем что это бот (по username или контексту)
    text = message.text
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    try:
        from price_parser import parse_prices, detect_network, detect_city, detect_availability
        prices = parse_prices(text)
        if not prices:
            return
        network = detect_network(text)
        city = detect_city(text)
        available = detect_availability(text)
        logger.info(f"forwarded_bot: {fwd_sender} → {len(prices)} цен (network={network}, city={city})")
        await get_or_create_user(message)
        uid = await get_user_id_by_telegram_id(_tg_id(message))

        # Ищем существующую АЗС по сети+городу
        station_id = None
        if network and city:
            rows = await _fetch(
                "SELECT id FROM stations WHERE LOWER(operator) LIKE ? AND LOWER(city) LIKE ? LIMIT 1",
                f"%{network.lower()}%", f"%{city.lower()}%",
            )
            if rows:
                station_id = rows[0]["id"]

        if not station_id:
            station_name = f"Forward: {fwd_sender} ({network or '?'}/{city or '?'})"
            rows = await _fetch("SELECT id FROM stations WHERE name = ? LIMIT 1", station_name)
            if rows:
                station_id = rows[0]["id"]
            else:
                new_id = await _execute(
                    """INSERT INTO stations (name, lat, lon, city, region, operator, is_active, created_at)
                       VALUES (?, 0, 0, ?, '', ?, TRUE, datetime('now'))""",
                    station_name, city or "", network or "",
                    returning=True,
                )
                if new_id:
                    station_id = new_id

        if not station_id:
            return

        for fuel, price in prices.items():
            await add_report(
                station_id=station_id,
                user_id=uid,
                fuel_type=fuel,
                available=available,
                price=price,
                source="bot_competitor_forwarded",
                comment=f"forwarded from {fwd_sender}: {text[:100]}",
            )
    except Exception as e:
        logger.warning(f"handle_forwarded_bot_message: {e}")


async def report_fuel(callback: CallbackQuery):
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    await callback.message.answer(
        f"⛽ <b>АИ-{fuel}</b> — какой статус?",
        reply_markup=report_status_keyboard(station_id, fuel),
    )
    await callback.answer()


async def report_submit(callback: CallbackQuery, state: FSMContext = None):
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    status = parts[3]

    available_map = {"yes": True, "queue": True, "low": None, "no": False}
    queue_map = {"yes": None, "queue": 5, "low": None, "no": None}

    if status not in available_map:
        await callback.answer("Неизвестный статус", show_alert=True)
        return

    # После выбора статуса — спрашиваем доп. данные (цена/лимит/канистры/очередь)
    if state is not None:
        await state.update_data(
            report_station_id=station_id,
            report_fuel=fuel,
            report_status=status,
        )
    status_text = {
        "yes": "✅ Есть",
        "queue": "🕐 Большая очередь",
        "low": "⚠️ Кончается",
        "no": "❌ Нет",
    }[status]
    await callback.message.answer(
        f"Принято: <b>{status_text}</b>\n\n"
        f"Можешь добавить подробности (или сразу сохранить):",
        reply_markup=report_extras_keyboard(station_id, fuel, status),
    )
    await callback.answer()


async def report_extra_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопки 'Указать цену/лимит/канистры/очередь'."""
    parts = callback.data.split(":")
    # report_extra:price:station:fuel:status
    extra_type = parts[1]
    station_id = int(parts[2])
    fuel = parts[3]
    status = parts[4]

    if state is None:
        await callback.answer("Сессия истекла, начни заново", show_alert=True)
        return

    if extra_type == "price":
        await state.set_state(ReportExtrasStates.waiting_price)
        await callback.message.answer(
            f"💰 <b>Введи цену за литр АИ-{fuel} в рублях:</b>\n"
            f"Например: <code>55.40</code> или <code>55</code>\n\n"
            f"Или нажми /cancel для отмены.",
        )
    elif extra_type == "limit":
        await state.set_state(ReportExtrasStates.waiting_limit)
        await callback.message.answer(
            f"🚫 <b>Какой лимит на заправку?</b>\n"
            f"Введи число литров (например: <code>30</code>)\n\n"
            f"Или /cancel для отмены.",
        )
    elif extra_type == "canister":
        # Сразу ставим canister_ban=True и возвращаемся к экстрам
        await state.update_data(report_canister_ban=True)
        await callback.message.answer(
            f"✅ Запрет канистр зафиксирован.\n\n"
            f"Что ещё добавить?",
            reply_markup=report_extras_keyboard(station_id, fuel, status),
        )
    elif extra_type == "queue":
        await state.set_state(ReportExtrasStates.waiting_queue)
        await callback.message.answer(
            f"🚗 <b>Сколько машин в очереди?</b>\n"
            f"Введи число от 1 до 50 (например: <code>3</code>)\n\n"
            f"Или /cancel для отмены.",
        )
    await callback.answer()


async def report_save_with_extras(callback: CallbackQuery, state: FSMContext = None):
    """Сохраняет отчёт со всеми собранными данными (цена/лимит/канистры/очередь)."""
    if state is None:
        await callback.answer("Сессия истекла", show_alert=True)
        return

    data = await state.get_data()
    station_id = data.get("report_station_id")
    fuel = data.get("report_fuel")
    status = data.get("report_status")

    if not (station_id and fuel and status):
        await callback.answer("Нет данных для сохранения", show_alert=True)
        return

    available_map = {"yes": True, "queue": True, "low": None, "no": False}
    base_queue = {"yes": None, "queue": 5, "low": None, "no": None}

    available = available_map[status]
    queue_size = data.get("report_queue") or base_queue[status]
    has_limit = bool(data.get("report_limit"))
    limit_liters = data.get("report_limit")
    canister_ban = bool(data.get("report_canister_ban"))
    price = data.get("report_price")

    uid = await _ensure_callback_user(callback)

    report_id = await add_report(
        station_id=station_id,
        user_id=uid,
        fuel_type=fuel,
        available=available,
        queue_size=queue_size,
        price=price,
        has_limit=has_limit,
        limit_liters=limit_liters,
        canister_ban=canister_ban,
        source="user",
    )

    celebration = await _check_and_celebrate_badges(uid)
    status_text = {
        "yes": "✅ Есть",
        "queue": "🕐 Большая очередь",
        "low": "⚠️ Кончается",
        "no": "❌ Нет",
    }[status]

    extras = []
    if price is not None:
        extras.append(f"💰 Цена: {price:.2f}₽")
    if has_limit and limit_liters:
        extras.append(f"🚫 Лимит: {limit_liters}л")
    if canister_ban:
        extras.append("❌ Канистры запрещены")
    if queue_size and status != "queue":
        extras.append(f"🚗 Очередь: {queue_size} машин")

    extras_text = ("\n" + " · ".join(extras)) if extras else ""
    await callback.message.answer(
        f"✅ <b>Спасибо! Отчёт записан.</b>\n\n"
        f"АЗС #{station_id}, АИ-{fuel}: {status_text}{extras_text}\n\n"
        f"Твой отчёт увидят другие водители.{celebration}",
    )
    await state.clear()
    await callback.answer()


async def report_price_callback(callback: CallbackQuery, state: FSMContext = None):
    """Обработка кнопки 'Только цена' — спрашиваем цену и сохраняем сразу."""
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    if state is not None:
        await state.set_state(ReportExtrasStates.waiting_price)
        await state.update_data(
            report_station_id=station_id,
            report_fuel=fuel,
            report_status="yes",
            report_price_only=True,
        )
        await callback.message.answer(
            f"💰 <b>Введи цену за литр АИ-{fuel}:</b>\n"
            f"Например: <code>55.40</code>\n\n"
            f"Или /cancel для отмены.",
        )
    await callback.answer()


async def handle_report_extras_input(message: Message, state: FSMContext):
    """Обрабатывает текстовый ввод цены/лимита/очереди."""
    if state is None:
        return
    current = await state.get_state()
    text = (message.text or "").strip()

    if current == ReportExtrasStates.waiting_price.state:
        try:
            price = float(text.replace(",", ".").replace("₽", "").replace("р", "").strip())
            if price < 0 or price > 500:
                raise ValueError
        except ValueError:
            await message.answer("❌ Неверная цена. Введи число от 0 до 500, например <code>55.40</code>")
            return
        await state.update_data(report_price=price)
        data = await state.get_data()
        station_id = data.get("report_station_id")
        fuel = data.get("report_fuel")
        status = data.get("report_status", "yes")
        if data.get("report_price_only"):
            # Сразу сохраняем
            uid = await _ensure_message_user(message)
            await add_report(
                station_id=station_id,
                user_id=uid,
                fuel_type=fuel,
                available=True,
                price=price,
                source="user",
            )
            await message.answer(
                f"✅ Цена {price:.2f}₽ записана для АЗС #{station_id}, АИ-{fuel}.",
            )
            await state.clear()
        else:
            await state.set_state(None)
            await message.answer(
                f"✅ Цена: {price:.2f}₽\n\n"
                f"Что ещё добавить?",
                reply_markup=report_extras_keyboard(station_id, fuel, status),
            )
    elif current == ReportExtrasStates.waiting_limit.state:
        try:
            limit = int(text.replace("л", "").strip())
            if limit < 1 or limit > 1000:
                raise ValueError
        except ValueError:
            await message.answer("❌ Неверный лимит. Введи число от 1 до 1000, например <code>30</code>")
            return
        await state.update_data(report_limit=limit)
        data = await state.get_data()
        station_id = data.get("report_station_id")
        fuel = data.get("report_fuel")
        status = data.get("report_status", "yes")
        await state.set_state(None)
        await message.answer(
            f"✅ Лимит: {limit}л\n\n"
            f"Что ещё добавить?",
            reply_markup=report_extras_keyboard(station_id, fuel, status),
        )
    elif current == ReportExtrasStates.waiting_queue.state:
        try:
            queue = int(text.replace("машин", "").strip())
            if queue < 1 or queue > 50:
                raise ValueError
        except ValueError:
            await message.answer("❌ Неверное число. Введи от 1 до 50, например <code>3</code>")
            return
        await state.update_data(report_queue=queue)
        data = await state.get_data()
        station_id = data.get("report_station_id")
        fuel = data.get("report_fuel")
        status = data.get("report_status", "yes")
        await state.set_state(None)
        await message.answer(
            f"✅ Очередь: {queue} машин\n\n"
            f"Что ещё добавить?",
            reply_markup=report_extras_keyboard(station_id, fuel, status),
        )
    await callback.answer()


# === Report flow: поиск АЗС по адресу ===
async def report_address_start(callback: CallbackQuery, state: FSMContext):
    """Начало поиска АЗС по адресу."""
    await callback.answer()
    await state.set_state(ReportAddressStates.waiting_query)
    await callback.message.answer(
        "🔍 <b>Напиши название АЗС и улицу</b>\n\n"
        "Например:\n"
        "• <code>Лукойл Мира</code>\n"
        "• <code>Газпром Ленина 42</code>\n"
        "• <code>Роснефть Советская</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data="menu:report")]
        ]),
    )


async def report_address_search(message: Message, state: FSMContext):
    """Поиск АЗС по введённому запросу."""
    query = message.text.strip()
    if len(query) < 3:
        await message.answer("⚠️ Введи минимум 3 символа")
        return

    stations = await find_stations_by_address(query, limit=10)
    await state.clear()

    if not stations:
        await message.answer(
            f"😔 АЗС по запросу «{query}» не найдены.\n"
            "Попробуй другой запрос или вернись к выбору города.",
            reply_markup=report_city_keyboard(),
        )
        return

    await message.answer(
        f"🔍 <b>Найдено {len(stations)} АЗС:</b>",
        reply_markup=report_address_results_keyboard(stations),
    )


# === Review flow: выбор типа топлива для отзыва ===
async def review_start(callback: CallbackQuery):
    """Начало отзыва — выбор типа топлива."""
    station_id = int(callback.data.split(":")[1])
    await callback.answer()
    await callback.message.answer(
        "⭐ <b>Оцени качество бензина</b>\n\n"
        "Выбери тип топлива:",
        reply_markup=review_fuel_keyboard(station_id),
    )


async def review_pick_fuel(callback: CallbackQuery):
    """Выбран тип топлива — показать рейтинг."""
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    await callback.answer()
    await callback.message.answer(
        f"⛽ <b>АИ-{fuel if fuel != 'diesel' else 'ДТ'}</b> — оцени качество:",
        reply_markup=review_rating_keyboard(station_id, fuel),
    )


async def review_submit(callback: CallbackQuery):
    """Отправка отзыва с рейтингом."""
    parts = callback.data.split(":")
    station_id = int(parts[1])
    fuel = parts[2]
    rating = int(parts[3])

    uid = await _ensure_callback_user(callback)

    if not uid:
        await callback.answer("Ошибка. Попробуй /start", show_alert=True)
        return

    await add_review(
        station_id=station_id,
        user_id=uid,
        fuel_type=fuel,
        rating=rating,
    )

    stars = "⭐" * rating if rating > 0 else "Без звёзд"
    fuel_label = f"АИ-{fuel}" if fuel != "diesel" else "Дизель"

    await callback.message.answer(
        f"✅ <b>Отзыв принят!</b>\n\n"
        f"АЗС #{station_id}, {fuel_label}\n"
        f"Рейтинг: {stars}\n\n"
        f"Спасибо за оценку! Это помогает другим водителям.",
    )
    await callback.answer()


# === Подписки ===
async def subscribe_radius(callback: CallbackQuery, state: FSMContext):
    radius = int(callback.data.split(":")[1])
    data = await state.get_data()
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        await callback.answer("Сначала отправь геолокацию", show_alert=True)
        return

    uid = await _ensure_callback_user(callback)
    if not uid:
        await callback.answer("Ошибка. Нажми /start", show_alert=True)
        return

    sub_id = await add_subscription(
        user_id=uid,
        lat=lat,
        lon=lon,
        radius_km=radius,
    )

    await state.clear()
    await callback.message.answer(
        f"🔔 <b>Подписка оформлена.</b>\n\n"
        f"Радиус: {radius} км\n"
        f"Координаты: {lat:.4f}, {lon:.4f}\n\n"
        f"Пришлю уведомление, как только кто-то сообщит о наличии топлива рядом.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# === Mini App data ===
async def handle_web_app_data(message: Message):
    raw = message.web_app_data.data if isinstance(message.web_app_data, WebAppData) else ""
    if not raw:
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid web_app_data: %s", raw[:200])
        return

    data_type = data.get("type")
    if data_type == "report":
        station_id = data.get("station_id")
        fuel_type = str(data.get("fuel_type", ""))
        available_raw = data.get("available")
        if available_raw is None:
            available = None
        elif isinstance(available_raw, bool):
            available = available_raw
        elif available_raw == "queue":
            # "queue" из Mini App → есть, но с очередью
            available = True
        else:
            try:
                available = bool(int(available_raw))
            except (ValueError, TypeError):
                available = True

        if not station_id or fuel_type not in ("92", "95", "98", "diesel", "100", "lpg", "all"):
            await message.answer("⚠️ Не удалось обработать отчёт. Попробуй ещё раз.")
            return

        # Доп. поля из Mini App
        price = data.get("price")
        queue_size = data.get("queue_size")
        if queue_size is not None:
            try:
                queue_size = int(queue_size)
            except (ValueError, TypeError):
                queue_size = 5 if available_raw == "queue" else None
        elif available_raw == "queue":
            queue_size = 5
        has_limit = bool(data.get("has_limit", False))
        limit_liters = data.get("limit_liters")
        if limit_liters is not None:
            try:
                limit_liters = int(limit_liters)
            except (ValueError, TypeError):
                limit_liters = None
        limit_per_visit = data.get("limit_per_visit")
        if limit_per_visit is not None:
            try:
                limit_per_visit = int(limit_per_visit)
            except (ValueError, TypeError):
                limit_per_visit = None
        limit_daily = data.get("limit_daily")
        if limit_daily is not None:
            try:
                limit_daily = int(limit_daily)
            except (ValueError, TypeError):
                limit_daily = None
        limit_weekly = data.get("limit_weekly")
        if limit_weekly is not None:
            try:
                limit_weekly = int(limit_weekly)
            except (ValueError, TypeError):
                limit_weekly = None
        canister_ban = bool(data.get("canister_ban", False))
        comment = data.get("comment")

        uid = await get_or_create_user(message)
        try:
            price_f = float(price) if price is not None else None
        except (ValueError, TypeError):
            price_f = None

        await add_report(
            station_id=int(station_id),
            user_id=uid,
            fuel_type=fuel_type,
            available=available,
            queue_size=queue_size,
            price=price_f,
            has_limit=has_limit,
            limit_liters=limit_liters,
            limit_per_visit=limit_per_visit,
            limit_daily=limit_daily,
            limit_weekly=limit_weekly,
            canister_ban=canister_ban,
            comment=str(comment)[:500] if comment else None,
            source="miniapp",
        )
        celebration = await _check_and_celebrate_badges(uid)
        fuel_label = "АИ-" + fuel_type if fuel_type not in ("diesel", "lpg") else ("Дизель" if fuel_type == "diesel" else "Газ")
        await message.answer(
            f"✅ <b>Спасибо! Отчёт с карты записан.</b>\n\n"
            f"АЗС #{station_id}, {fuel_label}{celebration}",
        )
    elif data_type == "review":
        station_id = data.get("station_id")
        fuel_type = str(data.get("fuel_type", "92"))
        rating = data.get("rating")
        comment = data.get("comment")
        if not station_id or rating is None or rating < 0 or rating > 5:
            await message.answer("⚠️ Не удалось обработать отзыв.")
            return
        uid = await get_or_create_user(message)
        await add_review(
            station_id=int(station_id),
            user_id=uid,
            fuel_type=fuel_type,
            rating=int(rating),
            comment=str(comment)[:1000] if comment else None,
        )
        celebration = await _check_and_celebrate_badges(uid)
        fuel_label = "АИ-" + fuel_type if fuel_type not in ("diesel", "lpg") else ("Дизель" if fuel_type == "diesel" else "Газ")
        await message.answer(
            f"✅ <b>Спасибо за отзыв!</b>\n\n"
            f"АЗС #{station_id}, {fuel_label}: {'⭐' * int(rating)}{celebration}",
        )
    else:
        logger.info("Unknown web_app_data type: %s", data_type)


# === Back / cancel ===
async def handle_cancel(callback: CallbackQuery):
    await callback.message.answer("Ок, отменил.", reply_markup=main_menu_keyboard())
    await callback.answer()


async def handle_back_to_list(callback: CallbackQuery):
    await callback.message.answer(
        "🔍 Нажми «🔍 Найти АЗС» или напиши город.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


# === 🏠 "В начало" ===
async def go_home_callback(callback: CallbackQuery, state: FSMContext = None):
    telegram_id = callback.from_user.id if callback.from_user else 0
    _waiting_owner_search.discard(telegram_id)
    _waiting_owner_role.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)
    _owner_state.pop(telegram_id, None)
    if state is not None:
        await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выбери действие на клавиатуре внизу 👇",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


async def go_home_text(message: Message, state: FSMContext = None):
    telegram_id = _tg_id(message)
    _waiting_owner_search.discard(telegram_id)
    _waiting_owner_role.pop(telegram_id, None)
    _waiting_inn_nosm.discard(telegram_id)
    _owner_state.pop(telegram_id, None)
    if state is not None:
        await state.clear()
    await message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выбери действие на клавиатуре внизу 👇",
        reply_markup=main_menu_keyboard(),
    )


# === Подписка на конкретную АЗС ===
async def subscribe_station(callback: CallbackQuery):
    station_id = int(callback.data.split(":")[1])
    uid = await _ensure_callback_user(callback)
    if not uid:
        await callback.answer("Ошибка. Нажми /start", show_alert=True)
        return

    await add_subscription(user_id=uid, station_id=station_id, radius_km=0)
    await callback.answer("🔔 Подписался. Сообщу, как только появятся отчёты.", show_alert=True)


# === Открыть приложение ===
async def cmd_open_app(message: Message):
    """Показывает кнопку для открытия Telegram Web App."""
    web_app_url = settings.WEB_APP_URL or "https://benzin-ryadom.onrender.com/miniapp"
    if not web_app_url:
        await message.answer(
            "📱 <b>Приложение «Бензин рядом»</b>\n\n"
            "Скоро будет доступно! Следи за обновлениями.",
            reply_markup=main_menu_keyboard(),
        )
        return
    await message.answer(
        "📱 <b>Приложение «Бензин рядом»</b>\n\n"
        "Открой приложение для удобного поиска АЗС с картой и фильтрами.",
        reply_markup=web_app_keyboard(web_app_url),
    )


# === Поддержать разработку ===
async def cmd_donate(message: Message):
    """Показывает варианты поддержки проекта."""
    await message.answer(
        "❤️ <b>Поддержать «Бензин рядом»</b>\n\n"
        "Проект бесплатный и работает на энтузиазме. "
        "Твоя поддержка помогает развивать сервис!\n\n"
        "Выбери сумму:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ 50", callback_data="donate:50"),
             InlineKeyboardButton(text="⭐ 100", callback_data="donate:100"),
             InlineKeyboardButton(text="⭐ 250", callback_data="donate:250")],
            [InlineKeyboardButton(text="⭐ 500", callback_data="donate:500")],
            [InlineKeyboardButton(text="🏠 В начало", callback_data="go_home")],
        ]),
    )


async def donate_callback(callback: CallbackQuery):
    """Обработка выбора суммы донейта — отправляет invoice."""
    if not await _require_subscription_callback(callback):
        try:
            await callback.answer()
        except Exception:
            pass
        return
    try:
        amount = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка", show_alert=True)
        return

    await callback.message.answer_invoice(
        title="Поддержка «Бензин рядом»",
        description=f"Донейт на развитие проекта — {amount} ⭐",
        payload=f"donate:{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Донейт", amount=amount)],
    )
    await callback.answer()


# === Баг-репорт ===
async def cmd_bug_report(message: Message, state: FSMContext | None = None):
    """Отправка баг-репорта."""
    if state:
        await state.set_state(BugReportStates.waiting_description)
    await message.answer(
        "🐛 <b>Сообщи о ошибке</b>\n\n"
        "Опиши что пошло не так. Чем подробнее — тем быстрее исправим.\n\n"
        "📸 Можно прикрепить скриншот.",
        reply_markup=bug_report_keyboard(),
    )


# === Предложение ===
async def cmd_idea(message: Message, state: FSMContext | None = None):
    """Отправка предложения по доработке."""
    if state:
        await state.set_state(IdeaStates.waiting_idea)
    await message.answer(
        "💡 <b>Предложение по доработке</b>\n\n"
        "Напиши что бы ты хотел видеть в боте. Мы всё читаем!",
        reply_markup=idea_keyboard(),
    )


# === Регистрация ===
def register_all_handlers(dp: Dispatcher):
    # Callback «Я подписался»
    dp.callback_query.register(_on_check_subscribe, F.data == "check_subscribe")

    dp.message.register(cmd_open_app, Command("app"))
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_find, Command("find"))
    dp.message.register(cmd_subscribe, Command("subscribe"))
    dp.message.register(cmd_register_owner, Command("register_owner"))
    dp.message.register(cmd_my_stations, Command("my_stations"))
    dp.message.register(cmd_profile, Command("profile"))
    dp.message.register(cmd_stats, Command("stats"))
    dp.message.register(cmd_moderate, Command("moderate"))
    dp.message.register(cmd_set_ad, Command("set_ad"))
    dp.message.register(cmd_promote, Command("promote"))
    dp.message.register(cmd_my_id, Command("my_id"))
    dp.message.register(cmd_find_raw, Command("find_raw"))

    # FSM: подписки
    dp.message.register(handle_location, F.location, StateFilter(SubscribeStates.waiting_geo))
    dp.callback_query.register(subscribe_radius, F.data.startswith("sub_radius:"), StateFilter(SubscribeStates.waiting_radius))

    # Non-FSM owner flow
    dp.message.register(owner_inn_input_nosm, _OwnerWaitingInnFilter())
    dp.message.register(owner_search_input, _OwnerWaitingSearchFilter())
    dp.callback_query.register(owner_pick_search, F.data.startswith("owner_pick_search:"))
    dp.callback_query.register(owner_role_picked, F.data.startswith("owner_role:"))
    dp.callback_query.register(owner_inn_skip_nosm, F.data == "owner_inn_nosm:skip")
    dp.callback_query.register(owner_search_cancel, F.data == "owner_search_cancel")

    # Геолокация
    dp.message.register(handle_location, F.location)

    # Парсинг сообщений от ботов-конкурентов
    dp.message.register(handle_bot_message, F.from_user.is_bot)
    dp.message.register(handle_forwarded_bot_message, F.forward_origin)

    # Mini App data
    dp.message.register(handle_web_app_data, F.web_app_data)

    # Текстовые кнопки главного меню (catch-all — ПОСЛЕ FSM!)
    dp.message.register(report_address_search, ReportAddressStates.waiting_query, F.text)
    dp.message.register(handle_report_extras_input, ReportExtrasStates.waiting_price, F.text)
    dp.message.register(handle_report_extras_input, ReportExtrasStates.waiting_limit, F.text)
    dp.message.register(handle_report_extras_input, ReportExtrasStates.waiting_queue, F.text)
    dp.message.register(handle_route_query, RouteSearchStates.waiting_route_query, F.text)

    # Callback (кнопки)
    dp.callback_query.register(show_station_details, F.data.startswith("st:"))
    # Route to station (existing — uses lat:lon:station_id format)
    dp.callback_query.register(route_callback, F.data.regexp(r"^route:\d+:"))
    # Route new search
    dp.callback_query.register(route_new_callback, F.data == "route:new")
    # Route more (other routes from search)
    dp.callback_query.register(route_more_callback, F.data.startswith("route_more:"))
    # Route pick (select specific route)
    dp.callback_query.register(route_pick_callback, F.data.startswith("route_pick:"))
    # Promote
    dp.callback_query.register(promote_callback, F.data.startswith("promote:"))
    # Report flow
    dp.callback_query.register(report_city_callback, F.data.startswith("report_city:"))
    dp.callback_query.register(report_pick_callback, F.data.startswith("report_pick:"))
    dp.callback_query.register(report_start, F.data.regexp(r"^report:\d+$"))
    dp.callback_query.register(report_fuel, F.data.startswith("report_fuel:"))
    dp.callback_query.register(report_submit, F.data.startswith("report_status:"))
    dp.callback_query.register(report_extra_callback, F.data.startswith("report_extra:"))
    dp.callback_query.register(report_save_with_extras, F.data.startswith("report_save:"))
    dp.callback_query.register(report_price_callback, F.data.startswith("report_price:"))
    dp.callback_query.register(subscribe_station, F.data.startswith("sub_station:"))
    dp.callback_query.register(handle_cancel, F.data == "cancel")
    dp.callback_query.register(handle_back_to_list, F.data == "back_to_list")

    # Report flow: поиск АЗС по адресу
    dp.callback_query.register(report_address_start, F.data == "report_address:start")

    # Review flow: отзывы о качестве бензина
    dp.callback_query.register(review_start, F.data.startswith("review_start:"))
    dp.callback_query.register(review_pick_fuel, F.data.startswith("review_fuel:"))
    dp.callback_query.register(review_submit, F.data.startswith("review:"))

    # Owner-режим
    dp.callback_query.register(owner_quick_set, F.data.startswith("oset:"))
    dp.callback_query.register(show_my_station, F.data.startswith("mystation:"))
    dp.callback_query.register(my_stations_back, F.data == "my_stations_back")

    # Модерация
    dp.callback_query.register(approve_owner, F.data.startswith("approve:"))

    # Глобальная кнопка «В начало»
    dp.callback_query.register(go_home_callback, F.data == "go_home")

    # Фаза 2 callbacks
    dp.callback_query.register(go_register_owner_callback, F.data == "go_register_owner")
    dp.callback_query.register(profile_callback, F.data == "cmd_profile")
    dp.callback_query.register(help_callback, F.data == "cmd_help")
    dp.callback_query.register(menu_callback, F.data.startswith("menu:"))

    # Donate
    dp.callback_query.register(donate_callback, F.data.startswith("donate:"))

    # Premium
    dp.message.register(cmd_premium, Command("premium"))
    dp.message.register(cmd_link, Command("link"))
    dp.message.register(cmd_alarm, Command("alarm"))
    dp.message.register(cmd_referral, Command("referral"))
    dp.message.register(cmd_broadcast, Command("broadcast"))
    dp.message.register(cmd_freetrial, Command("freetrial"))
    dp.callback_query.register(link_create_callback, F.data == "link:create")
    dp.callback_query.register(link_enter_callback, F.data == "link:enter")
    dp.message.register(link_code_input_handler, StateFilter(LinkStates.waiting_code))

    # IMPORTANT: handle_main_button должен быть ПОСЛЕДНИМ —
    # он перехватывает все текстовые сообщения.
    # Все state-specific handlers (link_code_input и т.д.) должны быть зарегистрированы раньше.
    dp.message.register(handle_main_button, F.text)
    dp.callback_query.register(buy_tier_callback, F.data.in_({"buy_economy", "buy_standard", "buy_elite"}))
    dp.callback_query.register(check_payment_callback, F.data.startswith("check_pay_"))
    dp.callback_query.register(buy_premium_callback, F.data == "buy_premium")
    dp.callback_query.register(premium_callback, F.data == "cmd_premium")
    dp.callback_query.register(premium_trial_callback, F.data == "premium_trial")
    dp.pre_checkout_query.register(pre_checkout_handler)
    dp.message.register(successful_payment_handler, F.successful_payment)

    # Inline mode
    dp.inline_query.register(inline_search)

    # Аналитика владельца
    dp.callback_query.register(station_analytics_callback, F.data.startswith("analy:"))

    # Фильтры по городу
    dp.callback_query.register(city_callback, F.data.startswith("city:"))
    dp.callback_query.register(fuel_callback, F.data.startswith("fuel:"))
    dp.callback_query.register(all_stations_callback, F.data.startswith("all:"))
    dp.callback_query.register(price_menu_callback, F.data.startswith("price_menu:"))
    dp.callback_query.register(price_callback, F.data.startswith("price:"))
    dp.callback_query.register(net_menu_callback, F.data.startswith("net_menu:"))
    dp.callback_query.register(net_callback, F.data.startswith("net:"))
    dp.callback_query.register(emergency_city_callback, F.data.startswith("emergency:"))


# === Аналитика владельца ===
async def cmd_analytics(message: Message):
    await get_or_create_user(message)
    telegram_id = _tg_id(message)
    uid = await get_user_id_by_telegram_id(telegram_id)
    if not uid:
        await message.answer("Сначала нажми /start")
        return
    stations = await get_owner_stations(uid)
    if not stations:
        await message.answer(
            "У тебя нет зарегистрированных АЗС.\n"
            "Нажми /register_owner, чтобы добавить и увидеть аналитику.",
        )
        return

    from db import get_station_analytics
    total_views = 0
    total_reports = 0
    total_subs = 0
    for s in stations:
        sid = s.get("station_id") or s.get("id")
        a = await get_station_analytics(sid, days=30)
        total_views += a.get("views", 0)
        total_reports += a.get("reports_30d", 0)
        total_subs += a.get("subscribers", 0)

    text = (
        f"📊 <b>Аналитика за 30 дней:</b>\n\n"
        f"👁 Просмотры: <b>{total_views}</b>\n"
        f"📝 Отчёты (все): <b>{total_reports}</b>\n"
        f"🔔 Подписчики: <b>{total_subs}</b>\n\n"
    )
    if total_views == 0 and total_reports == 0:
        text += "💡 <i>Данные появятся когда водители начнут открывать карточки и оставлять отчёты.</i>\n\n"

    text += "<b>По АЗС:</b>\n"
    for s in stations[:10]:
        sid = s.get("station_id") or s.get("id")
        a = await get_station_analytics(sid, days=30)
        text += (
            f"\n{ '✅' if s.get('is_verified') else '⏳' } <b>{s.get('name', 'АЗС')[:30]}</b>\n"
            f"   👁 {a.get('views', 0)} · 📝 {a.get('reports_30d', 0)} · 🔔 {a.get('subscribers', 0)}"
        )
        if a.get("avg_price"):
            text += f" · 💰 {a.get('avg_price'):.2f}₽"

    kb_rows = []
    for s in stations[:5]:
        sid = s.get("station_id") or s.get("id")
        kb_rows.append([InlineKeyboardButton(
            text=f"📊 {s.get('name', 'АЗС')[:25]}", callback_data=f"analy:{sid}",
        )])
    await message.answer(text, reply_markup=with_home_inline(InlineKeyboardMarkup(inline_keyboard=kb_rows)))


async def station_analytics_callback(callback: CallbackQuery):
    await callback.answer()
    try:
        station_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        return
    from db import get_station_analytics
    a = await get_station_analytics(station_id, days=30)
    text = (
        f"📊 <b>Аналитика АЗС #{station_id} · 30 дней:</b>\n\n"
        f"👁 Просмотры: <b>{a.get('views', 0)}</b>\n"
        f"📝 Отчёты: <b>{a.get('reports_30d', 0)}</b>\n"
        f"🔔 Подписчики: <b>{a.get('subscribers', 0)}</b>\n"
    )
    if a.get("avg_price"):
        text += f"💰 Средняя цена: <b>{a.get('avg_price'):.2f}₽</b>\n"
    if a.get("last_report_at"):
        text += f"⏰ Последний отчёт: {str(a.get('last_report_at'))[:16]}\n"

    fuels = a.get("reports_by_fuel", {})
    if fuels:
        text += "\n<b>По топливу:</b>\n"
        for fuel, data in fuels.items():
            line = f"  ⛽ АИ-{fuel}: {data['count']} отчётов"
            if data.get("avg_price"):
                line += f", ~{data['avg_price']:.2f}₽"
            text += line + "\n"

    chart = a.get("views_chart", [])[-7:]
    if chart:
        max_v = max((c["count"] for c in chart), default=1) or 1
        text += "\n<b>Просмотры по дням:</b>\n"
        for c in chart:
            bar = "█" * int(c["count"] / max_v * 10) if max_v > 0 else ""
            text += f"  {c['date']}: {bar} {c['count']}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    await callback.message.answer(text, reply_markup=with_home_inline(kb))


async def _check_and_celebrate_badges(uid):
    """Проверяет и поздравляет с новыми бейджами."""
    try:
        from db import check_and_award_badges
        new_badges = await check_and_award_badges(uid)
        if new_badges:
            text = "\n\n🏆 <b>Новые бейджи!</b>\n"
            for b in new_badges:
                text += f"  {b['emoji']} <b>{b['name']}</b>\n"
            return text
    except Exception:
        pass
    return ""


# === /broadcast — рассылка по всем юзерам (admin) ===
ADMIN_TG_IDS = [772577887]  # darkt30


async def cmd_broadcast(message: Message):
    """Рассылает сообщение всем пользователям бота. Только для admin."""
    if message.from_user.id not in ADMIN_TG_IDS:
        return
    # Текст рассылки — аргумент после /broadcast
    text = (message.text or "").replace("/broadcast", "", 1).strip()
    if not text:
        await message.answer(
            "📢 <b>Рассылка</b>\n\n"
            "Использование: <code>/broadcast текст сообщения</code>\n\n"
            "Пример:\n"
            "<code>/broadcast 🎉 Premium запущен! 3 дня бесплатно → /premium</code>"
        )
        return
    # Получаем всех юзеров из БД
    import db as _db
    users = await _db._fetch(
        "SELECT telegram_id FROM users WHERE telegram_id > 0",
    )
    sent = 0
    failed = 0
    await message.answer(f"📢 Начинаю рассылку для {len(users)} юзеров...")
    for row in users:
        tid = row["telegram_id"] if isinstance(row, dict) else row[0]
        try:
            await message.bot.send_message(chat_id=tid, text=text)
            sent += 1
        except Exception:
            failed += 1
        # Rate limit
        import asyncio as _aio
        await _aio.sleep(0.05)
    await message.answer(f"✅ Рассылка завершена: {sent} отправлено, {failed} ошибок")


# === /freetrial — выдать 3 дня Premium (admin) ===
async def cmd_freetrial(message: Message):
    """Выдаёт 3 дня Premium указанному юзеру. Только для admin."""
    if message.from_user.id not in ADMIN_TG_IDS:
        return
    text = (message.text or "").replace("/freetrial", "", 1).strip()
    if not text:
        await message.answer("Использование: <code>/freetrial TG_ID</code>")
        return
    try:
        tid = int(text.split()[0])
    except (ValueError, IndexError):
        await message.answer("❌ Неверный формат. Пример: <code>/freetrial 772577887</code>")
        return
    import db as _db
    uid = await _db.get_user_id_by_telegram_id(tid)
    if not uid:
        await message.answer(f"❌ Юзер {tid} не найден")
        return
    sub = await _db.activate_premium(uid, "standard", days=3, payment_id=f"admin_trial_{tid}")
    await message.answer(f"✅ Trial выдан: {tid} → standard на 3 дня (до {sub.get('expires_at', '')[:10]})")
