#!/usr/bin/env python3
"""Парсер fuelmap.ru — АЗС с ценами по всей России.

Scrape city pages from fuelmap.ru, parse station HTML blocks,
match to existing DB stations by lat/lon, and save price reports.

Usage:
    python scripts/parse_fuelmap.py
    python scripts/parse_fuelmap.py --limit 5       # first 5 cities only
    python scripts/parse_fuelmap.py --city chelyabinsk
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import aiohttp
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SOURCE = "fuelmap"
BASE_URL = "https://fuelmap.ru"
CONCURRENT = 20
DELAY_BETWEEN = 0.3
TIMEOUT = aiohttp.ClientTimeout(total=30)

FUEL_TYPE_MAP = {
    "Аи-92": "92",
    "Аи-95": "95",
    "Аи-98": "98",
    "Аи-100": "100",
    "ДТ": "diesel",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}


def city_to_slug(city_name: str) -> str:
    """Convert Russian city name to FuelMap.ru URL slug."""
    tr = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
    slug = "".join(tr.get(c.lower(), c) for c in city_name)
    slug = re.sub(r"[^a-z0-9-]", "", slug.replace(" ", "-"))
    return slug


def parse_stations(html: str, city_slug: str) -> list[dict]:
    """Parse fuel station data from a city page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for div in soup.find_all("div", class_="gas-station"):
        data_id = div.get("data-id")
        lat_str = div.get("data-lat", "")
        lon_str = div.get("data-lon", "")
        if not lat_str or not lon_str:
            continue

        try:
            lat = float(lat_str)
            lon = float(lon_str)
        except ValueError:
            continue

        name = ""
        name_div = div.find("div", class_="gas-station-name")
        if name_div:
            name = name_div.get_text(strip=True)
        if not name:
            name = "АЗС"

        prices = {}
        details_div = div.find("div", class_="gas-station-details")
        if details_div:
            for detail in details_div.find_all("div", class_="gas-station-detail"):
                text = detail.get_text(strip=True)
                # Find fuel type from class list (ai92 default-fuel, dt, ai95, etc.)
                css_classes = detail.get("class", [])
                # Find the numeric price in text: e.g. "Аи-9263.23р." or "ДТ80.35р."
                price_match = re.search(r"(\d{1,3}[.,]\d{2})\s*р\.?", text)
                if not price_match:
                    continue
                price = float(price_match.group(1).replace(",", "."))

                # Determine fuel type from class names first
                fuel_type = None
                for cls in css_classes:
                    if cls == "ai92":
                        fuel_type = "92"
                    elif cls == "ai95":
                        fuel_type = "95"
                    elif cls == "ai98":
                        fuel_type = "98"
                    elif cls == "ai100":
                        fuel_type = "100"
                    elif cls == "dt":
                        fuel_type = "diesel"

                # Fallback: match from text prefix
                if not fuel_type:
                    for pattern, ftype in FUEL_TYPE_MAP.items():
                        if text.lower().startswith(pattern.lower()):
                            fuel_type = ftype
                            break

                if fuel_type and fuel_type not in prices:
                    prices[fuel_type] = price

        if prices:
            results.append({
                "id": data_id,
                "name": name,
                "lat": lat,
                "lon": lon,
                "prices": prices,
                "city": city_slug,
            })

    return results


