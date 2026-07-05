#!/usr/bin/env python3
"""Парсер данных с gdebenz.ru API — краудсорсинговая карта наличия топлива.

API: https://gdebenz.ru/api/stations?lat1=...&lon1=...&lat2=...&lon2=...

Возвращает станции с real-time статусами наличия от водителей.
"""

import asyncio
import sys
import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Области поиска: 15 главных городов (меньше = нет rate limit)
SEARCH_AREAS = [
    # Москва
    {"name": "Москва", "lat1": 55.50, "lon1": 37.30, "lat2": 55.95, "lon2": 37.85},
    # Санкт-Петербург
    {"name": "СПб", "lat1": 59.80, "lon1": 30.10, "lat2": 60.10, "lon2": 30.50},
    # Краснодар
    {"name": "Краснодар", "lat1": 44.90, "lon1": 38.80, "lat2": 45.10, "lon2": 39.10},
    # Новосибирск
    {"name": "Новосибирск", "lat1": 54.80, "lon1": 82.80, "lat2": 55.10, "lon2": 83.10},
    # Екатеринбург
    {"name": "Екатеринбург", "lat1": 56.70, "lon1": 60.40, "lat2": 56.95, "lon2": 60.70},
    # Казань
    {"name": "Казань", "lat1": 55.70, "lon1": 49.00, "lat2": 55.90, "lon2": 49.30},
    # Самара
    {"name": "Самара", "lat1": 53.10, "lon1": 50.00, "lat2": 53.35, "lon2": 50.30},
    # Нижний Новгород
    {"name": "Нижний", "lat1": 56.25, "lon1": 43.80, "lat2": 56.40, "lon2": 44.10},
    # Ростов-на-Дону
    {"name": "Ростов", "lat1": 47.15, "lon1": 39.55, "lat2": 47.35, "lon2": 39.85},
    # Воронеж
    {"name": "Воронеж", "lat1": 51.60, "lon1": 39.10, "lat2": 51.75, "lon2": 39.30},
    # Красноярск
    {"name": "Красноярск", "lat1": 55.95, "lon1": 92.70, "lat2": 56.10, "lon2": 93.00},
    # Уфа
    {"name": "Уфа", "lat1": 54.65, "lon1": 55.85, "lat2": 54.85, "lon2": 56.15},
    # Волгоград
    {"name": "Волгоград", "lat1": 48.60, "lon1": 44.30, "lat2": 48.85, "lon2": 44.60},
    # Саратов
    {"name": "Саратов", "lat1": 51.45, "lon1": 45.90, "lat2": 51.60, "lon2": 46.10},
    # Тольятти
    {"name": "Тольятти", "lat1": 53.45, "lon1": 49.30, "lat2": 53.60, "lon2": 49.55},
]


async def fetch_stations(area: dict, session: aiohttp.ClientSession) -> list:
    """Загружает станции для области из gdebenz.ru API."""
    try:
        url = f"https://gdebenz.ru/api/stations?lat1={area['lat1']}&lon1={area['lon1']}&lat2={area['lat2']}&lon2={area['lon2']}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30),
                               headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
            else:
                logger.warning(f"HTTP {resp.status} for {area['name']}")
                return []
    except Exception as e:
        logger.error(f"Failed to fetch {area['name']}: {e}")
        return []


async def find_or_create_station(station_data: dict) -> Optional[int]:
    """Находит или создаёт станцию по данным gdebenz.ru."""
    osm_id = station_data.get("osm_id", "")
    name = station_data.get("name", "АЗС")
    brand = station_data.get("brand", "")
    lat = station_data.get("lat")
    lon = station_data.get("lon")

    if not lat or not lon:
        return None

    # Ищем по названию + координатам
    existing = await db._fetch(
        """SELECT id FROM stations WHERE name LIKE ? AND ABS(lat - ?) < 0.01 AND ABS(lon - ?) < 0.01 LIMIT 1""",
        f"%{name}%", lat, lon
    )
    if existing:
        return existing[0]["id"]

    # Создаём новую станцию
    city = ""
    # Определяем город по координатам (упрощённо)
    for area in SEARCH_AREAS:
        if (area["lat1"] <= lat <= area["lat2"] and
                area["lon1"] <= lon <= area["lon2"]):
            city = area["name"]
            break

    cursor = await db._execute(
        """INSERT INTO stations (name, brand, city, lat, lon, address)
           VALUES (?, ?, ?, ?, ?, ?)""",
        name, brand, city, lat, lon, station_data.get("addr", ""),
        returning=True
    )
    return cursor


