"""
OSM Overpass Availability Parser — определяет наличие/отсутствие топлива
по данным OpenStreetMap (opening_hours + fuel:* теги).

Бесплатно, без ключа авторизации.
Покрывает ВСЕ города России через Overpass API.

Использование:
    python scripts/parse_osm_availability.py
    python scripts/parse_osm_availability.py --limit 50   # только 50 bbox
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("osm_availability")

SOURCE = "osm_availability"
OVERPASS_URL = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
OVERPASS_TIMEOUT = 25

# Bounding boxes для России — покрывают всю территорию
# (lat_min, lon_min, lat_max, lon_max)
RUSSIA_BBOXES = [
    # Центральная Россия
    (55.0, 37.0, 56.5, 39.5),   # Москва + МО
    (56.5, 37.0, 58.5, 40.0),   # Владимир, Иваново, Ярославль
    (54.0, 37.0, 55.5, 40.0),   # Тула, Калуга
    (53.0, 35.0, 54.5, 38.0),   # Орёл, Курск
    (51.5, 35.0, 53.0, 38.0),   # Курск, Белгород
    (50.0, 37.0, 52.0, 40.0),   # Воронеж, Белгород
    (51.5, 40.0, 53.5, 43.0),   # Пенза, Саратов
    (53.0, 43.0, 55.0, 46.0),   # Пенза, Ульяновск
    # Поволжье
    (55.5, 48.0, 57.0, 51.0),   # Казань
    (53.0, 49.0, 54.5, 51.0),   # Самара
    (54.0, 55.0, 55.5, 58.0),   # Уфа
    (56.0, 51.0, 57.5, 54.0),   # Ижевск
    (55.5, 46.0, 57.0, 49.0),   # Чебоксары
    (57.5, 55.0, 59.0, 58.0),   # Пермь
    (51.5, 54.0, 53.0, 57.0),   # Оренбург
    (53.0, 43.0, 54.5, 46.0),   # Ульяновск
    (57.5, 59.5, 59.5, 62.0),   # Екатеринбург
    # Урал
    (55.0, 59.5, 57.0, 63.0),   # Челябинск
    (57.0, 65.0, 58.5, 69.0),   # Тюмень
    (54.0, 61.0, 56.0, 65.0),   # Курган
    (57.5, 59.5, 59.0, 62.0),   # Нижний Тагил
    # Западная Сибирь
    (54.5, 82.0, 56.0, 85.0),   # Новосибирск
    (53.0, 83.0, 54.5, 86.0),   # Барнаул
    (55.5, 86.0, 57.0, 89.0),   # Кемерово
    (53.5, 87.0, 55.0, 90.0),   # Новокузнецк
    (56.0, 74.0, 57.5, 77.0),   # Омск
    (56.0, 84.0, 57.5, 87.0),   # Томск
    (59.0, 92.0, 61.0, 96.0),   # Красноярск
    (51.5, 107.0, 53.0, 110.0), # Улан-Удэ
    (52.0, 104.0, 53.5, 107.0), # Иркутск
    (102.0, 52.0, 104.0, 53.5), # Чита
    # ДФО
    (48.0, 131.0, 49.0, 133.0), # Хабаровск
    (42.5, 131.5, 43.5, 133.0), # Владивосток
    (61.5, 129.5, 62.5, 131.0), # Якутск
    (53.0, 158.0, 54.0, 160.0), # Петропавловск-Камчатский
    (59.5, 150.0, 60.5, 152.0), # Магадан
    (46.5, 143.0, 47.5, 144.5), # Южно-Сахалинск
    # Юг
    (44.5, 38.0, 45.5, 40.5),   # Краснодар
    (43.5, 39.5, 44.5, 41.0),   # Сочи
    (45.0, 42.0, 46.0, 44.0),   # Ставрополь
    (42.0, 44.0, 43.5, 46.5),   # Владикавказ
    (43.0, 45.0, 44.0, 47.0),   # Грозный
    (46.0, 47.5, 47.0, 49.5),   # Астрахань
    (48.5, 43.0, 50.0, 45.0),   # Волгоград
    (47.0, 39.5, 48.5, 42.0),   # Ростов
    (45.0, 38.5, 46.5, 40.5),   # Краснодар-восток
    (48.5, 37.5, 49.5, 39.5),   # Донецк
    # Северо-Запад
    (59.5, 29.5, 61.0, 32.0),   # Питер + Ленобласть
    (57.5, 28.0, 59.0, 31.0),   # Псков
    (58.0, 31.0, 59.5, 34.0),   # Великий Новгород
    (59.0, 37.5, 60.5, 40.0),   # Вологда
    (61.0, 38.0, 63.0, 41.0),   # Архангельск
    (62.0, 50.0, 64.0, 54.0),   # Сыктывкар
    (67.5, 32.0, 69.5, 35.0),   # Мурманск
    (54.0, 20.0, 55.5, 22.0),   # Калининград
    # Крым + Севастополь
    (44.5, 33.5, 46.0, 36.5),   # Симферополь
    (44.5, 33.3, 44.8, 33.7),   # Севастополь
]

# Маппинг fuel:* тегов на наши типы топлива
FUEL_TAG_MAP = {
    "fuel:octane_92": "АИ-92",
    "fuel:octane_95": "АИ-95",
    "fuel:octane_98": "АИ-98",
    "fuel:octane_100": "АИ-100",
    "fuel:diesel": "ДТ",
    "fuel:lpg": "Газ",
    "fuel:cng": "Метан",
}


def parse_opening_hours(oh_str: str) -> dict:
    """Парсит строку opening_hours и определяет открыта ли АЗС сейчас.

    Returns: {"is_open": bool, "schedule": str, "raw": str}
    """
    if not oh_str:
        return {"is_open": True, "schedule": "unknown", "raw": ""}

    oh = oh_str.strip().lower()
    now = datetime.now(timezone.utc)

    # Простые паттерны
    if oh in ("24/7", "24/7;"):
        return {"is_open": True, "schedule": "24/7", "raw": oh_str}

    if "closed" in oh:
        return {"is_open": False, "schedule": "closed", "raw": oh_str}

    # День недели → номер (0=Mo, 6=Su)
    day_map = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}
    current_weekday = now.weekday()  # 0=Monday
    current_hour = now.hour
    current_minute = now.minute
    current_time = current_hour * 60 + current_minute

    # Парсим "Mo-Fr 06:00-23:00; Sa-Su 08:00-22:00"
    parts = re.split(r'[;]', oh)
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Проверяем временные диапазоны
        time_match = re.search(r'(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})', part)
        if not time_match:
            continue

        open_time = time_match.group(1)
        close_time = time_match.group(2)
        try:
            oh_min = int(open_time.split(":")[0]) * 60 + int(open_time.split(":")[1])
            ch_min = int(close_time.split(":")[0]) * 60 + int(close_time.split(":")[1])
        except (ValueError, IndexError):
            continue

        # Проверяем дни
        day_part = part[:time_match.start()].strip()
        if not day_part:
            # Нет указания на дни — каждый день
            if oh_min <= current_time <= ch_min:
                return {"is_open": True, "schedule": part, "raw": oh_str}
            continue

        # Парсим диапазон дней "Mo-Fr" или "Sa"
        for token in re.split(r'[,\s]+', day_part):
            token = token.strip().lower()
            if "-" in token:
                tokens = token.split("-", 1)
                start_d = day_map.get(tokens[0].strip()[:2], -1)
                end_d = day_map.get(tokens[1].strip()[:2], -1)
                if start_d <= current_weekday <= end_d:
                    if oh_min <= current_time <= ch_min:
                        return {"is_open": True, "schedule": part, "raw": oh_str}
            elif token[:2] in day_map:
                d = day_map[token[:2]]
                if d == current_weekday:
                    if oh_min <= current_time <= ch_min:
                        return {"is_open": True, "schedule": part, "raw": oh_str}

    return {"is_open": False, "schedule": oh_str, "raw": oh_str}


async def query_overpass(session: aiohttp.ClientSession, bbox: tuple) -> list:
    """Запрашивает все АЗС в bounding box через Overpass API."""
    lat_min, lon_min, lat_max, lon_max = bbox
    query = f"""
