"""
Парсер Яндекс.Карт — POI АЗС через Playwright + опциональный HTTP API.

Стратегия:
1. С API ключом: HTTP API yandex.ru/maps/api/business/fetchReviews + JavaScript API
2. Без ключа: Playwright рендерит страницу и парсит HTML

⚠️ Яндекс.Карты не публикуют endpoint для цен на топливо. Этот парсер собирает:
- АЗС на карте (название, адрес, координаты)
- Рейтинг, отзывы
- Расписание работы, контакты

Цены на топливо:
- В Яндекс.Картах обычно НЕ показаны (только в отдельном приложении «Яндекс.Заправки»)
- Яндекс.Заправки — отдельный сервис, без публичного API
- Цены можно парсить только из мобильного приложения (reverse engineering)

Использование:
  pip install playwright
  python -m playwright install chromium

  # С API ключом (быстрее, лимит 1К/день)
  python scripts/parse_yandex_maps_playwright.py --city Иваново --api-key xxx

  # Без ключа (через Playwright)
  python scripts/parse_yandex_maps_playwright.py --city Иваново --limit 50

  # С загрузкой в backend
  python scripts/parse_yandex_maps_playwright.py --city Ивановo --upload-url https://benzin-ryadom.onrender.com/api/import_prices --api-key xxx
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, quote

import aiohttp
from bs4 import BeautifulSoup

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("pip install playwright")
    print("python -m playwright install chromium")
    raise  # пробрасываем ImportError, чтобы _safe_import в orchestrator знал

# Импортируем db только если нужно сохранять локально
_db = None
def get_db():
    global _db
    if _db is None:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
        import db
        _db = db
    return _db

BASE_URL = "https://yandex.ru/maps"
SOURCE_NAME = "yandex_maps"

# Список крупных городов с city_id в Яндекс.Картах
CITY_IDS = {
    "Москва": "213",
    "Санкт-Петербург": "2",
    "Новосибирск": "65",
    "Екатеринбург": "54",
    "Казань": "43",
    "Краснодар": "35",
    "Челябинск": "56",
    "Нижний Новгород": "47",
    "Самара": "51",
    "Ростов-на-Дону": "39",
    "Уфа": "172",
    "Красноярск": "62",
    "Воронеж": "193",
    "Пермь": "50",
    "Волгоград": "38",
    "Иваново": "1059",
    "Ярославль": "16",
    "Тверь": "14",
    "Тула": "15",
    "Калуга": "6",
    "Кострома": "9",
    "Владимир": "8",
    "Ижевск": "44",
    "Омск": "66",
    "Барнаул": "197",
    "Кемерово": "64",
    "Томск": "67",
    "Иркутск": "63",
    "Хабаровск": "76",
    "Владивосток": "75",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


async def search_via_http_api(
    session: aiohttp.ClientSession,
    city: str,
    city_id: str,
    api_key: str,
    query: str = "АЗС",
) -> list[dict]:
    """Ищет АЗС через HTTP API (требует API ключ).
    
    Использует endpoint search-maps.yandex.ru (тот же что в JS API).
    """
    url = "https://search-maps.yandex.ru/v1/"
    params = {
        "text": query,
        "type": "biz",
        "lang": "ru_RU",
        "apikey": api_key,
        "results": 200,
        "ll": "37.6173,55.7558",  # Будет переопределено если есть координаты города
        "spn": "0.5,0.5",  # Большой bbox для покрытия города
    }
    try:
        async with session.get(
            url,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": random.choice(USER_AGENTS)},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                print(f"  ⚠ API {resp.status}: {text[:200]}")
                return []
            data = await resp.json()
    except Exception as e:
        print(f"  ⚠ API error: {e}")
        return []
    
    results = []
    items = data.get("features", [])
    for item in items:
        try:
            props = item.get("properties", {})
            geom = item.get("geometry", {}).get("coordinates", [None, None])
            if len(geom) >= 2 and geom[0] is not None and geom[1] is not None:
                # В API coordinates = [lon, lat]
                lon, lat = geom[0], geom[1]
            else:
                continue
            
            name = props.get("name") or props.get("CompanyMetaData", {}).get("name")
            if not name:
                continue
            
            meta = props.get("CompanyMetaData", {})
            address = meta.get("address", "")
            hours = meta.get("Hours", {}).get("text", "")
            rating = meta.get("Reviews", {}).get("rating")
            reviews_count = meta.get("Reviews", {}).get("review_count") or meta.get("Reviews", {}).get("general_review_count")
            phone = ""
            contacts = meta.get("Contacts", [])
            if contacts:
                phone = contacts[0].get("value", "")
            
            categories = meta.get("Categories", [])
            category_names = [c.get("name", "") for c in categories]
            
            results.append({
                "name": name[:200],
                "lat": lat,
                "lon": lon,
                "address": address[:300] if address else None,
                "rating": float(rating) if rating else None,
                "reviews_count": int(reviews_count) if reviews_count else 0,
                "phone": phone[:50] if phone else None,
                "hours": hours[:200] if hours else None,
                "categories": category_names,
                "city_id": city_id,
            })
        except Exception as e:
            print(f"  ⚠ parse item: {e}")
            continue
    
    return results


async def search_via_playwright(
    page,
    city: str,
    city_id: str,
    limit: int,
) -> list[dict]:
    """Ищет АЗС через Playwright (без API ключа)."""
    url = f"{BASE_URL}/{city_id}/?text=АЗС&z=12"
    print(f"  Открываю: {url[:80]}...")
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(3)  # Даём JS прогрузиться
    except PlaywrightTimeout:
        pass
    
    # Ищем кнопку "Показать результаты" или что-то подобное
    # Ждём появления результатов
    try:
        await page.wait_for_selector("div.search-snippet, a.search-snippet-view", timeout=10000)
    except PlaywrightTimeout:
        # Если нет результатов, пробуем кликнуть
        try:
            await page.click("button[type='submit']", timeout=3000)
            await asyncio.sleep(3)
        except Exception:
            pass
    
    # Парсим результаты
    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    
    pois = []
    
    # Яндекс.Карты рендерят результаты в <div class="search-snippet-view">
    # или в <a class="search-snippet-view">
    for snippet in soup.find_all(["a", "div"], class_=re.compile(r"search-snippet|search-item|business-item")):
        try:
            # Название
            name_elem = snippet.find(["h3", "div", "span"], class_=re.compile(r"title|name"))
            if not name_elem:
                name_elem = snippet.find("span", class_=re.compile(r"text"))
            name = name_elem.get_text(strip=True) if name_elem else ""
            
            if not name or len(name) < 3:
                continue
            
            # Ссылка
            href = snippet.get("href", "")
            if not href and snippet.name == "a":
                href = snippet.get("href", "")
            if not href:
                # Поищем внутри
                a = snippet.find("a", href=True)
                href = a.get("href", "") if a else ""
            
            # Координаты — обычно в URL ?ll=lat,lon
            lat = None
            lon = None
            ll_match = re.search(r"ll=([\d.]+),([\d.]+)", href or "")
            if ll_match:
                lat = float(ll_match.group(2))  # Yandex использует lat,lon
                lon = float(ll_match.group(1))
            
            # Рейтинг
            rating = None
            rating_elem = snippet.find("span", class_=re.compile(r"rating"))
            if rating_elem:
                try:
                    rating = float(rating_elem.get_text(strip=True).replace(",", "."))
                except ValueError:
                    pass
            
            pois.append({
                "name": name[:200],
                "lat": lat,
                "lon": lon,
                "address": None,
                "rating": rating,
                "reviews_count": 0,
                "phone": None,
                "hours": None,
                "categories": [],
                "city_id": city_id,
            })
            
            if len(pois) >= limit:
                break
        except Exception:
            continue
    
    return pois


async def save_to_db(results: list[dict]) -> tuple[int, int]:
    """Сохраняет в локальную БД (создаёт новые АЗС или обновляет координаты)."""
    db = get_db()
    await db.init_db()
    saved = 0
    errors = 0
    for r in results:
        try:
            # Простая логика: если есть name + lat+lon, создаём
            # В реальной БД должна быть умная дедупликация
            await db._execute(
                """INSERT INTO stations (name, lat, lon, address, rating, is_active, source)
                   VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (r["name"], r["lat"], r["lon"], r.get("address"), r.get("rating"), SOURCE_NAME),
            )
            saved += 1
        except Exception:
            errors += 1
    return saved, errors


