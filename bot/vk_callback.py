"""
VK Callback API — обработка событий от сообщества через webhook.

Эндпоинт: /api/vk/callback (POST)

События:
  - confirmation: вернуть токен подтверждения
  - message_new: новое сообщение от пользователя
  - message_event: нажатие inline-кнопки (callback)

Все ответы отправляются через VK API напрямую.
"""
import json
import logging
import os
import time

import aiohttp
import db
from config import settings

from db import (
    find_nearest_stations,
    find_stations_by_address,
    find_stations_by_city,
    find_stations_by_name,
    get_premium_info,
    get_station_by_id,
    get_station_current_status,
    get_station_rating,
    get_user_id_by_vk_id,
    is_premium,
    log_event,
    get_user_stats_summary,
    add_report,
    add_review,
    add_subscription,
    USE_SQLITE,
)
from vk_keyboards import (
    vk_main_menu,
    vk_city_keyboard,
    vk_fuel_filter_keyboard,
    vk_price_filter_keyboard,
    vk_network_filter_keyboard,
    vk_report_status_keyboard,
    vk_subscribe_geo_keyboard,
    vk_station_actions,
    vk_review_fuel_keyboard,
    vk_review_rating_keyboard,
    vk_premium_keyboard,
    vk_donate_keyboard,
    vk_admin_keyboard,
    _callback_button,
    _link_button,
    _vkapp_button,
    vk_keyboard,
)

logger = logging.getLogger("vk_callback")

VK_API_VERSION = "5.199"
USER_STATE_TTL = 1800  # 30 минут


def format_for_vk(text: str) -> str:
    """Преобразует HTML-разметку в VK-совместимый формат.

    VK по умолчанию НЕ парсит HTML (<b>, <i>), теги видны как текст.
    Используем VK markdown: **bold**, *italic*.

    Правила:
      <b>text</b>  → **text**
      <i>text</i>  → [text]  (italic не поддерживается стабильно, убираем)
      <br>         → \\n (новая строка)
      <code>text</code> → `text`
      прочие теги  → удаляются
    """
    import re
    if not text:
        return text
    # Сохраняем переносы строк до замены
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?p\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Bold: <b>text</b> → **text**
    text = re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=re.IGNORECASE | re.DOTALL)
    # Italic: <i>text</i> → просто текст (VK markdown *italic* нестабилен)
    text = re.sub(r'<i>(.*?)</i>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    # Underline, strikethrough — убираем теги
    text = re.sub(r'</?u\s*/?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?s\s*/?>', '', text, flags=re.IGNORECASE)
    # Code: <code>text</code> → `text`
    text = re.sub(r'<code>(.*?)</code>', r'`\1`', text, flags=re.IGNORECASE | re.DOTALL)
    # Удаляем все остальные теги
    text = re.sub(r'<[^>]+>', '', text)
    return text


# === State management с TTL ===
_user_state: dict[int, tuple[dict, float]] = {}
_vk_subscribe_cache: dict[int, tuple[bool, float]] = {}
_VK_SUBSCRIBE_TTL = 300  # 5 минут

# Event deduplication (чтобы не обработать одно и то же дважды)
_processed_events: dict[str, float] = {}
_EVENT_DEDUP_TTL = 60  # 1 минута


def _set_state(peer_id: int, state: dict) -> None:
    """Устанавливает состояние пользователя с TTL."""
    _user_state[peer_id] = (state, time.time() + USER_STATE_TTL)


def _get_state(peer_id: int) -> dict:
    """Получает состояние (None если истекло)."""
    entry = _user_state.get(peer_id)
    if not entry:
        return {}
    state, expires_at = entry
    if time.time() > expires_at:
        _user_state.pop(peer_id, None)
        return {}
    return state


def _clear_state(peer_id: int) -> None:
    _user_state.pop(peer_id, None)


def _cleanup_states() -> None:
    """Периодическая очистка истёкших state'ов."""
    now = time.time()
    expired = [pid for pid, (_, exp) in _user_state.items() if now > exp]
    for pid in expired:
        _user_state.pop(pid, None)
    if expired:
        logger.debug("Cleaned up %d expired user states", len(expired))


async def _periodic_cleanup() -> None:
    """Фоновая задача: очистка _user_state и _processed_events каждые 60 сек."""
    import asyncio
    while True:
        await asyncio.sleep(60)
        _cleanup_states()
        now = time.time()
        for k in list(_processed_events.keys()):
            if now - _processed_events[k] > _EVENT_DEDUP_TTL * 2:
                _processed_events.pop(k, None)


# === VK API wrapper ===
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
    # Конвертируем HTML в VK markdown
    text = format_for_vk(text)
    params = {
        "peer_id": peer_id,
        "message": text[:4000],  # VK лимит 4096 символов
        "random_id": int(time.time() * 1000) % (2**31),
    }
    if keyboard:
        params["keyboard"] = keyboard
    result = await _vk_api_call("messages.send", params)
    if "error" in result:
        logger.error("VK messages.send ERROR to peer=%d: %s", peer_id, result.get("error"))
    else:
        logger.info("VK messages.send OK to peer=%d (msg_id=%s)", peer_id, result.get("response"))
    return result


async def _vk_send_event_answer(event_id: str, user_id: int, peer_id: int,
                                 text: str = "", toast: str = "") -> bool:
    """Отвечает на message_event (callback) — обязательно в течение 5 сек.

    Возвращает True если успешно, False при ошибке.
    НЕ выбрасывает исключение — просто логирует.
    """
    params = {
        "event_id": event_id,
        "user_id": user_id,
        "peer_id": peer_id,
    }
    if text:
        params["text"] = text
    if toast:
        params["toast"] = toast
    result = await _vk_api_call("messages.sendMessageEventAnswer", params)
    if "error" in result:
        logger.warning("VK sendMessageEventAnswer error: %s | event_id=%r", result.get("error"), event_id)
        return False
    return True


async def _vk_edit_message(peer_id: int, conversation_message_id: int, text: str,
                            keyboard: str | None = None) -> dict:
    """Редактирует сообщение."""
    params = {
        "peer_id": peer_id,
        "conversation_message_id": conversation_message_id,
        "message": text,
    }
    if keyboard:
        params["keyboard"] = keyboard
    return await _vk_api_call("messages.edit", params)


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
        [_link_button("📢 Подписаться на сообщество", "https://vk.ru/benzyn_ryadom")],
        [_callback_button("✅ Я подписался", {"a": "check_sub"}, "positive")],
    ])


# === Helpers ===
async def _get_user_id(peer_id: int) -> int | None:
    """Получает внутренний user_id из VK peer_id."""
    from db import get_user_id_by_vk_id
    return await get_user_id_by_vk_id(peer_id)


async def _ensure_user(peer_id: int, first_name: str = "VK") -> int | None:
    """Создаёт/обновляет пользователя по VK ID, возвращает user_id."""
    from db import upsert_user_vk
    return await upsert_user_vk(peer_id, first_name=first_name)


async def _handle_chat_message(peer_id: int, text: str, msg: dict) -> None:
    """Обработка сообщений в VK чате — упрощённый бот для поиска АЗС."""
    low = text.lower().strip()

    if not text:
        return

    # Подсказка по командам
    if low in ("/help", "help", "помощь", "/menu", "menu", "начать", "/start", "start"):
        await _vk_send(peer_id,
            "⛽ <b>Бензин рядом</b> — бот для поиска АЗС\n\n"
            "Просто напиши <b>название города</b> или <b>адрес</b>:\n"
            "• Шарья\n"
            "• Кострома\n"
            "• Москва, Ленина 15\n\n"
            "Или команды:\n"
            "• <code>/find Шарья</code> — поиск АЗС в городе\n"
            "• <code>/prices Шарья</code> — цены на топливо\n"
            "• <code>/status Шарья</code> — статус наличия\n\n"
            "📱 <a href=\"https://t.me/benzyn_ryadom_bot?start=ref\">Полная версия в Telegram</a>")
        return

    # Поиск АЗС по городу или адресу
    if low.startswith("/find ") or low.startswith("найти "):
        query = text.split(maxsplit=1)[1] if " " in text else ""
        if not query:
            await _vk_send(peer_id, "Укажи город или адрес. Пример: <code>/find Шарья</code>")
            return
        await _chat_find_stations(peer_id, query)
        return

    # Цены
    if low.startswith("/prices ") or low.startswith("цены "):
        query = text.split(maxsplit=1)[1] if " " in text else ""
        if not query:
            query = ""
        await _chat_prices(peer_id, query)
        return

    # Статус наличия
    if low.startswith("/status ") or low.startswith("наличие "):
        query = text.split(maxsplit=1)[1] if " " in text else ""
        if not query:
            query = ""
        await _chat_status(peer_id, query)
        return

    # По умолчанию — ищем АЗС по тексту (название города/адреса)
    if len(text) >= 2:
        await _chat_find_stations(peer_id, text)


async def _chat_find_stations(peer_id: int, query: str) -> None:
    """Поиск АЗС для VK чата — с реальным наличием."""
    from db import find_stations_by_city, find_stations_by_address, find_stations_by_name, get_station_current_status
    try:
        stations = await find_stations_by_city(query, has_stock=False)
        if not stations:
            stations = await find_stations_by_address(query)
        if not stations:
            stations = await find_stations_by_name(query)
    except Exception as e:
        logger.error("Chat find error: %s", e)
        await _vk_send(peer_id, "❌ Ошибка поиска. Попробуй позже.")
        return

    if not stations:
        await _vk_send(peer_id,
            f"🔍 АЗС по запросу «{query}» не найдены.\n\n"
            "Попробуй:\n"
            "• Другое название города\n"
            "• Улицу или район\n"
            "• Название сети АЗС")
        return

    lines = [f"⛽ <b>Найдено АЗС: {len(stations[:10])}</b>\n"]
    for s in stations[:10]:
        name = s.get("name", "АЗС")
        addr = s.get("address", "")
        city = s.get("city", "")
        operator = s.get("operator") or s.get("brand") or ""
        sid = s.get("id")

        display = addr or city or name

        # Получаем реальный статус наличия
        status_map = {}
        price_map = {}
        if sid:
            try:
                statuses = await get_station_current_status(sid)
                for st in statuses:
                    ft = st.get("fuel_type", "")
                    avail = st.get("available")
                    price = st.get("price")
                    if avail is True:
                        status_map[ft] = "🟢"
                    elif avail is False:
                        status_map[ft] = "🔴"
                    elif avail is None:
                        status_map[ft] = "🟡"
                    if price:
                        price_map[ft] = price
            except Exception:
                pass

        net_str = f" ({operator})" if operator else ""

        # Формируем строку топлива с реальным статусом
        if status_map:
            fuel_parts = []
            for ft in ["92", "95", "98", "100", "diesel", "lpg", "all"]:
                if ft in status_map:
                    emoji = status_map[ft]
                    label = "АИ-" + ft if ft in ("92", "95", "98", "100") else "Дизель" if ft == "diesel" else "Газ" if ft == "lpg" else ft.upper()
                    price_str = f" — {price_map[ft]:.2f}₽" if ft in price_map else ""
                    fuel_parts.append(f"{emoji} {label}{price_str}")
            fuel_str = "\n    " + "\n    ".join(fuel_parts) if fuel_parts else ""
        else:
            fuel_str = ""

        lines.append(f"⚪ <b>{name}</b>{net_str}")
        if display:
            lines.append(f"    📍 {display}")
        if fuel_str:
            lines.append(fuel_str)

    if len(stations) > 10:
        lines.append(f"\n... и ещё {len(stations) - 10} АЗС")

    lines.append(f"\n📱 <a href=\"https://t.me/benzyn_ryadom_bot?start=ref\">Полная карта в Telegram</a>")
    await _vk_send(peer_id, "\n".join(lines))


