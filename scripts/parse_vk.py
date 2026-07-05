"""
Парсер ВКонтакте: цены на бензин из публичных постов и пабликов.

Два режима:
  1. Web-скрапинг m.vk.com (по умолчанию) — без токена, может нарушать ToS.
  2. VK API (--api) — нужен VK_SERVICE_TOKEN, официально и стабильно.

Источники:
  - Поиск постов: wall.search с фильтром по дате
  - Конкретные паблики: wall.get по owner_id
  - Конкретные ключевые слова: АИ-92, АИ-95, АИ-98, ДТ, цена

⚠️ Web-режим может нарушать ToS VK. Рекомендуется API.

Использование:
  # Web-скрапинг (без токена)
  python scripts/parse_vk.py --query "АИ-95" --limit 50

  # VK API (нужен VK_SERVICE_TOKEN)
  export VK_SERVICE_TOKEN='your_token'
  python scripts/parse_vk.py --api --groups avto_benzin,fuel_price --limit 100
"""
import argparse
import asyncio
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

BASE_URL = "https://m.vk.com"
API_URL = "https://api.vk.com/method"
API_VERSION = "5.199"
SOURCE_NAME = "vk"


# === Паттерны ===

PRICE_PATTERNS = {
    "92": r"(?:аи-?92|92)[\s\-:—–~]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "95": r"(?:аи-?95|95)[\s\-:—–~]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "98": r"(?:аи-?98|98)[\s\-:—–~]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "diesel": r"(?:дизель|диз|дт)[\s\-:—–~]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "lpg": r"(?:газ|пропан)[\s\-:—–~]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
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

# Крупные города РФ
CITY_KEYWORDS = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
    "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград", "Краснодар",
    "Саратов", "Тюмень", "Тольятти", "Ижевск", "Барнаул", "Иркутск",
    "Ульяновск", "Хабаровск", "Владивосток", "Ярославль", "Махачкала",
    "Томск", "Оренбург", "Кемерово", "Новокузнецк", "Рязань", "Астрахань",
    "Набережные Челны", "Киров", "Пенза", "Севастополь", "Калининград",
    "Тверь", "Тула", "Иваново", "Брянск", "Курск", "Магнитогорск", "Сочи",
]


def detect_network(text: str) -> Optional[str]:
    text_lower = text.lower()
    for network, keywords in NETWORK_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return network
    return None


