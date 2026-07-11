#!/usr/bin/env python3
"""Парсер новостей про топливо через публичные RSS-ленты российских СМИ.

Покрывает ВСЮ Россию — федеральные новости, упоминающие топливо в городах.
Без авторизации. Использует рабочие RSS: Kommersant, Lenta, Vedomosti, Interfax.

Использование:
  python3 scripts/parse_news.py
  python3 scripts/parse_news.py --feeds kommersant,lenta
"""
import argparse
import asyncio
import logging
import os
import re
import sys
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402
from parse_vk import (  # noqa: E402
    parse_prices, detect_network, detect_city, detect_queue,
    detect_availability, parse_next_delivery,
)

logger = logging.getLogger("parse_news")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Публичные RSS-ленты (без auth, без капчи)
RSS_FEEDS = {
    "kommersant": {
        "url": "https://www.kommersant.ru/RSS/news.xml",
        "name": "Kommersant",
        "source": "news_kommersant",
    },
    "lenta": {
        "url": "https://lenta.ru/rss/news",
        "name": "Lenta.ru",
        "source": "news_lenta",
    },
    "vedomosti": {
        "url": "https://www.vedomosti.ru/rss/news",
        "name": "Vedomosti",
        "source": "news_vedomosti",
    },
    "interfax": {
        "url": "https://www.interfax.ru/rss.asp",
        "name": "Interfax",
        "source": "news_interfax",
    },
    "tass": {
        "url": "https://tass.ru/rss/v2.xml",
        "name": "TASS",
        "source": "news_tass",
    },
    "ria": {
        "url": "https://ria.ru/export/rss2/archive/index.xml",
        "name": "RIA",
        "source": "news_ria",
    },
    "avto": {
        "url": "https://www.avto.ru/rss/news.xml",
        "name": "Avto.ru",
        "source": "news_avto",
    },
}

# Ключевые слова для определения топливной тематики
FUEL_KEYWORDS = [
    "бензин", "топлив", "азс", "заправк", "дизель", "горюч",
    "нефтепродукт", "аи-92", "аи-95", "аи-98", "аи-100",
    "литр", "топливо есть", "бензин есть", "дефицит топлив",
    "очередь на заправк", "рост цен на бензин",
]

# Исключения (чтобы не ловить шум)
EXCLUDE_KEYWORDS = [
    "автобус", "троллейбус", "электро", "водород",
    "акциз", "налог на бензин", "нк рф ст",
]


def is_fuel_related(text: str) -> bool:
    """Проверяет, относится ли текст к топливу."""
    text_lower = text.lower()
    if not any(kw in text_lower for kw in FUEL_KEYWORDS):
        return False
    # Исключаем если про электричество и т.п.
    return True


async def fetch_rss(
    session: aiohttp.ClientSession,
    url: str,
    limit: int = 50,
) -> list[dict]:
    """Получает посты с RSS-ленты."""
    try:
        async with session.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                logger.warning(f"  RSS {url}: {r.status}")
                return []
            text = await r.text()
    except Exception as e:
        logger.warning(f"  RSS fetch: {e}")
        return []

    # Парсим XML
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except Exception as e:
        logger.debug(f"  XML parse: {e}")
        return []

    items = []
    # RSS 2.0: <item> внутри <channel>
    for item in root.iter("item"):
        title = ""
        description = ""
        link = ""
        for child in item:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "title":
                title = child.text or ""
            elif tag == "description":
                description = child.text or ""
            elif tag == "link":
                link = child.text or ""

        full_text = f"{title}. {description}".strip()
        if is_fuel_related(full_text):
            items.append({
                "title": title,
                "description": description,
                "text": full_text,
                "link": link,
            })

        if len(items) >= limit:
            break

    return items


