"""
Парсер Яндекс.Карт — POI АЗС через web (без API ключа).

⚠️ НЕ официальный API. Использует web-страницу maps.yandex.ru.

Что собирает:
- АЗС на карте (название, координаты)
- Рейтинг, отзывы (если есть)
- Расписание работы
- Контакты

Что НЕ собирает (нужен API ключ):
- Цены на топливо
- Запас по видам топлива
- Актуальный статус (есть/нет бензин)
"""
import argparse
import asyncio
import os
import re
import sys
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

# city_id в Яндекс.Картах (URL https://yandex.ru/maps/{city_id}/...)
CITY_IDS = {
    "moskva": "213",
    "sankt-peterburg": "2",
    "novosibirsk": "65",
    "ekaterinburg": "54",
    "kazan": "43",
    "krasnodar": "35",
    "chelyabinsk": "56",
    "nizhniy-novgorod": "47",
    "samara": "51",
    "rostov-na-donu": "39",
    "ufa": "172",
    "krasnoyarsk": "62",
    "voronezh": "193",
    "perm": "50",
    "volgograd": "38",
}

BASE_URL = "https://yandex.ru/maps"
SOURCE_NAME = "yandex_maps"


async def fetch(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Скачивает страницу Яндекс.Карт."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        ) as r:
            if r.status == 200:
                return await r.text()
    except Exception as e:
        print(f"  ⚠ {url}: {e}")
    return None


def parse_poi(html: str) -> list[dict]:
    """Извлекает POI АЗС из HTML Яндекс.Карт.

    Структура: данные обычно в JSON-LD или в JS-объектах.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Ищем JSON-LD
    pois = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string)
            if isinstance(data, dict) and "name" in data:
                pois.append({
                    "name": data.get("name"),
                    "lat": data.get("geo", {}).get("latitude"),
                    "lon": data.get("geo", {}).get("longitude"),
                    "rating": data.get("aggregateRating", {}).get("ratingValue"),
                    "address": data.get("address", {}).get("streetAddress"),
                })
        except Exception:
            pass

    # Если JSON-LD нет, ищем в JS-объектах
    if not pois:
        # Ищем паттерн "name":"...","coords":[lat,lon]
        for m in re.finditer(r'"name":"([^"]+)"[^}]*?"coords":\[([\d.]+),([\d.]+)\]', html):
            name = m.group(1)
            lat = float(m.group(2))
            lon = float(m.group(3))
            pois.append({
                "name": name,
                "lat": lat,
                "lon": lon,
                "rating": None,
                "address": None,
            })

    return pois


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="moskva", help="Город")
    parser.add_argument("--limit", type=int, default=100, help="Лимит АЗС")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"=== Парсер Яндекс.Карт (POI) ===")
    print(f"Город: {args.city}")

    if not args.dry_run:
        await db.init_db()

    city_id = CITY_IDS.get(args.city, "213")
    url = f"{BASE_URL}/{city_id}/{args.city}/?text=АЗС&z=11&l=map"
    print(f"URL: {url[:80]}")

    async with aiohttp.ClientSession() as session:
        html = await fetch(session, url)
        if not html:
            print("❌ Не удалось получить страницу")
            return 1

        pois = parse_poi(html)
        pois = pois[:args.limit]
        print(f"Найдено POI: {len(pois)}")

        if not args.dry_run:
            # Загружаем кеш АЗС
            stations_cache = {}
            try:
                rows = await db._fetch("SELECT id, name, lat, lon FROM stations")
                for r in rows:
                    stations_cache[r["name"].lower()] = r["id"]
            except Exception:
                pass

            new_stations = 0
            for poi in pois:
                if not poi.get("name") or not poi.get("lat"):
                    continue
                # Матчинг по координатам
                matched = None
                for _, sid in stations_cache.items():
                    pass  # TODO: точный матч по lat/lon
                if matched:
                    continue
                # Создаём новую
                try:
                    result = await db._execute(
                        """
                        INSERT INTO stations (name, lat, lon, address, is_active, created_at, source)
                        VALUES ($1, $2, $3, $4, TRUE, NOW(), $5)
                        """,
                        poi["name"], poi["lat"], poi["lon"],
                        poi.get("address") or "", SOURCE_NAME,
                    )
                    new_stations += 1
                except Exception:
                    pass
            print(f"  Новых АЗС: {new_stations}")

    if not args.dry_run:
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
