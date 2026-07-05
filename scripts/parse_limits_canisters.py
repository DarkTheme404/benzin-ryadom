#!/usr/bin/env python3
"""Парсер лимитов на топливо и запретов на заливку в канистры.

Парсит:
1. Минэнерго (minenergo.gov.ru) — официальные ограничения
2. Новости РИА, ТАСС, РБК — лимиты и запреты
3. Официальные сайты сетей АЗС — внутренние ограничения
4. Региональные данные — Крым, ЛНР, ДНР, Дальний Восток

Сохраняет в reports:
- has_limit=True, limit_liters=N — лимит на заправку
- comment содержит детали

Использование:
    python scripts/parse_limits_canisters.py
    python scripts/parse_limits_canisters.py --city Москва
"""

import asyncio
import os
import sys
import re
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# Ключевые слова для поиска лимитов и запретов
# ============================================================

LIMIT_KEYWORDS = [
    "лимит", "ограничени", "запрет", "запрещ", "канистр",
    "не более", "не свыше", "максимум", "до литров",
    "в одни руки", "за один", "за посещени", "за заправк",
    "дефицит", "нехватк", "ограничива",
]

CANISTER_KEYWORDS = [
    "канистр", "каанист", "ёмкост", "емкост",
    "заливать", "залив", "отпуск", "отпускать",
    "тара", "бутыл", "корпус",
]

NEWS_SOURCES = [
    # Основные СМИ
    "https://ria.ru/search/?query=лимит+бензина",
    "https://ria.ru/search/?query=запрет+канистры+бензин",
    "https://tass.ru/search?query=ограничения+бензин+лимит",
    "https://tass.ru/search?query=канистры+запрет+заправк",
    "https://www.rbc.ru/search/?query=лимит+топлива+АЗС",
    "https://www.rbc.ru/search/?query=запрет+канистры+бензин",
    # Региональные
    "https://ria.ru/search/?query=дефицит+бензин+регион",
    "https://tass.ru/search?query=топливо+ограничени+регион",
    # Минэнерго
    "https://minenergo.gov.ru/press-center/news",
]

# ============================================================
# Известные лимиты по сетям АЗС (обновляется из новостей)
# ============================================================

KNOWN_LIMITS = {
    "лукойл": {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "comment": "Лукойл — без лимитов (норма)",
    },
    "роснефть": {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "comment": "Роснефть — без лимитов (норма)",
    },
    "газпромнефть": {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "comment": "Газпромнефть — без лимитов (норма)",
    },
    "татнефть": {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "comment": "Татнефть — без лимитов (норма)",
    },
    "никос": {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "comment": "НК «Ниско» — без лимитов (норма)",
    },
}

# ============================================================
# Известные лимиты по регионам
# ============================================================

REGIONAL_LIMITS = {
    "республика крым": {
        "has_limit": True,
        "limit_liters": 40,
        "canister_ban": True,
        "reason": "транспортная изоляция, дефицит",
    },
    "севастополь": {
        "has_limit": True,
        "limit_liters": 40,
        "canister_ban": True,
        "reason": "транспортная изоляция, дефицит",
    },
    "днр": {
        "has_limit": True,
        "limit_liters": 40,
        "canister_ban": True,
        "reason": "боевые действия, дефицит",
    },
    "лнр": {
        "has_limit": True,
        "limit_liters": 40,
        "canister_ban": True,
        "reason": "боевые действия, дефицит",
    },
    "краснодарский край": {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "reason": "сезонный рост цен",
    },
    "дальний восток": {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "reason": "удалённость от НПЗ",
    },
}