async def save_news_post(
    item: dict,
    stations_cache: dict,
    source: str,
) -> int:
    """Сохраняет новость про топливо как отчёт."""
    text = item.get("text", "")
    if len(text) < 30:
        return 0

    prices = parse_prices(text)
    network = detect_network(text)
    detected_city = detect_city(text) or ""
    queue = detect_queue(text)
    available = detect_availability(text)

    # Создаём виртуальную станцию (используем "all" если город не определён)
    station_name = f"{source}: {network or 'news'} ({detected_city or 'Россия'})"
    if station_name.lower() not in stations_cache:
        try:
            if db.USE_SQLITE:
                result = await db._execute(
                    """INSERT INTO stations (name, lat, lon, city, region, operator, is_active, fuel_types, created_at)
                       VALUES (?, ?, ?, ?, '', ?, 1, ?, datetime('now'))""",
                    station_name, 0.0, 0.0, detected_city, network or "", '["92","95"]',
                    returning=True,
                )
                new_id = result if isinstance(result, int) else None
            else:
                async with db._db.acquire() as conn:
                    row = await conn.fetchrow(
                        """INSERT INTO stations (name, lat, lon, city, region, operator, is_active, fuel_types, created_at)
                           VALUES ($1, $2, $3, $4, '', $5, TRUE, $6, NOW())
                           RETURNING id""",
                        station_name, 0.0, 0.0, detected_city, network or "", ["92", "95"],
                    )
                    new_id = row["id"] if row else None
            if not new_id:
                rows = await db._fetch("SELECT id FROM stations WHERE name = ?", station_name)
                new_id = rows[0]["id"] if rows else None
            if not new_id:
                return 0
            stations_cache[station_name.lower()] = new_id
            station_id = new_id
        except Exception as e:
            logger.warning(f"  insert station '{station_name}': {e}")
            return 0
    else:
        station_id = stations_cache[station_name.lower()]

    saved = 0
    if prices:
        for fuel, price in prices.items():
            try:
                await db.add_report(
                    station_id=station_id,
                    fuel_type=fuel,
                    available=available,
                    price=price,
                    queue_size=queue,
                    source=source,
                    comment=f"{source}: {text[:200]}",
                )
                saved += 1
            except Exception as e:
                logger.debug(f"  add_report: {e}")
    elif available is not None:
        try:
            await db.add_report(
                station_id=station_id,
                fuel_type="all",
                available=available,
                queue_size=queue,
                source=source,
                comment=f"{source}: {text[:200]}",
            )
            saved += 1
        except Exception as e:
            logger.warning(f"  add_report failed: {e}")
    else:
        # Даже без цены/availability — сохраняем как информационный отчёт
        try:
            await db.add_report(
                station_id=station_id,
                fuel_type="all",
                available=None,  # неизвестно
                source=source,
                comment=f"{source}: {text[:300]}",
            )
            saved += 1
        except Exception as e:
            logger.warning(f"  add_report failed: {e}")

    return saved


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feeds", default="kommersant,lenta,vedomosti,interfax",
                        help="Какие ленты парсить (через запятую)")
    parser.add_argument("--limit", type=int, default=100, help="Лимит на ленту")
    args = parser.parse_args()

    if not db.API_MODE:
        await db.init_db()

    selected_feeds = []
    for feed_id in args.feeds.split(","):
        feed_id = feed_id.strip()
        if feed_id in RSS_FEEDS:
            selected_feeds.append((feed_id, RSS_FEEDS[feed_id]))

    logger.info(f"Парсинг {len(selected_feeds)} лент: {[f[0] for f in selected_feeds]}")

    # Загружаем кеш станций
    stations_cache: dict[str, int] = {}
    try:
        rows = await db._fetch("SELECT id, name FROM stations")
        for r in rows:
            stations_cache[r["name"].lower()] = r["id"]
    except Exception as e:
        logger.warning(f"Cache load: {e}")

    total_saved = 0
    total_items = 0
    fuel_items = 0

    async with aiohttp.ClientSession() as session:
        for feed_id, feed_info in selected_feeds:
            try:
                items = await fetch_rss(session, feed_info["url"], args.limit)
                total_items += len(items)
                feed_saved = 0
                for item in items:
                    fuel_items += 1
                    saved = await save_news_post(
                        item, stations_cache, feed_info["source"]
                    )
                    feed_saved += saved
                total_saved += feed_saved
                logger.info(f"  {feed_info['name']}: {len(items)} статей, {feed_saved} отчётов")
            except Exception as e:
                logger.warning(f"  {feed_info['name']}: {e}")
            await asyncio.sleep(1)

    logger.info(f"\n=== NEWS ИТОГО ===")
    logger.info(f"  Лент обработано: {len(selected_feeds)}")
    logger.info(f"  Всего статей: {total_items}")
    logger.info(f"  Топливных: {fuel_items}")
    logger.info(f"  Отчётов сохранено: {total_saved}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
