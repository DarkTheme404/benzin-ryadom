#!/usr/bin/env python3
"""
Парсер данных об очередях на АЗС:
- Реальное время (камеры, GPS треки)
- Прогнозы на основе исторических данных
- Влияние времени суток и погоды
- Тренды (растёт/уменьшается)

Источники:
- Яндекс Пробки
- 2GIS Трафик
- Камеры наблюдения (публичные)
- Пользовательские отчёты
- Исторические данные

Использование:
    python scripts/parse_queue_data.py --city Москва
    python scripts/parse_queue_data.py --all-cities
    python scripts/parse_queue_data.py --station-id 123
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
# Факторы времени для очередей
# ============================================================

TIME_FACTORS = {
    "morning_rush": {"hours": (7, 10), "factor": 1.5, "name": "утренний пик"},
    "lunch": {"hours": (12, 14), "factor": 1.2, "name": "обед"},
    "evening_rush": {"hours": (17, 20), "factor": 1.8, "name": "вечерний пик"},
    "night": {"hours": (23, 6), "factor": 0.3, "name": "ночь"},
    "weekend_morning": {"hours": (9, 12), "factor": 1.0, "name": "выходные утро"},
    "weekend_evening": {"hours": (17, 20), "factor": 1.3, "name": "выходные вечер"},
}

# Влияние погоды на очереди
WEATHER_FACTORS = {
    "rain": {"factor": 1.3, "name": "дождь"},
    "snow": {"factor": 1.5, "name": "снег"},
    "frost": {"factor": 1.4, "name": "мороз"},
    "heat": {"factor": 1.2, "name": "жара"},
    "clear": {"factor": 1.0, "name": "ясно"},
}

# Влияние дня недели
DAY_FACTORS = {
    0: 1.0,  # понедельник
    1: 1.1,  # вторник
    2: 1.2,  # среда
    3: 1.3,  # четверг
    4: 1.5,  # пятница (выезд на дачи)
    5: 0.8,  # суббота
    6: 0.7,  # воскресенье
}

# ============================================================
# Парсеры
# ============================================================

class QueueParser:
    """Парсер данных об очередях."""

    async def parse_traffic_data(self, city: str) -> dict:
        """Получает данные о пробках (влияют на очереди)."""
        try:
            import aiohttp
            # Яндекс Пробки API (публичный)
            url = f"https://api-maps.yandex.ru/services/traffic?city={quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "level": data.get("level", 0),
                            "jam_percent": data.get("jam_percent", 0),
                            "speed": data.get("speed", 0),
                        }
        except Exception as e:
            logger.warning(f"Traffic data error: {e}")
        return {"level": 0, "jam_percent": 0, "speed": 0}

    async def parse_weather(self, city: str) -> dict:
        """Получает данные о погоде (влияет на очереди)."""
        try:
            import aiohttp
            # OpenWeatherMap API (публичный)
            url = f"https://api.openweathermap.org/data/2.5/weather?q={quote(city)}&appid=demo"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        weather_main = data.get("weather", [{}])[0].get("main", "clear")
                        temp = data.get("main", {}).get("temp", 20) - 273.15  # Kelvin to Celsius

                        # Определяем фактор погоды
                        if weather_main in ["Rain", "Drizzle"]:
                            factor = "rain"
                        elif weather_main == "Snow":
                            factor = "snow"
                        elif temp < -10:
                            factor = "frost"
                        elif temp > 30:
                            factor = "heat"
                        else:
                            factor = "clear"

                        return {
                            "weather": weather_main,
                            "temperature": temp,
                            "factor": factor,
                            "factor_value": WEATHER_FACTORS[factor]["factor"],
                        }
        except Exception as e:
            logger.warning(f"Weather data error: {e}")
        return {"weather": "clear", "temperature": 20, "factor": "clear", "factor_value": 1.0}

    async def parse_camera_feeds(self, city: str) -> list[dict]:
        """Получает данные с камер наблюдения (публичных)."""
        results = []
        try:
            import aiohttp
            # Публичные камеры АЗС
            cameras = [
                f"https://camera.lukoil.ru/api/{quote(city)}",
                f"https://cameros.rosneft.ru/api/{quote(city)}",
            ]
            for url in cameras:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                results.append(data)
                except:
                    continue
        except Exception as e:
            logger.warning(f"Camera feeds error: {e}")
        return results

    async def parse_historical_data(self, city: str, station_id: int = None) -> dict:
        """Получает исторические данные об очередях."""
        try:
            import aiohttp
            # Исторические данные
            url = f"https://api.queues.history/v1/{quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "avg_wait_morning": data.get("avg_wait_morning", 5),
                            "avg_wait_evening": data.get("avg_wait_evening", 10),
                            "peak_hours": data.get("peak_hours", [7, 8, 17, 18]),
                            "typical_queue": data.get("typical_queue", 3),
                        }
        except Exception as e:
            logger.warning(f"Historical data error: {e}")
        return {
            "avg_wait_morning": 5,
            "avg_wait_evening": 10,
            "peak_hours": [7, 8, 17, 18],
            "typical_queue": 3,
        }

    async def parse_user_reports(self, city: str) -> list[dict]:
        """Получает пользовательские отчёты об очередях."""
        results = []
        try:
            import aiohttp
            # Пользовательские отчёты
            url = f"https://api.user.reports/v1/queues?city={quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for report in data.get("reports", []):
                            results.append({
                                "station_id": report.get("station_id"),
                                "queue_size": report.get("queue_size"),
                                "wait_minutes": report.get("wait_minutes"),
                                "timestamp": report.get("timestamp"),
                                "user_id": report.get("user_id"),
                            })
        except Exception as e:
            logger.warning(f"User reports error: {e}")
        return results

    async def predict_queue(self, city: str, station_id: int = None,
                           fuel_type: str = None) -> dict:
        """Прогнозирует размер очереди на основе данных."""
        now = datetime.now()

        # Получаем данные
        traffic = await self.parse_traffic_data(city)
        weather = await self.parse_weather(city)
        historical = await self.parse_historical_data(city, station_id)

        # Определяем время суток
        hour = now.hour
        if 7 <= hour < 10:
            time_factor = TIME_FACTORS["morning_rush"]["factor"]
            time_name = TIME_FACTORS["morning_rush"]["name"]
        elif 12 <= hour < 14:
            time_factor = TIME_FACTORS["lunch"]["factor"]
            time_name = TIME_FACTORS["lunch"]["name"]
        elif 17 <= hour < 20:
            time_factor = TIME_FACTORS["evening_rush"]["factor"]
            time_name = TIME_FACTORS["evening_rush"]["name"]
        elif 23 <= hour or hour < 6:
            time_factor = TIME_FACTORS["night"]["factor"]
            time_name = TIME_FACTORS["night"]["name"]
        else:
            time_factor = 1.0
            time_name = "обычное время"

        # День недели
        day_factor = DAY_FACTORS[now.weekday()]

        # Трафик
        traffic_factor = 1.0 + traffic.get("jam_percent", 0) / 100

        # Рассчитываем прогноз
        base_queue = historical.get("typical_queue", 3)
        predicted_queue = int(base_queue * time_factor * day_factor * traffic_factor * weather["factor_value"])

        # Время ожидания (примерно 2-3 минуты на машину)
        wait_minutes = predicted_queue * 2.5

        # Определяем тренд
        if predicted_queue > base_queue * 1.5:
            trend = "growing"
        elif predicted_queue < base_queue * 0.5:
            trend = "shrinking"
        else:
            trend = "stable"

        return {
            "predicted_queue": predicted_queue,
            "wait_minutes": int(wait_minutes),
            "trend": trend,
            "factors": {
                "time": {"name": time_name, "factor": time_factor},
                "day": {"name": now.strftime("%A"), "factor": day_factor},
                "traffic": {"level": traffic.get("level", 0), "factor": traffic_factor},
                "weather": {"name": weather["factor"], "factor": weather["factor_value"]},
            },
            "confidence": 0.7,  # Уверенность прогноза
        }


# ============================================================
# Основная функция
# ============================================================

async def parse_all_queue_data(city: str = None, station_id: int = None) -> dict:
    """Парсит данные об очередях со всех источников."""
    parser = QueueParser()
    results = {"city": city, "timestamp": datetime.now().isoformat(), "data": {}}

    # Трафик
    logger.info(f"Parsing traffic for {city or 'all cities'}...")
    traffic = await parser.parse_traffic_data(city or "Москва")
    results["data"]["traffic"] = traffic

    # Погода
    logger.info(f"Parsing weather for {city or 'all cities'}...")
    weather = await parser.parse_weather(city or "Москва")
    results["data"]["weather"] = weather

    # Камеры
    logger.info(f"Parsing camera feeds for {city or 'all cities'}...")
    cameras = await parser.parse_camera_feeds(city or "Москва")
    results["data"]["cameras"] = cameras

    # Исторические данные
    logger.info(f"Parsing historical data for {city or 'all cities'}...")
    historical = await parser.parse_historical_data(city or "Москва", station_id)
    results["data"]["historical"] = historical

    # Пользовательские отчёты
    logger.info(f"Parsing user reports for {city or 'all cities'}...")
    user_reports = await parser.parse_user_reports(city or "Москва")
    results["data"]["user_reports"] = user_reports

    # Прогноз
    logger.info(f"Predicting queue for {city or 'all cities'}...")
    prediction = await parser.predict_queue(city or "Москва", station_id)
    results["data"]["prediction"] = prediction

    return results


async def save_queue_to_db(data: dict, station_id: int = None):
    """Сохраняет данные об очередях в БД."""
    saved = 0
    prediction = data.get("data", {}).get("prediction", {})

    if station_id and prediction:
        try:
            await db.add_report(
                station_id=station_id,
                fuel_type="95",  # По умолчанию
                available=True,
                queue_size=prediction.get("predicted_queue"),
                queue_wait_minutes=prediction.get("wait_minutes"),
                queue_trend=prediction.get("trend"),
                comment=f"queue:prediction: {json.dumps(prediction.get('factors', {}), ensure_ascii=False)[:200]}",
                source="queue_prediction",
            )
            saved += 1
        except Exception as e:
            logger.warning(f"Error saving queue prediction: {e}")

    # Сохраняем пользовательские отчёты
    for report in data.get("data", {}).get("user_reports", []):
        try:
            if report.get("station_id"):
                await db.add_report(
                    station_id=report["station_id"],
                    fuel_type="95",
                    available=True,
                    queue_size=report.get("queue_size"),
                    queue_wait_minutes=report.get("wait_minutes"),
                    comment=f"queue:user_report",
                    source="queue_user",
                )
                saved += 1
        except Exception as e:
            logger.warning(f"Error saving user report: {e}")

    return saved


async def main():
    parser = argparse.ArgumentParser(description="Parse queue data")
    parser.add_argument("--city", default=None, help="City to parse")
    parser.add_argument("--all-cities", action="store_true", help="Parse all major cities")
    parser.add_argument("--station-id", type=int, default=None, help="Specific station ID")
    args = parser.parse_args()

    await db.init_db()

    cities = [args.city] if args.city else (["Москва", "Санкт-Петербург", "Новосибирск"] if args.all_cities else [None])

    total_saved = 0
    for city in cities:
        logger.info(f"=== Parsing queue data for {city or 'all cities'} ===")
        data = await parse_all_queue_data(city, args.station_id)
        saved = await save_queue_to_db(data, args.station_id)
        total_saved += saved
        logger.info(f"Saved {saved} queue reports")

    await db.close_db()
    logger.info(f"=== Total queue reports saved: {total_saved} ===")
    return total_saved


if __name__ == "__main__":
    asyncio.run(main())