async def fetch_url(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Загружает URL с обработкой ошибок."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        ) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                logger.debug(f"HTTP {resp.status}: {url}")
    except Exception as e:
        logger.debug(f"Error fetching {url}: {e}")
    return None


def extract_limit_from_text(text: str) -> dict:
    """Извлекает данные о лимитах из текста."""
    result = {
        "has_limit": False,
        "limit_liters": None,
        "canister_ban": False,
        "keywords_found": [],
    }

    text_lower = text.lower()

    # Проверяем ключевые слова лимитов
    for kw in LIMIT_KEYWORDS:
        if kw in text_lower:
            result["keywords_found"].append(kw)

    # Проверяем ключевые слова канистр
    for kw in CANISTER_KEYWORDS:
        if kw in text_lower:
            result["canister_ban"] = True
            result["keywords_found"].append(kw)

    # Извлекаем числа — лимиты в литрах
    # Паттерн: "не более X литров", "лимит X л", "до X л", "не свыше X"
    limit_patterns = [
        r'(?:не более|лимит|не свыше|до|максимум)\s*(\d{1,4})\s*(?:л(?:итр)?|канистр)',
        r'(\d{1,4})\s*(?:л(?:итр)?|канистр)\s*(?:в одни руки|за заправк|за посещени)',
        r'(?:в одни руки|за заправк|за посещени)\s*(?:не более|лимит)?\s*(\d{1,4})\s*(?:л(?:итр)?)?',
    ]

    for pattern in limit_patterns:
        matches = re.findall(pattern, text_lower)
        for match in matches:
            try:
                num = int(match)
                if 5 <= num <= 1000:  # Разумный диапазон лимитов
                    result["has_limit"] = True
                    result["limit_liters"] = num
                    break
            except ValueError:
                continue

    return result


async def parse_news_limits(session: aiohttp.ClientSession) -> list:
    """Парсит новости о лимитах."""
    results = []

    for url in NEWS_SOURCES:
        html = await fetch_url(session, url)
        if not html:
            continue

        # Извлекаем заголовки и описания
        # RIA, TASS, RBC используют разные форматы

        # Паттерн 1: <h2> или <h3> с текстом
        headlines = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL)

        # Паттерн 2: <a> с заголовками статей
        headlines += re.findall(r'<a[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</a>', html, re.DOTALL)

        # Паттерн 3: description/meta
        headlines += re.findall(r'<p[^>]*class="[^"]*lead[^"]*"[^>]*>(.*?)</p>', html, re.DOTALL)

        for headline in headlines[:20]:  # Ограничиваем
            clean = re.sub(r'<[^>]+>', '', headline).strip()
            if len(clean) < 10:
                continue

            # Проверяем на наличие ключевых слов лимитов
            has_limit_words = any(kw in clean.lower() for kw in LIMIT_KEYWORDS)
            has_canister_words = any(kw in clean.lower() for kw in CANISTER_KEYWORDS)

            if has_limit_words or has_canister_words:
                limit_data = extract_limit_from_text(clean)
                results.append({
                    "source": url.split("//")[1].split("/")[0],
                    "headline": clean[:300],
                    "has_limit": limit_data["has_limit"],
                    "limit_liters": limit_data["limit_liters"],
                    "canister_ban": limit_data["canister_ban"],
                })

    return results


async def parse_minenergo_limits(session: aiohttp.ClientSession) -> dict:
    """Парсит данные Минэнерго."""
    result = {
        "has_gov_limits": False,
        "gov_limit_liters": None,
        "affected_regions": [],
    }

    html = await fetch_url(session, "https://minenergo.gov.ru/press-center/news")
    if not html:
        return result

    text_lower = html.lower()

    # Ищем упоминания лимитов
    if "лимит" in text_lower or "ограничени" in text_lower:
        result["has_gov_limits"] = True

        # Извлекаем числа
        limit_match = re.search(r'лимит.*?(\d+)\s*литр', text_lower)
        if limit_match:
            result["gov_limit_liters"] = int(limit_match.group(1))

        # Ищем регионы
        regions = re.findall(r'(?:в|на)\s+([\w\s]+(?:области|краю|республике|республики))', text_lower)
        if regions:
            result["affected_regions"] = [r.strip() for r in regions[:10]]

    return result


