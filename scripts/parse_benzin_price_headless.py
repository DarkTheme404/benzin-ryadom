"""
Парсер benzin-price.ru через Playwright (headless browser).

⚠️ benzin-price.ru использует JS-challenge (anti-bot).
Без headless browser парсинг не работает.

Использование:
  pip install playwright beautifulsoup4
  python -m playwright install chromium
  python scripts/parse_benzin_price_headless.py --region 1 --limit 10
  python scripts/parse_benzin_price_headless.py --all --output prices.json
  python scripts/parse_benzin_price_headless.py --all --upload-url https://benzin-ryadom.onrender.com/api/import_prices

⚠️ НЕ ЗАПУСКАТЬ на Render Free (тяжёлый, требует Chromium).
Рекомендуется: GitHub Actions (.github/workflows/benzin-price.yml).

Выходы:
  --output FILE     Сохранить в JSON файл
  --upload-url URL  Отправить в backend API
  --stdout          Печатать JSON в stdout (для piping)
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

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install playwright beautifulsoup4")
    print("python -m playwright install chromium")
    sys.exit(1)

# Импортируем db только если нужно сохранять локально
_db = None
def get_db():
    global _db
    if _db is None:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
        import db
        _db = db
    return _db

BASE_URL = "https://www.benzin-price.ru"
SOURCE_NAME = "benzin_price_ru"

REGIONS = {
    "1": "Москва и МО",
    "2": "Санкт-Петербург",
    "3": "Ленинградская обл.",
    "4": "Краснодарский край",
    "5": "Ростовская обл.",
    "7": "Свердловская обл.",
    "8": "Челябинская обл.",
    "9": "Башкортостан",
    "10": "Татарстан",
    "12": "Самарская обл.",
    "22": "Новосибирская обл.",
    "23": "Красноярский край",
    "38": "Иркутская обл.",
    "44": "Кемеровская обл.",
    "50": "Хабаровский край",
    "51": "Приморский край",
    "54": "Воронежская обл.",
    "55": "Тюменская обл.",
    "63": "Ставропольский край",
    "76": "Тверская обл.",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
]


def parse_prices(text: str) -> dict[str, float]:
    """Парсит цены из HTML-текста страницы АЗС."""
    prices = {}
    patterns = {
        "92": r"(?:аи-?92|92)[\s\-:—–]+(\d{2,3}[.,]\d{2})",
        "95": r"(?:аи-?95|95)[\s\-:—–]+(\d{2,3}[.,]\d{2})",
        "98": r"(?:аи-?98|98)[\s\-:—–]+(\d{2,3}[.,]\d{2})",
        "100": r"(?:аи-?100|100)[\s\-:—–]+(\d{2,3}[.,]\d{2})",
        "diesel": r"(?:дизель|диз|дт)[\s\-:—–]+(\d{2,3}[.,]\d{2})",
        "lpg": r"(?:газ|пропан)[\s\-:—–]+(\d{2,3}[.,]\d{2})",
    }
    for fuel, pattern in patterns.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                prices[fuel] = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


def parse_region_stations(html: str) -> list[dict]:
    """Извлекает список АЗС региона из HTML."""
    soup = BeautifulSoup(html, "html.parser")
    stations = []
    for a in soup.find_all("a", href=re.compile(r"zapravka\.php\?id=\d+")):
        try:
            azs_id = int(re.search(r"id=(\d+)", a["href"]).group(1))
            name = a.get_text(strip=True)
            if name and azs_id:
                stations.append({"id": azs_id, "name": name})
        except (ValueError, AttributeError):
            continue
    return stations


async def scrape_station(page, azs_id: int, name: str, retries: int = 2) -> Optional[dict]:
    """Парсит одну АЗС с retry."""
    url = f"{BASE_URL}/zapravka.php?id={azs_id}"
    for attempt in range(retries + 1):
        try:
            await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(0.5)

            html = await page.content()

            if "проверка" in html.lower() or "captcha" in html.lower():
                raise PlaywrightTimeout("JS challenge detected")

            text = BeautifulSoup(html, "html.parser").get_text()
            prices = parse_prices(text)
            if prices:
                return {"id": azs_id, "name": name, "prices": prices}
        except (PlaywrightTimeout, Exception) as e:
            if attempt < retries:
                await asyncio.sleep(2 + random.random() * 2)
                continue
            return {"id": azs_id, "name": name, "error": str(e)[:80]}
    return None


async def scrape_region(page, region_id: str, region_name: str, limit: int) -> list[dict]:
    """Собирает цены АЗС одного региона."""
    list_url = f"{BASE_URL}/zapravka.php?region={region_id}"
    try:
        await page.goto(list_url, timeout=30000, wait_until="networkidle")
        await asyncio.sleep(2)
    except PlaywrightTimeout:
        # networkidle может не дождаться из-за JS
        pass

    html = await page.content()
    stations = parse_region_stations(html)[:limit]

    if not stations:
        print(f"  [Регион {region_id}: {region_name}] 0 АЗС")
        return []

    results = []
    print(f"  [Регион {region_id}: {region_name}] {len(stations)} АЗС", end="", flush=True)

    for i, st in enumerate(stations):
        result = await scrape_station(page, st["id"], st["name"])
        if result and "prices" in result:
            results.append({
                "external_id": result["id"],
                "name": result["name"],
                "region_id": region_id,
                "region_name": region_name,
                "prices": result["prices"],
            })

        # Прогресс в строке
        if (i + 1) % 5 == 0 or (i + 1) == len(stations):
            print(f"\r  [Регион {region_id}: {region_name}] {i+1}/{len(stations)}, цен: {len(results)}  ", end="", flush=True)

        await asyncio.sleep(0.3 + random.random() * 0.2)

    print()
    return results


async def save_to_db(all_results: list[dict]) -> tuple[int, int]:
    """Сохраняет результаты в локальную БД. Возвращает (saved, errors)."""
    db = get_db()
    await db.init_db()
    saved = 0
    errors = 0
    for r in all_results:
        for fuel, price in r.get("prices", {}).items():
            try:
                await db.add_report(
                    station_id=r["external_id"],
                    fuel_type=fuel,
                    available=True,
                    price=price,
                    source=SOURCE_NAME,
                    comment=f"benzin-price.ru: {r['name']}",
                )
                saved += 1
            except Exception:
                errors += 1
    return saved, errors


async def upload_to_api(all_results: list[dict], upload_url: str, api_key: str = "") -> bool:
    """Отправляет результаты в backend API."""
    try:
        import aiohttp
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "source": SOURCE_NAME,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "regions": list(REGIONS.keys()),
            "results": all_results,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                upload_url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    print(f"  ✅ Загружено в API: {body.get('saved', '?')} отчётов")
                    return True
                else:
                    print(f"  ⚠ API {resp.status}: {await resp.text()[:200]}")
                    return False
    except Exception as e:
        print(f"  ⚠ Upload: {e}")
        return False


def write_json(all_results: list[dict], path: str):
    """Сохраняет результаты в JSON файл."""
    payload = {
        "source": SOURCE_NAME,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "regions": list(REGIONS.keys()),
        "results": all_results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  💾 Сохранено в {path}: {len(all_results)} АЗС")


async def main():
    parser = argparse.ArgumentParser(
        description="Парсер benzin-price.ru через Playwright. Подходит для GitHub Actions."
    )
    parser.add_argument("--region", default=None, help="ID региона (1-76) или 'all'")
    parser.add_argument("--all", action="store_true", help="Все регионы (по умолчанию)")
    parser.add_argument("--limit", type=int, default=50, help="Лимит АЗС на регион")
    parser.add_argument("--regions-filter", default=None,
                        help="Только эти регионы через запятую (например '1,2,4')")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    parser.add_argument("--save-db", action="store_true", help="Сохранить в локальную БД")
    parser.add_argument("--output", default=None, help="Путь к JSON файлу")
    parser.add_argument("--stdout", action="store_true", help="Печатать JSON в stdout")
    parser.add_argument("--upload-url", default=None, help="URL для POST с JSON")
    parser.add_argument("--api-key", default="", help="API ключ для upload-url")
    parser.add_argument("--headed", action="store_true", help="Запустить с UI (для отладки)")
    args = parser.parse_args()

    if args.regions_filter:
        regions_to_scrape = [r for r in args.regions_filter.split(",") if r in REGIONS]
    elif args.region and args.region != "all":
        regions_to_scrape = [args.region]
    else:
        regions_to_scrape = list(REGIONS.keys())

    print(f"=== benzin-price.ru (Playwright) ===")
    print(f"Регионов: {len(regions_to_scrape)}, лимит: {args.limit}/регион")
    print(f"Режим: {'headless' if not args.headed else 'headed'}")
    print(f"Сохранение: DB={args.save_db}, file={args.output or '-'}, stdout={args.stdout}, api={args.upload_url or '-'}")

    started = time.time()
    all_results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not args.headed,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
        )
        page = await context.new_page()

        for region_id in regions_to_scrape:
            region_name = REGIONS.get(region_id, region_id)
            try:
                results = await scrape_region(page, region_id, region_name, args.limit)
                all_results.extend(results)
            except Exception as e:
                print(f"  ❌ Регион {region_id}: {e}")

        await browser.close()

    elapsed = time.time() - started
    print(f"\n=== Итого ===")
    print(f"  АЗС с ценами: {len(all_results)}")
    print(f"  Время: {elapsed:.1f} сек ({elapsed/60:.1f} мин)")
    if all_results:
        print(f"  Среднее время на АЗС: {elapsed/len(all_results):.1f} сек")

    if args.stdout:
        payload = {
            "source": SOURCE_NAME,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "results": all_results,
        }
        print("\n--- JSON OUTPUT ---")
        print(json.dumps(payload, ensure_ascii=False))

    if args.output:
        write_json(all_results, args.output)

    if args.save_db and not args.dry_run and all_results:
        saved, errors = await save_to_db(all_results)
        print(f"  💾 В БД: {saved} сохранено, {errors} ошибок")
        await get_db().close_db()

    if args.upload_url and all_results:
        await upload_to_api(all_results, args.upload_url, args.api_key)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