async def _chat_prices(peer_id: int, city: str) -> None:
    """Показать цены на топливо для VK чата — из реальных отчётов."""
    from db import find_stations_by_city, get_station_current_status
    try:
        stations = await find_stations_by_city(city or "Шарья", has_stock=False)
    except Exception:
        await _vk_send(peer_id, "❌ Ошибка")
        return

    if not stations:
        await _vk_send(peer_id, f"🔍 АЗС в «{city}» не найдены.")
        return

    fuel_prices: dict[str, float] = {}
    for s in stations[:20]:
        sid = s.get("id")
        if not sid:
            continue
        try:
            statuses = await get_station_current_status(sid)
            for st in statuses:
                ft = st.get("fuel_type", "")
                price = st.get("price")
                if price and (ft not in fuel_prices or price < fuel_prices[ft]):
                    fuel_prices[ft] = price
        except Exception:
            pass

    if not fuel_prices:
        await _vk_send(peer_id,
            f"⛽ <b>{city or 'Шарья'}</b> — цены пока неизвестны\n\n"
            "Данные обновляются от водителей. Будь первым — сообщи цены!")
        return

    lines = [f"⛽ <b>Цены в «{city or 'Шарья'}»</b>\n"]
    fuel_names = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100", "diesel": "ДТ", "gas": "Газ"}
    for fuel_key, price in sorted(fuel_prices.items()):
        name = fuel_names.get(fuel_key, fuel_key)
        lines.append(f"• {name}: <b>{price:.2f} ₽</b>")

    lines.append(f"\n📱 <a href=\"https://t.me/benzyn_ryadom_bot?start=ref\">Полная карта в Telegram</a>")
    await _vk_send(peer_id, "\n".join(lines))


async def _chat_status(peer_id: int, city: str) -> None:
    """Показать статус наличия топлива для VK чата — из реальных отчётов."""
    from db import find_stations_by_city, get_station_current_status
    try:
        stations = await find_stations_by_city(city or "Шарья", has_stock=False)
    except Exception:
        await _vk_send(peer_id, "❌ Ошибка")
        return

    if not stations:
        await _vk_send(peer_id, f"🔍 АЗС в «{city}» не найдены.")
        return

    available = 0
    limited = 0
    unavailable = 0
    for s in stations[:20]:
        sid = s.get("id")
        if not sid:
            continue
        try:
            statuses = await get_station_current_status(sid)
            for st in statuses:
                avail = st.get("available")
                if avail is True:
                    available += 1
                elif avail is None:
                    limited += 1
                elif avail is False:
                    unavailable += 1
        except Exception:
            pass

    total_reports = available + limited + unavailable

    lines = [
        f"📊 <b>Наличие топлива — {city or 'Шарья'}</b>\n",
        f"🟢 Есть топливо: <b>{available}</b>",
        f"🟡 Мало / кончается: <b>{limited}</b>",
        f"🔴 Нет топлива: <b>{unavailable}</b>",
    ]
    if total_reports == 0:
        lines.append(f"\n⚠️ Активных отчётов пока нет.\nБудь первым — сообщи о наличии!")
    lines.append(f"\n📊 Всего АЗС: <b>{len(stations)}</b>")
    lines.append(f"\n📱 <a href=\"https://t.me/benzyn_ryadom_bot?start=ref\">Полная карта в Telegram</a>")
    await _vk_send(peer_id, "\n".join(lines))
