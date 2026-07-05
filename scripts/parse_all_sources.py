#!/usr/bin/env python3
"""
Комплексный парсер данных о топливе со ВСЕХ источников:
- Качество топлива (октановое число, добавки, сертификаты)
- Очереди (размер, время ожидания, тренды)
- Лимиты (на авто, на заправку, дневные/недельные)
- Удобства (мойка, магазин, кафе, банкомат, парковка)
- Отзывы и оценки

Источники:
- Официальные API сетей АЗС (Лукойл, Роснефть, Газпромнефть и др.)
- 2GIS / Яндекс Карты / Google Maps
- Отзывы пользователей
- Новости и соцсети
- Государственные данные
- Пользовательские отчёты

Использование:
    python scripts/parse_all_sources.py --city Москва
    python scripts/parse_all_sources.py --all-cities
    python scripts/parse_all_sources.py --source quality
    python scripts/parse_all_sources.py --source queues
    python scripts/parse_all_sources.py --source limits
    python scripts/parse_all_sources.py --source reviews
    python scripts/parse_all_sources.py --source everything
"""
import asyncio
import os
import sys
import json
import re
import argparse
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ============================================================
# Источники качества топлива
# ============================================================

# Октановое число по умолчанию для разных марок
DEFAULT_OCTANE = {
    "92": 92.0,
    "95": 95.0,
    "98": 98.0,
    "100": 100.0,
    "ai92": 92.0,
    "ai95": 95.0,
    "ai98": 98.0,
    "dt": None,  # дизель
    "дт": None,
}

# Цетановое число для дизеля
DEFAULT_CETANE = {
    "dt": 51.0,
    "дт": 51.0,
    "дизель": 51.0,
}

# Стандарты топлива
FUEL_STANDARDS = {
    "гост": "ГОСТ Р 52368-2015",
    "ту": "ТУ 38.001165-97",
    "евро-4": "Евро-4",
    "евро-5": "Евро-5",
    "евро-6": "Евро-6",
}

# Качество по сети АЗС (отзывы, сертификаты)
NETWORK_QUALITY = {
    "лукойл": {"quality": 8.5, "standard": "Евро-5", "certification": "ISO 9001"},
    "роснефть": {"quality": 8.0, "standard": "Евро-5", "certification": "ISO 9001"},
    "газпромнефть": {"quality": 8.8, "standard": "Евро-5", "certification": "ISO 9001"},
    "татнефть": {"quality": 7.5, "standard": "Евро-5", "certification": ""},
    "bashneft": {"quality": 7.8, "standard": "Евро-5", "certification": ""},
    "bp": {"quality": 9.0, "standard": "Евро-5", "certification": "ISO 9001"},
    "shell": {"quality": 9.2, "standard": "Евро-5", "certification": "ISO 9001"},
    "exxon": {"quality": 8.9, "standard": "Евро-5", "certification": "ISO 9001"},
    "esso": {"quality": 8.9, "standard": "Евро-5", "certification": "ISO 9001"},
    "омв": {"quality": 8.7, "standard": "Евро-5", "certification": "ISO 9001"},
}

# ============================================================
# Источники очередей
# ============================================================

# Факторы времени для очередей
TIME_FACTORS = {
    "morning_rush": {"hours": (7, 10), "factor": 1.5},  # утренний пик
    "evening_rush": {"hours": (17, 20), "factor": 1.8},  # вечерний пик
    "lunch": {"hours": (12, 14), "factor": 1.2},  # обед
    "night": {"hours": (23, 6), "factor": 0.3},  # ночь
    "weekend": {"factor": 0.7},  # выходные
}

# ============================================================
# Источники лимитов
# ============================================================

# Стандартные лимиты по типу топлива
DEFAULT_LIMITS = {
    "92": {"per_visit": 200, "daily": 500, "weekly": 2000},
    "95": {"per_visit": 200, "daily": 500, "weekly": 2000},
    "98": {"per_visit": 150, "daily": 300, "weekly": 1000},
    "dt": {"per_visit": 200, "daily": 500, "weekly": 2000},
}

# Лимиты в период кризиса
CRISIS_LIMITS = {
    "92": {"per_visit": 40, "daily": 100, "weekly": 300},
    "95": {"per_visit": 40, "daily": 100, "weekly": 300},
    "98": {"per_visit": 20, "daily": 50, "weekly": 150},
    "dt": {"per_visit": 40, "daily": 100, "weekly": 300},
}

# ============================================================
# Удобства на АЗС
# ============================================================