def detect_city(text: str) -> Optional[str]:
    for city in CITY_KEYWORDS:
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
    """Определяет размер очереди (примерно)."""
    m = re.search(r"очередь\s*(?:~\s*)?(\d+)\s*(?:машин|авто|чел)?", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if re.search(r"большая очередь|огромная очередь|длинная очередь", text, re.IGNORECASE):
        return 10
    if re.search(r"маленькая очередь|нет очереди|без очереди|очереди нет", text, re.IGNORECASE):
        return 0
    return None


def detect_availability(text: str) -> Optional[bool]:
    text_lower = text.lower()
    if re.search(r"топливо\s+есть|бензин\s+есть|заправился|есть\s+аи|есть\s+бензин", text_lower):
        return True
    if re.search(r"топлива\s+нет|бензина\s+нет|нет\s+бензина|нет\s+аи|закончился|нет\s+в\s+наличии", text_lower):
        return False
    if re.search(r"заканчивается|осталось\s+мало|мало\s+бензина", text_lower):
        return None
    return None


def parse_next_delivery(text: str) -> Optional[datetime]:
    """Извлекает время следующего завоза из текста поста.

    Возвращает datetime в UTC.
    Поддерживает: "завоз в 14:00", "привезут через 2 часа", "завтра в 10:00".
    """
    text_lower = text.lower()
    now = datetime.now()  # local

    # "через N часов/минут"
    m = re.search(r"через\s+(\d+)\s*(час|ч)", text_lower)
    if m:
        from datetime import timezone
        return (now + timedelta(hours=int(m.group(1)))).astimezone(timezone.utc)
    m = re.search(r"через\s+(\d+)\s*(минут|мин)", text_lower)
    if m:
        from datetime import timezone
        return (now + timedelta(minutes=int(m.group(1)))).astimezone(timezone.utc)

    # Дата: сегодня/завтра/послезавтра
    day_offset = 0
    for word, offset in [("сегодня", 0), ("завтра", 1), ("послезавтра", 2)]:
        if word in text_lower:
            day_offset = offset
            break

    # Время "HH:MM"
    m = re.search(r"(?:в\s+)?(\d{1,2}):(\d{2})", text)
    if m:
        try:
            from datetime import timezone
            hour, minute = int(m.group(1)), int(m.group(2))
            if 0 <= hour < 24 and 0 <= minute < 60:
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                target += timedelta(days=day_offset)
                if day_offset == 0 and target < now:
                    target += timedelta(days=1)
                return target.astimezone(timezone.utc)
        except ValueError:
            pass

    return None


async def save_vk_report(
    station_id: int,
    fuel_type: str,
    available: Optional[bool],
    raw_text: str,
    price: Optional[float] = None,
    queue: Optional[int] = None,
    next_delivery: Optional[datetime] = None,
) -> int:
    """Сохраняет отчёт от парсера VK."""
    report_id = await db.add_report(
        station_id=station_id,
        fuel_type=fuel_type,
        available=available,
        price=price,
        queue_size=queue,
        source="vk",
        comment=f"vk: {raw_text[:200]}",
        next_delivery_at=next_delivery,
    )
    print(f"  ✅ VK отчёт: station={station_id} fuel={fuel_type} avail={available} price={price} queue={queue} next={next_delivery}")
    return report_id


async def upload_vk_results(results: list, upload_url: str, api_key: str = "") -> bool:
    """Загружает в backend через /api/import_prices."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Import-Key"] = api_key
        payload = {
            "source": "vk",
            "scraped_at": datetime.now().isoformat(),
            "results": results,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                upload_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    print(f"  ✅ Загружено в API: {body}")
                    return True
                else:
                    text = await resp.text()
                    print(f"  ⚠ API {resp.status}: {text[:200]}")
                    return False
    except Exception as e:
        print(f"  ⚠ Upload: {e}")
        return False


async def fetch_web(session: aiohttp.ClientSession, url: str) -> Optional[str]:
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


async def fetch_api(
    session: aiohttp.ClientSession,
    method: str,
    params: dict,
    token: str,
) -> Optional[dict]:
    """Запрос к VK API."""
    params = {**params, "access_token": token, "v": API_VERSION}
    try:
        async with session.get(
            f"{API_URL}/{method}", params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status == 200:
                data = await r.json()
                if "error" in data:
                    print(f"  ⚠ VK API error: {data['error']}")
                    return None
                return data.get("response")
    except Exception as e:
        print(f"  ⚠ VK API {method}: {e}")
    return None


async def search_web(session: aiohttp.ClientSession, query: str, limit: int) -> list[dict]:
    """Web-поиск постов m.vk.com."""
    search_url = (
        f"{BASE_URL}/search"
        f"?c%5Bq%5D={query.replace(' ', '+')}"
        f"&c%5Bsection%5D=auto"
    )
    html = await fetch_web(session, search_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    posts = soup.find_all("div", class_=re.compile(r"pi_text|post|wall_text"))
    return [{"text": p.get_text(" ", strip=True), "source": "web"} for p in posts[:limit]]


async def search_api_groups(
    session: aiohttp.ClientSession,
    groups: list[str],
    limit: int,
    token: str,
    days_back: int = 7,
) -> list[dict]:
    """Поиск постов через VK API в конкретных пабликах."""
    cutoff = int(time.time()) - days_back * 86400
    all_posts = []
    for group in groups:
        group = group.lstrip("-").lstrip("@")
        # Резолвим group_id (если передано короткое имя)
        grp = await fetch_api(session, "groups.getById", {"group_id": group}, token)
        if not grp:
            continue
        owner_id = -grp[0]["id"]
        # Получаем посты
        posts = await fetch_api(
            session, "wall.get",
            {"owner_id": owner_id, "count": min(limit, 100), "filter": "all"},
            token,
        )
        if not posts:
            continue
        for p in posts.get("items", []):
            if p.get("date", 0) < cutoff:
                continue
            text = p.get("text", "")
            if not text:
                continue
            all_posts.append({
                "text": text,
                "source": f"api:vk.com/{group}",
                "date": p.get("date"),
                "post_url": f"https://vk.com/wall{owner_id}_{p['id']}",
            })
    return all_posts


async def search_api_global(
    session: aiohttp.ClientSession,
    query: str,
    limit: int,
    token: str,
    days_back: int = 7,
) -> list[dict]:
    """Глобальный поиск постов через VK newsfeed.search."""
    all_posts = []
    offset = 0
    while len(all_posts) < limit:
        data = await fetch_api(
            session, "newsfeed.search",
            {"q": query, "count": min(50, limit - len(all_posts)), "offset": offset},
            token,
        )
        if not data:
            break
        items = data.get("items", [])
        if not items:
            break
        for p in items:
            text = p.get("text", "")
            if not text or len(text) < 20:
                continue
            source_id = p.get("source_id", 0)
            all_posts.append({
                "text": text,
                "source": f"api:vk.com/search",
                "date": p.get("date"),
                "post_url": f"https://vk.com/wall{source_id}_{p.get('post_id', p.get('id', ''))}",
            })
        offset += len(items)
        if offset >= 200:
            break
        await asyncio.sleep(0.3)
    return all_posts[:limit]


async def save_posts(posts: list[dict], dry_run: bool) -> tuple[int, int]:
    """Сохраняет распарсенные посты в БД. Возвращает (цены, отчёты)."""
    if dry_run:
        return (0, 0)

    # Загружаем кеш АЗС
    stations_cache: dict[str, int] = {}
    try:
        rows = await db._fetch("SELECT id, name, lat, lon FROM stations")
        for r in rows:
            stations_cache[r["name"].lower()] = r["id"]
    except Exception as e:
        print(f"⚠ Cache load: {e}")

    total_prices = 0
    total_saved = 0
    for post in posts:
        text = post.get("text", "")
        if not text or len(text) < 20:
            continue

        prices = parse_prices(text)
        if not prices:
            continue

        network = detect_network(text)
        city = detect_city(text)
        queue = detect_queue(text)
        available = detect_availability(text)
        next_delivery = parse_next_delivery(text)
        post_source = post.get("source", "vk")

        # Создаём виртуальную АЗС
        vk_station_name = f"VK: {network or 'unknown'} ({city or 'unknown'})"
        if vk_station_name.lower() in stations_cache:
            station_id = stations_cache[vk_station_name.lower()]
        else:
            try:
                if db.USE_SQLITE:
                    result = await db._execute(
                        """INSERT INTO stations (name, lat, lon, city, region, operator, is_active)
                           VALUES (?, ?, ?, ?, '', ?, 1)""",
                        vk_station_name, 0.0, 0.0, city or "", network or "",
                        returning=True,
                    )
                    new_id = result if isinstance(result, int) else (result.get("id") if isinstance(result, dict) else None)
                else:
                    async with db._db.acquire() as conn:
                        row = await conn.fetchrow(
                            """INSERT INTO stations (name, lat, lon, city, region, operator, is_active)
                               VALUES ($1, $2, $3, $4, '', $5, TRUE)
                               ON CONFLICT DO NOTHING
                               RETURNING id""",
                            vk_station_name, 0.0, 0.0, city or "", network or "",
                        )
                        new_id = row["id"] if row else None
                if new_id:
                    stations_cache[vk_station_name.lower()] = new_id
                    station_id = new_id
                else:
                    continue
            except Exception as e:
                print(f"  ⚠ Insert station: {e}")
                continue

        for fuel, price in prices.items():
            try:
                await db.add_report(
                    station_id=station_id,
                    fuel_type=fuel,
                    available=available,
                    price=price,
                    queue_size=queue,
                    source=SOURCE_NAME,
                    comment=f"{post_source}: {text[:100]}",
                    next_delivery_at=next_delivery,
                )
                total_saved += 1
            except Exception as e:
                print(f"  ⚠ add_report: {e}")
        total_prices += len(prices)

    return (total_prices, total_saved)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", action="store_true", help="Использовать VK API вместо web-скрапинга")
    parser.add_argument("--search", action="store_true", help="Глобальный поиск через newsfeed.search (нужен --api)")
    parser.add_argument("--query", default="АИ-95 цена руб", help="Поисковый запрос")
    parser.add_argument("--groups", help="Группы через запятую (для API-режима), например: avto_benzin,fuel_price")
    parser.add_argument("--limit", type=int, default=20, help="Лимит постов")
    parser.add_argument("--days-back", type=int, default=7, help="Глубина поиска (дней)")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    token = os.getenv("VK_SERVICE_TOKEN", "")
    if args.api and not token:
        print("❌ VK_SERVICE_TOKEN не задан")
        print("Получить: https://dev.vk.com/api/access-token/getting-started")
        print("Тип: сервисный токен (без пользователя)")
        return 1

    print(f"=== Парсер ВКонтакте ===")
    print(f"Режим: {'VK API search' if args.search else 'VK API groups' if args.api else 'Web-скрапинг'}")
    print(f"Запрос: {args.query}")
    print(f"Лимит: {args.limit} постов")

    if not args.dry_run:
        await db.init_db()

    async with aiohttp.ClientSession() as session:
        if args.api and args.search:
            posts = await search_api_global(session, args.query, args.limit, token, args.days_back)
        elif args.api:
            groups = [g.strip() for g in (args.groups or "").split(",") if g.strip()]
            if not groups:
                print("❌ --groups не указаны")
                return 1
            posts = await search_api_groups(session, groups, args.limit, token, args.days_back)
        else:
            posts = await search_web(session, args.query, args.limit)

        print(f"Постов получено: {len(posts)}")

        # Сначала показываем что нашли
        found = 0
        for i, post in enumerate(posts):
            text = post.get("text", "")
            prices = parse_prices(text)
            if not prices:
                continue
            found += 1
            network = detect_network(text)
            city = detect_city(text)
            queue = detect_queue(text)
            available = detect_availability(text)
            print(f"\n[{i+1}] {network or '?'} {city or '?'} ({post.get('source')})")
            print(f"  {text[:120]}...")
            print(f"  Цены: {prices}")
            if queue is not None:
                print(f"  Очередь: {queue}")
            if available is not None:
                print(f"  Наличие: {'есть' if available else 'нет'}")

        # Сохраняем
        if not args.dry_run:
            total_prices, total_saved = await save_posts(posts, args.dry_run)
            print()
            print(f"=== Итого ===")
            print(f"  Постов с ценами: {found}")
            print(f"  Цен найдено: {total_prices}")
            print(f"  Сохранено отчётов: {total_saved}")
            await db.close_db()
        else:
            print()
            print(f"=== Итого (dry-run) ===")
            print(f"  Постов с ценами: {found}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
