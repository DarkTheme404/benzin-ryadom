"""
Парсер цен с сайтов сетей АЗС (Лукойл, Газпромнефть, Роснефть, Татнефть).

⚠️ ВНИМАНИЕ: парсинг может нарушать ToS сайтов.
Используй ответственно, не нагружай сервера.

Источники:
  - Лукойл: https://lukoil.ru (есть API, но закрытый)
  - Газпромнефть: https://www.gazprom-neft.ru
  - Роснефть: https://www.rosneft.ru
  - Татнефть: https://www.tatneft.ru

Реальные цены на АЗС конкретной сети.
"""
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from typing import Any

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

# Конфигурация парсеров
NETWORKS = {
    "lukoil": {
        "name": "Лукойл",
        "search_url": "https://lukoil.ru/api/v1/stations",
        "fallback_url": "https://lukoil.ru/portal/aroundme",
        "operator_keywords": ["lukoil", "лукойл"],
    },
    "gazprom": {
        "name": "Газпромнефть",
        "search_url": "https://www.gazprom-neft.ru/api/stations",
        "fallback_url": "https://www.gazprom-neft.ru/business/development/petrol-stations/",
        "operator_keywords": ["газпромнефть", "gazpromneft", "gazprom neft"],
    },
    "rosneft": {
        "name": "Роснефть",
        "search_url": "https://www.rosneft.ru/api/stations",
        "fallback_url": "https://www.rosneft.ru/business/retail/",
        "operator_keywords": ["роснефть", "rosneft"],
    },
    "tatneft": {
        "name": "Татнефть",
        "search_url": "https://www.tatneft.ru/api/stations",
        "fallback_url": "https://www.tatneft.ru/azs/",
        "operator_keywords": ["татнефть", "tatneft"],
    },
    "bashneft": {
        "name": "Башнефть",
        "search_url": "https://www.bashneft.ru/api/stations",
        "fallback_url": "https://www.bashneft.ru/products/",
        "operator_keywords": ["башнефть", "bashneft"],
    },
}


def parse_price_block(html: str) -> dict[str, float]:
    """Извлекает цены из HTML-блока (ищет паттерны: 92 - 54.40, 95 - 58.90, ...)."""
    prices = {}
    patterns = {
        "92": r"(?:аи-?92|92)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "95": r"(?:аи-?95|95)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "98": r"(?:аи-?98|98)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "100": r"(?:аи-?100|100)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "diesel": r"(?:дизель|диз|дт)[\s\-:]+(\d{2,3}[.,]\d{2})",
        "lpg": r"(?:газ|пропан)[\s\-:]+(\d{2,3}[.,]\d{2})",
    }
    for fuel, pattern in patterns.items():
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            try:
                prices[fuel] = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


async def fetch_network_page(session: aiohttp.ClientSession, network: str) -> str | None:
    """Скачивает страницу сети."""
    cfg = NETWORKS[network]
    for url in [cfg["search_url"], cfg["fallback_url"]]:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "Mozilla/5.0 (compatible; BenzinBot/1.0)"}
            ) as r:
                if r.status == 200:
                    text = await r.text()
                    if "АИ" in text or "аи" in text or "топливо" in text.lower():
                        return text
        except Exception as e:
            print(f"  ⚠ {url}: {e}")
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--network", choices=list(NETWORKS.keys()) + ["all"],
        default="all",
        help="Сеть (lukoil, gazprom, rosneft, tatneft, bashneft, all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    networks = list(NETWORKS.keys()) if args.network == "all" else [args.network]

    print(f"=== Парсер сайтов сетей АЗС ===")
    print(f"Сети: {', '.join(networks)}")
    print(f"⚠ ВНИМАНИЕ: может нарушать ToS")
    print()

    await db.init_db()
    found = 0

    async with aiohttp.ClientSession() as session:
        for net in networks:
            cfg = NETWORKS[net]
            print(f"[{cfg['name']}]", flush=True)
            html = await fetch_network_page(session, net)
            if not html:
                print(f"  ❌ Не удалось получить страницу")
                continue
            prices = parse_price_block(html)
            if prices:
                print(f"  ✓ Найдено цен: {len(prices)}")
                for f, p in prices.items():
                    print(f"    АИ-{f}: {p}₽")
                found += len(prices)
            else:
                print(f"  ⚠ Цены не найдены на странице (сайт мог измениться)")

    print()
    print(f"=== Итого ===")
    print(f"  Цен найдено: {found}")
    print()
    print("💡 Сайты сетей часто меняют структуру — парсер нужно обновлять.")
    print("💡 Лучше использовать официальные API или Telegram-каналы с ценами.")
    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