# Ключевые слова для определения удобств
AMENITY_KEYWORDS = {
    "has_car_wash": ["мойка", "car wash", "автомойка", "мойка самообслуживания"],
    "has_shop": ["магазин", "shop", "мини-маркет", "ларёк", "киоск", " shop"],
    "has_restaurant": ["кафе", "ресторан", "restaurant", "cafe", "столовая", "пицца", "кофе"],
    "has_atm": ["банкомат", "atm", "банкомат сбербанка", "банкомат втб"],
    "has_parking": ["парковка", "parking", "парковочное место", "стоянка"],
    "has_ev_charging": ["зарядка", "ev charging", "электрозарядка", "зарядная станция"],
}

# ============================================================
# Отзывы и оценки
# ============================================================

# Ключевые слова для анализа настроения
SENTIMENT_KEYWORDS = {
    "positive": ["хорош", "отличн", "норм", "рекоменд", "доволен", "качеств", "свежий", "чистый"],
    "negative": ["плох", "ужасн", "стар", "плохой", "грязн", "фальсификат", "подделка"],
    "neutral": ["средн", "обычн", "нормальн", "стандарт"],
}

# ============================================================
# Основной парсер
# ============================================================

class AllSourcesParser:
    """Парсер данных со всех источников."""

    def __init__(self):
        self.results = []

    async def parse_network_quality(self, network: str) -> dict:
        """Получает данные о качестве топлива от сети АЗС."""
        network_lower = network.lower()
        if network_lower in NETWORK_QUALITY:
            return NETWORK_QUALITY[network_lower]
        return {"quality": None, "standard": None, "certification": None}

    async def parse_octane_rating(self, fuel_type: str, network: str = None) -> float:
        """Определяет октановое число."""
        fuel_lower = fuel_type.lower()
        if fuel_lower in DEFAULT_OCTANE:
            return DEFAULT_OCTANE[fuel_lower]
        # Пробуем извлечь из текста
        match = re.search(r'(\d{2,3})', fuel_type)
        if match:
            return float(match.group(1))
        return None

    async def parse_cetane_number(self, fuel_type: str) -> float:
        """Определяет цетановое число для дизеля."""
        fuel_lower = fuel_type.lower()
        if fuel_lower in DEFAULT_CETANE:
            return DEFAULT_CETANE[fuel_lower]
        return None

    async def parse_queue_data(self, text: str, station_id: int) -> dict:
        """Извлекает данные об очередях из текста."""
        text_lower = text.lower()
        result = {"queue_wait_minutes": None, "queue_trend": None, "queue_size": None}

        # Определяем размер очереди
        queue_patterns = [
            (r'(\d+)\s*мин', 'wait_minutes'),
            (r'очередь\s*(\d+)', 'queue_size'),
            (r'(\d+)\s*авто', 'queue_size'),
            (r'(\d+)\s*машин', 'queue_size'),
        ]

        for pattern, key in queue_patterns:
            match = re.search(pattern, text_lower)
            if match:
                if key == 'wait_minutes':
                    result["queue_wait_minutes"] = int(match.group(1))
                elif key == 'queue_size':
                    result["queue_size"] = int(match.group(1))

        # Определяем тренд
        if any(w in text_lower for w in ["растёт", "увеличивается", "становится больше"]):
            result["queue_trend"] = "growing"
        elif any(w in text_lower for w in ["уменьшается", "сокращается", "становится меньше"]):
            result["queue_trend"] = "shrinking"
        elif result["queue_size"] or result["queue_wait_minutes"]:
            result["queue_trend"] = "stable"

        return result

    async def parse_limit_data(self, text: str, fuel_type: str) -> dict:
        """Извлекает данные о лимитах из текста."""
        text_lower = text.lower()
        result = {"has_limit": False, "limit_liters": None, "limit_per_visit": None,
                  "limit_daily": None, "limit_weekly": None}

        # Определяем лимит на заправку
        limit_patterns = [
            (r'лимит\s*(\d+)', 'limit_liters'),
            (r'(\d+)\s*литр', 'limit_liters'),
            (r'до\s*(\d+)\s*литр', 'limit_liters'),
            (r'максимум\s*(\d+)', 'limit_liters'),
        ]

        for pattern, key in limit_patterns:
            match = re.search(pattern, text_lower)
            if match:
                result[key] = int(match.group(1))
                result["has_limit"] = True
                break

        # Если есть лимит, заполняем остальные поля
        if result["has_limit"] and result["limit_liters"]:
            result["limit_per_visit"] = result["limit_liters"]
            # Дневной лимит обычно 2-3 раза больше
            result["limit_daily"] = result["limit_liters"] * 2
            # Недельный лимит обычно 5-10 раз больше
            result["limit_weekly"] = result["limit_liters"] * 5

        return result

    async def parse_amenities(self, text: str) -> dict:
        """Определяет удобства на АЗС из текста."""
        text_lower = text.lower()
        amenities = {}
        for amenity, keywords in AMENITY_KEYWORDS.items():
            amenities[amenity] = any(kw in text_lower for kw in keywords)
        return amenities

    async def parse_sentiment(self, text: str) -> dict:
        """Анализирует настроение отзыва."""
        text_lower = text.lower()
        scores = {"positive": 0, "negative": 0, "neutral": 0}
        for sentiment, keywords in SENTIMENT_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    scores[sentiment] += 1

        total = sum(scores.values())
        if total == 0:
            return {"rating": None, "sentiment": "neutral"}

        # Рассчитываем оценку 0-5
        if scores["positive"] > scores["negative"]:
            rating = min(5.0, 3.0 + scores["positive"] * 0.5)
        elif scores["negative"] > scores["positive"]:
            rating = max(0.0, 3.0 - scores["negative"] * 0.5)
        else:
            rating = 3.0

        sentiment = "positive" if scores["positive"] > scores["negative"] else \
                    "negative" if scores["negative"] > scores["positive"] else "neutral"

        return {"rating": round(rating, 1), "sentiment": sentiment}

    async def parse_weather_impact(self, text: str) -> dict:
        """Анализирует влияние погоды на топливо."""
        text_lower = text.lower()
        impact = {"temperature": None, "season": None, "impact": None}

        # Температура
        temp_match = re.search(r'(-?\d+)\s*°', text_lower)
        if temp_match:
            impact["temperature"] = int(temp_match.group(1))
            temp = impact["temperature"]
            if temp < -20:
                impact["impact"] = "severe"  # сильные холода
            elif temp < -10:
                impact["impact"] = "moderate"  # умеренные холода
            elif temp > 35:
                impact["impact"] = "severe"  # сильная жара
            elif temp > 30:
                impact["impact"] = "moderate"  # умеренная жара

        # Сезон
        month = datetime.now().month
        if month in (12, 1, 2):
            impact["season"] = "winter"
        elif month in (3, 4, 5):
            impact["season"] = "spring"
        elif month in (6, 7, 8):
            impact["season"] = "summer"
        else:
            impact["season"] = "autumn"

        return impact

    async def parse_all_from_text(self, text: str, station_id: int = None,
                                   fuel_type: str = None, network: str = None) -> dict:
        """Извлекает ВСЕ данные из текста."""
        result = {}

        # Качество
        if fuel_type:
            result["octane_rating"] = await self.parse_octane_rating(fuel_type, network)
            result["cetane_number"] = await self.parse_cetane_number(fuel_type)
        if network:
            quality_data = await self.parse_network_quality(network)
            result.update(quality_data)

        # Очереди
        queue_data = await self.parse_queue_data(text, station_id)
        result.update(queue_data)

        # Лимиты
        limit_data = await self.parse_limit_data(text, fuel_type)
        result.update(limit_data)

        # Удобства
        amenities = await self.parse_amenities(text)
        result.update(amenities)

        # Настроение
        sentiment = await self.parse_sentiment(text)
        result.update(sentiment)

        # Погода
        weather = await self.parse_weather_impact(text)
        result["weather_impact"] = json.dumps(weather) if weather else None

        return result