async def parse_network_limits(session: aiohttp.ClientSession, network: str) -> dict:
    """Парсит лимиты конкретной сети АЗС."""
    network_lower = network.lower()
    result = {
        "network": network,
        "has_limits": False,
        "canister_ban": False,
        "limit_liters": None,
        "comment": "",
    }

    # Проверяем известные лимиты
    if network_lower in KNOWN_LIMITS:
        known = KNOWN_LIMITS[network_lower]
        result["has_limits"] = known["has_limit"]
        result["limit_liters"] = known.get("limit_liters")
        result["canister_ban"] = known.get("canister_ban", False)
        result["comment"] = known.get("comment", "")
        return result

    # Пробуем загрузить сайт сети
    network_urls = {
        "лукойл": "https://lk.lukoil.ru/",
        "роснефть": "https://rngrup.ru/",
        "газпромнефть": "https://www.gazpromneft.ru/",
        "татнефть": "https://tatneft.ru/",
        "никос": "https://nk-nisco.ru/",
    }

    if network_lower in network_urls:
        html = await fetch_url(session, network_urls[network_lower])
        if html:
            text_lower = html.lower()
            # Ищем упоминания лимитов
            if "лимит" in text_lower or "ограничени" in text_lower:
                result["has_limits"] = True
                limit_match = re.search(r'лимит.*?(\d+)\s*литр', text_lower)
                if limit_match:
                    result["limit_liters"] = int(limit_match.group(1))
            # Ищем запрет канистр
            if "канистр" in text_lower and ("запрет" in text_lower or "запрещ" in text_lower):
                result["canister_ban"] = True

    return result


async def find_stations_for_limit(station_id: int = None, city: str = None) -> list:
    """Находит станции для которых нужно сохранить лимиты."""
    if station_id:
        row = await db._fetch("SELECT id FROM stations WHERE id=?", station_id)
        return [row[0]] if row else []

    if city:
        rows = await db._fetch(
            "SELECT id FROM stations WHERE city LIKE ? OR address LIKE ? LIMIT 100",
            f"%{city}%", f"%{city}%"
        )
        return [r["id"] for r in rows]

    # Все станции (ограничиваем чтобы не перегружать)
    rows = await db._fetch("SELECT id FROM stations LIMIT 5000")
    return [r["id"] for r in rows]


async def save_limit_to_db(station_id: int, fuel_type: str, has_limit: bool,
                           limit_liters: int = None, canister_ban: bool = False,
                           comment: str = "", source: str = "limits_news"):
    """Сохраняет лимит в БД."""
    try:
        # Проверяем дубликаты
        if db.USE_SQLITE:
            existing = await db._fetch(
                """SELECT id FROM reports
                   WHERE station_id=? AND source=?
                   AND created_at > datetime('now', '-12 hours') LIMIT 1""",
                station_id, source
            )
        else:
            existing = await db._fetch(
                """SELECT id FROM reports
                   WHERE station_id=$1 AND source=$2
                   AND created_at > NOW() - INTERVAL '12 hours' LIMIT 1""",
                station_id, source
            )
        if existing:
            return False

        # Формируем комментарий
        full_comment = comment
        if canister_ban:
            full_comment += " | ЗАПРЕТ НА КАНИСТРЫ"
        if has_limit and limit_liters:
            full_comment += f" | ЛИМИТ: {limit_liters} л"

        await db._execute(
            """INSERT INTO reports (station_id, fuel_type, available, has_limit, limit_liters,
                                    source, created_at, comment)
               VALUES (?, ?, 1, ?, ?, ?, datetime('now'), ?)""",
            station_id,
            fuel_type,
            has_limit,
            limit_liters,
            source,
            full_comment[:500],
        )
        return True
    except Exception as e:
        logger.warning(f"Error saving limit for station {station_id}: {e}")
        return False


