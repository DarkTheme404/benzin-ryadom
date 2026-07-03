"""
VK Callback API — обработка событий от сообщества через webhook.

Альтернатива Long Poll. Более надёжная, поддерживает retry.

Эндпоинт: /api/vk/callback (POST)

События:
  - confirmation: вернуть токен подтверждения
  - message_new: новое сообщение от пользователя
  - message_event: нажатие inline-кнопки (callback)

Все ответы отправляются через VK API напрямую.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp

from db import (
    find_nearest_stations,
    find_stations_by_address,
    find_stations_by_city,
    find_stations_by_name,
    get_or_create_user,
    get_premium_info,
    get_station_by_id,
    get_station_current_status,
    get_station_rating,
    get_user_id_by_telegram_id,
    is_premium,
    log_event,
    get_user_stats_summary,
)
from vk_keyboards import (
    VK_BTN_HOME,
    vk_main_menu,
    vk_city_keyboard,
    vk_station_actions,
    vk_subscribe_geo_keyboard,
    vk_review_rating_keyboard,
    vk_premium_keyboard,
    vk_donate_keyboard,
    _button,
    _link_button,
    vk_keyboard,
)

logger = logging.getLogger("vk_callback")

# === Состояние пользователей (peer_id → state) ===
_user_state: dict[int, dict] = {}
_vk_subscribe_cache: dict[int, tuple[bool, float]] = {}
_VK_SUBSCRIBE_TTL = 300  # 5 минут

VK_API_VERSION = "5.199"


async def _vk_api_call(method: str, params: dict) -> dict:
    """Вызов метода VK API через aiohttp."""
    token = os.getenv("VK_TOKEN", "")
    if not token:
        return {"error": "no token"}
    params["access_token"] = token
    params["v"] = VK_API_VERSION
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://api.vk.com/method/{method}",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if "error" in data:
                    logger.warning("VK API %s error: %s", method, data["error"])
                return data
    except Exception as e:
        logger.warning("VK API %s failed: %s", method, e)
        return {"error": str(e)}


async def _vk_send(peer_id: int, text: str, keyboard: str | None = None) -> dict:
    """Отправляет сообщение пользователю."""
    params = {
        "peer_id": peer_id,
        "message": text,
        "random_id": int(time.time() * 1000) % (2**31),
    }
    if keyboard:
        params["keyboard"] = keyboard
    return await _vk_api_call("messages.send", params)


# === Проверка подписки ===
async def _check_vk_subscription(user_id: int) -> bool:
    from config import settings
    now = time.time()
    cached = _vk_subscribe_cache.get(user_id)
    if cached and now - cached[1] < _VK_SUBSCRIBE_TTL:
        return cached[0]
    group_id = settings.SUBSCRIBE_COMMUNITY_VK
    if not group_id:
        return True
    try:
        data = await _vk_api_call("groups.isMember", {
            "group_id": group_id, "user_id": user_id,
        })
        is_sub = bool(data.get("response", 0))
    except Exception as e:
        logger.warning("subscription check failed: %s", e)
        is_sub = False
    _vk_subscribe_cache[user_id] = (is_sub, now)
    return is_sub


def _vk_subscribe_keyboard() -> str:
    return vk_keyboard([
        [_link_button("📢 Подписаться", "https://vk.com/benzyn_ryadom")],
        [_button("✅ Я подписался", "positive")],
    ])


# === Helpers ===
async def _get_user(peer_id: int) -> int | None:
    """Получает внутренний user_id из peer_id (используем как telegram_id)."""
    return await get_user_id_by_telegram_id(peer_id)


# === Handlers ===
async def handle_start(peer_id: int) -> None:
    uid = await _get_user(peer_id)
    if uid:
        await log_event(uid, "vk_start")
    text = (
        "👋 Привет! Я — Бензин рядом.\n\n"
        "Помогу найти бензин за 5 секунд. 26 000+ АЗС в России.\n\n"
        "🟢 live · цены · очереди · push о завозе\n\n"
        "❤️ Если бот помог — поддержи проект:\n"
        "☕ 50₽ · ⛽ 100₽ · 🔧 250₽ · 💎 500₽ · 👑 Шейх 10 000₽\n"
        "👉 vk.com/donut/benzyn_ryadom\n\n"
        "👇 <b>Главное меню:</b>"
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_help(peer_id: int) -> None:
    text = (
        "ℹ️ <b>Команды</b>\n\n"
        "🔍 <b>Найти АЗС</b> — нажми кнопку или напиши город\n"
        "📝 <b>Сообщить</b> — отметь наличие топлива\n"
        "🔔 <b>Подписки</b> — push о завозе рядом\n"
        "👤 <b>Профиль</b> — репутация, бейджи\n"
        "🏪 <b>Владелец</b> — verified-бейдж\n"
        "💎 <b>Premium</b> — push без задержек\n\n"
        "💡 Напиши название АЗС, город или сеть — я покажу результат."
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_find(peer_id: int) -> None:
    await _vk_send(
        peer_id,
        "📍 <b>Выбери населённый пункт</b>\n\n"
        "Иваново, Москва, СПб, и другие. "
        "Или напиши свой город в сообщении — бот найдёт АЗС.",
        vk_city_keyboard(),
    )


async def handle_subscribe(peer_id: int) -> None:
    _user_state[peer_id] = {"awaiting": "subscribe_geo"}
    await _vk_send(
        peer_id,
        "🔔 <b>Подписка на уведомления о завозе.</b>\n\n"
        "Отправь геолокацию — буду присылать уведомления, когда "
        "в радиусе 5 км от тебя появится бензин.",
        vk_subscribe_geo_keyboard(),
    )


async def handle_profile(peer_id: int) -> None:
    uid = await _get_user(peer_id)
    if not uid:
        await _vk_send(peer_id, "Профиль не найден. Нажми «🏠 В начало».", vk_main_menu())
        return
    stats = await get_user_stats_summary(uid)
    if not stats:
        await _vk_send(peer_id, "Профиль не найден.", vk_main_menu())
        return
    text = (
        f"👤 <b>Твой профиль:</b>\n\n"
        f"🆔 VK ID: <code>{peer_id}</code>\n"
        f"📊 Репутация: <b>{stats.get('reputation', 0)}</b>/100\n"
        f"📝 Отчётов сделано: <b>{stats.get('total_reports', 0)}</b>\n"
        f"✅ Подтверждено: <b>{stats.get('confirmed_reports', 0)}</b>\n"
    )
    if stats.get("region") or stats.get("city"):
        loc = ", ".join(filter(None, [stats.get("city"), stats.get("region")]))
        text += f"📍 Регион: {loc}\n"
    if await is_premium(uid):
        text += "\n⭐ <b>Premium</b> — push без cooldown\n"
    badges = stats.get("badges", [])
    if badges:
        text += f"\n🏆 <b>Бейджи ({len(badges)}):</b>\n"
        for b in badges:
            text += f"  {b['emoji']} <b>{b['name']}</b> — {b['desc']}\n"
    else:
        text += "\n🎯 Сделай первый отчёт, чтобы получить бейдж 🥉 «Новичок»!"
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_donate(peer_id: int) -> None:
    text = (
        "❤️ <b>Поддержать проект</b>\n\n"
        "Бот бесплатный. Сервер, парсеры, база данных — всё стоит денег.\n"
        "Любая сумма поможет:\n\n"
        "☕ 50₽ · ⛽ 100₽ · 🔧 250₽ · 💎 500₽ · 👑 Шейх 10 000₽\n\n"
        "👉 vk.com/donut/benzyn_ryadom"
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_text_search(peer_id: int, query: str) -> None:
    """Универсальный поиск: город / сеть / название / адрес."""
    if not query or len(query) < 2:
        await _vk_send(peer_id, "Введи минимум 2 символа.", vk_main_menu())
        return
    # 1) Сначала пробуем как город
    stations = await find_stations_by_city(city=query, has_stock=False, limit=5)
    if not stations:
        # 2) Как название/сеть
        stations = await find_stations_by_name(query, limit=5)
    if not stations:
        # 3) Как адрес
        stations = await find_stations_by_address(query, limit=5)
    if not stations:
        await _vk_send(peer_id, f"😔 По «{query}» ничего не нашёл.", vk_main_menu())
        return
    # Берём первый, показываем детали
    s = stations[0]
    await show_station(peer_id, s, city_hint=query)


async def show_station(peer_id: int, station: dict, city_hint: str = "") -> None:
    """Показывает детали АЗС."""
    from utils import format_station_card
    sid = station.get("id")
    if not sid:
        return
    statuses = await get_station_current_status(sid)
    text = format_station_card(station, statuses)
    await _vk_send(peer_id, text[:4000], vk_station_actions(sid))


# === Main router ===
async def process_message_new(event: dict) -> None:
    """Обрабатывает message_new от VK Callback API."""
    msg = event.get("object", {}).get("message", {})
    if not msg:
        return
    peer_id = msg.get("peer_id", 0)
    if not peer_id or peer_id < 0:
        return  # групповые чаты игнорируем
    text = (msg.get("text") or "").strip()
    if not text and not msg.get("geo"):
        return
    logger.info("[vk-cb] peer=%d text=%r", peer_id, text[:50])

    # Проверка подписки (пропускаем /start)
    if text.lower() not in ("/start", "start"):
        is_sub = await _check_vk_subscription(peer_id)
        if not is_sub:
            await _vk_send(peer_id,
                "📢 <b>Подпишись на сообщество, чтобы пользоваться ботом!</b>\n\n"
                "Бот бесплатный. Взамен — подпишись на наше сообщество с новостями о топливе.",
                _vk_subscribe_keyboard())
            return

    # Регистрация пользователя
    if text.lower() in ("/start", "start"):
        await get_or_create_user(telegram_id=peer_id, first_name="VK", last_name="User")
        await handle_start(peer_id)
        return

    # Кнопки главного меню
    if text == "🔍 Найти АЗС":
        await handle_find(peer_id)
    elif text == "❓ Помощь" or text == "/help":
        await handle_help(peer_id)
    elif text == "🔔 Уведомления" or text == "/subscribe":
        await handle_subscribe(peer_id)
    elif text == "👤 Профиль":
        await handle_profile(peer_id)
    elif text == "👤 Я владелец":
        await _vk_send(peer_id, "🏪 <b>Регистрация владельца</b>\n\n"
            "Эта функция доступна в полной версии бота. Открой приложение 📱\n"
            "👉 или напиши 'меню' для возврата.", vk_main_menu())
    elif text == "❤️ Поддержать":
        await handle_donate(peer_id)
    elif text == "🏠 В начало" or text == "/home":
        await _vk_send(peer_id, "Главное меню:", vk_main_menu())
    elif text.startswith("📍 "):  # Выбор города
        city = text[2:].strip()
        await handle_text_search(peer_id, city)
    elif text == "✏️ Другой город":
        _user_state[peer_id] = {"awaiting": "city_input"}
        await _vk_send(peer_id, "Напиши название города:", vk_main_menu())
    elif text == "✅ Я подписался":
        _vk_subscribe_cache.pop(peer_id, None)
        await _vk_send(peer_id, "✅ Спасибо! Теперь ты подписан.", vk_main_menu())
    else:
        # Текстовый поиск
        state = _user_state.get(peer_id, {})
        if state.get("awaiting") == "city_input":
            _user_state.pop(peer_id, None)
            await handle_text_search(peer_id, text)
        else:
            await handle_text_search(peer_id, text)


async def process_message_event(event: dict) -> None:
    """Обрабатывает message_event (нажатие inline-кнопки)."""
    obj = event.get("object", {})
    peer_id = obj.get("peer_id", 0)
    payload_str = obj.get("payload", "")
    event_id = obj.get("event_id", "")
    logger.info("[vk-cb-event] peer=%d payload=%r", peer_id, payload_str[:50])
    # Подтверждаем получение события
    if event_id:
        await _vk_api_call("messages.sendMessageEventAnswer", {
            "event_id": event_id,
            "user_id": peer_id,
            "peer_id": peer_id,
        })
    # Обрабатываем payload (заглушка — основные кнопки текстовые)