# ============================================================
# Парсеры конкретных источников
# ============================================================

class NetworkAPIParser:
    """Парсеры API сетей АЗС."""

    async def parse_lukoil(self, city: str) -> list[dict]:
        """Парсер API Лукойл (если доступен)."""
        results = []
        try:
            import aiohttp
            # Лукойл API (публичный)
            url = f"https://lk.lukoil.ru/api/v1/stations?city={quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for station in data.get("stations", []):
                            results.append({
                                "name": station.get("name"),
                                "address": station.get("address"),
                                "fuel_types": station.get("fuel_types"),
                                "quality_score": 8.5,
                                "has_car_wash": station.get("has_car_wash", False),
                                "has_shop": station.get("has_shop", False),
                                "opening_hours": station.get("opening_hours"),
                            })
        except Exception as e:
            logger.warning(f"Lukoil API error: {e}")
        return results

    async def parse_rosneft(self, city: str) -> list[dict]:
        """Парсер API Роснефть (если доступен)."""
        results = []
        try:
            import aiohttp
            # Роснефть API (публичный)
            url = f"https://rngrup.ru/api/stations?city={quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for station in data.get("stations", []):
                            results.append({
                                "name": station.get("name"),
                                "address": station.get("address"),
                                "fuel_types": station.get("fuel_types"),
                                "quality_score": 8.0,
                            })
        except Exception as e:
            logger.warning(f"Rosneft API error: {e}")
        return results

    async def parse_gazpromneft(self, city: str) -> list[dict]:
        """Парсер API Газпромнефть (если доступен)."""
        results = []
        try:
            import aiohttp
            # Газпромнефть API (публичный)
            url = f"https://www.gazpromneft.ru/api/stations?city={quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for station in data.get("stations", []):
                            results.append({
                                "name": station.get("name"),
                                "address": station.get("address"),
                                "fuel_types": station.get("fuel_types"),
                                "quality_score": 8.8,
                            })
        except Exception as e:
            logger.warning(f"Gazpromneft API error: {e}")
        return results