async def handle_start(peer_id: int) -> None:
    uid = await _ensure_user(peer_id)
    if uid:
        await log_event(uid, "vk_start")

    # === Принятие юридических документов (обязательно) ===
    try:
        import aiohttp
        backend = settings.BACKEND_URL
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{backend}/api/user/legal-status",
                params={"vk_id": peer_id},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                status = await r.json()
        if not status.get("legal_accepted"):
            legal_kb = vk_keyboard([
                [_link_button("📄 Пользовательское соглашение", f"{backend}/legal/terms.html"),
                 _link_button("🔒 Политика конфиденциальности", f"{backend}/legal/privacy.html")],
                [_link_button("✅ Согласие на обработку ПДн", f"{backend}/legal/consent.html"),
                 _link_button("⚠️ Дисклеймер", f"{backend}/legal/disclaimer.html")],
                [_callback_button("✅ Принять все документы", {"a": "accept_legal"}, color="positive")],
            ])
            await _vk_send(peer_id,
                "👋 <b>Привет!</b>\n\n"
                "Перед использованием бота необходимо принять юридические документы:\n\n"
                "📄 Пользовательское соглашение\n"
                "🔒 Политика конфиденциальности\n"
                "✅ Согласие на обработку ПДн (152-ФЗ)\n"
                "⚠️ Дисклеймер\n\n"
                "Открой каждый документ (это важно) и нажми «Принять все».\n"
                "Без согласия с документами бот не работает.",
                legal_kb)
            return
    except Exception as e:
        logger.warning("VK legal check failed: %s", e)

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
        "🛣 <b>Поиск по трассе</b> — М-4, М-7, Р-217 и другие\n"
        "📝 <b>Сообщить</b> — отметь наличие топлива\n"
        "🔔 <b>Подписки</b> — push о завозе рядом\n"
        "👤 <b>Профиль</b> — репутация, бейджи\n"
        "🏪 <b>Владелец</b> — verified-бейдж\n"
        "💎 <b>Premium</b> — push без задержек\n\n"
        "💡 Напиши название АЗС, город или сеть — я покажу результат."
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_route_search_start(peer_id: int) -> None:
    """Запуск поиска АЗС по трассе."""
    _set_state(peer_id, {"flow": "route_search", "step": "waiting_query"})
    text = (
        "🛣 <b>Поиск АЗС вдоль трассы</b>\n\n"
        "Введи номер или название трассы:\n"
        "• <code>М-4</code> или <code>М4</code> — трасса «Дон»\n"
        "• <code>М-7</code> — «Волга»\n"
        "• <code>Р-217</code> — «Кавказ»\n"
        "• <code>дон</code>, <code>кавказ</code>, <code>крым</code> — по названию\n\n"
        "Бот покажет АЗС вдоль трассы с адресами, ценами и наличием."
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_route_search_text(peer_id: int, text: str) -> None:
    """Обрабатывает ввод названия/номера трассы и показывает результаты."""
    text_clean = text.strip()
    if text_clean.lower() in ("отмена", "отменить", "cancel"):
        _clear_state(peer_id)
        await _vk_send(peer_id, "Отменено.", vk_main_menu())
        return

    from db import search_routes, find_stations_by_route
    routes = await search_routes(text_clean, limit=5)
    if not routes:
        await _vk_send(
            peer_id,
            f"🔍 По запросу <b>«{text_clean}»</b> трасс не найдено.\n"
            f"Попробуй: М-4, М-7, Р-217, Дон, Кавказ, Крым.",
            vk_main_menu(),
        )
        return

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

    for i, s in enumerate(stations[:10], 1):
        addr = s.get("address") or "—"
        city = s.get("city") or ""
        km = s.get("km_marker")
        km_str = f" (≈{km} км)" if km else ""
        has_fuel = s.get("has_fuel", False)
        status = "✅ Есть топливо" if has_fuel else "❓ Нет данных"
        net = s.get("operator") or s.get("brand") or ""
        net_str = f" <i>{net}</i>" if net else ""

        lines.append(f"{i}. <b>#{s['id']}</b>{net_str} — {s['name']}")
        lines.append(f"   📍 {city}, {addr}{km_str}")
        lines.append(f"   {status}")
        lines.append("")

    if len(stations) > 10:
        lines.append(f"<i>...и ещё {len(stations) - 10} АЗС</i>")

    buttons = []
    if len(routes) > 1:
        buttons.append([_callback_button(
            f"Другие трассы ({len(routes) - 1})",
            {"a": "route_more", "q": text_clean[:50]},
        )])
    buttons.append([_callback_button("🔍 Новый поиск", {"a": "route_search"})])
    buttons.append([_callback_button("🏠 В начало", {"a": "home"})])

    await _vk_send(peer_id, "\n".join(lines), vk_keyboard(buttons))
    _clear_state(peer_id)


async def handle_anti_traffic_start(peer_id: int) -> None:
    """Анти-пробка: Elite-only."""
    from db import get_user_id_by_vk_id, get_user_premium, has_feature
    uid = await get_user_id_by_vk_id(peer_id)
    if uid:
        sub = await get_user_premium(uid)
        tier = sub.get("tier") if sub else None
        if not has_feature(tier, "anti_traffic"):
            await _vk_send(peer_id,
                "🚗 <b>Анти-пробка</b> — Elite-фича.\n\n"
                "Показывает пробки, время в пути и лучшее время поездки.\n\n"
                "⭐ Купи Premium Elite.",
                vk_main_menu())
            return
    _set_state(peer_id, {"flow": "anti_traffic", "step": "waiting_from"})
    await _vk_send(peer_id,
        "🚗 <b>Анти-пробка</b>\n\n"
        "📍 <b>Откуда едешь?</b>\n"
        "Введи координаты или город:\n"
        "• <code>56.85,40.98</code>\n"
        "• <code>Иваново</code>",
        vk_main_menu())


async def handle_anti_traffic_from(peer_id: int, text: str) -> None:
    """Получаем точку отправления."""
    import re
    lat, lon = None, None
    parts = text.replace(" ", "").split(",")
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            pass
    if lat is None:
        import aiohttp, os
        backend = os.getenv("BACKEND_URL", "")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{backend}/api/search?q={text}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json()
                    results = data.get("results", [])
                    if results:
                        lat, lon = results[0].get("lat"), results[0].get("lon")
        except Exception:
            pass
    if lat is None or lon is None:
        await _vk_send(peer_id, "❌ Не распознал координаты. Попробуй:\n• <code>56.85,40.98</code>\n• Или город")
        return
    _set_state(peer_id, {"flow": "anti_traffic", "step": "waiting_to", "from_lat": lat, "from_lon": lon})
    await _vk_send(peer_id, f"✅ Откуда: <code>{lat}, {lon}</code>\n\n📍 <b>Куда едешь?</b>")


async def handle_anti_traffic_to(peer_id: int, text: str) -> None:
    """Получаем точку назначения, вызываем API."""
    state = _get_state(peer_id)
    from_lat, from_lon = state.get("from_lat"), state.get("from_lon")
    _clear_state(peer_id)

    lat, lon = None, None
    parts = text.replace(" ", "").split(",")
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            pass
    if lat is None:
        import aiohttp, os
        backend = os.getenv("BACKEND_URL", "")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{backend}/api/search?q={text}", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json()
                    results = data.get("results", [])
                    if results:
                        lat, lon = results[0].get("lat"), results[0].get("lon")
        except Exception:
            pass
    if lat is None or lon is None:
        await _vk_send(peer_id, "❌ Не распознал координаты.", vk_main_menu())
        return

    import aiohttp, os
    backend = os.getenv("BACKEND_URL", "")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{backend}/api/route/anti-traffic",
                params={"from_lat": from_lat, "from_lon": from_lon, "to_lat": lat, "to_lon": lon, "vk_user_id": peer_id, "fuel": "95"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                result = await r.json()
    except Exception as e:
        await _vk_send(peer_id, f"❌ Ошибка: {e}", vk_main_menu())
        return

    if result.get("error"):
        err = result["error"]
        if err == "elite_required":
            await _vk_send(peer_id, "🚗 Анти-пробка — Elite-only.\n\n⭐ Купи Premium Elite.", vk_main_menu())
        else:
            await _vk_send(peer_id, f"❌ {result.get('message', err)}", vk_main_menu())
        return

    t = result.get("traffic", {})
    dist = result.get("total_distance_km", 0)
    level = t.get("level", "?")
    desc = t.get("description", "")
    eta = t.get("eta_minutes", 0)
    delay = t.get("delay_minutes", 0)
    best = result.get("best_time")
    stops = result.get("stop_points", [])
    emoji = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(level, "⚪")

    text_msg = (
        f"🚗 <b>Анти-пробка</b>\n\n"
        f"📏 Расстояние: <b>{dist} км</b>\n"
        f"{emoji} Пробки: <b>{desc}</b>\n"
        f"⏱ Время в пути: <b>{eta} мин</b>"
    )
    if delay > 0:
        text_msg += f" (задержка +{delay} мин)"
    if best:
        text_msg += f"\n\n💡 {best}"
    if stops:
        text_msg += f"\n\n⛽ <b>Точки заправки ({len(stops)}):</b>"
        for sp in stops[:5]:
            text_msg += f"\n• {sp.get('km_from_start', '?')} км — {sp.get('suggestion', '')}"

    buttons = [[_callback_button("🏠 В начало", {"a": "home"})]]
    await _vk_send(peer_id, text_msg, vk_keyboard(buttons))
    _clear_state(peer_id)


async def handle_find(peer_id: int) -> None:
    _clear_state(peer_id)
    await _vk_send(
        peer_id,
        "📍 <b>Выбери населённый пункт</b>\n\n"
        "Иваново, Москва, СПб, и другие. "
        "Или напиши свой город в сообщении — бот найдёт АЗС.",
        vk_city_keyboard(),
    )


async def handle_sos(peer_id: int) -> None:
    """SOS Elite — VK (без геолокации — открывает MiniApp)."""
    from db import get_user_id_by_vk_id, get_user_premium, has_feature
    uid = await get_user_id_by_vk_id(peer_id)
    if uid:
        sub = await get_user_premium(uid)
        tier = sub.get("tier") if sub else None
        if not has_feature(tier, "sos_elite"):
            await _vk_send(peer_id,
                "🚨 <b>SOS-режим</b> — Elite-фича.\n\n"
                "Рассылает SOS-сигнал всем водителям в радиусе 50 км.\n\n"
                "⭐ Купи Premium Elite.",
                vk_main_menu())
            return
    app_id = os.getenv("VK_MINI_APP_ID", "")
    if app_id and app_id.isdigit():
        app_url = f"https://vk.com/app{app_id}#sos"
        buttons = [[_vkapp_button("🚨 Отправить SOS", int(app_id))]]
        await _vk_send(peer_id,
            "🚨 <b>SOS — экстренная помощь</b>\n\n"
            "Нажми кнопку, чтобы отправить SOS с геолокацией.\n"
            "Водители в радиусе 50 км получат уведомление.",
            vk_keyboard(buttons))
    else:
        await _vk_send(peer_id,
            "🚨 <b>SOS — экстренная помощь</b>\n\n"
            "VK Mini App не настроен. Используй Telegram-бота:\n"
            "https://t.me/benzyn_ryadom_bot",
            vk_main_menu())


async def handle_subscribe(peer_id: int) -> None:
    _set_state(peer_id, {"awaiting": "subscribe_geo"})
    await _vk_send(
        peer_id,
        "🔔 <b>Подписка на уведомления о завозе.</b>\n\n"
        "Отправь геолокацию — буду присылать уведомления, когда "
        "в радиусе 5 км от тебя появится бензин.",
        vk_subscribe_geo_keyboard(),
    )


async def handle_profile(peer_id: int) -> None:
    uid = await _get_user_id(peer_id)
    if not uid:
        await _vk_send(peer_id, "Профиль не найден. Нажми 🏠 В начало.", vk_main_menu())
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
    user_row = None
    try:
        if USE_SQLITE:
            from db import _fetch
            user_row = await _fetch("SELECT vk_id, telegram_id FROM users WHERE id = ?", uid, one=True)
        else:
            import db as _db_mod
            if _db_mod._db:
                async with _db_mod._db.acquire() as conn:
                    user_row = await conn.fetchrow("SELECT vk_id, telegram_id FROM users WHERE id = $1", uid)
    except Exception:
        pass
    if user_row:
        u = dict(user_row) if not isinstance(user_row, dict) else user_row
        vk = u.get("vk_id")
        tg = u.get("telegram_id", 0)
        platforms = []
        if tg and tg > 0:
            platforms.append(f"TG: <code>{tg}</code>")
        if vk:
            platforms.append(f"VK: <code>{vk}</code>")
        if platforms:
            text += f"\n\n🔗 <b>Привязанные аккаунты:</b> {' | '.join(platforms)}"
    await _vk_send(peer_id, text, vk_main_menu())
async def handle_link(peer_id: int, text: str = "") -> None:
    """Привязка VK аккаунта к TG по ссылке на профиль.

    Команды:
    - "link" — показать меню
    - "link <TG_URL>" — привязать по ссылке (t.me/username, @username, username)
    """
    import aiohttp
    backend = settings.BACKEND_URL

    parts = (text or "").strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else "link"
    arg = parts[1].strip() if len(parts) >= 2 else ""

    # === link <URL> — привязать по ссылке ===
    if subcmd == "link" and arg:
        import re
        # Нормализуем: убираем протокол, www, домен — оставляем username
        s = arg.strip().strip("/").strip().lstrip("@")
        s = re.sub(r'^https?://', '', s, flags=re.IGNORECASE)
        s = re.sub(r'^(www|m|mobile)\.', '', s, flags=re.IGNORECASE)
        m = re.match(r'vk\.(com|ru)/([\w.]+)', s, re.IGNORECASE)
        if m:
            s = m.group(2).strip(".")
        m = re.match(r'(?:t\.me|telegram\.me)/([\w]+)', s, re.IGNORECASE)
        if m:
            s = m.group(1)
        profile_url = s

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{backend}/api/account/link-by-profile",
                    json={"vk_user_id": peer_id, "profile_url": profile_url},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
            if data.get("ok"):
                target_name = data.get("linked_to_name") or "пользователь"
                await _vk_send(peer_id,
                    f"✅ <b>Аккаунт привязан!</b>\n\n"
                    f"Твой VK привязан к <b>{target_name}</b>.\n"
                    f"Premium работает на всех площадках.",
                )
            else:
                err = data.get("error", "Неизвестная ошибка")
                await _vk_send(peer_id,
                    f"❌ <b>Не удалось привязать</b>\n\n{err}",
                )
        except Exception as e:
            logger.exception(f"handle_link error: {e}")
            await _vk_send(peer_id, "❌ Ошибка соединения. Попробуй позже.")
        return

    # === link — показать меню привязки ===
    kb = vk_keyboard([
        [_callback_button("🔗 Ввести ссылку на TG", {"a": "link_tg_prompt"}, "primary")],
        [_callback_button("◀️ Назад", {"a": "home"}, "secondary")],
    ])
    await _vk_send(peer_id,
        "🔗 <b>Привязка аккаунта</b>\n\n"
        "Premium работает и в TG, и в VK, и в Mini App.\n\n"
        "Нажми кнопку ниже и введи ссылку на TG профиль.\n"
        "Формат: <code>t.me/username</code> или <code>@username</code>",
        kb,
    )


async def handle_alarm(peer_id: int) -> None:
    """Показывает список топливных будильников (Premium)."""
    from db import get_user_id_by_vk_id, get_user_premium, get_fuel_alarms_for_user

    uid = await get_user_id_by_vk_id(peer_id)
    if not uid:
        await _vk_send(peer_id, "Сначала нажми «Начать»")
        return

    sub = await get_user_premium(uid)
    from db import has_feature
    is_prem = has_feature(sub.get("tier") if sub else None, "fuel_alarm")

    if not is_prem:
        kb = vk_keyboard([
            [_callback_button("💎 Купить Premium", {"a": "premium"}, "positive")],
        ])
        await _vk_send(peer_id,
            "⛽ <b>Топливный будильник</b>\n\n"
            "Эта фича доступна только для <b>Premium</b> пользователей.\n\n"
            "Купи Premium и получи:\n"
            "• Уведомления когда топливо появится\n"
            "• Прогноз цен на 7 дней\n"
            "• Экспорт данных в CSV\n"
            "• Маршрут A→B с ближайшими ценами",
            kb,
        )
        return

    alarms = await get_fuel_alarms_for_user(uid)
    if not alarms:
        await _vk_send(peer_id,
            "⛽ <b>Топливный будильник</b>\n\n"
            "У тебя нет активных будильников.\n\n"
            "Чтобы создать будильник:\n"
            "1. Найди нужную АЗС через поиск\n"
            "2. Нажми «🔔 Уведомить»\n"
            "3. Выбери тип топлива\n\n"
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
    await _vk_send(peer_id, "\n".join(lines))


async def handle_referral(peer_id: int, text: str = "") -> None:
    """Реферальная программа VK."""
    from db import get_user_id_by_vk_id, create_referral_code, get_referral_stats
    import aiohttp

    uid = await get_user_id_by_vk_id(peer_id)
    if not uid:
        await _vk_send(peer_id, "Сначала нажми «Начать»")
        return

    # Проверяем есть ли код в тексте (referral ABC123)
    parts = text.strip().split()
    if len(parts) >= 2:
        code = parts[1].strip().upper()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{settings.BACKEND_URL}/api/referral/apply",
                    json={"vk_user_id": peer_id, "code": code},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
        except Exception:
            data = {"error": "connection error"}

        if data.get("ok"):
            await _vk_send(peer_id,
                "🎉 <b>Реферал применён!</b>\n\n"
                "Ты получил 15% скидку на первую оплату Premium.\n"
                "Твой пригласивший будет получать 50% с каждой твоей оплаты.",
            )
        else:
            err = data.get("error", "unknown")
            if err == "invalid referral code":
                await _vk_send(peer_id, "❌ Код не найден. Проверь и попробуй ещё раз.")
            elif err == "cannot use your own referral code":
                await _vk_send(peer_id, "❌ Нельзя использовать свой же код.")
            else:
                await _vk_send(peer_id, f"❌ Ошибка: {err}")
        return

    # Показываем свой код + баланс
    code = await create_referral_code(uid)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{settings.BACKEND_URL}/api/referral/balance",
                params={"vk_user_id": peer_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                balance_data = await r.json()
    except Exception:
        balance_data = {"balance": {"total_earned": 0, "balance": 0}, "stats": {"total": 0, "completed": 0}}

    balance = balance_data.get("balance", {})
    stats = balance_data.get("stats", {})

    ref_link_tg = f"https://t.me/benzyn_ryadom_bot?start=ref_{code}"
    ref_link_vk = f"https://vk.com/benzyn_ryadom?start=ref_{code}"

    from db import get_user_premium, has_feature
    sub = await get_user_premium(uid) if uid else None
    tier = sub.get("tier") if sub else None
    is_elite = has_feature(tier, "anti_traffic")

    # Получаем данные о тире
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{settings.BACKEND_URL}/api/referral/tier",
                params={"vk_user_id": peer_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                tier_data = await r.json()
    except Exception:
        tier_data = {"tier": "basic", "active_referrals": 0, "commission": 50, "is_top3": False}

    ref_tier = tier_data.get("tier", "basic")
    active_refs = tier_data.get("active_referrals", 0)
    commission = tier_data.get("commission", 50)
    is_top3 = tier_data.get("is_top3", False)
    next_tier = tier_data.get("next_tier")

    tier_names = {
        "basic": "Базовый", "ambassador": "Посол",
        "top_ref": "Топ-реферер", "legend": "Легенда",
    }

    tier_text = f"🎯 Тир: {tier_names.get(ref_tier, ref_tier)}"
    if is_top3:
        tier_text += " 🏆 + топ-3!"
    if next_tier:
        tier_text += f"\n📈 До «{next_tier['name']}» осталось {next_tier['need']} активных рефералов"

    if is_elite:
        commission_text = (
            f"💰 Комиссия: {commission}% с каждой оплаты реферала\n"
            f"(50/55/60/65% в зависимости от тира, 70% для топ-3)\n"
            f"5% со второго уровня\n"
            f"3% с третьего уровня (только для топ-3)"
        )
    else:
        commission_text = (
            "⚠️ <i>Комиссия начисляется только для Elite и Founder. "
            "Сейчас ты можешь приглашать, но заработок начнётся после покупки Elite.</i>"
        )

    calc_lines = ""
    if not is_elite:
        examples = [(5, 50), (20, 50), (50, 55), (100, 60), (200, 65)]
        calc_lines = "\n📊 <b>Калькулятор дохода:</b>\n"
        for n_refs, rate in examples:
            monthly = int(n_refs * 250 * rate / 100)
            calc_lines += f"  {n_refs} рефералов × {rate}% = ~{monthly}₽/мес\n"
        calc_lines += "\n💡 <i>Приглашай друзей — Elite быстро окупается!</i>"

    referred_users = balance_data.get("referred_users", [])
    referred_text = ""
    for r in referred_users[:5]:
        referred_text += f"  • {r.get('name', '?')} — {r.get('total_commission', 0)}₽ ({r.get('payment_count', 0)} оплат)\n"

    kb = vk_keyboard(inline=False)
    kb["buttons"].append([
        _callback_button("📋 Продающие тексты", {"a": "selling_texts"}),
        _callback_button("📚 Как заработать", {"a": "training"}),
    ])
    kb["buttons"].append([
        _callback_button("🏆 Топ рефереров", {"a": "leaderboard"}),
    ])

    await _vk_send(peer_id,
        f"🎁 <b>Реферальная программа</b>\n\n"
        f"{commission_text}\n\n"
        f"{tier_text}\n\n"
        f"Твой друг получит <b>15% скидку</b> на первую покупку.\n\n"
        f"<b>Telegram:</b>\n{ref_link_tg}\n\n"
        f"<b>VK:</b>\n{ref_link_vk}\n\n"
        f"<b>Твой код:</b> <code>{code}</code>\n\n"
        f"<b>💰 Баланс:</b> {balance.get('balance', 0)}₽\n"
        f"<b>📊 Всего заработано:</b> {balance.get('total_earned', 0)}₽\n"
        f"<b>💸 Выведено:</b> {balance.get('total_withdrawn', 0)}₽\n\n"
        f"<b>Статистика:</b>\n"
        f"👥 Приглашено: {stats.get('total', 0)}\n"
        f"✅ Активировали: {stats.get('completed', 0)}\n\n"
        f"{referred_text}"
        f"{calc_lines}",
        json.dumps(kb),
    )


async def handle_leaderboard(peer_id: int) -> None:
    """Топ рефереров по заработку."""
    import aiohttp, os
    backend = os.getenv("BACKEND_URL", "")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{backend}/api/referral/leaderboard",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
    except Exception:
        data = {"leaderboard": []}

    top = data.get("leaderboard", [])
    if not top:
        await _vk_send(peer_id,
            "🏆 <b>Топ рефереров</b>\n\n"
            "Пока нет данных. Будь первым — пригласи друга!",
            vk_main_menu())
        return

    lines = ["🏆 <b>Топ рефереров</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(top):
        medal = medals[i] if i < 3 else f"  {i+1}."
        lines.append(
            f"{medal} {r.get('name', 'User')} — "
            f"💰 {r.get('total_earned', 0)}₽ "
            f"({r.get('referral_count', 0)} рефералов)"
        )

    await _vk_send(peer_id, "\n".join(lines), vk_main_menu())


async def handle_selling_texts(peer_id: int) -> None:
    """Продающие тексты для копирования."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{settings.BACKEND_URL}/api/referral/selling-texts",
                params={"vk_user_id": peer_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                data = await r.json()
    except Exception:
        data = {"texts": []}

    texts = data.get("texts", [])
    if not texts:
        await _vk_send(peer_id, "Нет текстов для копирования.", vk_main_menu())
        return

    lines = ["📝 <b>Продающие тексты</b> (копируй и отправляй):\n"]
    for t in texts:
        lines.append(f"<b>{t['platform']}:</b>\n<code>{t['text']}</code>\n")
    lines.append("💡 Отправляй друзьям в мессенджерах, группах, соцсетях!")

    await _vk_send(peer_id, "\n".join(lines), vk_main_menu())


async def handle_training(peer_id: int) -> None:
    """Обучающий блок — как заработать на рефералах."""
    text = (
        "📚 <b>Как заработать на реферальной программе</b>\n\n"
        "<b>Шаг 1: Кого приглашать</b>\n"
        "• Водителей в своём городе\n"
        "• Друзей, которые жалуются на цены на бензин\n"
        "• В чатах/группах про авто\n\n"
        "<b>Шаг 2: Где делиться ссылкой</b>\n"
        "• WhatsApp/Telegram группы\n"
        "• VK группы про авто\n"
        "• Личные сообщения\n"
        "• В комментариях к постам о бензине\n\n"
        "<b>Шаг 3: Что писать</b>\n"
        "• Скажи про экономию (~500₽/мес)\n"
        "• Покажи сколько АЗС рядом\n"
        "• Скинь ссылку\n\n"
        "<b>Шаг 4: Когда делиться</b>\n"
        "• Утром (люди едут на работу)\n"
        "• Когда бензин дорожает (новости)\n"
        "• Выходные (планируют поездки)\n\n"
        "<b>💰 Сколько зарабатываешь:</b>\n"
        "• 50% от каждой оплаты реферала (базовый тир)\n"
        "• 55% при 50 активных рефералах\n"
        "• 60% при 100\n"
        "• 65% при 200+\n"
        "• 70% если вошёл в топ-3 месяца!\n\n"
        "💡 <i>Приглашай 5-10 друзей — уже 1250-2500₽/мес!</i>"
    )
    await _vk_send(peer_id, text, vk_main_menu())


async def handle_premium(peer_id: int) -> None:
    """Показать 3 тарифа Premium — красивый формат."""
    from db import PREMIUM_PLANS, is_premium, get_premium_info, get_user_id_by_vk_id, get_founder_remaining, FOUNDER_MAX
    from datetime import datetime

    founder_remaining = await get_founder_remaining()

    uid = await get_user_id_by_vk_id(peer_id)
    if uid and await is_premium(uid):
        info = await get_premium_info(uid)
        if info:
            days_left = (info["expires_at"] - datetime.now()).days if isinstance(info["expires_at"], datetime) else 30
            tier = info.get("tier", "")
            tier_name = {"economy": "📊 Эконом", "standard": "🗺️ Стандарт", "elite": "👑 Элит", "founder": "🏆 Founder"}.get(tier, tier)
            tier_features = {
                "economy": "📈 График цен · 📦 CSV-экспорт · 🗺️ Офлайн-карта",
                "standard": "📈 График цен · 📦 CSV · 🗺️ Офлайн · 🛣 Маршрут A→B · 🔮 Прогноз · 🔔 Будильник",
                "elite": "Всё из Стандарт + 🚗 Антипробка · 🆘 SOS-режим",
                "founder": "Пожизненный Элит + 🏆 Founder-бейдж + 📋 Основатель",
            }
            days_text = "навсегда" if tier == "founder" else f"{max(days_left, 0)} дн."
            text = (
                f"✅ <b>У тебя Premium!</b>\n\n"
                f"Тариф: <b>{tier_name}</b>\n"
                f"Осталось: <b>{days_text}</b>\n\n"
                f"<b>Твои фичи:</b>\n{tier_features.get(tier, '')}\n\n"
                f"💡 Открой Mini App для управления"
            )
            await _vk_send(peer_id, text, vk_main_menu())
            return

    plans = PREMIUM_PLANS
    from premium_texts import format_tier_text

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
        "🏆 <b>Founder Pack</b> — <b>1990₽ навсегда</b>\n"
        "├ Пожизненный Элит\n"
        "├ 🏆 Founder-бейдж\n"
        f"└ 📋 Осталось мест: <b>{founder_remaining} из {FOUNDER_MAX}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🧮 <b>Калькулятор:</b>\n"
        "40л/мес × 2 заправки × 3₽/л = <b>240₽/мес</b> экономии\n"
        "→ Стандарт уже окупается!\n\n"
        "👇 <b>Выбери тариф:</b>"
    )
    await _vk_send(peer_id, text, vk_premium_keyboard())


async def handle_donate(peer_id: int) -> None:
    text = (
        "❤️ <b>Поддержать проект</b>\n\n"
        "Бот бесплатный. Сервер, парсеры, база данных — всё стоит денег.\n"
        "Любая сумма поможет:\n\n"
        "👉 vk.com/donut/benzyn_ryadom"
    )
    await _vk_send(peer_id, text, vk_donate_keyboard())


async def handle_owner_info(peer_id: int) -> None:
    """Информация о регистрации владельца."""
    text = (
        "🏪 <b>Регистрация владельца АЗС</b>\n\n"
        "Открой мини-приложение для регистрации — это быстрее и удобнее.\n\n"
        "📱 В мини-приложении:\n"
        "• Загрузка документов (ИНН, ОГРН)\n"
        "• Привязка Telegram/VK\n"
        "• Управление verified-бейджем\n\n"
        "👉 Или напиши в поддержку: vk.me/benzyn_ryadom"
    )
    kb = vk_keyboard([
        [_callback_button("📱 Открыть приложение", {"a": "open_app"})],
        [_callback_button("◀️ Назад", {"a": "home"}, "secondary")],
    ])
    await _vk_send(peer_id, text, kb)


# === Search ===
async def handle_text_search(
    peer_id: int,
    query: str,
    fuel: str = "all",
    max_price: float | None = None,
    network: str | None = None,
    page: int = 0,
) -> None:
    if not query or len(query) < 2:
        await _vk_send(peer_id, "Введи минимум 2 символа.", vk_main_menu())
        return

    fuel_param = None if fuel in ("all", "", None) else fuel
    PAGE_SIZE = 5

    # 1) Сначала пробуем как город
    stations = await find_stations_by_city(
        city=query,
        has_stock=False,
        limit=50,
        fuel_type=fuel_param,
        max_price=max_price,
        network=network,
    )
    if not stations:
        stations = await find_stations_by_name(query, limit=50)
    if not stations:
        stations = await find_stations_by_address(query, limit=50)

    if not stations:
        filter_desc = []
        if fuel_param:
            fuel_label = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98",
                         "100": "АИ-100", "diesel": "Дизель", "lpg": "Газ"}.get(fuel_param, fuel_param)
            filter_desc.append(f"топливо {fuel_label}")
        if max_price:
            filter_desc.append(f"до {max_price}₽")
        if network:
            filter_desc.append(f"сеть: {network}")
        await _vk_send(peer_id,
            f"😔 В городе «{query}» ничего не найдено.\n"
            + (f"Фильтры: {', '.join(filter_desc)}\n\n" if filter_desc else "\n")
            + "Попробуй сбросить фильтры или выбрать другой город.",
            vk_fuel_filter_keyboard(query),
        )
        return

    # Сортировка: получаем статусы и сортируем по наличию топлива
    scored = []
    for s in stations:
        sid = s.get("id")
        try:
            statuses = await get_station_current_status(sid)
        except Exception:
            statuses = []
        # Подсчёт: есть ли данные
        has_any_fuel = False
        has_low = False
        fuel_count = 0
        fuel_parts = []
        best_price = None
        best_price_fuel = None
        for st in statuses:
            ft = st.get("fuel_type", "")
            av = st.get("available")
            price = st.get("price")
            if av is True:
                has_any_fuel = True
                fuel_count += 1
                fuel_parts.append(ft)
                if price is not None and (best_price is None or price < best_price):
                    best_price = price
                    best_price_fuel = ft
            elif av is None:  # "кончается"
                has_low = True
                fuel_parts.append(f"{ft}⚠")
            elif av is False:
                fuel_parts.append(f"{ft}❌")

        # Оценка: 3=есть топливо, 2=заканчивается, 1=нет топлива, 0=нет данных
        if has_any_fuel:
            score = 300 + fuel_count * 10  # больше видов = выше
        elif has_low:
            score = 200
        elif statuses:
            score = 100
        else:
            score = 0

        s["_score"] = score
        s["_fuel_parts"] = fuel_parts
        s["_statuses"] = statuses
        s["_best_price"] = best_price
        s["_best_price_fuel"] = best_price_fuel
        scored.append(s)

    scored.sort(key=lambda x: x["_score"], reverse=True)

    # Пагинация
    total = len(scored)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    page_stations = scored[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    # Формируем сообщение
    fuel_label = {
        "92": "АИ-92", "95": "АИ-95", "98": "АИ-98",
        "100": "АИ-100", "diesel": "Дизель", "lpg": "Газ"
    }.get(fuel_param, "")
    filter_parts = []
    if fuel_label:
        filter_parts.append(fuel_label)
    if max_price:
        filter_parts.append(f"до {max_price}₽")
    if network:
        filter_parts.append(network)
    filter_text = f" · {'/'.join(filter_parts)}" if filter_parts else ""

    lines = [f"🗺 <b>{query}</b>{filter_text} — {total} АЗС (стр. {page+1}/{total_pages})\n"]
    buttons = []
    for i, s in enumerate(page_stations):
        idx = page * PAGE_SIZE + i + 1
        op = s.get("operator") or s.get("name") or "АЗС"
        addr = s.get("address") or ""
        fuel_parts = s.get("_fuel_parts", [])

        # Статус-индикатор
        score = s.get("_score", 0)
        if score >= 300:
            indicator = "✅"
        elif score >= 200:
            indicator = "⚠️"
        elif score >= 100:
            indicator = "❌"
        else:
            indicator = "❓"

        fuel_str = ", ".join(fuel_parts[:4]) if fuel_parts else "нет данных"
        price_str = ""
        price_short = ""
        if s.get("_best_price") is not None:
            pf = s.get("_best_price_fuel", "")
            pf_label = f"АИ-{pf}" if pf not in ("diesel", "lpg", "all") else ("Дизель" if pf == "diesel" else pf)
            price_str = f" · от <b>{s['_best_price']:.2f}₽</b> ({pf_label})"
            price_short = f" от{s['_best_price']:.0f}₽"
        lines.append(f"\n{indicator} {idx}. <b>{op}</b>{price_str}")
        if addr:
            lines.append(f"📍 {addr[:50]}")
        lines.append(f"⛽ {fuel_str}")

        btn_label = f"{indicator} {idx}. {op[:22]}{price_short}"
        buttons.append([_callback_button(
            btn_label[:40],
            {"a": "station", "s": s.get("id")},
            "primary"
        )])

    # Навигация
    nav_row = []
    if page > 0:
        nav_row.append(_callback_button(
            "◀️ Назад",
            {"a": "city_page", "c": query, "p": page - 1, "f": fuel or "", "mp": max_price or 0, "n": network or ""},
            "secondary"
        ))
    if page < total_pages - 1:
        nav_row.append(_callback_button(
            "Вперёд ▶️",
            {"a": "city_page", "c": query, "p": page + 1, "f": fuel or "", "mp": max_price or 0, "n": network or ""},
            "secondary"
        ))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        _callback_button("◀️ К фильтрам", {"a": "city", "c": query}, "secondary"),
        _callback_button("🏠 В начало", {"a": "home"}),
    ])

    text = "\n".join(lines)
    await _vk_send(peer_id, text, vk_keyboard(buttons))


async def show_station(peer_id: int, station: dict) -> None:
    """Показывает детали АЗС."""
    from utils import format_station_card
    from db import get_station_rating
    sid = station.get("id")
    if not sid:
        return
    statuses = await get_station_current_status(sid)
    # Обогащаем рейтингом (как в TG)
    try:
        rating_info = await get_station_rating(sid)
        station["avg_rating"] = rating_info["avg_rating"]
        station["total_reviews"] = rating_info["total_reviews"]
    except Exception:
        station["avg_rating"] = None
        station["total_reviews"] = 0
    text = format_station_card(station, statuses)
    await _vk_send(peer_id, text[:4000], vk_station_actions(
        sid, lat=station.get("lat"), lon=station.get("lon"),
    ))


# === Report flow ===
async def handle_report_start(peer_id: int) -> None:
    """Начало flow отчёта — выбрать АЗС."""
    _set_state(peer_id, {"flow": "report", "step": "choose_station"})
    await _vk_send(
        peer_id,
        "📝 <b>Сообщить о наличии топлива</b>\n\n"
        "1️⃣ Напиши название АЗС, сеть или адрес\n"
        "2️⃣ Выбери АЗС из списка\n"
        "3️⃣ Укажи тип топлива и статус\n\n"
        "💡 Можно просто отправить геолокацию!",
        vk_city_keyboard(),
    )


async def handle_report_fuel(peer_id: int, station_id: int, fuel: str) -> None:
    """Шаг: выбрано топливо → спрашиваем статус."""
    _set_state(peer_id, {"flow": "report", "step": "status", "station_id": station_id, "fuel": fuel})
    fuel_name = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
                 "diesel": "Дизель", "lpg": "Газ"}.get(fuel, fuel)
    await _vk_send(
        peer_id,
        f"📝 <b>Отчёт для #{station_id}</b>\n\n"
        f"Топливо: <b>{fuel_name}</b>\n\n"
        f"Какой статус?",
        vk_report_status_keyboard(station_id, fuel),
    )


async def handle_report_status(peer_id: int, station_id: int, fuel: str, value: str) -> None:
    """Шаг: выбран статус → спрашиваем доп. данные."""
    state_data = _get_state(peer_id) or {}
    state_data.update({
        "flow": "report",
        "step": "extras",
        "station_id": station_id,
        "fuel": fuel,
        "status": value,
        "price": None,
        "limit": None,
        "canister_ban": False,
        "queue_size": 5 if value == "queue" else None,
    })
    _set_state(peer_id, state_data)
    fuel_name = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
                 "diesel": "Дизель", "lpg": "Газ"}.get(fuel, fuel)
    status_text = {"yes": "✅ Есть", "queue": "🕐 Большая очередь",
                   "low": "⚠️ Кончается", "no": "❌ Нет"}.get(value, "?")
    from vk_keyboards import vk_report_extras_keyboard
    await _vk_send(
        peer_id,
        f"Принято: <b>{status_text}</b>\n\n"
        f"Можешь добавить подробности (или сразу сохранить):",
        vk_report_extras_keyboard(station_id, fuel, value),
    )


async def handle_report_extra(peer_id: int, station_id: int, fuel: str, status: str, extra_type: str) -> None:
    """Обработка кнопки доп. данных в VK."""
    state_data = _get_state(peer_id) or {}
    state_data.update({
        "flow": "report",
        "step": "extras_input",
        "station_id": station_id,
        "fuel": fuel,
        "status": status,
    })

    if extra_type == "price":
        state_data["awaiting"] = "price"
        _set_state(peer_id, state_data)
        await _vk_send(peer_id, "💰 <b>Введи цену за литр в рублях.</b>\nНапример: <code>55.40</code>")
    elif extra_type == "limit":
        state_data["awaiting"] = "limit"
        _set_state(peer_id, state_data)
        await _vk_send(peer_id, "🚫 <b>Введи лимит на заправку в литрах.</b>\nНапример: <code>30</code>")
    elif extra_type == "canister":
        # Сразу ставим canister_ban=True
        state_data["canister_ban"] = True
        state_data.pop("awaiting", None)
        _set_state(peer_id, state_data)
        from vk_keyboards import vk_report_extras_keyboard
        await _vk_send(
            peer_id,
            f"✅ Запрет канистр зафиксирован.\n\n"
            f"Что ещё добавить?",
            vk_report_extras_keyboard(station_id, fuel, status),
        )
    elif extra_type == "queue":
        state_data["awaiting"] = "queue"
        _set_state(peer_id, state_data)
        await _vk_send(peer_id, "🚗 <b>Сколько машин в очереди?</b>\nВведи число от 1 до 50.")


async def handle_report_save(peer_id: int, station_id: int, fuel: str, status: str) -> None:
    """Сохраняет отчёт со всеми собранными данными."""
    state_data = _get_state(peer_id) or {}
    avail = {"yes": True, "queue": True, "low": None, "no": False}.get(status)
    queue_size = state_data.get("queue_size") or (5 if status == "queue" else None)
    has_limit = bool(state_data.get("limit"))
    limit_liters = state_data.get("limit")
    canister_ban = bool(state_data.get("canister_ban"))
    price = state_data.get("price")

    user_id = await _get_user_id(peer_id)
    try:
        await add_report(
            station_id=station_id,
            fuel_type=fuel,
            available=avail,
            user_id=user_id,
            queue_size=queue_size,
            price=price,
            has_limit=has_limit,
            limit_liters=limit_liters,
            canister_ban=canister_ban,
            source="vk_user",
        )
    except Exception as e:
        logger.warning("add_report failed: %s", e)
    if user_id:
        await log_event(user_id, "vk_report")
    fuel_name = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
                 "diesel": "Дизель", "lpg": "Газ"}.get(fuel, fuel)
    status_text = {"yes": "✅ Есть", "queue": "🕐 Большая очередь",
                   "low": "⚠️ Кончается", "no": "❌ Нет"}.get(status, "?")
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
    await _vk_send(
        peer_id,
        f"✅ <b>Спасибо! Отчёт записан.</b>\n\n"
        f"АЗС #{station_id}, {fuel_name}: {status_text}{extras_text}\n\n"
        f"Твой вклад помогает другим водителям!",
        vk_main_menu(),
    )
    _clear_state(peer_id)


async def handle_report_price_only(peer_id: int, station_id: int, fuel: str) -> None:
    """Кнопка 'Только цена' — спрашиваем цену и сохраняем сразу."""
    _set_state(peer_id, {
        "flow": "report",
        "step": "extras_input",
        "station_id": station_id,
        "fuel": fuel,
        "status": "yes",
        "price_only": True,
        "awaiting": "price",
    })
    await _vk_send(peer_id, "💰 <b>Введи цену за литр в рублях.</b>\nНапример: <code>55.40</code>")


async def handle_report_extras_text(peer_id: int, text: str) -> None:
    """Обрабатывает текстовый ввод цены/лимита/очереди в VK."""
    state_data = _get_state(peer_id) or {}
    awaiting = state_data.get("awaiting")
    if not awaiting:
        return False

    station_id = state_data.get("station_id")
    fuel = state_data.get("fuel")
    status = state_data.get("status", "yes")
    text_clean = text.strip().replace(",", ".").replace("₽", "").replace("р", "").replace("л", "").replace("машин", "").strip()

    if awaiting == "price":
        try:
            price = float(text_clean)
            if price < 0 or price > 500:
                raise ValueError
        except ValueError:
            await _vk_send(peer_id, "❌ Неверная цена. Введи число от 0 до 500, например <code>55.40</code>")
            return True
        if state_data.get("price_only"):
            # Сразу сохраняем
            user_id = await _get_user_id(peer_id)
            try:
                await add_report(
                    station_id=station_id,
                    fuel_type=fuel,
                    available=True,
                    user_id=user_id,
                    price=price,
                    source="vk_user",
                )
            except Exception as e:
                logger.warning("add_report failed: %s", e)
            await _vk_send(
                peer_id,
                f"✅ Цена {price:.2f}₽ записана для АЗС #{station_id}, {fuel}.",
                vk_main_menu(),
            )
            _clear_state(peer_id)
        else:
            state_data["price"] = price
            state_data.pop("awaiting", None)
            _set_state(peer_id, state_data)
            from vk_keyboards import vk_report_extras_keyboard
            await _vk_send(
                peer_id,
                f"✅ Цена: {price:.2f}₽\n\nЧто ещё добавить?",
                vk_report_extras_keyboard(station_id, fuel, status),
            )
        return True

    elif awaiting == "limit":
        try:
            limit = int(text_clean)
            if limit < 1 or limit > 1000:
                raise ValueError
        except ValueError:
            await _vk_send(peer_id, "❌ Неверный лимит. Введи число от 1 до 1000.")
            return True
        state_data["limit"] = limit
        state_data.pop("awaiting", None)
        _set_state(peer_id, state_data)
        from vk_keyboards import vk_report_extras_keyboard
        await _vk_send(
            peer_id,
            f"✅ Лимит: {limit}л\n\nЧто ещё добавить?",
            vk_report_extras_keyboard(station_id, fuel, status),
        )
        return True

    elif awaiting == "queue":
        try:
            queue = int(text_clean)
            if queue < 1 or queue > 50:
                raise ValueError
        except ValueError:
            await _vk_send(peer_id, "❌ Неверное число. Введи от 1 до 50.")
            return True
        state_data["queue_size"] = queue
        state_data.pop("awaiting", None)
        _set_state(peer_id, state_data)
        from vk_keyboards import vk_report_extras_keyboard
        await _vk_send(
            peer_id,
            f"✅ Очередь: {queue} машин\n\nЧто ещё добавить?",
            vk_report_extras_keyboard(station_id, fuel, status),
        )
        return True

    return False


# === Subscribe to station ===
async def handle_subscribe_station(peer_id: int, station_id: int) -> None:
    """Подписаться на завоз конкретной АЗС."""
    user_id = await _get_user_id(peer_id)
    if not user_id:
        await _vk_send(peer_id, "Сначала нажми /start", vk_main_menu())
        return
    try:
        await add_subscription(
            user_id=user_id,
            kind="station",
            target_id=station_id,
        )
    except Exception as e:
        logger.warning("add_subscription failed: %s", e)
    await _vk_send(
        peer_id,
        f"🔔 <b>Подписка оформлена</b>\n\n"
        f"АЗС #{station_id} — будем присылать уведомления о завозе топлива.\n\n"
        f"💎 Premium: push без задержек (5₽ через VK Донат)",
        vk_station_actions(station_id),
    )


# === Review flow ===
async def handle_review_start(peer_id: int, station_id: int) -> None:
    """Начало отзыва — выбрать тип топлива."""
    _set_state(peer_id, {"flow": "review", "step": "fuel", "station_id": station_id})
    await _vk_send(
        peer_id,
        f"⭐ <b>Оценить качество топлива</b>\n\n"
        f"АЗС #{station_id}\n\n"
        f"Какое топливо оцениваем?",
        vk_review_fuel_keyboard(station_id),
    )


async def handle_review_fuel(peer_id: int, station_id: int, fuel: str) -> None:
    """Шаг: выбрано топливо → выбрать рейтинг."""
    _set_state(peer_id, {"flow": "review", "step": "rating", "station_id": station_id, "fuel": fuel})
    fuel_name = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
                 "diesel": "Дизель", "lpg": "Газ"}.get(fuel, fuel)
    await _vk_send(
        peer_id,
        f"⭐ <b>Оценка качества</b>\n\n"
        f"АЗС #{station_id}, {fuel_name}\n\n"
        f"Сколько звёзд?",
        vk_review_rating_keyboard(station_id, fuel),
    )


async def handle_review_rating(peer_id: int, station_id: int, fuel: str, rating: int) -> None:
    """Сохраняет отзыв."""
    user_id = await _get_user_id(peer_id)
    if not user_id:
        await _vk_send(peer_id, "Сначала нажми /start", vk_main_menu())
        return
    try:
        await add_review(
            station_id=station_id,
            user_id=user_id,
            fuel_type=fuel,
            rating=rating,
        )
    except Exception as e:
        logger.warning("add_review failed: %s", e)
    await _vk_send(
        peer_id,
        f"✅ <b>Спасибо за отзыв!</b>\n\n"
        f"АЗС #{station_id}, {fuel}: {'⭐' * rating}",
        vk_station_actions(station_id),
    )
    _clear_state(peer_id)


# === Geo handler ===
async def handle_geo(peer_id: int, geo: dict) -> None:
    """Обрабатывает геолокацию."""
    state = _get_state(peer_id)
    if state.get("awaiting") == "subscribe_geo":
        # Подписка на уведомления
        lat = geo.get("coordinates", {}).get("latitude") or geo.get("latitude")
        lon = geo.get("coordinates", {}).get("longitude") or geo.get("longitude")
        if not lat or not lon:
            await _vk_send(peer_id, "⚠️ Не удалось получить координаты.", vk_subscribe_geo_keyboard())
            return
        user_id = await _get_user_id(peer_id)
        if user_id:
            try:
                await add_subscription(
                    user_id=user_id,
                    kind="geo",
                    lat=lat,
                    lon=lon,
                    radius_km=5,
                )
            except Exception as e:
                logger.warning("add_subscription geo failed: %s", e)
        await _vk_send(
            peer_id,
            f"🔔 <b>Подписка оформлена!</b>\n\n"
            f"Координаты: {lat:.4f}, {lon:.4f}\n"
            f"Радиус: 5 км\n\n"
            f"Будем присылать push о завозе в этом районе.",
            vk_main_menu(),
        )
        _clear_state(peer_id)
        return
    # Иначе — ищем ближайшие АЗС
    lat = geo.get("coordinates", {}).get("latitude") or geo.get("latitude")
    lon = geo.get("coordinates", {}).get("longitude") or geo.get("longitude")
    if not lat or not lon:
        await _vk_send(peer_id, "⚠️ Не удалось получить координаты.", vk_main_menu())
        return
    stations = await find_nearest_stations(lat=lat, lon=lon, radius_km=30, limit=10)
    if not stations:
        await _vk_send(peer_id, "😔 Рядом АЗС не найдено.", vk_main_menu())
        return
    # Показываем все станции с краткой информацией
    from utils import get_main_status
    lines = [f"📍 <b>Ближайшие АЗС ({len(stations)} шт):</b>\n"]
    buttons = []
    for i, s in enumerate(stations[:10]):
        dist = s.get("distance_km", 0)
        op = s.get("operator") or s.get("name") or "АЗС"
        addr = (s.get("address") or "")[:30]
        ms = get_main_status(s)
        price_str = ""
        if ms.get("price"):
            price_str = f" · {ms['price']:.0f}₽"
        lines.append(f"{ms['icon']} {i+1}. <b>{op}</b> · {dist:.1f}км{price_str}")
        if addr:
            lines.append(f"   📍 {addr}")
        buttons.append([_callback_button(
            f"{ms['icon']} {i+1}. {op[:22]}",
            {"a": "station", "s": s.get("id")},
            "primary"
        )])
    buttons.append([_callback_button("🏠 В начало", {"a": "home"})])
    text = "\n".join(lines)
    await _vk_send(peer_id, text, vk_keyboard(buttons))


# === Message router ===
async def process_message_new(event: dict) -> None:
    """Обрабатывает message_new от VK Callback API."""
    msg = event.get("object", {}).get("message", {})
    if not msg:
        return
    peer_id = msg.get("peer_id", 0)
    if not peer_id or peer_id < 0:
        return  # сообщества (группы) игнорируем

    is_chat = peer_id > 2000000000  # VK чат (2000000001+)

    # Дедупликация
    msg_id = str(msg.get("id", ""))
    if msg_id:
        last_seen = _processed_events.get(f"msg:{msg_id}", 0)
        if time.time() - last_seen < _EVENT_DEDUP_TTL:
            return
        _processed_events[f"msg:{msg_id}"] = time.time()

    text = (msg.get("text") or "").strip()
    geo = msg.get("geo")
    has_attachments = bool(msg.get("attachments"))

    # VK deep link: ?start=ref_CODE
    start_payload = msg.get("start") or ""
    if not text and start_payload:
        text = start_payload

    if not text and not geo and not has_attachments:
        return
    logger.info("[vk-cb] peer=%d text=%r geo=%s is_chat=%s", peer_id, text[:50], bool(geo), is_chat)

    # === ЧАТ: упрощённая обработка (только поиск АЗС) ===
    if is_chat:
        await _handle_chat_message(peer_id, text, msg)
        return

    # Регистрация пользователя
    user_info = msg.get("from") or {}
    first_name = user_info.get("first_name", "VK")
    await _ensure_user(peer_id, first_name)

    # Проверка подписки на сообщество (только для личных сообщений)
    is_sub = await _check_vk_subscription(peer_id)
    if not is_sub:
        await _vk_send(peer_id,
            "📢 <b>Чтобы пользоваться ботом — подпишись на наше сообщество</b>\n\n"
            "Бот бесплатный. Взамен — подпишись на сообщество с новостями о ценах на топливо.\n\n"
            "👇 Нажми кнопку ниже, подпишись, затем нажми «✅ Я подписался»",
            _vk_subscribe_keyboard())
        return

    # Geo
    if geo:
        await handle_geo(peer_id, geo)
        return

    # Текстовые команды
    low = text.lower()
    if low in ("/start", "start", "начать"):
        await handle_start(peer_id)
    elif low in ("/help", "help", "помощь"):
        await handle_help(peer_id)
    elif low in ("/find", "find", "искать"):
        await handle_find(peer_id)
    elif low in ("/subscribe", "subscribe", "подписаться"):
        await handle_subscribe(peer_id)
    elif low in ("/profile", "profile", "профиль"):
        await handle_profile(peer_id)
    elif low in ("/donate", "donate", "донат", "поддержать"):
        await handle_donate(peer_id)
    elif low in ("/premium", "premium", "премиум"):
        await handle_premium(peer_id)
    elif low in ("/alarm", "alarm", "будильник"):
        await handle_alarm(peer_id)
    elif low.startswith("/referral") or low.startswith("referral"):
        await handle_referral(peer_id, text)
    elif "ref_" in low:
        import re
        ref_match = re.search(r'ref_([A-Z0-9]+)', text, re.IGNORECASE)
        if ref_match:
            await handle_referral(peer_id, f"referral {ref_match.group(1)}")
    elif low in ("/leaderboard", "leaderboard", "топ", "лидерборд"):
        await handle_leaderboard(peer_id)
    elif low in ("/selling", "selling", "продающие", "тексты"):
        await handle_selling_texts(peer_id)
    elif low in ("/training", "training", "как заработать", "обучение"):
        await handle_training(peer_id)
    elif low.startswith("/link") or low.startswith("link "):
        await handle_link(peer_id, text)
    elif low in ("/anti-traffic", "anti-traffic", "анти-пробка", "🚗 анти-пробка"):
        await handle_anti_traffic_start(peer_id)
    elif low in ("/sos", "sos", "экстренная помощь"):
        await handle_sos(peer_id)
    elif low in ("/owner", "owner", "владелец", "я владелец"):
        await handle_owner_info(peer_id)
    elif low in ("/home", "home", "в начало", "главное меню"):
        _clear_state(peer_id)
        await _vk_send(peer_id, "Главное меню:", vk_main_menu())
    elif low in ("/menu", "menu"):
        _clear_state(peer_id)
        await _vk_send(peer_id, "Главное меню:", vk_main_menu())
    elif low.startswith("/broadcast") or low.startswith("broadcast "):
        # VK broadcast — admin only
        import os
        vk_admin_ids = [int(x) for x in os.getenv("VK_ADMIN_IDS", "772577887").split(",") if x.strip().isdigit()]
        if peer_id not in vk_admin_ids:
            await _vk_send(peer_id, "⛔ Нет доступа", vk_main_menu())
            return

        broadcast_text = text.replace("/broadcast", "", 1).replace("broadcast", "", 1).strip()
        if not broadcast_text:
            from db import get_broadcast_stats
            stats = await get_broadcast_stats()
            await _vk_send(peer_id,
                f"📢 <b>Рассылка VK</b>\n\n"
                f"👥 VK юзеров: {stats['vk']}\n"
                f"👥 TG юзеров: {stats['tg']}\n"
                f"📊 Всего: {stats['total']}\n\n"
                "Для отправки текста:\n"
                "<code>broadcast текст сообщения</code>",
                vk_main_menu())
            return

        from db import get_all_vk_user_ids
        user_ids = await get_all_vk_user_ids()
        if not user_ids:
            await _vk_send(peer_id, "⚠️ Нет VK пользователей для рассылки", vk_main_menu())
            return

        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                await _vk_send(uid, broadcast_text)
                sent += 1
            except Exception:
                failed += 1
            import asyncio as _aio
            await _aio.sleep(0.1)

        await _vk_send(peer_id, f"✅ VK рассылка: {sent} отправлено, {failed} ошибок", vk_admin_keyboard())

    elif low.startswith("/chat_welcome") or low.startswith("chat_welcome "):
        import os
        vk_admin_ids = [int(x) for x in os.getenv("VK_ADMIN_IDS", "772577887").split(",") if x.strip().isdigit()]
        if peer_id not in vk_admin_ids:
            await _vk_send(peer_id, "⛔ Нет доступа")
            return
        parts = text.split()
        target_peer = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if not target_peer:
            await _vk_send(peer_id, "Использование: <code>/chat_welcome PEER_ID</code>\nPEER_ID = ID чата VK (>2000000000)")
            return
        welcome = (
            "⛽ <b>Добро пожаловать в «Бензин рядом»!</b>\n\n"
            "🔍 <b>Как найти АЗС:</b>\n"
            "Просто напиши название города или адрес:\n"
            "• Шарья\n"
            "• Кострома, ул. Советская\n\n"
            "📋 <b>Команды:</b>\n"
            "• <code>/find Шарья</code> — поиск АЗС\n"
            "• <code>/prices Шарья</code> — цены на топливо\n"
            "• <code>/status Шарья</code> — статус наличия\n"
            "• <code>/help</code> — все команды\n\n"
            "📊 <b>Данные обновляются</b> каждые 2 часа от водителей и парсеров.\n\n"
            "📱 <b>Полная версия</b> (карта, отчёты, уведомления):\n"
            "• Telegram: @benzyn_ryadom_bot\n"
            "• VK: https://vk.com/benzyn_ryadom\n\n"
            "💡 <b>Помоги сообществу:</b> сообщи о наличии/ценах на ближайшей АЗС!"
        )
        await _vk_send(target_peer, welcome)
        await _vk_send(peer_id, f"✅ Приветственный пост отправлен в чат {target_peer}")

    elif low.startswith("/chat_post") or low.startswith("chat_post "):
        import os
        vk_admin_ids = [int(x) for x in os.getenv("VK_ADMIN_IDS", "772577887").split(",") if x.strip().isdigit()]
        if peer_id not in vk_admin_ids:
            await _vk_send(peer_id, "⛔ Нет доступа")
            return
        parts = text.split()
        target_peer = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if not target_peer:
            await _vk_send(peer_id, "Использование: <code>/chat_post PEER_ID</code>")
            return
        from vk_chat_poster import _post_to_chat
        await _post_to_chat(target_peer)
        await _vk_send(peer_id, f"✅ Данные по топливу отправлены в чат {target_peer}")

    elif low.startswith("/chat_list"):
        import os
        from vk_chat_poster import VK_CHAT_PEER_IDS
        vk_admin_ids = [int(x) for x in os.getenv("VK_ADMIN_IDS", "772577887").split(",") if x.strip().isdigit()]
        if peer_id not in vk_admin_ids:
            await _vk_send(peer_id, "⛔ Нет доступа")
            return
        if VK_CHAT_PEER_IDS:
            chats = "\n".join(f"• {c}" for c in VK_CHAT_PEER_IDS)
            await _vk_send(peer_id, f"📋 VK Chat IDs:\n{chats}\n\nНастройка: <code>VK_CHAT_PEER_IDS={','.join(str(c) for c in VK_CHAT_PEER_IDS)}</code>")
        else:
            await _vk_send(peer_id, "⚠️ VK_CHAT_PEER_IDS не задан.\n\nЧтобы добавить чат:\n1. Добавь бота в чат\n2. Узнай peer_id чата (напиши боту /my_peer)\n3. Задай в Render: VK_CHAT_PEER_IDS=peer_id")

    elif low in ("/my_peer", "my_peer"):
        await _vk_send(peer_id, f"Твой peer_id: <code>{peer_id}</code>\n\nДля чата: peer_id > 2000000000")

    else:
        # Контекстный ввод
        state = _get_state(peer_id)
        if state.get("awaiting") == "city_input":
            _clear_state(peer_id)
            await handle_text_search(peer_id, text)
        elif state.get("flow") == "report" and state.get("awaiting") in ("price", "limit", "queue"):
            await handle_report_extras_text(peer_id, text)
        elif state.get("flow") == "route_search" and state.get("step") == "waiting_query":
            await handle_route_search_text(peer_id, text)
        elif state.get("flow") == "anti_traffic" and state.get("step") == "waiting_from":
            await handle_anti_traffic_from(peer_id, text)
        elif state.get("flow") == "anti_traffic" and state.get("step") == "waiting_to":
            await handle_anti_traffic_to(peer_id, text)
        elif state.get("flow") == "report" and state.get("step") == "choose_station":
            # Поиск АЗС для отчёта
            stations = await find_stations_by_name(text, limit=5)
            if not stations:
                stations = await find_stations_by_address(text, limit=5)
            if not stations:
                await _vk_send(peer_id, f"😔 По «{text}» АЗС не найдено.", vk_main_menu())
                return
            # Показываем первую
            await show_station(peer_id, stations[0])
        elif state.get("awaiting") == "link_tg_url":
            _clear_state(peer_id)
            await handle_link(peer_id, f"link {text.strip()}")
        else:
            await handle_text_search(peer_id, text)


# === Callback event router ===
async def process_message_event(event: dict) -> None:
    """Обрабатывает message_event (нажатие inline-кнопки)."""
    obj = event.get("object", {})

    # ДИАГНОСТИКА: логируем весь object чтобы понять структуру
    logger.info("VK msg_event raw object: %s", json.dumps(obj, ensure_ascii=False)[:500])

    peer_id = obj.get("peer_id", 0)
    user_id = obj.get("user_id", 0)
    event_id = obj.get("event_id", "")
    payload_str = obj.get("payload", "")
    conversation_msg_id = obj.get("conversation_message_id", 0)

    if not peer_id or not event_id:
        logger.warning("VK msg_event: missing peer_id or event_id. obj=%s", obj)
        return

    # СРАЗУ отвечаем VK (event_answer обязателен в течение 5 сек)
    # Это ДО любой обработки — чтобы кнопка не грузилась бесконечно
    try:
        await _vk_send_event_answer(event_id, user_id, peer_id, toast="⏳")
    except Exception as e:
        logger.warning("event_answer failed: %s", e)

    # Дедупликация по event_id
    if event_id in _processed_events:
        return
    _processed_events[event_id] = time.time()
    # Очистка старых
    now = time.time()
    for k in list(_processed_events.keys()):
        if now - _processed_events[k] > _EVENT_DEDUP_TTL * 2:
            _processed_events.pop(k, None)

    # Парсим payload (может быть строкой или dict)
    payload = {}
    if isinstance(payload_str, dict):
        payload = payload_str
    elif isinstance(payload_str, str) and payload_str:
        try:
            payload = json.loads(payload_str)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("VK msg_event: failed to parse payload=%r: %s", payload_str, e)
            payload = {}
    action = payload.get("a", "")
    logger.info("[vk-cb-evt] peer=%d action=%r payload=%r", peer_id, action, payload)

    # Регистрация пользователя
    await _ensure_user(peer_id)

    # Сразу отвечаем (event_answer обязателен в течение 5 сек)
    # Если ответ не пройдёт — не страшно, главное отправить сообщение
    # ack_ok = await _vk_send_event_answer(  # УЖЕ ОТПРАВЛЕНО В НАЧАЛЕ
    #     event_id, user_id, peer_id,
    #     toast="⏳",
    # )
    # logger.info("[vk-cb-evt] peer=%d action=%r ack_ok=%s", peer_id, action, ack_ok)

    # === Роутер по action ===
    # Пустой action = VK прислал message_event без payload (например, для
    # кнопок без callback data или internal UI events). Трактуем как "home".
    if not action:
        logger.info("[vk-cb-router] empty action, treating as 'home'")
        action = "home"

    logger.info("[vk-cb-router] entering router with action=%r", action)
    if action == "home":
        _clear_state(peer_id)
        await _vk_send(peer_id, "Главное меню:", vk_main_menu())

    elif action == "accept_legal":
        # Принятие юридических документов
        try:
            import aiohttp
            backend = settings.BACKEND_URL
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{backend}/api/user/accept-legal",
                    json={"vk_user_id": user_id, "version": "2026-07-21"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    resp = await r.json()
            if resp and resp.get("ok"):
                await _vk_send(peer_id,
                    "🎉 <b>Документы приняты!</b>\n\n"
                    "Добро пожаловать в «Бензин рядом»! Можешь пользоваться ботом 👇",
                    vk_main_menu())
            else:
                await _vk_send(peer_id, f"❌ Ошибка: {resp.get('error', 'неизвестно')}", vk_main_menu())
        except Exception as e:
            await _vk_send(peer_id, f"❌ Ошибка соединения: {e}", vk_main_menu())

    elif action == "find":
        logger.info("[vk-cb-router] calling handle_find for peer=%d", peer_id)
        await handle_find(peer_id)

    elif action == "help":
        await handle_help(peer_id)

    elif action == "route_search":
        await handle_route_search_start(peer_id)

    elif action == "anti_traffic":
        await handle_anti_traffic_start(peer_id)

    elif action == "sos":
        await handle_sos(peer_id)

    elif action == "route_more":
        q = payload.get("q", "")
        if q:
            await handle_route_search_text(peer_id, q)

    elif action == "profile":
        await handle_profile(peer_id)

    elif action == "premium":
        await handle_premium(peer_id)

    elif action == "trial":
        # Кнопка "Попробовать бесплатно" — 3 дня Standard
        from db import get_user_id_by_vk_id, activate_trial, is_premium
        uid = await get_user_id_by_vk_id(peer_id)
        if not uid:
            await _vk_send(peer_id, "Сначала напиши /start", vk_main_menu())
            return
        if await is_premium(uid):
            await _vk_send(peer_id, "✅ У тебя уже есть Premium. Используй «Premium» в меню.", vk_main_menu())
            return
        result = await activate_trial(uid, tier="standard", days=3)
        if result.get("ok"):
            await _vk_send(peer_id,
                "🎁 <b>Trial Premium активирован!</b>\n\n"
                "📅 На 3 дня (до " + str(result.get("expires_at", ""))[:10] + ")\n"
                "💎 Тариф: Стандарт\n\n"
                "Что попробовать:\n"
                "1️⃣ Маршрут A→B с ценами\n"
                "2️⃣ Прогноз цен на 7 дней\n"
                "3️⃣ Топливный будильник\n\n"
                "Если понравится — выбери «Premium» в меню.",
                vk_main_menu())
        elif result.get("error") == "already_used":
            await _vk_send(peer_id, "⚠️ Ты уже использовал trial раньше. Оформи Premium через меню.", vk_main_menu())
        else:
            await _vk_send(peer_id, f"❌ Ошибка: {result.get('error', 'неизвестно')}", vk_main_menu())

    elif action == "link":
        await handle_link(peer_id, "link")

    elif action == "link_tg_prompt":
        _set_state(peer_id, {"awaiting": "link_tg_url"})
        await _vk_send(peer_id,
            "🔗 <b>Введи ссылку на TG профиль</b>\n\n"
            "Формат: <code>t.me/username</code>\n"
            "Или просто <code>@username</code>",
        )

    elif action == "alarm":
        await handle_alarm(peer_id)

    elif action == "referral":
        await handle_referral(peer_id)

    elif action == "leaderboard":
        await handle_leaderboard(peer_id)

    elif action == "donate":
        await handle_donate(peer_id)

    elif action == "owner":
        await handle_owner_info(peer_id)

    elif action == "subscribe":
        await handle_subscribe(peer_id)

    elif action == "check_sub":
        # Принудительная перепроверка подписки
        _vk_subscribe_cache.pop(peer_id, None)
        is_sub = await _check_vk_subscription(peer_id)
        if is_sub:
            await _vk_send(peer_id, "✅ Спасибо! Подписка подтверждена.", vk_main_menu())
        else:
            await _vk_send(peer_id, "❌ Не вижу подписки. Подпишись и нажми ещё раз.", _vk_subscribe_keyboard())

    elif action == "city":
        city = payload.get("c", "")
        if city:
            _set_state(peer_id, {"flow": "search", "city": city})
            # Показываем фильтр по типу топлива ПЕРЕД поиском
            await _vk_send(
                peer_id,
                f"📍 <b>{city}</b>\n\n"
                f"Выбери тип топлива для поиска или «Все АЗС» для просмотра всех заправок:",
                vk_fuel_filter_keyboard(city),
            )

    elif action == "city_fuel":
        city = payload.get("c", "")
        fuel = payload.get("f", "all")
        if city:
            _set_state(peer_id, {"flow": "search", "city": city, "fuel": fuel})
            await handle_text_search(peer_id, city, fuel=fuel)

    elif action == "city_price":
        city = payload.get("c", "")
        if city:
            state = _get_state(peer_id)
            fuel = state.get("fuel") if state else None
            await _vk_send(
                peer_id,
                f"💰 <b>Фильтр по цене для {city}</b>{f' (АИ-{fuel})' if fuel else ''}:\n\n"
                f"Выбери максимальную цену за литр:",
                vk_price_filter_keyboard(city, fuel),
            )

    elif action == "city_price_set":
        city = payload.get("c", "")
        max_price = payload.get("p", 0)
        fuel = payload.get("f") or None
        if city:
            _set_state(peer_id, {"flow": "search", "city": city, "fuel": fuel, "max_price": max_price})
            await handle_text_search(peer_id, city, fuel=fuel, max_price=max_price if max_price else None)

    elif action == "city_net":
        city = payload.get("c", "")
        if city:
            state = _get_state(peer_id)
            fuel = state.get("fuel") if state else None
            await _vk_send(
                peer_id,
                f"🏪 <b>Фильтр по сети АЗС для {city}</b>:\n\n"
                f"Выбери сеть или «Любая сеть»:",
                vk_network_filter_keyboard(city, fuel),
            )

    elif action == "city_net_set":
        city = payload.get("c", "")
        network = payload.get("n", "")
        fuel = payload.get("f") or None
        if city:
            _set_state(peer_id, {"flow": "search", "city": city, "fuel": fuel, "network": network})
            await handle_text_search(peer_id, city, fuel=fuel, network=network if network else None)

    elif action == "city_emergency":
        city = payload.get("c", "")
        if city:
            _set_state(peer_id, {"flow": "search", "city": city})
            await handle_text_search(peer_id, city, fuel=None)

    elif action == "city_page":
        city = payload.get("c", "")
        page = payload.get("p", 0)
        fuel = payload.get("f", "all")
        max_price = payload.get("mp", 0) or None
        network = payload.get("n", "") or None
        if city:
            await handle_text_search(peer_id, city, fuel=fuel, max_price=max_price, network=network, page=page)

    elif action == "city_input":
        _set_state(peer_id, {"awaiting": "city_input"})
        await _vk_send(peer_id, "✏️ Напиши название города:", vk_main_menu())

    elif action == "report_start":
        await handle_report_start(peer_id)

    elif action == "report":
        station_id = payload.get("s")
        if station_id:
            await handle_report_fuel(peer_id, int(station_id), "")

    elif action == "report_fuel":
        station_id = payload.get("s")
        fuel = payload.get("f")
        if station_id and fuel:
            await handle_report_fuel(peer_id, int(station_id), fuel)

    elif action == "report_status":
        station_id = payload.get("s")
        fuel = payload.get("f")
        value = payload.get("v")
        if station_id and fuel and value:
            await handle_report_status(peer_id, int(station_id), fuel, value)

    elif action == "report_price":
        station_id = payload.get("s")
        fuel = payload.get("f")
        if station_id and fuel:
            await handle_report_price_only(peer_id, int(station_id), fuel)

    elif action == "report_extra":
        station_id = payload.get("s")
        fuel = payload.get("f")
        status = payload.get("st")
        extra_type = payload.get("e")
        if station_id and fuel and status and extra_type:
            await handle_report_extra(peer_id, int(station_id), fuel, status, extra_type)

    elif action == "report_save":
        station_id = payload.get("s")
        fuel = payload.get("f")
        status = payload.get("st")
        if station_id and fuel and status:
            await handle_report_save(peer_id, int(station_id), fuel, status)

    elif action == "review":
        station_id = payload.get("s")
        if station_id:
            await handle_review_start(peer_id, int(station_id))

    elif action == "review_fuel":
        station_id = payload.get("s")
        fuel = payload.get("f")
        if station_id and fuel:
            await handle_review_fuel(peer_id, int(station_id), fuel)

    elif action == "review_rating":
        station_id = payload.get("s")
        fuel = payload.get("f")
        rating = payload.get("r")
        if station_id and fuel and rating:
            await handle_review_rating(peer_id, int(station_id), fuel, int(rating))

    elif action == "sub_station":
        station_id = payload.get("s")
        if station_id:
            await handle_subscribe_station(peer_id, int(station_id))

    elif action == "sub_radius":
        radius = payload.get("r", 5)
        # Обновляем radius последней geo-подписки
        user_id = await _get_user_id(peer_id)
        if user_id:
            try:
                from db import _execute
                if db.USE_SQLITE:
                    await _execute(
                        "UPDATE subscriptions SET radius_km = ? WHERE user_id = ? AND kind = 'geo' ORDER BY id DESC LIMIT 1",
                        radius, user_id,
                    )
                else:
                    async with db._db.acquire() as conn:
                        await conn.execute(
                            "UPDATE subscriptions SET radius_km = $1 WHERE user_id = $2 AND kind = 'geo' ORDER BY id DESC LIMIT 1",
                            radius, user_id,
                        )
            except Exception as e:
                logger.warning("update sub radius: %s", e)
        await _vk_send(peer_id, f"✅ Радиус подписки обновлён: {radius} км", vk_main_menu())

    elif action == "station":
        # Возврат к карточке АЗС
        station_id = payload.get("s")
        if station_id:
            station = await get_station_by_id(int(station_id))
            if station:
                await show_station(peer_id, station)

    elif action == "selling_texts":
        await handle_selling_texts(peer_id)

    elif action == "training":
        await handle_training(peer_id)

    elif action == "leaderboard":
        await handle_leaderboard(peer_id)

    elif action == "open_app":
        import os
        direct_url = os.getenv("VK_MINI_APP_DIRECT_URL", f"{settings.BACKEND_URL}/v2")
        separator = "&" if "?" in direct_url else "?"
        app_url = f"{direct_url}{separator}vk_user_id={peer_id}"
        await _vk_send(peer_id, f"👉 Открой приложение:\n{app_url}", vk_main_menu())

    elif action == "broadcast":
        # VK broadcast — admin only
        from db import get_all_vk_user_ids, get_broadcast_stats
        from config import settings as _settings

        # Check admin (same as TG admin)
        ADMIN_VK_IDS = [772577887]  # darkt30's TG ID used as reference
        # For VK, we check if the user is in the admin list via TG ID lookup
        # Since VK peer_id != TG ID, we need another approach
        # We'll use a simple VK admin list from env
        import os
        vk_admin_ids = [int(x) for x in os.getenv("VK_ADMIN_IDS", "772577887").split(",") if x.strip().isdigit()]

        if peer_id not in vk_admin_ids:
            await _vk_send(peer_id, "⛔ Нет доступа", vk_main_menu())
            return

        user_ids = await get_all_vk_user_ids()
        if not user_ids:
            await _vk_send(peer_id, "⚠️ Нет VK пользователей для рассылки", vk_main_menu())
            return

        # The text is in the payload, e.g. {"a": "broadcast", "text": "..."}
        broadcast_text = payload.get("text", "")
        if not broadcast_text:
            stats = await get_broadcast_stats()
            await _vk_send(peer_id,
                f"📢 <b>Рассылка VK</b>\n\n"
                f"👥 VK юзеров: {stats['vk']}\n"
                f"👥 TG юзеров: {stats['tg']}\n"
                f"📊 Всего: {stats['total']}\n\n"
                "Для отправки текста используй TG бот:\n"
                "<code>/broadcast текст</code>",
                vk_admin_keyboard())
            return

        sent = 0
        failed = 0
        for uid in user_ids:
            try:
                await _vk_send(uid, broadcast_text)
                sent += 1
            except Exception:
                failed += 1
            import asyncio as _aio
            await _aio.sleep(0.1)

        await _vk_send(peer_id, f"✅ VK рассылка: {sent} отправлено, {failed} ошибок", vk_admin_keyboard())

    else:
        logger.warning("Unknown action: %r", action)