async def fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch a single page, return HTML or None."""
    try:
        async with session.get(url, timeout=TIMEOUT, headers=HEADERS) as resp:
            if resp.status == 200:
                return await resp.text()
            if resp.status == 404:
                return None
            logger.warning(f"HTTP {resp.status}: {url}")
    except asyncio.TimeoutError:
        logger.debug(f"Timeout: {url}")
    except Exception as e:
        logger.debug(f"Error fetching {url}: {e}")
    return None


async def find_station(lat: float, lon: float, name: str) -> int | None:
    """Find existing station in DB by name+lat+lon within 0.01 degrees."""
    rows = await db._fetch(
        """SELECT id, lat, lon FROM stations
           WHERE ABS(lat - ?) < 0.01 AND ABS(lon - ?) < 0.01
           LIMIT 10""",
        lat, lon,
    )
    if not rows:
        return None

    best_id = None
    best_dist = float("inf")
    for r in rows:
        dist = abs(r["lat"] - lat) + abs(r["lon"] - lon)
        if dist < best_dist:
            best_dist = dist
            best_id = r["id"]

    if best_id and best_dist < 0.02:
        return best_id

    # Fallback: match by name within bbox
    rows2 = await db._fetch(
        """SELECT id FROM stations
           WHERE name = ? AND ABS(lat - ?) < 0.01 AND ABS(lon - ?) < 0.01
           LIMIT 1""",
        name, lat, lon,
    )
    return rows2[0]["id"] if rows2 else None


async def create_station(name: str, lat: float, lon: float, city: str) -> int | None:
    """Create a new station in DB."""
    try:
        now_sql = "datetime('now')" if db.USE_SQLITE else "NOW()"
        sid = await db._execute(
            f"""INSERT INTO stations (name, brand, network, city, lat, lon, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, TRUE, {now_sql}, {now_sql})""",
            name, name, name, city, lat, lon,
            returning=True,
        )
        return sid
    except Exception as e:
        logger.debug(f"Failed to create station {name}: {e}")
        return None


async def save_reports(station_id: int, prices: dict, city: str, name: str) -> int:
    """Save price reports for a station. Returns count of saved reports."""
    saved = 0
    now_sql = "datetime('now')" if db.USE_SQLITE else "NOW()"
    expires_sql = "datetime('now', '+24 hours')" if db.USE_SQLITE else "NOW() + INTERVAL '24 hours'"

    for fuel_type, price in prices.items():
        try:
            await db._execute(
                f"""INSERT INTO reports
                   (station_id, fuel_type, price, available, source, created_at, expires_at, comment)
                   VALUES (?, ?, ?, TRUE, ?, {now_sql},
                           {expires_sql}, ?)""",
                station_id, fuel_type, price, SOURCE,
                f"[fuelmap.ru] {city}: {name} {fuel_type}={price}₽",
            )
            saved += 1
        except Exception as e:
            logger.debug(f"Failed to save report: {e}")
    return saved


async def process_city(
    session: aiohttp.ClientSession,
    city_slug: str,
    city_idx: int,
    total_cities: int,
    semaphore: asyncio.Semaphore,
    stats: dict,
) -> tuple[int, int]:
    """Process a single city page. Returns (stations_found, prices_saved)."""
    url = f"{BASE_URL}/{city_slug}"
    async with semaphore:
        html = await fetch_page(session, url)
        await asyncio.sleep(DELAY_BETWEEN)

    if not html:
        return 0, 0

    stations = parse_stations(html, city_slug)
    if not stations:
        return 0, 0

    prices_saved = 0
    for st in stations:
        existing_id = await find_station(st["lat"], st["lon"], st["name"])
        if existing_id:
            sid = existing_id
            stats["matched"] += 1
        else:
            sid = await create_station(st["name"], st["lat"], st["lon"], city_slug)
            if sid:
                stats["created"] += 1
            else:
                continue

        prices_saved += await save_reports(sid, st["prices"], city_slug, st["name"])

    log_msg = f"[{city_idx}/{total_cities}] {city_slug}: {len(stations)} stations, {prices_saved} prices"
    logger.info(log_msg)

    return len(stations), prices_saved


async def load_city_slugs(limit: int = 0) -> list[str]:
    """Load city slugs from file or generate from DB."""
    # Check scripts dir first, then /tmp
    scripts_dir = os.path.dirname(__file__)
    slugs_file = os.path.join(scripts_dir, "fuelmap_city_slugs.txt")
    if not os.path.exists(slugs_file):
        slugs_file = "/tmp/fuelmap_city_slugs.txt"
    if os.path.exists(slugs_file):
        with open(slugs_file, "r") as f:
            slugs = [line.strip() for line in f if line.strip()]
        logger.info(f"Loaded {len(slugs)} slugs from {slugs_file}")
        if limit > 0:
            slugs = slugs[:limit]
        return slugs

    # Generate from DB
    logger.info("Slugs file not found, generating from DB...")
    rows = await db._fetch(
        "SELECT DISTINCT city FROM stations WHERE city IS NOT NULL AND city != ''"
    )
    slugs = sorted(set(city_to_slug(r["city"]) for r in rows if city_to_slug(r["city"])))
    # Write to file for next time
    with open(slugs_file, "w") as f:
        f.write("\n".join(slugs) + "\n")
    logger.info(f"Generated {len(slugs)} slugs from DB cities")
    if limit > 0:
        slugs = slugs[:limit]
    return slugs


async def main():
    parser = argparse.ArgumentParser(description="Parse fuelmap.ru for fuel station prices")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of cities (0=all)")
    parser.add_argument("--city", type=str, help="Process a single city slug")
    args = parser.parse_args()

    logger.info("=== fuelmap.ru parser ===")

    if not db.API_MODE:
        await db.init_db()

    slugs = await load_city_slugs(args.limit)
    if args.city:
        slugs = [args.city]

    if not slugs:
        logger.warning("No city slugs to process")
        if not db.API_MODE:
            await db.close_db()
        return

    logger.info(f"Processing {len(slugs)} cities with {CONCURRENT} concurrent requests")

    semaphore = asyncio.Semaphore(CONCURRENT)
    stats = {"matched": 0, "created": 0}
    total_stations = 0
    total_prices = 0
    cities_with_data = 0

    connector = aiohttp.TCPConnector(limit=CONCURRENT, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_city(session, slug, i + 1, len(slugs), semaphore, stats)
            for i, slug in enumerate(slugs)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for res in results:
        if isinstance(res, Exception):
            logger.error(f"Task failed: {res}")
            continue
        stations, prices = res
        total_stations += stations
        total_prices += prices
        if stations > 0:
            cities_with_data += 1

    logger.info("=== Done ===")
    logger.info(f"  Cities: {len(slugs)} (with data: {cities_with_data})")
    logger.info(f"  Stations found: {total_stations}")
    logger.info(f"  Prices saved: {total_prices}")
    logger.info(f"  Matched existing: {stats['matched']}")
    logger.info(f"  New stations created: {stats['created']}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