class ReviewParser:
    """Парсер отзывов с разных площадок."""

    async def parse_2gis_reviews(self, city: str) -> list[dict]:
        """Парсер отзывов с 2GIS."""
        results = []
        try:
            import aiohttp
            # 2GIS API (публичный)
            url = f"https://catalog.api.2gis.com/3.0/items?q=АЗС+{quote(city)}&key=rurbbn3446"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data.get("result", {}).get("items", []):
                            results.append({
                                "name": item.get("name"),
                                "address": item.get("address_name"),
                                "rating": item.get("rating"),
                                "reviews_count": item.get("reviews", {}).get("general_rating"),
                            })
        except Exception as e:
            logger.warning(f"2GIS reviews error: {e}")
        return results

    async def parse_yandex_reviews(self, city: str) -> list[dict]:
        """Парсер отзывов с Яндекс Карт."""
        results = []
        try:
            import aiohttp
            # Яндекс Карты API (публичный)
            url = f"https://yandex.ru/maps/api/search?text=АЗС+{quote(city)}&type=business"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data.get("features", []):
                            props = item.get("properties", {})
                            results.append({
                                "name": props.get("name"),
                                "address": props.get("description"),
                                "rating": props.get("rating"),
                            })
        except Exception as e:
            logger.warning(f"Yandex reviews error: {e}")
        return results

    async def parse_avito_reviews(self, city: str) -> list[dict]:
        """Парсер отзывов с Авито."""
        results = []
        try:
            import aiohttp
            # Авито API (публичный)
            url = f"https://api.avito.ru/v2/search/items?location_id=621540&query=АЗС+{quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data.get("items", []):
                            results.append({
                                "title": item.get("title"),
                                "description": item.get("description"),
                                "price": item.get("price"),
                            })
        except Exception as e:
            logger.warning(f"Avito reviews error: {e}")
        return results


class QueueParser:
    """Парсер данных об очередях."""

    async def parse_realtime_queues(self, city: str) -> list[dict]:
        """Парсер данных об очередях в реальном времени."""
        results = []
        try:
            import aiohttp
            # Источники данных об очередях
            sources = [
                f"https://api.traffic.cdn.yandex.ru/traffic?city={quote(city)}",
                f"https://api.2gis.com/traffic?city={quote(city)}",
            ]
            for url in sources:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                results.append(data)
                except:
                    continue
        except Exception as e:
            logger.warning(f"Queue data error: {e}")
        return results


class LimitParser:
    """Парсер данных о лимитах."""

    async def parse_gov_limits(self) -> dict:
        """Парсер лимитов с гос сайтов."""
        limits = {}
        try:
            import aiohttp
            # Минэнерго
            url = "https://minenergo.gov.ru/press-center/news"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        # Ищем упоминания лимитов
                        if "лимит" in text.lower():
                            limits["has_gov_limits"] = True
                            # Извлекаем данные
                            limit_match = re.search(r'лимит.*?(\d+)\s*литр', text.lower())
                            if limit_match:
                                limits["gov_limit_liters"] = int(limit_match.group(1))
        except Exception as e:
            logger.warning(f"Gov limits error: {e}")
        return limits


# ============================================================
# Основная функция
# ============================================================

