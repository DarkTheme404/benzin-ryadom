"""
Парсер официальных цен ЛУКОЙЛ (auto.lukoil.ru).

⚠️ auto.lukoil.ru не имеет публичного API для цен.
   Сайт использует JavaScript-рендеринг (KnockoutJS + AJAX).

Стратегия:
  1. Пытаемся найти API-эндпоинты (trial & error с известными паттернами)
  2. Парсим HTML-страницу топлива (auto.lukoil.ru/ru/ProductsAndServices/Fuel)
  3. Для каждой АЗС ЛУКОЙЛ в БД — парсим страницу АЗС
  4. Мэтчим станции по operator/network содержащих "лукойл"

Использование:
    python scripts/parse_lukoil.py                 # парсинг всех ЛУКОЙЛ АЗС в БД
    python scripts/parse_lukoil.py --dry-run       # без сохранения в БД
    python scripts/parse_lukoil.py --city Москва    # только конкретный город
    python scripts/parse_lukoil.py --limit 50       # лимит АЗС
"""
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

SOURCE = "lukoil_official"

# Известные (попробованные) API-эндпоинты auto.lukoil.ru
LUKOIL_API_CANDIDATES = [
    "https://auto.lukoil.ru/umbraco/api/petrolstation/getprices",
    "https://auto.lukoil.ru/api/stations",
    "https://auto.lukoil.ru/api/station/search",
    "https://auto.lukoil.ru/Handlers/PetrolStationHandler.ashx",
    "https://lukoil.ru/api/stations",
    "https://lukoil.ru/api/v1/stations",
    "https://www.lukoil.ru/api/stations",
]

# Страница топлива (маркетинг — без реальных цен, но пробуем)
LUKOIL_FUEL_PAGE = "https://auto.lukoil.ru/ru/ProductsAndServices/Fuel"

# Страница АЗС ( individial station pages)
LUKOIL_STATION_PAGE = "https://auto.lukoil.ru/ru/ProductsAndServices/PetrolStation"

# Паттерны для парсинга цен из HTML
PRICE_PATTERNS = {
    "92": [
        r"(?:АИ[-\s]?92|92)[^\d]*(\d{2,3}[.,]\d{1,2})",
        r"(?:бензин\s*)?(?:92|АИ-?92)[^\d]*(\d{2,3}[.,]\d{1,2})",
        r"class=[\"'].*?price.*?[\"'][^>]*>(\d{2,3}[.,]\d{1,2})",
    ],
    "95": [
        r"(?:АИ[-\s]?95|95)[^\d]*(\d{2,3}[.,]\d{1,2})",
        r"(?:бензин\s*)?(?:95|АИ-?95)[^\d]*(\d{2,3}[.,]\d{1,2})",
        r"(?:ЭКТО\s*(?:PLUS|Плюс)?\s*\(?\s*95\s*\)?)[^\d]*(\d{2,3}[.,]\d{1,2})",
    ],
    "98": [
        r"(?:АИ[-\s]?98|98)[^\d]*(\d{2,3}[.,]\d{1,2})",
        r"(?:ЭКТО[-\s]?100|100)[^\d]*(\d{2,3}[.,]\d{1,2})",
    ],
    "diesel": [
        r"(?:дт|дизель|ДТ|Diesel)[^\d]*(\d{2,3}[.,]\d{1,2})",
        r"(?:ЭКТО[-\s]?diesel)[^\d]*(\d{2,3}[.,]\d{1,2})",
    ],
    "lpg": [
        r"(?:газ|пропан|LPG|ГАЗ)[^\d]*(\d{2,3}[.,]\d{1,2})",
    ],
}


