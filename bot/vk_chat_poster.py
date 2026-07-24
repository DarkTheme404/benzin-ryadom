"""
Worker для автопубликации данных по топливу в VK чаты.
Каждые 2 часа публикует актуальные данные по ценам и наличию.
"""
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

VK_CHAT_PEER_IDS = [
    int(x.strip()) for x in os.getenv("VK_CHAT_PEER_IDS", "").split(",") if x.strip().isdigit()
]

CHAT_POST_INTERVAL = 7200  # 2 часа
CHAT_CITIES = ["Шарья", "Кострома"]  # города для постов


async def vk_chat_poster_loop() -> None:
    """Главный цикл: каждые 2 часа публикует данные в VK чаты."""
    if not VK_CHAT_PEER_IDS:
        logger.info("VK Chat Poster: VK_CHAT_PEER_IDS не задан, пропускаем")
        return

    logger.info("VK Chat Poster started, targets: %s", VK_CHAT_PEER_IDS)
    while True:
        try:
            for peer_id in VK_CHAT_PEER_IDS:
                await _post_to_chat(peer_id)
        except Exception as e:
            logger.exception("VK Chat Poster iteration failed: %s", e)
        await asyncio.sleep(CHAT_POST_INTERVAL)


async def _post_to_chat(peer_id: int) -> None:
    """Публикует данные по топливу в VK чат."""
    from db import find_stations_by_city

    for city in CHAT_CITIES:
        try:
            stations = await find_stations_by_city(city)
        except Exception as e:
            logger.error("VK Chat Poster: db error for %s: %s", city, e)
            continue

        if not stations:
            continue

        available = sum(1 for s in stations if (s.get("status") or "") == "available")
        limited = sum(1 for s in stations if (s.get("status") or "") == "limited")
        unavailable = sum(1 for s in stations if (s.get("status") or "") == "unavailable")

        fuel_prices = {}
        for s in stations:
            prices = s.get("prices") or {}
            for fuel, price in prices.items():
                if price and (fuel not in fuel_prices or price < fuel_prices[fuel]):
                    fuel_prices[fuel] = price

        lines = [f"⛽ <b>Обновление — {city}</b>\n"]

        if fuel_prices:
            fuel_names = {"92": "АИ-92", "95": "АИ-95", "98": "АИ-98", "100": "АИ-100", "diesel": "ДТ", "gas": "Газ"}
            for fuel_key in ["92", "95", "98", "diesel", "gas"]:
                price = fuel_prices.get(fuel_key)
                if price:
                    name = fuel_names.get(fuel_key, fuel_key)
                    lines.append(f"• {name}: <b>{price} ₽</b>")
            lines.append("")

        status_lines = []
        if available:
            status_lines.append(f"🟢 есть: {available}")
        if limited:
            status_lines.append(f"🟡 мало: {limited}")
        if unavailable:
            status_lines.append(f"🔴 нет: {unavailable}")
        if status_lines:
            lines.append("Статус АЗС: " + " · ".join(status_lines))
            lines.append("")

        lines.append(f"📊 АЗС: {len(stations)} · Данные от водителей")
        lines.append("📱 Telegram: @benzyn_ryadom_bot")

        from vk_callback import _vk_send, format_for_vk
        try:
            await _vk_send(peer_id, "\n".join(lines))
            logger.info("VK Chat Poster: posted %s to peer=%d", city, peer_id)
        except Exception as e:
            logger.error("VK Chat Poster: send failed peer=%d: %s", peer_id, e)
