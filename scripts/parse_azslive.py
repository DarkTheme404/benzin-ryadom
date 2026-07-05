#!/usr/bin/env python3
"""Парсер данных с azslive.ru — наличие топлива на АЗС.

Загружает данные о наличии/отсутствии топлива с azslive.ru API.
26,124 АЗС по всей России, обновляется каждые 6 часов.

API:
    GET /api/meta — метаданные
    GET /api/stations?bbox=south,west,north,east&only_with_data=true — станции

Статусы:
    have (green) — есть топливо
    low (amber) — заканчивается
    queue (yellow) — большая очередь
    none (red) — нет топлива
    closed (black) — закрыта

Использование:
    python scripts/parse_azslive.py
"""

import asyncio
import sys
import os
import logging
import math
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

AZSLIVE_API = "https://azslive.ru/api"

STATUS_MAP = {
    "have": True,
    "low": True,
    "queue": None,
    "none": False,
    "closed": None,
}


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# bbox-ячейки для покрытия всей России (~5-7 градусов)
RUSSIA_BBOXES = [
    (50.0, 33.0, 57.0, 40.0),
    (50.0, 40.0, 57.0, 50.0),
    (57.0, 33.0, 62.0, 50.0),
    (44.0, 36.0, 51.0, 42.0),
    (42.0, 42.0, 47.0, 47.0),
    (50.0, 50.0, 58.0, 60.0),
    (50.0, 60.0, 58.0, 70.0),
    (50.0, 70.0, 58.0, 85.0),
    (50.0, 85.0, 58.0, 100.0),
    (42.0, 100.0, 52.0, 120.0),
    (42.0, 120.0, 55.0, 135.0),
    (55.0, 120.0, 65.0, 140.0),
    (42.0, 135.0, 55.0, 155.0),
    (55.0, 140.0, 68.0, 170.0),
    (44.0, 33.0, 47.0, 37.0),
]


async def fetch_meta():
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{AZSLIVE_API}/meta",
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
    except Exception as e:
        logger.error(f"Failed to fetch azslive meta: {e}")
    return None


async def fetch_stations_batch(session, bbox):
    south, west, north, east = bbox
    try:
        async with session.get(
            f"{AZSLIVE_API}/stations",
            params={
                "bbox": f"{south},{west},{north},{east}",
                "only_with_data": "true",
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                return data.get("stations", [])
    except Exception as e:
        logger.debug(f"Error fetching bbox {bbox}: {e}")
    return []


async def find_matching_station(lat, lon):
    rows = await db._fetch(
        """SELECT id, name, lat, lon FROM stations
           WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
           LIMIT 10""",
        lat - 0.01, lat + 0.01,
        lon - 0.01, lon + 0.01,
    )
    if not rows:
        return None
    best = None
    best_dist = float("inf")
    for row in rows:
        dist = haversine_km(lat, lon, row["lat"], row["lon"])
        if dist < 0.5 and dist < best_dist:
            best_dist = dist
            best = row
    return best


async def save_azslive_data(stations):
    saved = 0
    for station in stations:
        azs_lat = station.get("lat")
        azs_lon = station.get("lon")
        azs_status = station.get("status")
        azs_brand = station.get("brand", "")
        fuels = station.get("fuels", [])
        last_report = station.get("last_report")

        if not azs_lat or not azs_lon or not azs_status:
            continue

        available = STATUS_MAP.get(azs_status)

        matching = await find_matching_station(azs_lat, azs_lon)
        if not matching:
            continue

        comment_parts = [f"[azslive.ru]"]
        if azs_brand:
            comment_parts.append(f"Бренд: {azs_brand}")
        if fuels:
            comment_parts.append(f"Топливо: {', '.join(fuels)}")
        status_labels = {
            "have": "Есть топливо",
            "low": "Заканчивается",
            "queue": "Очередь",
            "none": "Нет топлива",
            "closed": "Закрыта",
        }
        comment_parts.append(f"Статус: {status_labels.get(azs_status, azs_status)}")
        if last_report:
            comment_parts.append(f"Отчёт: {last_report}")

        fuel_map = {"ai92": "92", "ai95": "95", "ai98": "98", "dt": "dt", "gas": "gas"}
        fuel_type = fuel_map.get(fuels[0], "all") if fuels else "all"

        try:
            await db.add_report(
                station_id=matching["id"],
                fuel_type=fuel_type,
                available=available,
                comment=" | ".join(comment_parts)[:500],
                source="azslive",
            )
            saved += 1
        except Exception as e:
            logger.debug(f"Error saving: {e}")

    return saved


async def main():
    await db.init_db()

    meta = await fetch_meta()
    if not meta:
        logger.error("Failed to fetch azslive meta")
        await db.close_db()
        return

    logger.info(f"azslive.ru: {meta.get('stations_count', 0)} станций")

    all_stations = {}
    async with aiohttp.ClientSession() as session:
        for i, bbox in enumerate(RUSSIA_BBOXES):
            stations = await fetch_stations_batch(session, bbox)
            for s in stations:
                all_stations[s["id"]] = s
            logger.info(f"  bbox {i+1}/{len(RUSSIA_BBOXES)}: +{len(stations)} станций (всего: {len(all_stations)})")
            await asyncio.sleep(0.5)

    logger.info(f"Уникальных станций с данными: {len(all_stations)}")

    saved = await save_azslive_data(list(all_stations.values()))
    logger.info(f"azslive.ru: saved {saved} reports")

    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
