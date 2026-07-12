#!/usr/bin/env python3
"""Обогащение адресов АЗС через Photon (komoot.io) reverse geocoding.

Заполняет street-level адреса для АЗС, у которых:
- address пустой или содержит только "г. <город>" / "город <город>"
- есть координаты (lat, lon)

Photon (OSM-based) — лимит ~10 req/s, но безопаснее 2-3 req/s.
"""
import argparse
import asyncio
import logging
import os
import re
import sys
import time

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("enrich_addresses")

PHOTON_URL = "https://photon.komoot.io/reverse"
USER_AGENT = "benzin-ryadom/1.0 (fuel-finder bot)"


def _clean(v):
    if v is None or v == "None" or v == "":
        return None
    return v


async def reverse_geocode(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
    retries: int = 3,
) -> str | None:
    """Reverse geocoding через Photon с retry + exponential backoff."""
    params = {"lat": str(lat), "lon": str(lon), "lang": "en"}
    for attempt in range(retries):
        try:
            async with session.get(
                PHOTON_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    break
                if r.status in (429, 503):
                    wait = 2 ** attempt * 2  # 2, 4, 8 сек
                    logger.debug(f"  HTTP {r.status}, retry in {wait}s (attempt {attempt+1}/{retries})")
                    await asyncio.sleep(wait)
                    continue
                return None
        except asyncio.TimeoutError:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            return None
        except Exception as e:
            logger.debug(f"  geocode: {e}")
            return None
    else:
        return None

    if "features" not in data or not data["features"]:
        return None

    props = data["features"][0].get("properties", {})
    street_raw = props.get("street")
    housenumber = _clean(props.get("housenumber"))
    osm_value = props.get("osm_value", "")

    # Используем только реальные улицы.
    # Photon иногда возвращает название POI (АЗС) в поле street для rural areas.
    # Фильтруем: street должен быть реальной улицей, а не названием бренда.
    street = _clean(street_raw)

    if not street:
        return None

    # Если osm_value = fuel — это АЗС, Photon вернул бренд как street
    if osm_value == "fuel":
        return None

    # Если street в кавычках — скорее всего бренд АЗС: "«Дон»", "«Холмогоры»"
    if street.startswith("«") or street.startswith('"') or street.endswith("»"):
        return None

    if housenumber:
        return f"{street}, {housenumber}"
    return street


# Слова-индикаторы плохого адреса (нет street-level)
_BAD_PREFIXES = re.compile(
    r'^(г\.?|город|п\.?г\.т\.?|пос\.\s*городского\s*типа|сельское\s*поселение|район)',
    re.IGNORECASE,
)

# Слова-индикаторы хорошего адреса (есть улица/номер)
_STREET_KEYWORDS = re.compile(
    r'ул|улица|шоссе|проспект|переул|проезд|бульв|км\s|просп|набережн|площадь|пер\.',
    re.IGNORECASE,
)


def is_good_address(address: str) -> bool:
    """Проверяет, есть ли street-level информация.

    Хорошо:  "ул. Ленина, 5", "Москва, ул. Тверская, 1", "Каширское шоссе, км 15"
    Плохо:  "", "г. Москва", "Москва, Московская область", "Россия"
    """
    if not address or not address.strip():
        return False
    address = address.strip()

    # Пустой / слишком короткий
    if len(address) < 5:
        return False

    # "г. Город" / "город Город" / "п.г.т. ..." — без улицы
    if _BAD_PREFIXES.match(address):
        # Но если дальше есть улица — ок: "г. Москва, ул. Ленина, 5"
        if _STREET_KEYWORDS.search(address):
            return True
        return False

    # Если содержит запятую — проверяем части
    if "," in address:
        parts = [p.strip() for p in address.split(",")]
        # Если последняя часть — регион/область, смотрим остальные
        for p in parts:
            if _STREET_KEYWORDS.search(p):
                return True
        # Только город + регион — плохо
        if len(parts) <= 2:
            return False
        return False

    # Одна строка — если есть ключевое слово улицы или "км" — хорошо
    if _STREET_KEYWORDS.search(address):
        return True

    return False


def needs_enrichment(station: dict) -> bool:
    """Определяет, нуждается ли станция в обогащении адреса."""
    addr = station.get("address") or ""
    if is_good_address(addr):
        return False
    if not station.get("lat") or not station.get("lon"):
        return False
    return True


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Лимит АЗС (0 = все)")
    parser.add_argument("--city", help="Только этот город")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    parser.add_argument("--concurrency", type=int, default=3, help="Параллельных запросов (default: 3)")
    args = parser.parse_args()

    if not db.API_MODE:
        await db.init_db()

    # Получаем АЗС с координатами
    if db.USE_SQLITE:
        query = """SELECT id, name, city, address, lat, lon FROM stations
                   WHERE is_active = 1 AND lat IS NOT NULL AND lon IS NOT NULL"""
        if args.city:
            query += " AND city = ?"
            params = [args.city]
        else:
            params = []
        if args.limit:
            query += " LIMIT ?"
            params.append(args.limit)
        all_stations = await db._fetch(query, *params)
    else:
        async with db._db.acquire() as conn:
            sql = """SELECT id, name, city, address, lat, lon FROM stations
                     WHERE is_active = TRUE AND lat IS NOT NULL AND lon IS NOT NULL"""
            if args.city:
                sql += f" AND city = $1"
                if args.limit:
                    sql += f" LIMIT $2"
                    rows = await conn.fetch(sql, args.city, args.limit)
                else:
                    rows = await conn.fetch(sql, args.city)
            else:
                if args.limit:
                    sql += f" LIMIT $1"
                    rows = await conn.fetch(sql, args.limit)
                else:
                    rows = await conn.fetch(sql)
            all_stations = [dict(r) for r in rows]

    logger.info(f"Всего АЗС с координатами: {len(all_stations)}")

    needs = [s for s in all_stations if needs_enrichment(s)]
    logger.info(f"Нужно обновить: {len(needs)}")

    if not needs:
        logger.info("Все адреса в порядке!")
        return

    updated = 0
    found = 0
    not_found = 0
    errors = 0
    semaphore = asyncio.Semaphore(args.concurrency)
    start_time = time.time()

    async def process_one(s, idx):
        nonlocal updated, found, not_found, errors
        async with semaphore:
            addr = await reverse_geocode(session, s["lat"], s["lon"])
            if addr:
                found += 1
                new_addr = addr
                if s.get("city") and s.get("city") not in addr:
                    new_addr = f"{s['city']}, {addr}"
                if not args.dry_run:
                    try:
                        if db.USE_SQLITE:
                            await db._execute(
                                "UPDATE stations SET address = ? WHERE id = ?",
                                new_addr, s["id"],
                            )
                        else:
                            async with db._db.acquire() as conn:
                                await conn.execute(
                                    "UPDATE stations SET address = $1 WHERE id = $2",
                                    new_addr, s["id"],
                                )
                        updated += 1
                    except Exception as e:
                        logger.warning(f"  update #{s['id']}: {e}")
                        errors += 1
                else:
                    updated += 1
                    if idx <= 5:
                        logger.info(f"  [dry] #{s['id']} {s.get('city', '')}: {new_addr}")
            else:
                not_found += 1
            # Throttle: ~2 req/s per worker, 3 workers = ~6 req/s total
            await asyncio.sleep(0.5)

    async with aiohttp.ClientSession() as session:
        tasks = [process_one(s, i) for i, s in enumerate(needs, 1)]
        for i in range(0, len(tasks), 30):
            batch = tasks[i:i+30]
            await asyncio.gather(*batch, return_exceptions=True)
            elapsed = time.time() - start_time
            rate = found / elapsed if elapsed > 0 else 0
            logger.info(
                f"  Прогресс: {min(i+30, len(tasks))}/{len(tasks)} "
                f"(найдено: {found}, обновлено: {updated}, не найдено: {not_found}, ошибки: {errors}) "
                f"[{rate:.1f} addr/s]"
            )

    elapsed = time.time() - start_time
    logger.info(f"\n=== ИТОГО за {elapsed:.0f}s ===")
    logger.info(f"  Найдено адресов: {found}")
    logger.info(f"  Обновлено в БД: {updated}")
    logger.info(f"  Не найдено: {not_found}")
    logger.info(f"  Ошибки: {errors}")
    logger.info(f"  Скорость: {found/elapsed:.1f} addr/s")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
