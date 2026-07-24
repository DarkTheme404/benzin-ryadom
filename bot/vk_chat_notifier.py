"""
Нотификатор для VK чата — отправляет обновления когда появляется новая информация
по отслеживаемым городам (Шарья, Кострома и др.).

Вызывается из add_report() как fire-and-forget задача.
"""
import asyncio
import logging
import os
import time

logger = logging.getLogger(__name__)

# Города для отслеживания (нормализованные к нижнему регистру)
WATCHED_CITIES = {
    "шарья",
    "кострома",
}

# Интервал дедупликации — не пушить одно и то же чаще чем раз в N секунд
DEDUP_INTERVAL = 300  # 5 минут на станцию+топливо

# Кеш: (station_id, fuel_type) -> timestamp последней нотификации
_notify_cache: dict[tuple, float] = {}

# VK chat peer IDs из env
_VK_CHAT_PEER_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("VK_CHAT_PEER_IDS", "").split(",") if x.strip().isdigit()
]


def is_watched_city(city: str | None) -> bool:
    """Проверяет, отслеживается ли город."""
    if not city:
        return False
    return city.strip().lower() in WATCHED_CITIES


async def notify_new_report(
    station_id: int,
    fuel_type: str,
    available: bool | None,
    price: float | None,
    source: str,
    station_city: str | None = None,
    station_name: str | None = None,
) -> None:
    """Отправляет уведомление в VK чат если станция в отслеживаемом городе.

    Fire-and-forget: вызывается из add_report, не блокирует основной поток.
    """
    if not _VK_CHAT_PEER_IDS:
        return

    if not is_watched_city(station_city):
        return

    # Дедупликация: не пушить одно и то же чаще чем раз в 5 мин
    cache_key = (station_id, fuel_type or "all")
    now = time.time()
    last = _notify_cache.get(cache_key, 0)
    if now - last < DEDUP_INTERVAL:
        return
    _notify_cache[cache_key] = now

    # Чистим старые записи из кеша
    if len(_notify_cache) > 500:
        cutoff = now - 3600
        stale = [k for k, v in _notify_cache.items() if v < cutoff]
        for k in stale:
            _notify_cache.pop(k, None)

    # Формируем сообщение
    fuel_names = {
        "92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100",
        "diesel": "ДТ", "dt": "ДТ", "gas": "Газ", "lpg": "Газ", "all": "Топливо",
    }
    fuel_label = fuel_names.get(fuel_type, fuel_type)

    if available is True:
        status = "✅ Есть"
    elif available is False:
        status = "❌ Нет"
    else:
        status = "⚠️ Кончается"

    name = station_name or f"АЗС #{station_id}"
    price_str = f" — {price}₽" if price else ""

    text = (
        f"⛽ <b>{station_city}</b> — обновление\n\n"
        f"<b>{name}</b>\n"
        f"{fuel_label}: {status}{price_str}\n"
        f"Источник: {source}"
    )

    # Отправляем в каждый чат
    for peer_id in _VK_CHAT_PEER_IDS:
        try:
            from vk_callback import _vk_send, format_for_vk
            await _vk_send(peer_id, format_for_vk(text))
            logger.info("VK chat notify: %s/%s → peer=%d", station_city, fuel_label, peer_id)
        except Exception as e:
            logger.error("VK chat notify failed: peer=%d: %s", peer_id, e)
