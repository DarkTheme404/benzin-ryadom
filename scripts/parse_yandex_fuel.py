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


async def fetch_fuel_prices(
    session: aiohttp.ClientSession,
    lat: float, lon: float, radius_km: float = 30,
) -> list[dict]:
    """Получает цены на топливо в радиусе."""
    url = f"{BASE_URL}"
    params = {
        "lang": "ru_RU",
        "apikey": API_KEY,
        "bbox": f"{lon-0.5},{lat-0.3},{lon+0.5},{lat+0.3}",  # rough bbox
        "zoom": 11,
    }
    async with session.get(url, params=params, timeout=20) as r:
        if r.status != 200:
            err = await r.text()
            print(f"  ❌ API {r.status}: {err[:200]}")
            return []
        data = await r.json()
        return data.get("features", [])


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

    async with aiohttp.ClientSession() as session:
        features = await fetch_fuel_prices(session, args.lat, args.lon, args.radius)
        print(f"Найдено: {len(features)} объектов")
        for f in features[:10]:
            geom = f.get("geometry", {}).get("coordinates", [None, None])
            props = f.get("properties", {})
            name = props.get("name", "?")
            print(f"  · {name} @ ({geom[1]}, {geom[0]})")
            # TODO: сохранить в БД

    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
