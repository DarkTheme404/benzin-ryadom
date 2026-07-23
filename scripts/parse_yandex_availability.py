"""
Yandex Maps Availability Parser — определяет статус АЗС (открыта/закрыта)
через Yandex Maps Search API.

Требует YANDEX_MAPS_API_KEY (бесплатный тир: 1000 запросов/день).

Использование:
    python scripts/parse_yandex_availability.py
    python scripts/parse_yandex_availability.py --limit 50
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("yandex_availability")

SOURCE = "yandex_availability"
YANDEX_MAPS_API = "https://search-maps.yandex.ru/v1/"

# Города для запросов (крупные + средние, покрывающие все регионы)
# lat, lon, search_text
CITIES_SEARCH = [
    # ЦФО
    (55.7558, 37.6173, "АЗС"),  # Москва
    (59.9343, 30.3351, "АЗС"),  # СПб
    (52.6139, 39.5958, "АЗС"),  # Липецк
    (54.1961, 37.6185, "АЗС"),  # Тула
    (54.7826, 32.0453, "АЗС"),  # Смоленск
    (53.2521, 34.3717, "АЗС"),  # Брянск
    (51.7304, 36.1929, "АЗС"),  # Курск
    (50.5957, 36.5873, "АЗС"),  # Белгород
    (51.6720, 39.1843, "АЗС"),  # Воронеж
    (54.6296, 21.0122, "АЗС"),  # Калининград
    (56.8587, 35.9176, "АЗС"),  # Тверь
    (57.6261, 39.8845, "АЗС"),  # Ярославль
    (58.5236, 31.2731, "АЗС"),  # Великий Новгород
    (56.3268, 44.0075, "АЗС"),  # Нижний Новгород
    (56.1322, 40.4066, "АЗС"),  # Владимир
    (54.3197, 48.3963, "АЗС"),  # Ульяновск
    (53.1945, 45.0131, "АЗС"),  # Пенза
    (51.5336, 46.0342, "АЗС"),  # Саратов
    (48.7080, 44.5133, "АЗС"),  # Волгоград
    (46.3498, 48.0408, "АЗС"),  # Астрахань
    (53.4085, 50.1135, "АЗС"),  # Самара
    (54.3180, 48.6069, "АЗС"),  # Ульяновск
    # ПФО
    (55.7963, 49.1082, "АЗС"),  # Казань
    (56.8519, 60.6122, "АЗС"),  # Екатеринбург
    (55.1644, 61.4368, "АЗС"),  # Челябинск
    (54.9914, 73.3645, "АЗС"),  # Омск
    (56.8389, 60.6057, "АЗС"),  # Екатеринбург (dup ok)
    (51.7681, 55.0968, "АЗС"),  # Оренбург
    (54.1931, 45.1840, "АЗС"),  # Саранск
    (56.0153, 40.4157, "АЗС"),  # Владимир (dup)
    (57.9900, 56.2390, "АЗС"),  # Пермь
    (58.0105, 56.2502, "АЗС"),  # Пермь
    (53.7210, 87.1200, "АЗС"),  # Кемерово
    # СФО
    (55.0302, 82.9204, "АЗС"),  # Новосибирск
    (53.3548, 83.7693, "АЗС"),  # Барнаул
    (56.0153, 92.8591, "АЗС"),  # Красноярск
    (55.0411, 82.9347, "АЗС"),  # Новосибирск (dup)
    (55.7558, 37.6173, "АЗС"),  # Москва (dup)
    # ДФО
    (48.4827, 135.0837, "АЗС"),  # Хабаровск
    (43.1155, 131.9110, "АЗС"),  # Владивосток
    (62.0355, 129.6755, "АЗС"),  # Якутск
    (53.0320, 158.6510, "АЗС"),  # Петропавловск-Камчатский
    (59.5718, 150.7947, "АЗС"),  # Магадан
    (46.9580, 142.7380, "АЗС"),  # Южно-Сахалинск
    # СКФО
    (45.0355, 38.9753, "АЗС"),  # Краснодар
    (43.6028, 39.7342, "АЗС"),  # Сочи
    (45.0428, 41.9734, "АЗС"),  # Ставрополь
    (43.0248, 44.6820, "АЗС"),  # Владикавказ
    (43.3125, 45.6989, "АЗС"),  # Грозный
    (47.2357, 39.7043, "АЗС"),  # Ростов
    (44.6054, 40.0978, "АЗС"),  # Майкоп
    # СЗФО
    (57.6269, 39.8937, "АЗС"),  # Ярославль (dup)
    (59.9343, 30.3351, "АЗС"),  # СПб (dup)
    (56.8519, 53.2350, "АЗС"),  # Ижевск
    (58.3920, 49.6770, "АЗС"),  # Киров
    (61.7959, 50.8463, "АЗС"),  # Сыктывкар
    # Крым
    (44.9521, 34.1024, "АЗС"),  # Симферополь
    (44.6166, 33.5254, "АЗС"),  # Севастополь
]


async def search_stations(session: aiohttp.ClientSession, lat: float, lon: float,
                          api_key: str) -> list:
    """Ищет АЗС через Yandex Maps Search API."""
    params = {
        "apikey": api_key,
        "text": "АЗС",
        "type": "biz",
        "ll": f"{lon},{lat}",
        "spn": "0.5,0.5",
        "lang": "ru_RU",
        "results": 50,
    }
    try:
        async with session.get(
            YANDEX_MAPS_API, params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning(f"Yandex {resp.status}")
                return []
            data = await resp.json()
            return data.get("features", [])
    except Exception as e:
        logger.error(f"Yandex error: {e}")
        return []


def extract_availability(feature: dict) -> dict | None:
    """Извлекает данные о Availability из Yandex Maps feature."""
    props = feature.get("properties", {})
    company = props.get("CompanyMetaData", {})
    geom = feature.get("geometry", {})
    coords = geom.get("coordinates", [None, None])

    if not coords or not coords[0]:
        return None

    lon, lat = coords[0], coords[1]
    name = company.get("name", "")
    address = company.get("address", "")
    hours = company.get("Hours", {})
    phone = company.get("Phones", [{}])
    categories = company.get("Categories", [])

    # Определяем открыта/закрыта
    is_open = None
    if hours:
        text = hours.get("text", "")
        availabilities = hours.get("Availabilities", [])
        if availabilities:
            today_avail = availabilities[0]
            state = today_avail.get("State", {})
            if "Everyday" in state:
                is_open = True
            elif "Days" in state:
                is_open = True
        # Если есть текст режима работы — считаем что работает
        if text and "закрыто" not in text.lower():
            is_open = True
        elif text and "закрыто" in text.lower():
            is_open = False
    else:
        is_open = True  # нет данных = предполагаем открыто

    # Извлекаем бренд/оператора
    brand = ""
    for cat in categories:
        if cat.get("name"):
            brand = cat["name"]
            break

    return {
        "name": name,
        "brand": brand,
        "lat": lat,
        "lon": lon,
        "address": address,
        "is_open": is_open,
        "opening_hours": hours.get("text", ""),
    }


async def find_db_station(st: dict, tolerance: float = 0.001) -> int | None:
    """Находит станцию в БД по координатам."""
    lat, lon = st["lat"], st["lon"]
    rows = await db._fetch(
        """SELECT id FROM stations
           WHERE ABS(lat - $1) < $2 AND ABS(lon - $3) < $2
           LIMIT 1""",
        lat, tolerance, lon,
    )
    if rows:
        return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]
    return None


async def main():
    if not db.API_MODE:
        await db.init_db()

    parser = argparse.ArgumentParser(description="Yandex Maps Availability Parser")
    parser.add_argument("--limit", type=int, default=0, help="Limit cities (0=all)")
    args = parser.parse_args()

    api_key = os.environ.get("YANDEX_MAPS_API_KEY", "")
    if not api_key:
        logger.error("YANDEX_MAPS_API_KEY not set! Get free key at https://developer.tech.yandex.ru/")
        if not db.API_MODE:
            await db.close_db()
        return

    cities = CITIES_SEARCH[:args.limit] if args.limit > 0 else CITIES_SEARCH
    # Убираем дубликаты
    seen = set()
    unique_cities = []
    for c in cities:
        key = (round(c[0], 2), round(c[1], 2))
        if key not in seen:
            seen.add(key)
            unique_cities.append(c)
    cities = unique_cities

    logger.info(f"Yandex Maps: querying {len(cities)} cities...")

    total_features = 0
    total_saved = 0
    total_matched = 0
    semaphore = asyncio.Semaphore(3)

    async def _search_one(city, sess):
        async with semaphore:
            features = await search_stations(sess, city[0], city[1], api_key)
            await asyncio.sleep(0.5)
            return features

    async with aiohttp.ClientSession() as session:
        tasks = [_search_one(city, session) for city in cities]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_stations = {}
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Search error: {result}")
            continue
        for feature in result:
            st = extract_availability(feature)
            if st:
                key = (round(st["lat"], 4), round(st["lon"], 4))
                all_stations[key] = st
                total_features += 1

    logger.info(f"Yandex features: {total_features}, unique: {len(all_stations)}")

    # Матчим с БД и сохраняем
    now = datetime.now(timezone.utc)
    for key, st in all_stations.items():
        station_id = await find_db_station(st)
        if not station_id:
            continue
        total_matched += 1

        is_open = st.get("is_open", True)
        fuel_types = ["АИ-92", "АИ-95"]  # Яндекс не даёт конкретные типы

        for ft in fuel_types:
            existing = await db._fetch(
                """SELECT id FROM reports
                   WHERE station_id = $1 AND fuel_type = $2 AND source = $3
                   AND created_at > NOW() - INTERVAL '20 hours'
                   LIMIT 1""",
                station_id, ft, SOURCE,
            )
            if existing:
                continue

            comment = f"Яндекс: {st['opening_hours']}" if st.get("opening_hours") else ""
            await db._execute(
                """INSERT INTO reports (station_id, fuel_type, available, source, created_at, expires_at, comment)
                   VALUES ($1, $2, $3, $4, NOW(), NOW() + INTERVAL '22 hours', $5)""",
                station_id, ft, is_open, SOURCE, comment,
            )
            total_saved += 1

    logger.info(f"\n=== YANDEX AVAILABILITY ИТОГО ===")
    logger.info(f"  Cities queried: {len(cities)}")
    logger.info(f"  Features: {total_features}")
    logger.info(f"  Matched to DB: {total_matched}")
    logger.info(f"  Reports saved: {total_saved}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
