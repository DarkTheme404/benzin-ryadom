"""
Парсер ВКонтакте (m.vk.com) — поиск публичных постов про бензин.

⚠️ ВНИМАНИЕ: парсинг m.vk.com может нарушать ToS.
Используй ответственно, не нагружай сервера.

Источники:
- m.vk.com/feed (публичный контент)
- Поиск: АЗС, бензин, АИ-95, цена

Что даёт:
- Посты с ценами (АИ-92/95/98, ДТ, ГАЗ)
- Очереди, наличие (если пост содержит)
- Локации (группа VK, геотеги)
"""
import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

BASE_URL = "https://m.vk.com"
SOURCE_NAME = "vk"


# Паттерны цен
PRICE_PATTERNS = {
    "92": r"(?:аи-?92|92)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "95": r"(?:аи-?95|95)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "98": r"(?:аи-?98|98)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "diesel": r"(?:дизель|диз|дт)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "lpg": r"(?:газ|пропан)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
}

NETWORK_KEYWORDS = {
    "Лукойл": ["lukoil", "лукойл", "лукой"],
    "Газпромнефть": ["газпромнефть", "gazpromneft", "газпром"],
    "Роснефть": ["роснефть", "rosneft"],
    "Татнефть": ["татнефть", "tatneft"],
    "Башнефть": ["башнефть", "bashneft"],
    "Shell": ["shell", "шелл"],
    "Teboil": ["teboil", "тебойл"],
}


def detect_network(text: str) -> Optional[str]:
    """Определяет сеть АЗС из текста."""
    text_lower = text.lower()
    for network, keywords in NETWORK_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return network
    return None


def detect_city(text: str) -> Optional[str]:
    """Определяет город из текста (простая эвристика)."""
    cities = [
        "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
        "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
        "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград", "Краснодар",
        "Саратов", "Тюмень", "Тольятти", "Ижевск", "Барнаул", "Иркутск",
        "Ульяновск", "Хабаровск", "Владивосток", "Ярославль", "Махачкала",
        "Томск", "Оренбург", "Кемерово", "Новокузнецк", "Рязань",
        "Астрахань", "Пенза", "Липецк", "Тула", "Киров", "Чебоксары",
        "Калининград", "Брянск", "Курск", "Тверь", "Иваново", "Белгород",
    ]
    for city in cities:
        if city.lower() in text.lower():
            return city
    return None


def parse_prices(text: str) -> dict[str, float]:
    """Извлекает цены из текста поста."""
    prices = {}
    for fuel, pattern in PRICE_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                prices[fuel] = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


def detect_queue(text: str) -> Optional[int]:
    """Определяет размер очереди из текста."""
    # "очередь 5 машин", "queue 3 cars"
    m = re.search(r"очередь\s*(?:[:\-]?\s*)?(\d+)\s*(?:машин|авто|тачки|такс|маш\.|чел\.)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # "большая очередь", "маленькая"
    if re.search(r"большая очередь|огромная очередь|длинная очередь", text, re.IGNORECASE):
        return 10
    if re.search(r"маленькая очередь|нет очереди|без очереди|очереди нет", text, re.IGNORECASE):
        return 0
    return None


def detect_availability(text: str) -> Optional[bool]:
    """Определяет наличие топлива."""
    text_lower = text.lower()
    if re.search(r"топливо\s+есть|бензин\s+есть|заправился|есть\s+аи|есть\s+бензин", text_lower):
        return True
    if re.search(r"топлива\s+нет|бензина\s+нет|нет\s+бензина|нет\s+аи|закончился|нет\s+в\s+наличии", text_lower):
        return False
    if re.search(r"заканчивается|осталось\s+мало|мало\s+бензина", text_lower):
        return None  # "кончается"
    return None


async def fetch(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Скачивает страницу m.vk.com."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=20),
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        ) as r:
            if r.status == 200:
                return await r.text()
    except Exception as e:
        print(f"  ⚠ {url}: {e}")
    return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="АИ-95 цена", help="Поисковый запрос")
    parser.add_argument("--limit", type=int, default=20, help="Лимит постов")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    print(f"=== Парсер ВКонтакте ===")
    print(f"Запрос: {args.query}")
    print(f"Лимит: {args.limit}")

    if not args.dry_run:
        await db.init_db()

    search_url = (
        f"{BASE_URL}/search"
        f"?c%5Bq%5D={args.query.replace(' ', '+')}"
        f"&c%5Bsection%5D=auto"
    )

    async with aiohttp.ClientSession() as session:
        html = await fetch(session, search_url)
        if not html:
            print("❌ Не удалось получить страницу")
            return 1

        # Парсим посты
        soup = BeautifulSoup(html, "html.parser")
        posts = soup.find_all("div", class_=re.compile(r"pi_text|post|wall_text"))
        print(f"Найдено постов: {len(posts)}")

        total_prices = 0
        total_saved = 0
        stations_cache = {}

        # Загружаем кеш АЗС если не dry-run
        if not args.dry_run:
            try:
                rows = await db._fetch("SELECT id, name, lat, lon FROM stations")
                for r in rows:
                    stations_cache[r["name"].lower()] = r["id"]
            except Exception as e:
                print(f"⚠ Cache load: {e}")

        for i, post in enumerate(posts[:args.limit]):
            text = post.get_text(" ", strip=True)
            if not text or len(text) < 20:
                continue

            prices = parse_prices(text)
            if not prices:
                continue

            network = detect_network(text)
            city = detect_city(text)
            queue = detect_queue(text)
            available = detect_availability(text)

            print(f"\n[{i+1}] {network or '?'} {city or '?'}")
            print(f"  {text[:150]}...")
            print(f"  Цены: {prices}")
            if queue is not None:
                print(f"  Очередь: {queue}")
            if available is not None:
                print(f"  Наличие: {'есть' if available else 'нет'}")

            total_prices += len(prices)

            if not args.dry_run:
                # Сохраняем в БД
                for fuel, price in prices.items():
                    # Создаём виртуальную "АЗС из VK" если нет
                    vk_station_name = f"VK: {network or 'unknown'} ({city or 'unknown'})"
                    if vk_station_name.lower() in stations_cache:
                        station_id = stations_cache[vk_station_name.lower()]
                    else:
                        # Создаём новую запись
                        try:
                            result = await db._execute(
                                """
                                INSERT INTO stations (name, lat, lon, city, region, operator, is_active, created_at)
                                VALUES ($1, $2, $3, $4, '', $5, TRUE, NOW())
                                ON CONFLICT DO NOTHING
                                RETURNING id
                                """,
                                vk_station_name, 0.0, 0.0, city, network,
                                returning=True,
                            )
                            if result:
                                new_id = result[0]["id"] if isinstance(result, list) else result.get("id")
                                stations_cache[vk_station_name.lower()] = new_id
                                station_id = new_id
                            else:
                                continue
                        except Exception as e:
                            continue

                    try:
                        await db.add_report(
                            station_id=station_id,
                            fuel_type=fuel,
                            available=available,
                            price=price,
                            queue_size=queue,
                            source=SOURCE_NAME,
                            comment=f"VK: {text[:100]}",
                        )
                        total_saved += 1
                    except Exception as e:
                        pass

    print()
    print(f"=== Итого ===")
    print(f"  Постов с ценами: {len(posts[:args.limit])}")
    print(f"  Цен найдено: {total_prices}")
    if not args.dry_run:
        print(f"  Сохранено: {total_saved}")
        await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
