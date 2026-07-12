#!/usr/bin/env python3
"""Обогащение адресов АЗС через Nominatim (OpenStreetMap) reverse geocoding.

Заполняет street-level адреса для АЗС, у которых:
- address пустой или содержит только "г. <город>"
- есть координаты (lat, lon)

Использует Nominatim (https://nominatim.org/) — бесплатный, без auth, лимит 1 req/sec.
"""
import argparse
import asyncio
import logging
import os
import re
import sys

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("enrich_addresses")

# Photon (https://photon.komoot.io) — либеральный лимит, OSM-based
PHOTON_URL = "https://photon.komoot.io/reverse"
USER_AGENT = "benzin-ryadom/1.0 (fuel-finder bot)"


async def reverse_geocode(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
) -> str | None:
    """Reverse geocoding через Photon (komoot.io, OSM-based)."""
    params = {
        "lat": str(lat),
        "lon": str(lon),
        "lang": "en",  # Photon не поддерживает ru
    }
    try:
        async with session.get(
            PHOTON_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
    except Exception as e:
        logger.debug(f"  geocode: {e}")
        return None

    if "features" not in data or not data["features"]:
        return None

    props = data["features"][0].get("properties", {})

    def _clean(v):
        """Photon иногда возвращает 'None' как строку. Убираем это."""
        if v is None or v == "None" or v == "":
            return None
        return v

    # Собираем улицу + дом
    street_raw = props.get("street")
    name_raw = props.get("name")
    street = _clean(street_raw) or _clean(name_raw)
    housenumber = _clean(props.get("housenumber"))

    if not street:
        return None

    if housenumber:
        return f"{street}, {housenumber}"
    return street


def is_good_address(address: str) -> bool:
    """Проверяет, есть ли street-level информация."""
    if not address:
        return False
    if address.startswith("г ") or address.startswith("г.") or address.startswith("город"):
        return False
    if "," in address:
        # "Иваново, Ивановская область" — нет улицы
        parts = address.split(",")
        # Если только 1-2 части и нет улицы — плохо
        if len(parts) < 3 and not any(p for p in parts if re.search(r'ул|шоссе|проспект|переул|проезд|км', p, re.I)):
            return False
    # Должно быть слово с улицей
    if re.search(r'ул|шоссе|проспект|переул|проезд|трасса|км', address, re.I):
        return True
    return False


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Лимит АЗС")
    parser.add_argument("--city", help="Только этот город")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    if not db.API_MODE:
        await db.init_db()

    # Получаем АЗС без хороших адресов
    if db.USE_SQLITE:
        query = """SELECT id, name, city, address, lat, lon FROM stations
                   WHERE is_active = 1 AND lat IS NOT NULL AND lon IS NOT NULL"""
        if args.city:
            query += " AND city = ?"
            params = [args.city]
        else:
            params = []
        query += " LIMIT ?"
        params.append(args.limit or 999999)
        all_stations = await db._fetch(query, *params)
    else:
        async with db._db.acquire() as conn:
            sql = """SELECT id, name, city, address, lat, lon FROM stations
                     WHERE is_active = TRUE AND lat IS NOT NULL AND lon IS NOT NULL"""
            if args.city:
                sql += " AND city = $1"
                sql += " LIMIT $2"
                rows = await conn.fetch(sql, args.city, args.limit or 999999)
            else:
                sql += " LIMIT $1"
                rows = await conn.fetch(sql, args.limit or 999999)
            all_stations = [dict(r) for r in rows]

    logger.info(f"Всего АЗС для проверки: {len(all_stations)}")

    needs_update = []
    for s in all_stations:
        s_dict = s if isinstance(s, dict) else dict(s)
        if not is_good_address(s_dict.get("address") or ""):
            needs_update.append(s_dict)

    logger.info(f"Нужно обновить адресов: {len(needs_update)}")

    if not needs_update:
        logger.info("Все адреса в порядке!")
        return

    updated = 0
    failed = 0
    semaphore = asyncio.Semaphore(10)  # 10 параллельных (Photon либеральный)

    async def process_one(s, idx):
        nonlocal updated, failed
        async with semaphore:
            addr = await reverse_geocode(session, s["lat"], s["lon"])
            if addr:
                logger.info(f"  [{idx}/{len(needs_update)}] #{s['id']} {s.get('city', '')}: {addr}")
                if not args.dry_run:
                    try:
                        if db.USE_SQLITE:
                            new_addr = addr
                            if s.get('city') and s.get('city') not in addr:
                                new_addr = f"{s['city']}, {addr}"
                            await db._execute(
                                "UPDATE stations SET address = ? WHERE id = ?",
                                new_addr,
                                s["id"],
                            )
                        else:
                            new_addr = addr
                            if s.get('city') and s.get('city') not in addr:
                                new_addr = f"{s['city']}, {addr}"
                            async with db._db.acquire() as conn:
                                await conn.execute(
                                    "UPDATE stations SET address = $1 WHERE id = $2",
                                    new_addr, s["id"],
                                )
                        updated += 1
                    except Exception as e:
                        logger.warning(f"  update #{s['id']}: {e}")
                        failed += 1
            else:
                failed += 1
            await asyncio.sleep(0.12)  # 10 параллельных = ~0.56s/req, берём 0.12s

    async with aiohttp.ClientSession() as session:
        tasks = [process_one(s, i) for i, s in enumerate(needs_update, 1)]
        # Запускаем все параллельно батчами по 100
        for i in range(0, len(tasks), 100):
            batch = tasks[i:i+100]
            await asyncio.gather(*batch, return_exceptions=True)
            logger.info(f"  Прогресс: {min(i+100, len(tasks))}/{len(tasks)} (обновлено: {updated}, не найдено: {failed})")

    logger.info(f"\n=== ИТОГО ===")
    logger.info(f"  Обновлено: {updated}")
    logger.info(f"  Не найдено: {failed}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