[out:json][timeout:{OVERPASS_TIMEOUT}];
(
  node["amenity"="fuel"]({lat_min},{lon_min},{lat_max},{lon_max});
  way["amenity"="fuel"]({lat_min},{lon_min},{lat_max},{lon_max});
  relation["amenity"="fuel"]({lat_min},{lon_min},{lat_max},{lon_max});
);
out center tags;
"""
    try:
        async with session.post(
            OVERPASS_URL,
            data={"data": query},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.warning(f"Overpass {resp.status}: {text[:200]}")
                return []
            data = await resp.json()
            return data.get("elements", [])
    except asyncio.TimeoutError:
        logger.warning(f"Overpass timeout for bbox {bbox}")
        return []
    except Exception as e:
        logger.error(f"Overpass error: {e}")
        return []


def extract_station_data(element: dict) -> dict:
    """Извлекает данные АЗС из OSM element."""
    tags = element.get("tags", {})

    # Координаты
    if element.get("type") == "node":
        lat = element.get("lat")
        lon = element.get("lon")
    else:
        center = element.get("center", {})
        lat = center.get("lat")
        lon = center.get("lon")

    if not lat or not lon:
        return {}

    name = tags.get("name", tags.get("brand", "АЗС"))
    brand = tags.get("brand", "")
    operator = tags.get("operator", "")
    opening_hours = tags.get("opening_hours", "")

    # Определяем открыта/закрыта
    hours_info = parse_opening_hours(opening_hours)

    # Определяем типы топлива
    fuel_types = []
    for tag, fuel_name in FUEL_TAG_MAP.items():
        val = tags.get(tag, "")
        if val.lower() in ("yes", "true", "1", ""):
            # "" = тег есть но без значения = подразумевается
            fuel_types.append(fuel_name)
        elif val.lower() not in ("no", "false", "0"):
            fuel_types.append(fuel_name)

    # Если fuel тегов нет вообще — предполагаем стандартный набор
    if not fuel_types and not any(tags.get(t) for t in FUEL_TAG_MAP):
        fuel_types = ["АИ-92", "АИ-95"]

    address_parts = []
    for key in ("addr:street", "addr:housenumber", "addr:city"):
        if tags.get(key):
            address_parts.append(tags[key])
    address = ", ".join(address_parts) if address_parts else ""

    return {
        "osm_id": element.get("id"),
        "name": name,
        "brand": brand,
        "operator": operator,
        "lat": lat,
        "lon": lon,
        "address": address,
        "opening_hours": opening_hours,
        "is_open": hours_info["is_open"],
        "schedule": hours_info["schedule"],
        "fuel_types": fuel_types,
    }


async def find_db_station(station_data: dict, tolerance: float = 0.001) -> int | None:
    """Находит станцию в БД по координатам (±tolerance градусов ≈ ±100м)."""
    lat, lon = station_data["lat"], station_data["lon"]
    rows = await db._fetch(
        """SELECT id FROM stations
           WHERE ABS(lat - $1) < $2 AND ABS(lon - $3) < $2
           LIMIT 1""",
        lat, tolerance, lon,
    )
    if rows:
        return rows[0]["id"] if isinstance(rows[0], dict) else rows[0][0]
    return None


async def save_osm_reports(stations: list) -> int:
    """Сохраняет отчёты о наличии/отсутствии в БД."""
    saved = 0
    now = datetime.now(timezone.utc)

    for st in stations:
        station_id = await find_db_station(st)
        if not station_id:
            continue

        is_open = st["is_open"]
        fuel_types = st["fuel_types"]

        for ft in fuel_types:
            # Если АЗС открыта → топливо есть
            # Если АЗС закрыта → топлива нет
            available = True if is_open else False

            # Проверяем дубликат
            existing = await db._fetch(
                """SELECT id FROM reports
                   WHERE station_id = $1 AND fuel_type = $2 AND source = $3
                   AND created_at > NOW() - INTERVAL '20 hours'
                   LIMIT 1""",
                station_id, ft, SOURCE,
            )
            if existing:
                continue

            comment = f"OSM: {st['schedule']}" if st["schedule"] != "unknown" else ""
            await db._execute(
                """INSERT INTO reports (station_id, fuel_type, available, source, created_at, expires_at, comment)
                   VALUES ($1, $2, $3, $4, NOW(), NOW() + INTERVAL '22 hours', $5)""",
                station_id, ft, available, SOURCE, comment,
            )
            saved += 1

    return saved


async def main():
    if not db.API_MODE:
        await db.init_db()

    parser = argparse.ArgumentParser(description="OSM Overpass Availability Parser")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of bboxes (0=all)")
    args = parser.parse_args()

    bboxes = RUSSIA_BBOXES[:args.limit] if args.limit > 0 else RUSSIA_BBOXES
    logger.info(f"OSM Overpass: querying {len(bboxes)} bounding boxes...")

    total_elements = 0
    total_stations = 0
    total_saved = 0
    total_matched = 0

    semaphore = asyncio.Semaphore(5)  # 5 параллельных запросов

    async def _query_one(bbox, sess):
        async with semaphore:
            elements = await query_overpass(sess, bbox)
            await asyncio.sleep(1)  # пауза между запросами
            return elements

    async with aiohttp.ClientSession() as session:
        tasks = [_query_one(bbox, session) for bbox in bboxes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Собираем все станции
    all_osm_stations = {}
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Query error: {result}")
            continue
        for elem in result:
            st = extract_station_data(elem)
            if st and st.get("osm_id"):
                all_osm_stations[st["osm_id"]] = st
                total_elements += 1

    logger.info(f"OSM elements: {total_elements}, unique stations: {len(all_osm_stations)}")

    # Матчим с БД и сохраняем
    stations_list = list(all_osm_stations.values())
    matched = 0
    not_matched = 0

    # Батч-матчинг: собираем координаты и ищем в БД
    BATCH_SIZE = 100
    for i in range(0, len(stations_list), BATCH_SIZE):
        batch = stations_list[i:i+BATCH_SIZE]
        for st in batch:
            station_id = await find_db_station(st)
            if station_id:
                st["db_station_id"] = station_id
                matched += 1
            else:
                not_matched += 1

        if (i // BATCH_SIZE) % 10 == 0:
            logger.info(f"  Progress: {i+len(batch)}/{len(stations_list)} stations "
                       f"(matched: {matched}, not matched: {not_matched})")

    logger.info(f"Matched: {matched}/{len(stations_list)} ({not_matched} not in DB)")

    # Сохраняем отчёты
    matched_stations = [s for s in stations_list if "db_station_id" in s]
    total_saved = await save_osm_reports(matched_stations)

    logger.info(f"\n=== OSM AVAILABILITY ИТОГО ===")
    logger.info(f"  BBoxes: {len(bboxes)}")
    logger.info(f"  OSM elements: {total_elements}")
    logger.info(f"  Unique stations: {len(all_osm_stations)}")
    logger.info(f"  Matched to DB: {matched}")
    logger.info(f"  Reports saved: {total_saved}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
