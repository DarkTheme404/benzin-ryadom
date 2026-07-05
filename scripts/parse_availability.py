"""
Парсер данных о наличии топлива с crowdsourced сервисов:
- gdebenz.ru (ГдеБЕНЗ) — карта наличия топлива (27K+ АЗС)
- fuelprice.ru — цены и наличие
- benzup.ru — цены и наличие (18K+ АЗС)

⚠️ Использует только публичные данные. Не нарушает ToS.

Использование:
    python scripts/parse_availability.py              # один проход
    python scripts/parse_availability.py --source gdebenz  # только gdebenz
    python scripts/parse_availability.py --source fuelprice # только fuelprice
    python scripts/parse_availability.py --source benzup    # только benzup
    python scripts/parse_availability.py --all             # все источники
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Загружаем .env из bot/
ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"
load_dotenv(ENV_PATH)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("availability_parser")

# ======================================================================
# GdeBenz.ru — crowdsourced карта наличия топлива
# ======================================================================

GDEBENZ_BASE_URL = "https://gdebenz.ru"
GDEBENZ_API_URL = "https://gdebenz.ru/api"

# Городские страницы GdeBenz (название → slug)
GDEBENZ_CITIES = {
    "Москва": "moskva",
    "Санкт-Петербург": "sankt-peterburg",
    "Новосибирск": "novosibirsk",
    "Екатеринбург": "ekaterinburg",
    "Казань": "kazan",
    "Нижний Новгород": "nizhniy-novgorod",
    "Челябинск": "chelyabinsk",
    "Самара": "samara",
    "Омск": "omsk",
    "Ростов-на-Дону": "rostov-na-donu",
    "Уфа": "ufa",
    "Красноярск": "krasnoyarsk",
    "Воронеж": "voronezh",
    "Волгоград": "volgograd",
    "Пермь": "perm",
    "Краснодар": "krasnodar",
    "Тюмень": "tyumen",
    "Саратов": "saratov",
    "Тольятти": "togliatti",
    "Ижевск": "izhevsk",
    "Барнаул": "barnaul",
    "Иркутск": "irkutsk",
    "Хабаровск": "khabarovsk",
    "Владивосток": "vladivostok",
    "Ярославль": "yaroslavl",
    "Махачкала": "mahachkala",
    "Томск": "tomsk",
    "Оренбург": "orenburg",
    "Кемерово": "kemerovo",
    "Новокузнецк": "novokuznetsk",
    "Астрахань": "astrakhan",
    "Рязань": "ryazan",
    "Пенза": "penza",
    "Липецк": "lipetsk",
    "Тула": "tula",
    "Калуга": "kaluga",
    "Смоленск": "smolensk",
    "Брянск": "bryansk",
    "Курск": "kursk",
    "Тамбов": "tambov",
    "Псков": "pskov",
    "Владимир": "vladimir",
    "Кострома": "kostroma",
    "Иваново": "ivanovo",
    "Тверь": "tver",
    "Ульяновск": "ulyanovsk",
    "Чебоксары": "cheboksary",
    "Саранск": "saransk",
    "Киров": "kirov",
    "Сургут": "surgut",
    "Нижневартовск": "nizhnevartovsk",
    "Курган": "kurgan",
    "Чита": "chita",
    "Якутск": "yakutsk",
    "Ставрополь": "stavropol",
    "Калининград": "kaliningrad",
    "Мурманск": "murmansk",
    "Архангельск": "arkhangelsk",
    "Сочи": "sochi",
    "Ноябрьск": "noyabrsk",
    "Салехард": "salekhard",
    "Надым": "nadym",
    "Обнинск": "obninsk",
}

# Типы топлива GdeBenz
FUEL_TYPE_MAP = {
    "АИ-92": "92",
    "АИ-95": "95",
    "АИ-98": "98",
    "АИ-100": "100",
    "ДТ": "diesel",
    "92": "92",
    "95": "95",
    "98": "98",
    "100": "100",
    "дизель": "diesel",
    "диз": "diesel",
}

# Статусы GdeBenz → available
GDEBENZ_STATUS_MAP = {
    "есть": True,
    "есть бензин": True,
    "в наличии": True,
    "льют": True,
    "работает": True,
    "нет": False,
    "нет бензина": False,
    "закончился": False,
    "пусто": False,
    "очередь": None,  # "очередь" = есть, но долго ждать
    "ограничение": None,  # "ограничение" = есть, но с лимитом
}


async def parse_gdebenz_city(city_name: str, city_slug: str) -> list[dict]:
    """Парсит данные GdeBenz для одного города.

    Возвращает список [{station_name, fuel_type, available, source, city}, ...]
    """
    import aiohttp

    results = []
    url = f"{GDEBENZ_BASE_URL}/{city_slug}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0 (parser)"},
            ) as resp:
                if resp.status != 200:
                    logger.warning("GdeBenz %s: HTTP %d", city_name, resp.status)
                    return results

                html = await resp.text()

                # Парсим HTML: ищем данные о станциях
                # GdeBenz использует JSON в script тегах или API
                # Паттерн: station data в JSON
                station_pattern = re.compile(
                    r'"name"\s*:\s*"([^"]+)".*?'
                    r'"fuel"\s*:\s*"([^"]+)".*?'
                    r'"status"\s*:\s*"([^"]+)"',
                    re.DOTALL
                )

                for match in station_pattern.finditer(html):
                    station_name = match.group(1)
                    fuel_raw = match.group(2)
                    status_raw = match.group(3).lower()

                    # Нормализуем тип топлива
                    fuel_type = FUEL_TYPE_MAP.get(fuel_raw, fuel_raw.lower())

                    # Нормализуем статус
                    available = GDEBENZ_STATUS_MAP.get(status_raw)
                    if available is None and status_raw in ("есть", "есть бензин", "в наличии"):
                        available = True
                    elif available is None and status_raw in ("нет", "нет бензина", "закончился"):
                        available = False

                    results.append({
                        "station_name": station_name,
                        "fuel_type": fuel_type,
                        "available": available,
                        "source": "gdebenzru",
                        "city": city_name,
                    })

                logger.info("GdeBenz %s: found %d stations", city_name, len(results))

    except Exception as e:
        logger.warning("GdeBenz %s error: %s", city_name, e)

    return results


async def parse_gdebenz_all() -> list[dict]:
    """Парсит GdeBenz для всех городов."""
    all_results = []
    for city_name, city_slug in GDEBENZ_CITIES.items():
        results = await parse_gdebenz_city(city_name, city_slug)
        all_results.extend(results)
        await asyncio.sleep(1)  # не ддосить

    logger.info("GdeBenz total: %d results from %d cities",
                len(all_results), len(GDEBENZ_CITIES))
    return all_results


# ======================================================================
# FuelPrice.ru — цены на топливо по городам
# ======================================================================

FUELPRICE_BASE_URL = "https://fuelprice.ru"

FUELPRICE_CITIES = {
    "Москва": "moskva",
    "Санкт-Петербург": "sankt-peterburg",
    "Новосибирск": "novosibirsk",
    "Екатеринбург": "ekaterinburg",
    "Казань": "kazan",
    "Нижний Новгород": "nizhniy-novgorod",
    "Челябинск": "chelyabinsk",
    "Самара": "samara",
    "Омск": "omsk",
    "Ростов-на-Дону": "rostov-na-donu",
    "Уфа": "ufa",
    "Красноярск": "krasnoyarsk",
    "Воронеж": "voronezh",
    "Волгоград": "volgograd",
    "Пермь": "perm",
    "Краснодар": "krasnodar",
    "Тюмень": "tyumen",
    "Саратов": "saratov",
    "Тольятти": "togliatti",
    "Ижевск": "izhevsk",
    "Барнаул": "barnaul",
    "Иркутск": "irkutsk",
    "Хабаровск": "khabarovsk",
    "Владивосток": "vladivostok",
    "Ярославль": "yaroslavl",
    "Томск": "tomsk",
    "Оренбург": "orenburg",
    "Кемерово": "kemerovo",
    "Новокузнецк": "novokuznetsk",
    "Астрахань": "astrakhan",
    "Рязань": "ryazan",
    "Пенза": "penza",
    "Липецк": "lipetsk",
    "Тула": "tula",
    "Калуга": "kaluga",
    "Смоленск": "smolensk",
    "Брянск": "bryansk",
    "Курск": "kursk",
    "Тамбов": "tambov",
    "Ульяновск": "ulyanovsk",
    "Чебоксары": "cheboksary",
    "Киров": "kirov",
    "Сургут": "surgut",
    "Курган": "kurgan",
    "Чита": "chita",
    "Ставрополь": "stavropol",
    "Калининград": "kaliningrad",
    "Мурманск": "murmansk",
    "Архангельск": "arkhangelsk",
    "Сочи": "sochi",
}


async def parse_fuelprice_city(city_name: str, city_slug: str) -> list[dict]:
    """Парсит цены FuelPrice.ru для одного города."""
    import aiohttp

    results = []
    url = f"{FUELPRICE_BASE_URL}/{city_slug}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0 (parser)"},
            ) as resp:
                if resp.status != 200:
                    logger.warning("FuelPrice %s: HTTP %d", city_name, resp.status)
                    return results

                html = await resp.text()

                # Ищем таблицы с ценами
                # Паттерн: "АИ-95" ... "56.40"
                price_pattern = re.compile(
                    r'(?:АИ-?(92|95|98|100)|ДТ|дизель)\s*'
                    r'(?:</[^>]+>\s*)*?'
                    r'(?:<[^>]+>\s*)*?'
                    r'(\d{2,3}[.,]\d{2})\s*(?:₽|руб)',
                    re.IGNORECASE
                )

                for match in price_pattern.finditer(html):
                    fuel_raw = match.group(1).lower()
                    price_str = match.group(2).replace(",", ".")

                    try:
                        price = float(price_str)
                        if 20 < price < 200:
                            # FuelPrice показывает средние цены по городу
                            # Привязываем к случайной станции
                            results.append({
                                "station_name": f"FuelPrice {city_name}",
                                "fuel_type": fuel_raw,
                                "price": price,
                                "available": True,  # если есть цена — есть топливо
                                "source": "fuelprice_ru",
                                "city": city_name,
                            })
                    except ValueError:
                        pass

                logger.info("FuelPrice %s: found %d prices", city_name, len(results))

    except Exception as e:
        logger.warning("FuelPrice %s error: %s", city_name, e)

    return results


async def parse_fuelprice_all() -> list[dict]:
    """Парсит FuelPrice для всех городов."""
    all_results = []
    for city_name, city_slug in FUELPRICE_CITIES.items():
        results = await parse_fuelprice_city(city_name, city_slug)
        all_results.extend(results)
        await asyncio.sleep(1)

    logger.info("FuelPrice total: %d results from %d cities",
                len(all_results), len(FUELPRICE_CITIES))
    return all_results


# ======================================================================
# BenzUp.ru — цены и наличие (18K+ АЗС)
# ======================================================================

BENZUP_BASE_URL = "https://benzup.ru"


async def parse_benzup() -> list[dict]:
    """Парсит данные BenzUp.ru (API или сайт).

    BenzUp имеет API: https://benzup.ru/api
    Но для парсинга используем публичные данные.
    """
    import aiohttp

    results = []

    try:
        async with aiohttp.ClientSession() as session:
            # BenzUp API endpoint (публичный)
            url = f"{BENZUP_BASE_URL}/api/stations"
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0 (parser)"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        name = st.get("name", "")
                        city = st.get("city", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "station_name": name,
                                    "fuel_type": fuel.lower(),
                                    "price": float(price),
                                    "available": True,
                                    "source": "benzup_ru",
                                    "city": city,
                                })
                    logger.info("BenzUp API: found %d prices", len(results))
                else:
                    logger.warning("BenzUp API: HTTP %d", resp.status)

    except Exception as e:
        logger.warning("BenzUp error: %s", e)

    return results


# ======================================================================
# Сохранение в БД
# ======================================================================

async def save_availability_results(results: list[dict]) -> int:
    """Сохраняет результаты парсинга наличия в БД.

    Возвращает количество сохранённых отчётов.
    """
    saved = 0

    for r in results:
        station_name = r.get("station_name", "")
        city = r.get("city", "")
        fuel_type = r.get("fuel_type", "")
        available = r.get("available")
        price = r.get("price")
        source = r.get("source", "unknown")

        if not station_name or not fuel_type:
            continue

        # Ищем станцию в БД
        station_id = None

        # 1) По имени + городу
        if city:
            stations = await db.find_stations_by_name(
                f"{station_name} {city}",
                limit=1,
                priority_city=city,
            )
            if stations:
                station_id = stations[0].get("id")

        # 2) По городу (если имя не найдено)
        if not station_id and city:
            stations = await db.find_stations_by_city(
                city=city,
                limit=1,
            )
            if stations:
                station_id = stations[0].get("id")

        # 3) Создаём временную станцию (если не нашли)
        if not station_id:
            station_id = await db.upsert_station_for_import(
                name=station_name,
                region=city or "Россия",
                city=city,
                operator=source,
            )

        if not station_id:
            continue

        # Сохраняем отчёт
        await db.add_report(
            station_id=station_id,
            fuel_type=fuel_type,
            available=available,
            price=price,
            source=source,
            comment=f"{source}: {station_name}",
        )
        saved += 1

    return saved


# ======================================================================
# Основная функция
# ======================================================================

async def main():
    parser = argparse.ArgumentParser(description="Парсер наличия топлива")
    parser.add_argument("--source", choices=["gdebenz", "fuelprice", "benzup"],
                        help="Один источник")
    parser.add_argument("--all", action="store_true", help="Все источники")
    args = parser.parse_args()

    if not db.API_MODE:
        await db.init_db()

    all_results = []

    if args.source == "gdebenz" or args.all:
        print("=== Парсинг GdeBenz.ru ===")
        results = await parse_gdebenz_all()
        all_results.extend(results)
        print(f"  Найдено: {len(results)}")

    if args.source == "fuelprice" or args.all:
        print("=== Парсинг FuelPrice.ru ===")
        results = await parse_fuelprice_all()
        all_results.extend(results)
        print(f"  Найдено: {len(results)}")

    if args.source == "benzup" or args.all:
        print("=== Парсинг BenzUp.ru ===")
        results = await parse_benzup()
        all_results.extend(results)
        print(f"  Найдено: {len(results)}")

    if not all_results:
        print("Нет данных для сохранения")
        return

    print(f"\n=== Всего найдено: {len(all_results)} ===")
    print("Сохранение в БД...")
    saved = await save_availability_results(all_results)
    print(f"Сохранено: {saved}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
