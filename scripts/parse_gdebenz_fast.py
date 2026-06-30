#!/usr/bin/env python3
"""Быстрый парсер gdebenz.ru — только ключевые города."""

import asyncio
import sys
import os
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Все крупные города России
AREAS = [
    # Центр
    ("Иваново", 56.80, 40.80, 57.10, 41.20),
    ("Москва", 55.50, 37.30, 55.95, 37.85),
    ("Ярославль", 57.55, 39.80, 57.70, 40.05),
    ("Кострома", 57.75, 40.90, 57.85, 41.00),
    ("Владимир", 56.10, 40.35, 56.20, 40.45),
    ("Рязань", 54.60, 39.65, 54.70, 39.80),
    ("Тула", 54.15, 37.55, 54.25, 37.65),
    ("Калуга", 54.50, 36.25, 54.55, 36.35),
    ("Тверь", 56.85, 35.85, 56.90, 35.95),
    ("Смоленск", 54.75, 32.00, 54.80, 32.10),
    ("Орёл", 52.95, 36.05, 53.00, 36.10),
    ("Брянск", 53.20, 34.30, 53.25, 34.40),
    ("Курск", 51.70, 36.15, 51.75, 36.25),
    ("Липецк", 52.60, 39.55, 52.65, 39.65),
    ("Тамбов", 52.70, 41.40, 52.75, 41.50),
    ("Пенза", 53.20, 45.00, 53.25, 45.10),
    ("Саратов", 51.50, 46.00, 51.55, 46.10),
    ("Ульяновск", 54.30, 48.35, 54.35, 48.45),
    ("Чебоксары", 56.10, 47.20, 56.15, 47.30),
    # Северо-Запад
    ("СПб", 59.80, 30.10, 60.10, 30.50),
    ("Вологда", 59.20, 39.85, 59.25, 39.95),
    ("Архангельск", 64.50, 40.50, 64.55, 40.60),
    ("Мурманск", 68.95, 33.05, 69.00, 33.15),
    # Юг
    ("Краснодар", 44.90, 38.80, 45.10, 39.10),
    ("Ростов", 47.15, 39.55, 47.35, 39.85),
    ("Воронеж", 51.60, 39.10, 51.75, 39.30),
    ("Волгоград", 48.70, 44.45, 48.75, 44.55),
    ("Ставрополь", 45.00, 41.95, 45.05, 42.05),
    ("Сочи", 43.60, 39.70, 43.65, 39.80),
    ("Новороссийск", 44.70, 37.75, 44.75, 37.85),
    # Поволжье
    ("Казань", 55.70, 49.00, 55.90, 49.30),
    ("Самара", 53.10, 50.00, 53.35, 50.30),
    ("Уфа", 54.65, 55.85, 54.85, 56.15),
    ("Оренбург", 51.75, 55.00, 51.80, 55.10),
    ("Пермь", 58.00, 56.20, 58.05, 56.30),
    # Урал
    ("Екатеринбург", 56.70, 60.40, 56.95, 60.70),
    ("Челябинск", 55.10, 61.35, 55.15, 61.45),
    ("Тюмень", 57.10, 65.45, 57.20, 65.65),
    ("Курган", 55.40, 65.30, 55.45, 65.40),
    ("Сургут", 61.20, 73.35, 61.25, 73.45),
    # Сибирь
    ("Новосибирск", 54.80, 82.80, 55.10, 83.10),
    ("Красноярск", 55.95, 92.70, 56.10, 93.00),
    ("Иркутск", 52.20, 104.20, 52.35, 104.40),
    ("Омск", 54.95, 73.35, 55.00, 73.45),
    ("Барнаул", 53.30, 83.70, 53.35, 83.80),
    ("Кемерово", 55.30, 86.05, 55.35, 86.15),
    ("Томск", 56.45, 84.95, 56.50, 85.05),
    # Дальний Восток
    ("Владивосток", 43.10, 131.85, 43.15, 131.95),
    ("Хабаровск", 48.45, 135.05, 48.50, 135.15),
]

FUEL_MAP = {
    "92": "92", "95": "95", "98": "98", "100": "100",
    "ДТ": "diesel", "дт": "diesel",
    "газ": "lpg", "Газ": "lpg", "LPG": "lpg",
}

async def process_area(session, name, lat1, lon1, lat2, lon2):
    url = f"https://gdebenz.ru/api/stations?lat1={lat1}&lon1={lon1}&lat2={lat2}&lon2={lon2}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return 0
            stations = await resp.json(content_type=None)
    except Exception as e:
        logger.error(f"  {name}: {e}")
        return 0

    saved = 0
    for s in stations:
        sname = s.get("name", "АЗС")
        brand = s.get("brand", "")
        lat = s.get("lat")
        lon = s.get("lon")
        status = s.get("status", "")
        fuels_now = s.get("fuels_now", "")

        if not lat or not lon:
            continue

        # Ищем станцию
        existing = await db._fetch(
            "SELECT id FROM stations WHERE name LIKE ? AND ABS(lat-?)<0.01 AND ABS(lon-?)<0.01 LIMIT 1",
            f"%{sname}%", lat, lon
        )
        if existing:
            sid = existing[0]["id"]
        else:
            chain = brand or sname
            c = await db._execute(
                "INSERT INTO stations (name,brand,network,city,lat,lon) VALUES (?,?,?,?,?,?)",
                sname, chain, chain, name, lat, lon,
                returning=True
            )
            sid = c

        available = 1 if status == "yes" else 0  # default to 0 (no) if unknown
        fuels = [f.strip() for f in fuels_now.split(",") if f.strip()] if fuels_now else ["92", "95", "diesel"]

        for ft in fuels:
            ft_norm = FUEL_MAP.get(ft, ft)
            dup = await db._fetch(
                "SELECT id FROM reports WHERE station_id=? AND fuel_type=? AND source='gdebenz' AND created_at>datetime('now','-2 hours') LIMIT 1",
                sid, ft_norm
            )
            if dup:
                continue

            msg = f"[gdebenz] {name}: {'есть' if available else 'нет'}"
            if fuels_now:
                msg += f" ({fuels_now})"

            await db._execute(
                "INSERT INTO reports (station_id,fuel_type,available,source,created_at,comment) VALUES (?,?,?,?,?,?)",
                sid, ft_norm, available, "gdebenz", datetime.now(timezone.utc).isoformat(), msg[:500]
            )
            saved += 1

    return saved

async def main():
    await db.init_db()
    total = 0
    async with aiohttp.ClientSession() as session:
        for name, lat1, lon1, lat2, lon2 in AREAS:
            logger.info(f"Fetching {name}...")
            count = await process_area(session, name, lat1, lon1, lat2, lon2)
            total += count
            logger.info(f"  {name}: {count} reports")
    logger.info(f"\n=== Total gdebenz reports: {total} ===")
    await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