async def try_api_endpoints(session: aiohttp.ClientSession) -> Optional[dict]:
    """Trial & error: пробуем известные API-эндпоинты ЛУКОЙЛ."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/html, */*",
        "Referer": "https://auto.lukoil.ru/ru/ProductsAndServices/PetrolStations",
    }
    for url in LUKOIL_API_CANDIDATES:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers=headers,
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    ct = resp.headers.get("Content-Type", "")
                    if "json" in ct:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict) and (
                            "stations" in data
                            or "data" in data
                            or "items" in data
                            or "result" in data
                        ):
                            print(f"  ✅ API найден: {url}")
                            return data
                        elif isinstance(data, list) and len(data) > 0:
                            print(f"  ✅ API найден (list): {url}")
                            return {"stations": data}
                    elif "html" in ct:
                        text = await resp.text()
                        if len(text) > 500 and any(
                            kw in text.lower()
                            for kw in ["цена", "price", "топлив", "fuel", "станци"]
                        ):
                            print(f"  ⚠ HTML-ответ с контентом: {url}")
                            # Попробуем вытащить JSON из HTML
                            json_match = re.search(
                                r"var\s+\w+\s*=\s*(\{.*?\});", text, re.DOTALL
                            )
                            if json_match:
                                import json

                                try:
                                    return json.loads(json_match.group(1))
                                except Exception:
                                    pass
        except Exception:
            continue
    return None


def parse_prices_from_html(html: str) -> dict[str, float]:
    """Извлекает цены топлива из HTML-страницы."""
    prices = {}
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)

    for fuel_type, patterns in PRICE_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                try:
                    price = float(m.group(1).replace(",", "."))
                    if 20 < price < 200:
                        prices[fuel_type] = price
                        break
                except (ValueError, IndexError):
                    continue
    return prices


def parse_station_page(html: str) -> dict[str, float]:
    """Парсит страницу отдельной АЗС ЛУКОЙЛ для извлечения цен."""
    prices = {}
    soup = BeautifulSoup(html, "html.parser")

    # Ищем таблицы с ценами
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        for fuel_type, patterns in PRICE_PATTERNS.items():
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    try:
                        price = float(m.group(1).replace(",", "."))
                        if 20 < price < 200:
                            prices[fuel_type] = price
                            break
                    except (ValueError, IndexError):
                        continue

    # Если таблиц нет — ищем по всему тексту
    if not prices:
        text = soup.get_text(" ", strip=True)
        for fuel_type, patterns in PRICE_PATTERNS.items():
            for pattern in patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    try:
                        price = float(m.group(1).replace(",", "."))
                        if 20 < price < 200:
                            prices[fuel_type] = price
                            break
                    except (ValueError, IndexError):
                        continue

    # Ищем данные в JSON-LD или script-тегах
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json

            data = json.loads(script.string or "")
            if isinstance(data, dict):
                offers = data.get("offers", {})
                if isinstance(offers, dict):
                    price_val = offers.get("price")
                    if price_val:
                        # Нет маппинга fuel type из JSON-LD
                        pass
        except Exception:
            continue

    return prices


async def fetch_page(
    session: aiohttp.ClientSession, url: str, params: dict = None
) -> Optional[str]:
    """Скачивает страницу."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    try:
        async with session.get(
            url,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
            headers=headers,
            allow_redirects=True,
        ) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception as e:
        print(f"  ⚠ {url}: {e}")
    return None


async def get_lukoil_stations_from_db() -> list[dict]:
    """Получает все АЗС ЛУКОЙЛ из БД."""
    # Ищем по operator/network/name содержащих "лукойл"
    stations = await db.find_stations_by_city(
        city="",
        network="лукойл",
        has_stock=False,
        limit=10000,
    )
    return stations


async def parse_fuel_page(session: aiohttp.ClientSession) -> dict[str, float]:
    """Пытаемся спарсить цены со страницы топлива ЛУКОЙЛ."""
    html = await fetch_page(session, LUKOIL_FUEL_PAGE)
    if html:
        return parse_prices_from_html(html)
    return {}


async def parse_station_detail(
    session: aiohttp.ClientSession, station_id: int
) -> dict[str, float]:
    """Парсит страницу конкретной АЗС."""
    url = f"{LUKOIL_STATION_PAGE}?id={station_id}"
    html = await fetch_page(session, url)
    if html:
        return parse_station_page(html)
    return {}


