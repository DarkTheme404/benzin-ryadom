"""
Парсер цен из Яндекс.Заправки (официальный API).

Получить API key (бесплатно, 25 000 req/мес):
  1. https://developer.tech.yandex.ru/services/3
  2. JavaScript API и HTTP Geocoder
  3. Создать ключ → "API ключ для HTTP Geocoder"
  4. export YANDEX_GEOCODER_API_KEY='your_key'

API: https://yandex.ru/maps/api/fuel?lang=ru_RU

Возвращает цены на топливо для всех АЗС в bbox.

⚠️ API может быть deprecated. Если endpoint не работает — нужно парсить
Яндекс.Карты (сложно) или использовать другие источники.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

API_KEY = os.environ.get("YANDEX_GEOCODER_API_KEY", "")
BASE_URL = "https://yandex.ru/maps/api/fuel"

# Маппинг типов топлива Яндекса → наши
FUEL_MAP = {
    "AI-92":  "92",
    "AI-95":  "95",
    "AI-98":  "98",
    "AI-100": "100",
    "DT":    "diesel",
    "GAS":   "lpg",
    "92":    "92",
    "95":    "95",
    "98":    "98",
    "100":   "100",
    "ДТ":    "diesel",
    "Газ":   "lpg",
}


async def fetch_fuel_prices(
    session: aiohttp.ClientSession,
    lat: float, lon: float, radius_km: float = 30,
) -> list[dict]:
    """Получает цены на топливо в радиусе."""
    url = f"{BASE_URL}"
    params = {
        "lang": "ru_RU",
        "apikey": API_KEY,
        "bbox": f"{lon-0.5},{lat-0.3},{lon+0.5},{lat+0.3}",
        "zoom": 11,
    }
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as r:
        if r.status != 200:
            err = await r.text()
            print(f"  ❌ API {r.status}: {err[:200]}")
            return []
        data = await r.json()
        return data.get("features", [])


async def find_station_by_coords(lat: float, lon: float) -> int | None:
    """Ищет АЗС в БД по координатам (±500м)."""
    radius = 0.005
    if db.USE_SQLITE:
        rows = await db._fetch(
            "SELECT id FROM stations WHERE ABS(lat - ?) < ? AND ABS(lon - ?) < ? LIMIT 1",
            lat, radius, lon, radius,
        )
    else:
        async with db._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM stations WHERE ABS(lat - $1) < $2 AND ABS(lon - $3) < $2 LIMIT 1",
                lat, radius, lon,
            )
    if rows:
        r = rows[0]
        return r["id"] if isinstance(r, dict) else r[0]
    return None


async def main():
    if not API_KEY:
        print("❌ YANDEX_GEOCODER_API_KEY не задан")
        print("Получить: https://developer.tech.yandex.ru/services/3")
        print("export YANDEX_GEOCODER_API_KEY='your_key'")
        return 1

    parser = argparse.ArgumentParser()
    parser.add_argument("--city", help="Город (пока не работает — нужны координаты)")
    parser.add_argument("--lat", type=float, help="Широта")
    parser.add_argument("--lon", type=float, help="Долгота")
    parser.add_argument("--radius", type=float, default=30, help="Радиус (км)")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    if not (args.lat and args.lon):
        print("❌ Укажи --lat и --lon")
        return 1

    print(f"=== Яндекс.Заправки ===")
    print(f"Координаты: ({args.lat}, {args.lon})")
    print(f"Радиус: {args.radius} км")
    print()

    await db.init_db()
    saved = 0
    matched = 0

    async with aiohttp.ClientSession() as session:
        features = await fetch_fuel_prices(session, args.lat, args.lon, args.radius)
        print(f"Найдено: {len(features)} объектов")

        for f in features:
            geom = f.get("geometry", {}).get("coordinates", [None, None])
            props = f.get("properties", {})
            name = props.get("name", "?")
            f_lon = geom[0]
            f_lat = geom[1]
            if not f_lat or not f_lon:
                continue

            # Ищем АЗС в БД
            station_id = await find_station_by_coords(f_lat, f_lon)
            if not station_id:
                continue
            matched += 1

            # Извлекаем цены
            fuels = props.get("fuels", [])
            for fuel_obj in fuels:
                fuel_type_raw = fuel_obj.get("name", "")
                price = fuel_obj.get("price")
                if not price:
                    continue
                fuel_type = FUEL_MAP.get(fuel_type_raw, FUEL_MAP.get(fuel_type_raw.upper()))
                if not fuel_type:
                    continue

                if not args.dry_run:
                    try:
                        await db.add_report(
                            station_id=station_id,
                            fuel_type=fuel_type,
                            available=True,
                            price=float(price),
                            source="yandex_fuel",
                            comment=f"Яндекс.Заправки: {name}",
                        )
                        saved += 1
                    except Exception as e:
                        print(f"  ⚠ Save: {e}")

    print(f"  Сопоставлено АЗС: {matched}")
    print(f"  Сохранено отчётов: {saved}")
    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