async def save_reports(stations_data: list, area_name: str):
    """Сохраняет отчёты о наличии топлива."""
    saved = 0
    for s in stations_data:
        station_id = await find_or_create_station(s)
        if not station_id:
            continue

        status = s.get("status", "")
        fuels_now = s.get("fuels_now", "")
        conflict = s.get("conflict")

        # Определяем availability
        if status == "yes":
            available = True
        elif status == "no":
            available = False
        elif status == "queue":
            available = True  # очередь = станция работает
        else:
            available = True  # неизвестно = предполагаем доступность

        # Парсим типы топлива
        fuel_types = []
        if fuels_now:
            for ft in fuels_now.split(","):
                ft = ft.strip()
                if ft:
                    fuel_types.append(ft)

        if not fuel_types:
            # Сохраняем общий статус станции без конкретного типа топлива
            message = f"[gdebenz.ru] {area_name}: status={status}"
            if conflict:
                message += f" | конфликт данных"
            
            # Проверяем дубликаты
            if db.USE_SQLITE:
                existing = await db._fetch(
                    """SELECT id FROM reports
                       WHERE station_id=? AND source='gdebenz'
                       AND created_at > datetime('now', '-1 hour') LIMIT 1""",
                    station_id
                )
            else:
                existing = await db._fetch(
                    """SELECT id FROM reports
                       WHERE station_id=$1 AND source='gdebenz'
                       AND created_at > NOW() - INTERVAL '1 hour' LIMIT 1""",
                    station_id
                )
            if not existing:
                await db._execute(
                    """INSERT INTO reports (station_id, fuel_type, available, source, created_at, comment)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    station_id,
                    "all",
                    available,
                    "gdebenz",
                    datetime.now(timezone.utc).isoformat(),
                    message[:500],
                )
                saved += 1
            continue

        for fuel_type in fuel_types:
            # Нормализуем тип топлива
            fuel_map = {
                "92": "92", "95": "95", "98": "98", "100": "100",
                "ДТ": "diesel", "дт": "diesel",
                "газ": "lpg", "Газ": "lpg", "LPG": "lpg",
            }
            normalized_fuel = fuel_map.get(fuel_type, fuel_type)

            # Проверяем дубликаты
            if db.USE_SQLITE:
                existing = await db._fetch(
                    """SELECT id FROM reports
                       WHERE station_id=? AND fuel_type=? AND source='gdebenz'
                       AND created_at > datetime('now', '-2 hours') LIMIT 1""",
                    station_id, normalized_fuel
                )
            else:
                existing = await db._fetch(
                    """SELECT id FROM reports
                       WHERE station_id=$1 AND fuel_type=$2 AND source='gdebenz'
                       AND created_at > NOW() - INTERVAL '2 hours' LIMIT 1""",
                    station_id, normalized_fuel
                )
            if existing:
                continue

            message = f"[gdebenz.ru] {area_name}: {status}"
            if fuels_now:
                message += f" | есть: {fuels_now}"
            if conflict:
                message += f" | конфликт данных"

            await db._execute(
                """INSERT INTO reports (station_id, fuel_type, available, source, created_at, comment)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                station_id,
                normalized_fuel,
                available,
                "gdebenz",
                datetime.now(timezone.utc).isoformat(),
                message[:500],
            )
            saved += 1

    return saved


async def main():
    import os
    if not db.API_MODE:
        await db.init_db()
    await db.stale_old_reports("gdebenz")

    total_saved = 0
    async with aiohttp.ClientSession() as session:
        for i, area in enumerate(SEARCH_AREAS):
            logger.info(f"Fetching {area['name']}...")
            stations = await fetch_stations(area, session)
            if stations:
                count = await save_reports(stations, area["name"])
                total_saved += count
                logger.info(f"  {area['name']}: {len(stations)} stations, {count} reports saved")
            else:
                logger.warning(f"  {area['name']}: no data")
            # Задержка чтобы не получить 502 от rate limiter
            if i < len(SEARCH_AREAS) - 1:
                await asyncio.sleep(2)

    logger.info(f"\n=== Total gdebenz reports saved: {total_saved} ===")
    import os
    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
