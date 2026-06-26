"""
Парсер цен на топливо из 2ГИС Catalog API.

Получить API key (бесплатно):
  1. https://dev.2gis.ru/ → Sign up
  2. Создать проект → API ключ (Catalog API)
  3. Free tier: 1000 запросов/день

Использование:
  export TWO_GIS_API_KEY='your_key'
  python scripts/parse_2gis.py --city "Иваново"
  python scripts/parse_2gis.py --city "Иваново" --lat 56.99 --lon 40.97 --radius 30

Сохраняет в нашу БД с источником "2gis".
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

try:
    import aiohttp
except ImportError:
    print("pip install aiohttp")
    raise

# Добавляем bot/ в path для импорта db
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

API_KEY = os.environ.get("TWO_GIS_API_KEY", "")
BASE_URL = "https://catalog.api.2gis.ru/3.0"
DAILY_LIMIT = 1000

# Маппинг 2ГИС fuel_type → наши fuel_types
FUEL_MAP = {
    "fuel_92": "92",
    "fuel_95": "95",
    "fuel_98": "98",
    "fuel_diesel": "diesel",
    "fuel_diesel_winter": "diesel",
    "fuel_diesel_euro": "diesel",
    "fuel_lpg": "lpg",
    "fuel_cng": "cng",
    "fuel_100": "100",
}


async def find_city_id(session: aiohttp.ClientSession, city_name: str) -> str | None:
    """Ищет city_id в 2ГИС по имени города."""
    url = f"{BASE_URL}/items"
    params = {
        "q": city_name,
        "type": "city",
        "key": API_KEY,
        "page_size": 1,
    }
    async with session.get(url, params=params, timeout=15) as r:
        data = await r.json()
        if data.get("result", {}).get("items"):
            return data["result"]["items"][0]["id"]
    return None


async def find_fuel_stations(
    session: aiohttp.ClientSession,
    lat: float, lon: float, radius_km: float = 30,
    page_size: int = 50,
) -> list[dict]:
    """Ищет АЗС в радиусе."""
    url = f"{BASE_URL}/items"
    params = {
        "q": "АЗС",
        "type": "fuel",
        "key": API_KEY,
        "point": f"{lon},{lat}",
        "radius": int(radius_km * 1000),  # метры
        "page_size": page_size,
        "fields": (
            "items.point,"
            "items.full_address_name,"
            "items.name_ex,"
            "items.contact_groups,"
            "items.schedule,"
            "items.reviews,"
            "items.fuel_types,"
            "items.capacity"
        ),
    }
    async with session.get(url, params=params, timeout=20) as r:
        data = await r.json()
        if data.get("meta", {}).get("code") != 200:
            err = data.get("meta", {}).get("error", {}).get("message", "unknown")
            print(f"  API error: {err}")
            return []
        items = data.get("result", {}).get("items", [])
        return items


def extract_prices(item: dict) -> dict[str, float]:
    """Извлекает цены на топливо из item.

    2ГИС хранит цены в schedule → fuel_prices или в отдельном атрибуте.
    """
    prices = {}
    for attr in item.get("attrs", []):
        # attr: {"id": "fuel_price_95", "value": "55.40"}
        attr_id = attr.get("id", "")
        if attr_id.startswith("fuel_price_"):
            fuel = attr_id.replace("fuel_price_", "")
            try:
                price = float(attr["value"].replace(",", ".").replace(" ", ""))
                if price > 0:
                    prices[fuel] = price
            except (ValueError, AttributeError):
                pass
    return prices


async def upsert_station_with_prices(item: dict, prices: dict, source: str = "2gis") -> int:
    """Сохраняет/обновляет АЗС с ценами в БД."""
    name = item.get("name_ex", {}).get("primary", {}).get("name", "АЗС")
    addr = item.get("full_address_name", "")
    point = item.get("point", {})
    lon, lat = point.get("lon"), point.get("lat")
    if not (lat and lon):
        return 0

    # Контакт — телефон
    phone = None
    for grp in item.get("contact_groups", []):
        for c in grp.get("contacts", []):
            if c.get("type") == "phone" and c.get("value"):
                phone = c["value"]
                break

    # Сохраняем в БД (stations + reports)
    async with db._db.acquire() as conn:
        # Ищем существующую по близким координатам
        existing = await conn.fetchrow(
            """SELECT id FROM stations
               WHERE ABS(lat - $1) < 0.0001 AND ABS(lon - $2) < 0.0001
               LIMIT 1""",
            lat, lon,
        )
        if existing:
            station_id = existing["id"]
        else:
            row = await conn.fetchrow(
                """INSERT INTO stations (name, address, lat, lon, phone, country, is_active, created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, 'RU', TRUE, NOW(), NOW())
                   RETURNING id""",
                name, addr, lat, lon, phone,
            )
            station_id = row["id"]

        # Сохраняем цены как reports (source='2gis')
        expires = datetime.now().replace(hour=23, minute=59, second=59)
        for fuel, price in prices.items():
            # Проверяем: есть ли свежий отчёт за 24ч
            old = await conn.fetchrow(
                """SELECT id FROM reports
                   WHERE station_id = $1 AND fuel_type = $2
                     AND source = $3
                     AND created_at > NOW() - INTERVAL '24 hours'""",
                station_id, fuel, source,
            )
            if not old:
                await conn.execute(
                    """INSERT INTO reports (station_id, fuel_type, available, price, source, expires_at, created_at)
                       VALUES ($1, $2, TRUE, $3, $4, $5, NOW())""",
                    station_id, fuel, price, source, expires,
                )
        return station_id


async def main():
    if not API_KEY:
        print("❌ TWO_GIS_API_KEY не задан")
        print("Регистрация: https://dev.2gis.ru/")
        print("export TWO_GIS_API_KEY='your_key'")
        return 1

    parser = argparse.ArgumentParser()
    parser.add_argument("--city", help="Имя города (например 'Иваново')")
    parser.add_argument("--lat", type=float, help="Широта центра")
    parser.add_argument("--lon", type=float, help="Долгота центра")
    parser.add_argument("--radius", type=float, default=30, help="Радиус (км)")
    parser.add_argument("--limit", type=int, default=50, help="Макс АЗС")
    args = parser.parse_args()

    if not args.city and not (args.lat and args.lon):
        print("❌ Укажи --city или --lat + --lon")
        return 1

    print(f"=== 2ГИС парсер цен ===")
    print(f"Город: {args.city}")
    print(f"Радиус: {args.radius} км")
    print(f"Free tier: {DAILY_LIMIT} req/day")
    print()

    await db.init_db()
    saved = 0
    prices_collected = 0

    async with aiohttp.ClientSession() as session:
        # 1) Если указан city — найти координаты
        if args.city and not (args.lat and args.lon):
            print(f"Ищу город «{args.city}»...")
            city_id = await find_city_id(session, args.city)
            if not city_id:
                print(f"  Город «{args.city}» не найден")
                return 1
            # Получаем координаты города
            url = f"{BASE_URL}/items"
            async with session.get(url, params={"id": city_id, "key": API_KEY, "type": "city"}, timeout=15) as r:
                data = await r.json()
                city = data.get("result", {}).get("items", [{}])[0]
                point = city.get("point", {})
                args.lat = point.get("lat")
                args.lon = point.get("lon")
                print(f"  Найден: {city.get('name_ex', {}).get('primary', {}).get('name')} ({args.lat}, {args.lon})")

        # 2) Ищем АЗС
        print(f"\nИщу АЗС в радиусе {args.radius} км...")
        items = await find_fuel_stations(session, args.lat, args.lon, args.radius, args.limit)
        print(f"  Найдено: {len(items)} АЗС")

        # 3) Сохраняем
        for item in items:
            prices = extract_prices(item)
            station_id = await upsert_station_with_prices(item, prices, "2gis")
            if station_id:
                saved += 1
                prices_collected += len(prices)
                name = item.get("name_ex", {}).get("primary", {}).get("name", "?")
                if prices:
                    print(f"  ✓ {name}: {len(prices)} цен ({', '.join(f'{f}:{p}' for f, p in prices.items())})")
                else:
                    print(f"  · {name}: нет цен в 2ГИС")

    print()
    print(f"=== Итого ===")
    print(f"  АЗС: {saved}")
    print(f"  Цен: {prices_collected}")
    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
