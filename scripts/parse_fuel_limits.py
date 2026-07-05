#!/usr/bin/env python3
"""
Парсер данных о лимитах на топливо:
- Официальные ограничения (Минэнерго, Роспотребнадзор)
- Лимиты сетей АЗС
- Региональные ограничения
- Временные лимиты (кризисы, стихийные бедствия)
- Лимиты по типам транспорта

Источники:
- minenergo.gov.ru (Минэнерго)
- rpn.gov.ru (Роспотребнадзор)
- Официальные сайты сетей АЗС
- Новости и СМИ
- Пользовательские отчёты

Использование:
    python scripts/parse_fuel_limits.py --city Москва
    python scripts/parse_fuel_limits.py --all-cities
    python scripts/parse_fuel_limits.py --network Лукойл
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
# Стандартные лимиты
# ============================================================

# Лимиты по умолчанию (нормальная ситуация)
DEFAULT_LIMITS = {
    "92": {"per_visit": 200, "daily": 500, "weekly": 2000, "monthly": 8000},
    "95": {"per_visit": 200, "daily": 500, "weekly": 2000, "monthly": 8000},
    "98": {"per_visit": 150, "daily": 300, "weekly": 1000, "monthly": 4000},
    "dt": {"per_visit": 200, "daily": 500, "weekly": 2000, "monthly": 8000},
    "дт": {"per_visit": 200, "daily": 500, "weekly": 2000, "monthly": 8000},
}

# Лимиты в период кризиса
CRISIS_LIMITS = {
    "92": {"per_visit": 40, "daily": 100, "weekly": 300, "monthly": 1000},
    "95": {"per_visit": 40, "daily": 100, "weekly": 300, "monthly": 1000},
    "98": {"per_visit": 20, "daily": 50, "weekly": 150, "monthly": 500},
    "dt": {"per_visit": 40, "daily": 100, "weekly": 300, "monthly": 1000},
}

# Лимиты по типам транспорта
VEHICLE_LIMITS = {
    "легковой": {"per_visit": 200, "daily": 500, "weekly": 2000},
    "грузовой": {"per_visit": 500, "daily": 1000, "weekly": 5000},
    "автобус": {"per_visit": 400, "daily": 800, "weekly": 4000},
    "спецтехника": {"per_visit": 1000, "daily": 2000, "weekly": 10000},
    "мотоцикл": {"per_visit": 50, "daily": 100, "weekly": 400},
}

# Лимиты по регионам (в случае дефицита)
REGIONAL_LIMITS = {
    "москва": {"has_limits": False, "reason": None},
    "санкт-петербург": {"has_limits": False, "reason": None},
    "московская область": {"has_limits": False, "reason": None},
    "краснодарский край": {"has_limits": True, "reason": "сезонный дефицит"},
    "республика крым": {"has_limits": True, "reason": "транспортная изоляция"},
    "дальний восток": {"has_limits": True, "reason": "удалённость от НПЗ"},
}

# ============================================================
# Парсеры
# ============================================================

class LimitParser:
    """Парсер данных о лимитах."""

    async def parse_minenergo(self) -> dict:
        """Парсер данных Минэнерго."""
        limits = {}
        try:
            import aiohttp
            url = "https://minenergo.gov.ru/press-center/news"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        # Ищем упоминания лимитов
                        if "лимит" in text.lower() or "ограничени" in text.lower():
                            limits["has_gov_limits"] = True
                            # Извлекаем данные
                            limit_match = re.search(r'лимит.*?(\d+)\s*литр', text.lower())
                            if limit_match:
                                limits["gov_limit_liters"] = int(limit_match.group(1))
                            # Ищем регионы
                            regions_match = re.findall(r'(?:в|на)\s+([\w\s]+(?:области|краю|республике))', text.lower())
                            if regions_match:
                                limits["affected_regions"] = regions_match
        except Exception as e:
            logger.warning(f"Minenergo parse error: {e}")
        return limits

    async def parse_rpn(self) -> dict:
        """Парсер данных Роспотребнадзора."""
        limits = {}
        try:
            import aiohttp
            url = "https://rpn.gov.ru/press-center/news"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if "топлив" in text.lower() and ("качеств" in text.lower() or "ограничени" in text.lower()):
                            limits["has_rpn_notice"] = True
                            # Извлекаем рекомендации
                            if "рекоменду" in text.lower():
                                limits["recommendations"] = True
        except Exception as e:
            logger.warning(f"RPN parse error: {e}")
        return limits

    async def parse_network_limits(self, network: str) -> dict:
        """Парсер лимитов конкретной сети АЗС."""
        network_lower = network.lower()
        limits = {
            "network": network,
            "has_limits": False,
            "limits": {},
        }

        try:
            import aiohttp
            # Сайты сетей АЗС
            network_urls = {
                "лукойл": "https://lk.lukoil.ru/help/limits",
                "роснефть": "https://rngrup.ru/help/limits",
                "газпромнефть": "https://www.gazpromneft.ru/help/limits",
                "татнефть": "https://tatneft.ru/help/limits",
            }

            if network_lower in network_urls:
                url = network_urls[network_lower]
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            # Ищем лимиты
                            limit_match = re.search(r'лимит.*?(\d+)\s*литр', text.lower())
                            if limit_match:
                                limits["has_limits"] = True
                                limit_liters = int(limit_match.group(1))
                                limits["limits"] = {
                                    "per_visit": limit_liters,
                                    "daily": limit_liters * 2,
                                    "weekly": limit_liters * 5,
                                    "monthly": limit_liters * 20,
                                }
                            # Ищем исключения
                            if "исключени" in text.lower() or "освобожд" in text.lower():
                                limits["has_exceptions"] = True
                                # Извлекаем исключения
                                exceptions = re.findall(r'исключени[яе].*?([\w\s,]+)', text.lower())
                                if exceptions:
                                    limits["exceptions"] = exceptions
        except Exception as e:
            logger.warning(f"Network limits parse error: {e}")
        return limits

    async def parse_regional_limits(self, region: str) -> dict:
        """Парсер региональных лимитов."""
        region_lower = region.lower()
        if region_lower in REGIONAL_LIMITS:
            return REGIONAL_LIMITS[region_lower]
        return {"has_limits": False, "reason": None}

    async def parse_news_limits(self) -> list[dict]:
        """Парсер лимитов из новостей."""
        results = []
        try:
            import aiohttp
            # Поиск новостей о лимитах
            news_sources = [
                "https://ria.ru/search/?query=лимит+топлива",
                "https://tass.ru/search?query=ограничения+бензин",
                "https://www.rbc.ru/search/?query=дефицит+топлива",
            ]
            for url in news_sources:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                # Извлекаем заголовки и описания
                                headlines = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', text, re.DOTALL)
                                for headline in headlines[:5]:  # Ограничиваем 5 новостями
                                    clean = re.sub(r'<[^>]+>', '', headline).strip()
                                    if len(clean) > 10:
                                        results.append({
                                            "source": url.split("//")[1].split("/")[0],
                                            "headline": clean[:200],
                                            "has_limit_info": "лимит" in clean.lower() or "ограничени" in clean.lower(),
                                        })
                except:
                    continue
        except Exception as e:
            logger.warning(f"News limits error: {e}")
        return results

    async def parse_user_limits(self, city: str) -> list[dict]:
        """Парсер пользовательских отчётов о лимитах."""
        results = []
        try:
            import aiohttp
            url = f"https://api.user.reports/v1/limits?city={quote(city)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for report in data.get("reports", []):
                            results.append({
                                "station_id": report.get("station_id"),
                                "fuel_type": report.get("fuel_type"),
                                "limit_liters": report.get("limit_liters"),
                                "has_limit": report.get("has_limit", False),
                                "timestamp": report.get("timestamp"),
                            })
        except Exception as e:
            logger.warning(f"User limits error: {e}")
        return results

    async def determine_current_limits(self, city: str, network: str = None,
                                       fuel_type: str = None) -> dict:
        """Определяет текущие лимиты на основе всех данных."""
        now = datetime.now()
        limits = {
            "city": city,
            "network": network,
            "fuel_type": fuel_type,
            "timestamp": now.isoformat(),
            "limits": {},
            "source": "default",
        }

        # Проверяем региональные лимиты
        regional = await self.parse_regional_limits(city)
        if regional.get("has_limits"):
            limits["limits"] = CRISIS_LIMITS.get(fuel_type or "95", DEFAULT_LIMITS["95"])
            limits["source"] = "regional_crisis"
            limits["reason"] = regional.get("reason")
            return limits

        # Проверяем лимиты сети
        if network:
            network_limits = await self.parse_network_limits(network)
            if network_limits.get("has_limits"):
                limits["limits"] = network_limits.get("limits", DEFAULT_LIMITS.get(fuel_type or "95", {}))
                limits["source"] = f"network_{network}"
                return limits

        # Проверяем данные Минэнерго
        minenergo = await self.parse_minenergo()
        if minenergo.get("has_gov_limits"):
            limit_liters = minenergo.get("gov_limit_liters", 200)
            limits["limits"] = {
                "per_visit": limit_liters,
                "daily": limit_liters * 2,
                "weekly": limit_liters * 5,
                "monthly": limit_liters * 20,
            }
            limits["source"] = "minenergo"
            return limits

        # Лимиты по умолчанию
        limits["limits"] = DEFAULT_LIMITS.get(fuel_type or "95", DEFAULT_LIMITS["95"])
        limits["source"] = "default"
        return limits


# ============================================================
# Основная функция
# ============================================================

async def parse_all_limits(city: str = None, network: str = None) -> dict:
    """Парсит данные о лимитах со всех источников."""
    parser = LimitParser()
    results = {"city": city, "network": network, "timestamp": datetime.now().isoformat(), "data": {}}

    # Минэнерго
    logger.info("Parsing Minenergo...")
    minenergo = await parser.parse_minenergo()
    results["data"]["minenergo"] = minenergo

    # Роспотребнадзор
    logger.info("Parsing RPN...")
    rpn = await parser.parse_rpn()
    results["data"]["rpn"] = rpn

    # Лимиты сети
    if network:
        logger.info(f"Parsing network limits for {network}...")
        network_limits = await parser.parse_network_limits(network)
        results["data"]["network"] = network_limits

    # Региональные лимиты
    if city:
        logger.info(f"Parsing regional limits for {city}...")
        regional = await parser.parse_regional_limits(city)
        results["data"]["regional"] = regional

    # Новости
    logger.info("Parsing news for limits...")
    news = await parser.parse_news_limits()
    results["data"]["news"] = news

    # Пользовательские отчёты
    if city:
        logger.info(f"Parsing user reports for {city}...")
        user_reports = await parser.parse_user_limits(city)
        results["data"]["user_reports"] = user_reports

    # Определяем текущие лимиты
    logger.info("Determining current limits...")
    current_limits = await parser.determine_current_limits(city, network)
    results["data"]["current_limits"] = current_limits

    return results


async def save_limits_to_db(data: dict, station_id: int = None):
    """Сохраняет данные о лимитах в БД."""
    saved = 0
    current_limits = data.get("data", {}).get("current_limits", {})

    if station_id and current_limits:
        limits = current_limits.get("limits", {})
        try:
            await db.add_report(
                station_id=station_id,
                fuel_type=current_limits.get("fuel_type", "95"),
                available=True,
                has_limit=limits.get("per_visit", 200) < 200,  # Если лимит меньше стандартного
                limit_liters=limits.get("per_visit"),
                limit_per_visit=limits.get("per_visit"),
                limit_daily=limits.get("daily"),
                limit_weekly=limits.get("weekly"),
                comment=f"limits:{current_limits.get('source', 'unknown')}: {current_limits.get('reason', '')}",
                source=f"limits_{current_limits.get('source', 'default')}",
            )
            saved += 1
        except Exception as e:
            logger.warning(f"Error saving limits: {e}")

    # Сохраняем пользовательские отчёты
    for report in data.get("data", {}).get("user_reports", []):
        try:
            if report.get("station_id"):
                await db.add_report(
                    station_id=report["station_id"],
                    fuel_type=report.get("fuel_type", "95"),
                    available=True,
                    has_limit=report.get("has_limit", False),
                    limit_liters=report.get("limit_liters"),
                    comment=f"limits:user_report",
                    source="limits_user",
                )
                saved += 1
        except Exception as e:
            logger.warning(f"Error saving user report: {e}")

    return saved


async def main():
    parser = argparse.ArgumentParser(description="Parse fuel limits data")
    parser.add_argument("--city", default=None, help="City to parse")
    parser.add_argument("--all-cities", action="store_true", help="Parse all major cities")
    parser.add_argument("--network", default=None, help="Specific fuel network")
    parser.add_argument("--station-id", type=int, default=None, help="Specific station ID")
    args = parser.parse_args()

    await db.init_db()

    cities = [args.city] if args.city else (["Москва", "Санкт-Петербург", "Новосибирск"] if args.all_cities else [None])
    networks = [args.network] if args.network else ["лукойл", "роснефть", "газпромнефть"]

    total_saved = 0
    for city in cities:
        for network in networks:
            logger.info(f"=== Parsing limits for {city or 'all cities'} / {network} ===")
            data = await parse_all_limits(city, network)
            saved = await save_limits_to_db(data, args.station_id)
            total_saved += saved
            logger.info(f"Saved {saved} limit reports")

    await db.close_db()
    logger.info(f"=== Total limit reports saved: {total_saved} ===")
    return total_saved


if __name__ == "__main__":
    asyncio.run(main())