async def upload_to_api(results: list[dict], upload_url: str, api_key: str = "") -> bool:
    """Загружает в backend через /api/import_prices."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-Import-Key"] = api_key
        payload = {
            "source": SOURCE_NAME,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "results": [
                {
                    "external_id": f"yandex_{r['city_id']}_{i}",
                    "name": r["name"],
                    "region_name": "Россия",  # Регион не определяем из Я.Карт
                    "city": None,
                    "operator": None,
                    "lat": r.get("lat"),
                    "lon": r.get("lon"),
                    "prices": {},  # Цен в Я.Картах нет
                }
                for i, r in enumerate(results)
            ],
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
                    print(f"  ✅ Загружено: {body.get('stations_new', 0)} новых АЗС")
                    return True
                else:
                    text = await resp.text()
                    print(f"  ⚠ API {resp.status}: {text[:200]}")
                    return False
    except Exception as e:
        print(f"  ⚠ Upload: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(
        description="Парсер Яндекс.Карт (POI АЗС)"
    )
    parser.add_argument("--city", required=True, help="Город (например 'Иваново')")
    parser.add_argument("--limit", type=int, default=50, help="Лимит АЗС (default 50)")
    parser.add_argument("--api-key", default=os.environ.get("YANDEX_MAPS_API_KEY", ""),
                        help="API ключ HTTP Geocoder (опционально)")
    parser.add_argument("--query", default="АЗС", help="Поисковый запрос (default: АЗС)")
    parser.add_argument("--save-db", action="store_true", help="Сохранить в локальную БД")
    parser.add_argument("--output", default=None, help="JSON файл")
    parser.add_argument("--upload-url", default=None, help="URL для POST с JSON")
    parser.add_argument("--import-key", default=os.environ.get("IMPORT_API_KEY", ""),
                        help="API ключ для import_prices")
    parser.add_argument("--headed", action="store_true", help="Запустить с UI (для отладки)")
    args = parser.parse_args()
    
    city_id = CITY_IDS.get(args.city)
    if not city_id:
        # Ищем по всем city_id через Playwright
        city_id = None
    
    print(f"=== Парсер Яндекс.Карт ===")
    print(f"Город: {args.city} (city_id={city_id})")
    print(f"Режим: {'API ключ' if args.api_key else 'Playwright'}")
    print(f"Лимит: {args.limit}")
    
    started = time.time()
    results = []
    
    if args.api_key:
        # Быстрый путь через HTTP API
        async with aiohttp.ClientSession() as session:
            results = await search_via_http_api(
                session, args.city, city_id or "0", args.api_key, args.query
            )
        print(f"  Найдено через API: {len(results)}")
    else:
        # Через Playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=not args.headed,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
            )
            page = await context.new_page()
            
            try:
                results = await search_via_playwright(
                    page, args.city, city_id or "0", args.limit
                )
            except Exception as e:
                print(f"  ❌ Playwright error: {e}")
            finally:
                await browser.close()
        
        print(f"  Найдено через Playwright: {len(results)}")
    
    results = results[:args.limit]
    elapsed = time.time() - started
    
    # Фильтруем записи без координат (Playwright не всегда их даёт)
    with_coords = [r for r in results if r.get("lat") is not None and r.get("lon") is not None]
    without_coords = len(results) - len(with_coords)
    
    print(f"\n=== Итого ===")
    print(f"  АЗС найдено: {len(results)}")
    print(f"    С координатами: {len(with_coords)}")
    print(f"    Без координат: {without_coords}")
    print(f"  Время: {elapsed:.1f} сек")
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "source": SOURCE_NAME,
                "city": args.city,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "results": results,
            }, f, ensure_ascii=False, indent=2)
        print(f"  💾 Сохранено в {args.output}")
    
    if args.save_db and with_coords:
        saved, errors = await save_to_db(with_coords)
        print(f"  💾 В БД: {saved} сохранено, {errors} ошибок")
        await get_db().close_db()
    
    if args.upload_url and with_coords:
        await upload_to_api(with_coords, args.upload_url, args.import_key)
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
