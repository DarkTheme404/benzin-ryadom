#!/usr/bin/env python3
"""Парсер данных с benzinmap.ru — региональные ограничения на продажу топлива.

Загружает data.json с benzinmap.ru и сохраняет как source='benzinmap'.
Информация о лимитах по регионам.
"""

import asyncio
import sys
import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BENZINMAP_URL = "https://benzinmap.ru/data.json"

# Статусы benzinmap → наш формат
STATUS_MAP = {
    "stopped": "нет топлива",
    "limit": "лимиты",
    "local": "локальные проблемы",
}


async def fetch_data() -> Optional[dict]:
    """Загружает data.json с benzinmap.ru."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(BENZINMAP_URL, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                else:
                    logger.warning(f"HTTP {resp.status} from benzinmap.ru")
                    return None
    except Exception as e:
        logger.error(f"Failed to fetch benzinmap.ru data: {e}")
        return None


async def save_region_data(data: dict):
    """Сохраняет данные по регионам как 'отчёты' с source='benzinmap'."""
    if not data or "regions" not in data:
        logger.warning("No region data found")
        return

    saved = 0
    updated = data.get("updated", "unknown")

    for region in data["regions"]:
        code = region.get("code", "")
        name = region.get("ru", "")
        status = region.get("status", "")
        detail = region.get("detail", "")
        price = region.get("price", "")
        price_note = region.get("priceNote", "")
        src_name = region.get("srcName", "")

        status_text = STATUS_MAP.get(status, status)

        # Ищем станции в этом регионе
        stations = await db._fetch(
            """SELECT id FROM stations WHERE city LIKE ? OR address LIKE ? LIMIT 10""",
            (f"%{name.replace('область', '').replace('край', '').replace('республика', '').strip()}%",
             f"%{name}%")
        )

        if stations:
            for station in stations:
                # Создаём "отчёт" о ситуации в регионе
                report_text = f"[{src_name}] {status_text}: {detail}"
                if price:
                    report_text += f" | Цена: {price} ({price_note})"

                await db._execute(
                    """INSERT INTO reports (station_id, fuel_type, available, price, source, created_at, comment)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        station["id"],
                        "all",
                        None,  # available - неизвестно для региона
                        None,  # price - тоже
                        "benzinmap",
                        datetime.now(timezone.utc).isoformat(),
                        report_text[:500],
                    )
                )
                saved += 1
        else:
            # Если станций нет — создаём "виртуальную" запись о регионе
            # Как отчёт без привязки к станции
            pass

    logger.info(f"BenZinMap: saved {saved} region reports (data from {updated})")


async def main():
    await db.init_db()
    logger.info("Fetching benzinmap.ru data...")
    data = await fetch_data()
    if data:
        await save_region_data(data)
    else:
        logger.error("Failed to fetch data from benzinmap.ru")
    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