async def main():
    parser = argparse.ArgumentParser(
        description="Парсер официальных цен ЛУКОЙЛ"
    )
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    parser.add_argument("--city", help="Фильтр по городу")
    parser.add_argument("--limit", type=int, default=0, help="Лимит АЗС (0=все)")
    args = parser.parse_args()

    print("=== Парсер официальных цен ЛУКОЙЛ ===")
    print()

    if not db.API_MODE:
        await db.init_db()

    total_saved = 0
    total_stations = 0
    total_with_prices = 0

    async with aiohttp.ClientSession() as session:
        # 1. Пробуем найти API
        print("[1] Поиск API-эндпоинтов...")
        api_data = await try_api_endpoints(session)

        if api_data:
            print(f"  Данные из API: {api_data}")
            # Обработка API-данных (если найден рабочий эндпоинт)
            stations_data = api_data.get("stations") or api_data.get("data") or []
            if isinstance(stations_data, list):
                print(f"  Найдено станций в API: {len(stations_data)}")
                for st_data in stations_data:
                    if not isinstance(st_data, dict):
                        continue
                    name = st_data.get("name") or st_data.get("DisplayName", "")
                    city = st_data.get("city") or st_data.get("City", "")
                    api_prices = {}
                    raw_prices = st_data.get("prices") or st_data.get("FuelPrices") or {}
                    if isinstance(raw_prices, dict):
                        for fuel, price in raw_prices.items():
                            try:
                                p = float(price)
                                if 20 < p < 200:
                                    api_prices[fuel.lower().replace("аи-", "")] = p
                            except (ValueError, TypeError):
                                continue
                    if api_prices:
                        # Ищем станцию в БД
                        found = await db.find_stations_by_city(
                            city=city, network="лукойл", has_stock=False, limit=5
                        )
                        for s in found:
                            for fuel, price in api_prices.items():
                                if not args.dry_run:
                                    await db.add_report(
                                        station_id=s["id"],
                                        fuel_type=fuel,
                                        available=True,
                                        price=price,
                                        source=SOURCE,
                                        comment=f"LUKOIL API: {name or city}",
                                    )
                                    total_saved += 1
                        total_stations += 1
                            if api_prices:
                                total_with_prices += 1

        # 2. Парсим страницу топлива (маркетинг)
        print()
        print("[2] Парсинг страницы топлива...")
        fuel_prices = await parse_fuel_page(session)
        if fuel_prices:
            print(f"  Найдены цены на странице: {fuel_prices}")
        else:
            print("  Цены не найдены (маркетинговая страница)")

        # 3. Ищем АЗС ЛУКОЙЛ в БД и парсим их страницы
        print()
        print("[3] Поиск АЗС ЛУКОЙЛ в БД...")
        db_stations = await get_lukoil_stations_from_db()

        if args.city:
            db_stations = [
                s
                for s in db_stations
                if args.city.lower() in (s.get("city") or "").lower()
            ]

        if args.limit > 0:
            db_stations = db_stations[: args.limit]

        print(f"  Найдено АЗС ЛУКОЙЛ в БД: {len(db_stations)}")

        if not db_stations:
            print("  ⚠ АЗС ЛУКОЙЛ не найдены в БД.")
            print("  💡 Сначала запустите импорт АЗС (parse_networks.py, parse_osm.py и т.д.)")

        for i, station in enumerate(db_stations):
            station_id = station.get("id")
            station_name = station.get("name", "")
            station_city = station.get("city", "")

            if i > 0 and i % 5 == 0:
                await asyncio.sleep(1)  # Rate limit

            # Парсим страницу АЗС
            prices = await parse_station_detail(session, station_id)

            # Fallback к ценам со страницы топлива (если есть)
            if not prices and fuel_prices:
                prices = fuel_prices

            if not prices:
                continue

            total_stations += 1
            total_with_prices += 1

            # Сохраняем в БД
            if not args.dry_run:
                for fuel_type, price in prices.items():
                    try:
                        await db.add_report(
                            station_id=station_id,
                            fuel_type=fuel_type,
                            available=True,
                            price=price,
                            source=SOURCE,
                            comment=f"LUKOIL official: {station_name}",
                        )
                        total_saved += 1
                    except Exception as e:
                        print(f"  ⚠ Save error (station {station_id}): {e}")

            if (i + 1) % 20 == 0:
                print(f"  Обработано: {i + 1}/{len(db_stations)}")

        # 4. Если нет АЗС в БД — создаём отчёты по known city prices
        if not db_stations and fuel_prices:
            print()
            print("[4] Нет АЗС в БД — сохраняем общие цены по региону...")
            # Сохраняем как "region price" для крупных городов
            major_cities = [
                "Москва",
                "Санкт-Петербург",
                "Краснодар",
                "Екатеринбург",
                "Новосибирск",
                "Казань",
                "Самара",
                "Нижний Новгород",
                "Ростов-на-Дону",
                "Челябинск",
                "Уфа",
                "Волгоград",
                "Воронеж",
                "Тюмень",
                "Омск",
            ]
            for city in major_cities:
                found = await db.find_stations_by_city(
                    city=city, network="лукойл", has_stock=False, limit=3
                )
                for s in found:
                    for fuel, price in fuel_prices.items():
                        if not args.dry_run:
                            await db.add_report(
                                station_id=s["id"],
                                fuel_type=fuel,
                                available=True,
                                price=price,
                                source=SOURCE,
                                comment=f"LUKOIL regional price: {city}",
                            )
                            total_saved += 1
                        total_stations += 1
                        total_with_prices += 1

    print()
    print("=== Итого ===")
    print(f"  АЗС обработано: {total_stations}")
    print(f"  АЗС с ценами: {total_with_prices}")
    print(f"  Отчётов сохранено: {total_saved}")
    print()
    print("💡 auto.lukoil.ru не имеет публичного API для цен.")
    print("💡 Цены парсятся из HTML (может быть неполным).")
    print("💡 Для полных данных используйте парсеры АЗС-агрегаторов.")
    print()

    if not db.API_MODE:
        await db.close_db()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