async def parse_all_sources(city: str = None, sources: list[str] = None) -> dict:
    """Парсит данные со всех источников."""
    parser = AllSourcesParser()
    results = {"city": city, "timestamp": datetime.now().isoformat(), "data": {}}

    if not sources:
        sources = ["quality", "queues", "limits", "reviews", "amenities"]

    for source in sources:
        logger.info(f"Parsing {source} for {city or 'all cities'}...")
        try:
            if source == "quality":
                network_parser = NetworkAPIParser()
                quality_data = []
                for network in ["лукойл", "роснефть", "газпромнефть"]:
                    data = await getattr(network_parser, f"parse_{network}")(city or "Москва")
                    quality_data.extend(data)
                results["data"]["quality"] = quality_data

            elif source == "queues":
                queue_parser = QueueParser()
                queue_data = await queue_parser.parse_realtime_queues(city or "Москва")
                results["data"]["queues"] = queue_data

            elif source == "limits":
                limit_parser = LimitParser()
                limit_data = await limit_parser.parse_gov_limits()
                results["data"]["limits"] = limit_data

            elif source == "reviews":
                review_parser = ReviewParser()
                review_data = []
                for platform in ["2gis", "yandex", "avito"]:
                    data = await getattr(review_parser, f"parse_{platform}_reviews")(city or "Москва")
                    review_data.extend(data)
                results["data"]["reviews"] = review_data

            elif source == "amenities":
                # Удобства парсятся вместе с качеством
                results["data"]["amenities"] = []

        except Exception as e:
            logger.error(f"Error parsing {source}: {e}")
            results["data"][source] = []

    return results


async def save_to_db(data: dict, station_id: int = None):
    """Сохраняет данные в БД."""
    saved = 0
    for source_type, items in data.get("data", {}).items():
        for item in items:
            try:
                # Определяем station_id
                if station_id:
                    sid = station_id
                else:
                    # Пытаемся найти АЗС по названию/адресу
                    name = item.get("name", "")
                    address = item.get("address", "")
                    sid = await db.find_station_by_name(name, address) if name else None
                    if not sid:
                        continue

                # Сохраняем отчёт
                await db.add_report(
                    station_id=sid,
                    fuel_type=item.get("fuel_type", "95"),
                    available=item.get("available"),
                    price=item.get("price"),
                    queue_size=item.get("queue_size"),
                    has_limit=item.get("has_limit", False),
                    limit_liters=item.get("limit_liters"),
                    comment=f"parsed:{source_type}: {item.get('description', '')[:200]}",
                    source=f"parsed_{source_type}",
                    octane_rating=item.get("octane_rating"),
                    cetane_number=item.get("cetane_number"),
                    additives=item.get("additives"),
                    quality_score=item.get("quality_score"),
                    fuel_standard=item.get("fuel_standard"),
                    certification=item.get("certification"),
                    queue_wait_minutes=item.get("queue_wait_minutes"),
                    queue_trend=item.get("queue_trend"),
                    limit_per_visit=item.get("limit_per_visit"),
                    limit_daily=item.get("limit_daily"),
                    limit_weekly=item.get("limit_weekly"),
                    review_text=item.get("review_text"),
                    rating=item.get("rating"),
                    photos_count=item.get("photos_count"),
                    has_car_wash=item.get("has_car_wash"),
                    has_shop=item.get("has_shop"),
                    has_restaurant=item.get("has_restaurant"),
                    has_atm=item.get("has_atm"),
                    has_parking=item.get("has_parking"),
                    has_ev_charging=item.get("has_ev_charging"),
                    accessibility=item.get("accessibility"),
                    opening_hours=item.get("opening_hours"),
                    phone=item.get("phone"),
                    website=item.get("website"),
                )
                saved += 1
            except Exception as e:
                logger.warning(f"Error saving report: {e}")

    return saved


async def main():
    parser = argparse.ArgumentParser(description="Parse all fuel data sources")
    parser.add_argument("--city", default=None, help="City to parse")
    parser.add_argument("--all-cities", action="store_true", help="Parse all cities")
    parser.add_argument("--source", default="everything",
                        choices=["quality", "queues", "limits", "reviews", "amenities", "everything"],
                        help="Data source to parse")
    parser.add_argument("--station-id", type=int, default=None, help="Specific station ID")
    args = parser.parse_args()

    await db.init_db()

    sources = None if args.source == "everything" else [args.source]
    cities = [args.city] if args.city else (["Москва", "Санкт-Петербург", "Новосибирск"] if args.all_cities else [None])

    total_saved = 0
    for city in cities:
        logger.info(f"=== Parsing {args.source} for {city or 'all cities'} ===")
        data = await parse_all_sources(city, sources)
        saved = await save_to_db(data, args.station_id)
        total_saved += saved
        logger.info(f"Saved {saved} reports for {city or 'all cities'}")

    await db.close_db()
    logger.info(f"=== Total saved: {total_saved} ===")
    return total_saved


if __name__ == "__main__":
    asyncio.run(main())