async def main():
    if not db.API_MODE:
        await db.init_db()

    total_saved = 0
    total_stations = 0

    async with aiohttp.ClientSession() as session:
        # 1. Парсим новости о лимитах
        logger.info("=== Парсим новости о лимитах ===")
        news_limits = await parse_news_limits(session)
        logger.info(f"Найдено {len(news_limits)} новостей о лимитах")

        # 2. Парсим Минэнерго
        logger.info("=== Парсим Минэнерго ===")
        minenergo = await parse_minenergo_limits(session)
        if minenergo["has_gov_limits"]:
            logger.info(f"Минэнерго: лимит {minenergo['gov_limit_liters']} л")
        else:
            logger.info("Минэнерго: активных лимитов нет")

        # 3. Парсим сети АЗС
        logger.info("=== Парсим сети АЗС ===")
        networks = ["лукойл", "роснефть", "газпромнефть", "татнефть", "никос"]
        network_limits = {}
        for network in networks:
            limits = await parse_network_limits(session, network)
            network_limits[network] = limits
            logger.info(f"  {network}: limits={limits['has_limits']}, canister_ban={limits['canister_ban']}")

        # 4. Сохраняем данные о лимитах для всех станций
        logger.info("=== Сохраняем данные о лимитах ===")
        station_ids = await find_stations_for_limit()
        total_stations = len(station_ids)
        logger.info(f"Станций для обработки: {total_stations}")

        # Определяем глобальные лимиты из новостей
        global_has_limit = False
        global_limit_liters = None
        global_canister_ban = False

        for news in news_limits:
            if news.get("has_limit"):
                global_has_limit = True
                if news.get("limit_liters"):
                    global_limit_liters = news["limit_liters"]
            if news.get("canister_ban"):
                global_canister_ban = True

        # Если Минэнерго сообщает о лимитах
        if minenergo["has_gov_limits"] and minenergo["gov_limit_liters"]:
            global_has_limit = True
            global_limit_liters = minenergo["gov_limit_liters"]

        # Сохраняем для каждой станции
        for station_id in station_ids:
            saved = await save_limit_to_db(
                station_id=station_id,
                fuel_type="all",
                has_limit=global_has_limit,
                limit_liters=global_limit_liters,
                canister_ban=global_canister_ban,
                comment=f"Глобальные лимиты из новостей: {len(news_limits)} источников",
                source="limits_global_news",
            )
            if saved:
                total_saved += 1

        # 5. Сохраняем региональные лимиты (Крым, ЛНР, ДНР)
        logger.info("=== Сохраняем региональные лимиты ===")
        for region_name, region_data in REGIONAL_LIMITS.items():
            if region_data.get("has_limit"):
                # Находим станции в регионе
                rows = await db._fetch(
                    "SELECT id FROM stations WHERE city LIKE ? OR region LIKE ? LIMIT 500",
                    f"%{region_name}%", f"%{region_name}%"
                )
                for row in rows:
                    saved = await save_limit_to_db(
                        station_id=row["id"],
                        fuel_type="all",
                        has_limit=True,
                        limit_liters=region_data.get("limit_liters"),
                        canister_ban=region_data.get("canister_ban", False),
                        comment=f"Региональные лимиты: {region_data.get('reason', '')}",
                        source=f"limits_regional_{region_name}",
                    )
                    if saved:
                        total_saved += 1

    logger.info(f"\n=== LIMITS/CANISTERS ИТОГО ===")
    logger.info(f"  Станций обработано: {total_stations}")
    logger.info(f"  Лимитов сохранено: {total_saved}")
    logger.info(f"  Новостей о лимитах: {len(news_limits)}")
    logger.info(f"  Глобальный лимит: {global_has_limit} ({global_limit_liters} л)")
    logger.info(f"  Запрет канистр: {global_canister_ban}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
