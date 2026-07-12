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

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "benzin-ryadom/1.0 (fuel-finder bot)"


async def reverse_geocode(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
) -> str | None:
    """Reverse geocoding через Nominatim."""
    params = {
        "lat": str(lat),
        "lon": str(lon),
        "format": "json",
        "accept-language": "ru",
        "addressdetails": 1,
        "zoom": 18,  # street level
    }
    try:
        async with session.get(
            NOMINATIM_URL,
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

    if "error" in data or "address" not in data:
        return None

    addr = data["address"]

    # Собираем улицу + дом
    street_parts = []
    if "house_number" in addr:
        street_parts.append(addr["house_number"])

    # Приоритет улицы
    street_name = (
        addr.get("pedestrian")
        or addr.get("footway")
        or addr.get("residential")
        or addr.get("street")
    )
    if street_name:
        if street_parts:
            street = f"{street_name}, {street_parts[0]}"
        else:
            street = street_name
    elif street_parts:
        street = street_parts[0]
    else:
        return None

    # Добавляем ориентир если есть
    extra = []
    for k in ("suburb", "neighbourhood", "city_district"):
        if k in addr:
            extra.append(addr[k])
            break

    if extra:
        return f"{street} ({extra[0]})"
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
    async with aiohttp.ClientSession() as session:
        for i, s in enumerate(needs_update, 1):
            addr = await reverse_geocode(session, s["lat"], s["lon"])
            if addr:
                logger.info(f"  [{i}/{len(needs_update)}] #{s['id']} {s.get('city', '')}: {addr}")
                if not args.dry_run:
                    try:
                        if db.USE_SQLITE:
                            await db._execute(
                                "UPDATE stations SET address = ? WHERE id = ?",
                                f"{s.get('city', '')}, {addr}" if s.get('city') and s.get('city') not in addr else addr,
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

            if i % 50 == 0:
                logger.info(f"  Прогресс: {i}/{len(needs_update)} (обновлено: {updated}, не найдено: {failed})")
            await asyncio.sleep(1.1)  # Nominatim лимит 1 req/sec

    logger.info(f"\n=== ИТОГО ===")
    logger.info(f"  Обновлено: {updated}")
    logger.info(f"  Не найдено: {failed}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
