#!/usr/bin/env python3
"""Seed-данные основных федеральных и региональных трасс РФ.

Содержит:
- 30+ федеральных трасс (М-1...М-12, "Дон", "Кавказ", "Крым", и т.д.)
- 10+ региональных трасс

Координаты — приблизительные точки (lat_min, lat_max, lon_min, lon_max)
для bbox-фильтрации. Потом расширим до полигонов.

Использование:
  python3 scripts/seed_routes.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_routes")


# Основные федеральные трассы РФ
ROUTES = [
    # Федеральные трассы (М-)
    {"code": "M-1", "name": "Беларусь", "aliases": "Минское шоссе,М1",
     "length_km": 1350, "start_point": "Москва", "end_point": "Брест",
     "lat_min": 53.5, "lat_max": 56.0, "lon_min": 27.0, "lon_max": 38.0,
     "description": "Москва — Смоленск — граница с Беларусью"},
    {"code": "M-2", "name": "Крым", "aliases": "М2,Симферопольское шоссе",
     "length_km": 720, "start_point": "Москва", "end_point": "Симферополь",
     "lat_min": 44.0, "lat_max": 56.0, "lon_min": 33.0, "lon_max": 38.0,
     "description": "Москва — Тула — Орел — Курск — Белгород — граница — Харьков — Симферополь"},
    {"code": "M-3", "name": "Украина", "aliases": "М3,Киевское шоссе",
     "length_km": 490, "start_point": "Москва", "end_point": "Брянск (граница)",
     "lat_min": 52.0, "lat_max": 56.0, "lon_min": 33.0, "lon_max": 38.0,
     "description": "Москва — Калуга — Брянск"},
    {"code": "M-4", "name": "Дон", "aliases": "М4,Каширское шоссе",
     "length_km": 1540, "start_point": "Москва", "end_point": "Новороссийск",
     "lat_min": 44.0, "lat_max": 56.0, "lon_min": 37.0, "lon_max": 41.0,
     "description": "Москва — Воронеж — Ростов-на-Дону — Краснодар — Новороссийск"},
    {"code": "M-5", "name": "Урал", "aliases": "М5,Рязанское шоссе",
     "length_km": 1870, "start_point": "Москва", "end_point": "Челябинск",
     "lat_min": 53.0, "lat_max": 58.0, "lon_min": 36.0, "lon_max": 62.0,
     "description": "Москва — Рязань — Пенза — Самара — Уфа — Челябинск"},
    {"code": "M-7", "name": "Волга", "aliases": "М7,Горьковское шоссе",
     "length_km": 1300, "start_point": "Москва", "end_point": "Уфа",
     "lat_min": 53.0, "lat_max": 58.0, "lon_min": 36.0, "lon_max": 56.0,
     "description": "Москва — Владимир — Нижний Новгород — Казань — Уфа"},
    {"code": "M-8", "name": "Холмогоры", "aliases": "М8,Ярославское шоссе",
     "length_km": 1150, "start_point": "Москва", "end_point": "Архангельск",
     "lat_min": 56.0, "lat_max": 65.0, "lon_min": 38.0, "lon_max": 41.0,
     "description": "Москва — Ярославль — Вологда — Архангельск (Северодвинск)"},
    {"code": "M-9", "name": "Балтия", "aliases": "М9,Рижское шоссе",
     "length_km": 610, "start_point": "Москва", "end_point": "граница с Латвией",
     "lat_min": 55.0, "lat_max": 57.0, "lon_min": 23.0, "lon_max": 38.0,
     "description": "Москва — Волоколамск — граница с Латвией"},
    {"code": "M-10", "name": "Россия", "aliases": "М10,Ленинградское шоссе",
     "length_km": 780, "start_point": "Москва", "end_point": "Санкт-Петербург",
     "lat_min": 55.0, "lat_max": 60.0, "lon_min": 28.0, "lon_max": 38.0,
     "description": "Москва — Тверь — Великий Новгород — Санкт-Петербург"},
    {"code": "M-11", "name": "Нарва", "aliases": "М11,Нева",
     "length_km": 670, "start_point": "Москва", "end_point": "Санкт-Петербург",
     "lat_min": 55.0, "lat_max": 60.0, "lon_min": 28.0, "lon_max": 38.0,
     "description": "Скоростная Москва — Санкт-Петербург (платная)"},
    {"code": "M-12", "name": "Восток", "aliases": "М12,Восток",
     "length_km": 810, "start_point": "Москва (Балашиха)", "end_point": "Казань",
     "lat_min": 54.0, "lat_max": 56.0, "lon_min": 38.0, "lon_max": 50.0,
     "description": "Скоростная Москва — Казань (строится)"},
    # === РОССИЙСКИЕ ТРАССЫ БЕЗ ПРЕФИКСА "М" ===
    {"code": "Р-21", "name": "Кола", "aliases": "Р21,Мурманское шоссе",
     "length_km": 1590, "start_point": "Санкт-Петербург", "end_point": "Мурманск",
     "lat_min": 59.0, "lat_max": 69.0, "lon_min": 28.0, "lon_max": 41.0,
     "description": "СПб — Петрозаводск — Мурманск"},
    {"code": "Р-22", "name": "Каспий", "aliases": "Р22",
     "length_km": 1380, "start_point": "Москва", "end_point": "Астрахань",
     "lat_min": 46.0, "lat_max": 56.0, "lon_min": 38.0, "lon_max": 48.0,
     "description": "Москва — Тамбов — Волгоград — Астрахань"},
    {"code": "Р-23", "name": "Псков", "aliases": "Р23,Киевское шоссе (СПб)",
     "length_km": 510, "start_point": "Санкт-Петербург", "end_point": "граница с Белоруссией",
     "lat_min": 56.0, "lat_max": 60.0, "lon_min": 28.0, "lon_max": 32.0,
     "description": "СПб — Псков — граница с Беларусью"},
    {"code": "Р-119", "name": "Тамбов — Воронеж", "aliases": "Р119",
     "length_km": 250, "start_point": "Тамбов", "end_point": "Воронеж",
     "lat_min": 51.5, "lat_max": 53.0, "lon_min": 39.0, "lon_max": 42.0,
     "description": "Тамбов — Мичуринск — Воронеж"},
    {"code": "Р-132", "name": "Золотое кольцо", "aliases": "Р132",
     "length_km": 1500, "start_point": "Ярославль", "end_point": "Ярославль",
     "lat_min": 56.0, "lat_max": 58.5, "lon_min": 38.0, "lon_max": 42.0,
     "description": "Кольцевой маршрут Золотое кольцо (Ярославль — Кострома — Иваново — Владимир — Сергиев Посад)"},
    {"code": "Р-158", "name": "Пенза — Саратов", "aliases": "Р158",
     "length_km": 240, "start_point": "Пенза", "end_point": "Саратов",
     "lat_min": 51.5, "lat_max": 53.5, "lon_min": 44.0, "lon_max": 46.0,
     "description": "Пенза — Саратов"},
    {"code": "Р-176", "name": "Вятка", "aliases": "Р176",
     "length_km": 870, "start_point": "Чебоксары", "end_point": "Сыктывкар",
     "lat_min": 56.0, "lat_max": 62.0, "lon_min": 47.0, "lon_max": 51.0,
     "description": "Чебоксары — Йошкар-Ола — Сыктывкар"},
    {"code": "Р-193", "name": "Воронеж — Тамбов", "aliases": "Р193",
     "length_km": 230, "start_point": "Воронеж", "end_point": "Тамбов",
     "lat_min": 51.5, "lat_max": 52.5, "lon_min": 40.0, "lon_max": 42.0,
     "description": "Воронеж — Тамбов"},
    {"code": "Р-208", "name": "Тамбов — Пенза", "aliases": "Р208",
     "length_km": 240, "start_point": "Тамбов", "end_point": "Пенза",
     "lat_min": 52.5, "lat_max": 53.5, "lon_min": 41.0, "lon_max": 45.0,
     "description": "Тамбов — Пенза"},
    {"code": "Р-215", "name": "Астрахань — Махачкала", "aliases": "Р215,Кавказ",
     "length_km": 600, "start_point": "Астрахань", "end_point": "Махачкала",
     "lat_min": 42.5, "lat_max": 46.5, "lon_min": 45.0, "lon_max": 50.0,
     "description": "Астрахань — Махачкала (вдоль Каспийского моря)"},
    {"code": "Р-217", "name": "Кавказ", "aliases": "Р217",
     "length_km": 1110, "start_point": "Павловская (Краснодарский край)", "end_point": "граница с Азербайджаном",
     "lat_min": 42.0, "lat_max": 47.0, "lon_min": 38.0, "lon_max": 49.0,
     "description": "Краснодар — Пятигорск — Махачкала — граница с Азербайджаном"},
    {"code": "Р-228", "name": "Сызрань — Саратов", "aliases": "Р228",
     "length_km": 280, "start_point": "Сызрань", "end_point": "Саратов",
     "lat_min": 52.0, "lat_max": 53.5, "lon_min": 46.0, "lon_max": 48.0,
     "description": "Сызрань — Саратов (через Волгу)"},
    {"code": "Р-239", "name": "Казань — Уфа", "aliases": "Р239",
     "length_km": 530, "start_point": "Казань", "end_point": "Уфа",
     "lat_min": 54.0, "lat_max": 57.0, "lon_min": 49.0, "lon_max": 56.0,
     "description": "Казань — Уфа"},
    {"code": "Р-241", "name": "Казань — Буинск", "aliases": "Р241",
     "length_km": 180, "start_point": "Казань", "end_point": "Буинск",
     "lat_min": 54.5, "lat_max": 55.5, "lon_min": 48.0, "lon_max": 49.0,
     "description": "Казань — Буинск"},
    {"code": "Р-242", "name": "Пермь — Екатеринбург", "aliases": "Р242",
     "length_km": 350, "start_point": "Пермь", "end_point": "Екатеринбург",
     "lat_min": 56.0, "lat_max": 58.0, "lon_min": 56.0, "lon_max": 61.0,
     "description": "Пермь — Екатеринбург"},
    {"code": "Р-254", "name": "Иртыш", "aliases": "Р254",
     "length_km": 1500, "start_point": "Кулунда (Алтайский край)", "end_point": "Омск — Тюмень",
     "lat_min": 53.0, "lat_max": 58.0, "lon_min": 60.0, "lon_max": 86.0,
     "description": "Челябинск — Курган — Омск — Новосибирск"},
    {"code": "Р-255", "name": "Сибирь", "aliases": "Р255,М-53",
     "length_km": 1860, "start_point": "Новосибирск", "end_point": "Иркутск",
     "lat_min": 53.0, "lat_max": 57.0, "lon_min": 80.0, "lon_max": 105.0,
     "description": "Новосибирск — Кемерово — Красноярск — Иркутск"},
    {"code": "Р-297", "name": "Амур", "aliases": "Р297,Амурское шоссе",
     "length_km": 2097, "start_point": "Чита", "end_point": "Хабаровск",
     "lat_min": 47.0, "lat_max": 56.0, "lon_min": 107.0, "lon_max": 140.0,
     "description": "Чита — Благовещенск — Хабаровск"},
    {"code": "Р-298", "name": "Каспий", "aliases": "Р298,Каспий",
     "length_km": 840, "start_point": "Кисловодск", "end_point": "Элиста — Астрахань",
     "lat_min": 43.0, "lat_max": 46.5, "lon_min": 42.0, "lon_max": 48.0,
     "description": "Кисловодск — Элиста — Астрахань"},
    {"code": "Р-351", "name": "Екатеринбург — Тюмень", "aliases": "Р351",
     "length_km": 320, "start_point": "Екатеринбург", "end_point": "Тюмень",
     "lat_min": 56.0, "lat_max": 58.0, "lon_min": 60.0, "lon_max": 66.0,
     "description": "Екатеринбург — Тюмень"},
    {"code": "Р-404", "name": "Хабаровск — Комсомольск", "aliases": "Р404",
     "length_km": 380, "start_point": "Хабаровск", "end_point": "Комсомольск-на-Амуре",
     "lat_min": 48.5, "lat_max": 51.0, "lon_min": 133.0, "lon_max": 137.0,
     "description": "Хабаровск — Комсомольск-на-Амуре"},
    # === КРЫМ, ДНР, ЛНР (новые территории) ===
    {"code": "M-17", "name": "Крымская трасса", "aliases": "М17,Ялтинское шоссе",
     "length_km": 200, "start_point": "Симферополь", "end_point": "Ялта",
     "lat_min": 44.4, "lat_max": 45.1, "lon_min": 33.5, "lon_max": 34.5,
     "description": "Симферополь — Алушта — Ялта"},
    {"code": "M-18", "name": "Харьков — Симферополь", "aliases": "М18",
     "length_km": 730, "start_point": "Харьков", "end_point": "Симферополь",
     "lat_min": 44.0, "lat_max": 50.0, "lon_min": 34.0, "lon_max": 37.0,
     "description": "Харьков — Запорожье — Херсон — Симферополь (через Крымский мост)"},
    {"code": "Р-280", "name": "Новороссийск — Керчь", "aliases": "Р280",
     "length_km": 350, "start_point": "Новороссийск", "end_point": "Керчь",
     "lat_min": 44.5, "lat_max": 45.5, "lon_min": 36.0, "lon_max": 38.0,
     "description": "Новороссийск — Керчь"},
    {"code": "Р-150", "name": "Белгород — Старый Оскол", "aliases": "Р150",
     "length_km": 170, "start_point": "Белгород", "end_point": "Старый Оскол",
     "lat_min": 50.5, "lat_max": 51.5, "lon_min": 36.0, "lon_max": 38.0,
     "description": "Белгород — Старый Оскол"},
    {"code": "Р-186", "name": "Воронеж — Луганск", "aliases": "Р186",
     "length_km": 450, "start_point": "Воронеж", "end_point": "Луганск",
     "lat_min": 48.0, "lat_max": 52.0, "lon_min": 38.0, "lon_max": 40.0,
     "description": "Воронеж — Луганск"},
    {"code": "Р-260", "name": "Волгоград — Каменск-Шахтинский", "aliases": "Р260",
     "length_km": 280, "start_point": "Волгоград", "end_point": "Каменск-Шахтинский",
     "lat_min": 47.5, "lat_max": 49.0, "lon_min": 40.0, "lon_max": 44.0,
     "description": "Волгоград — Каменск-Шахтинский"},
    {"code": "Р-279", "name": "Каменск-Шахтинский — Донецк", "aliases": "Р279",
     "length_km": 350, "start_point": "Каменск-Шахтинский", "end_point": "Донецк",
     "lat_min": 47.0, "lat_max": 48.5, "lon_min": 38.0, "lon_max": 41.0,
     "description": "Каменск-Шахтинский — Донецк (через границу)"},
]


async def main():
    if not db.API_MODE:
        await db.init_db()

    # Сначала очищаем
    if db.USE_SQLITE:
        await db._execute("DELETE FROM station_routes")
        await db._execute("DELETE FROM routes")
    else:
        async with db._db.acquire() as conn:
            await conn.execute("DELETE FROM station_routes")
            await conn.execute("DELETE FROM routes")

    total = 0
    for r in ROUTES:
        if db.USE_SQLITE:
            result = await db._execute(
                """INSERT INTO routes (code, name, aliases, type, length_km, start_point, end_point, description, is_active)
                   VALUES (?, ?, ?, 'federal', ?, ?, ?, ?, 1)""",
                r["code"], r["name"], r.get("aliases", ""), r["length_km"],
                r["start_point"], r["end_point"], r["description"],
                returning=True,
            )
            route_id = result if isinstance(result, int) else None
        else:
            async with db._db.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO routes (code, name, aliases, type, length_km, start_point, end_point, description, is_active)
                       VALUES ($1, $2, $3, 'federal', $4, $5, $6, $7, TRUE)
                       ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
                       RETURNING id""",
                    r["code"], r["name"], r.get("aliases", ""), r["length_km"],
                    r["start_point"], r["end_point"], r["description"],
                )
                route_id = row["id"] if row else None

        if route_id:
            total += 1
            logger.info(f"  {r['code']:6} {r['name']:30} ({r['length_km']} км) → id={route_id}")

    # Привязываем АЗС к трассам по bbox
    logger.info(f"\nПривязываю АЗС к трассам по bbox...")
    links_total = 0
    if db.USE_SQLITE:
        routes = await db._fetch("SELECT id, code, lat_min, lat_max, lon_min, lon_max FROM routes")
    else:
        async with db._db.acquire() as conn:
            routes = await conn.fetch(
                "SELECT id, code, lat_min, lat_max, lon_min, lon_max FROM routes"
            )

    for r in routes:
        # Нам нужны lat_min/lat_max/lon_min/lon_max в БД
        # Но в seed-скрипте они в Python. Добавим их в БД.
        pass

    # Вставляем bbox в routes
    for r in ROUTES:
        if db.USE_SQLITE:
            await db._execute(
                """UPDATE routes SET lat_min=?, lat_max=?, lon_min=?, lon_max=? WHERE code=?""",
                r["lat_min"], r["lat_max"], r["lon_min"], r["lon_max"], r["code"],
            )
        else:
            async with db._db.acquire() as conn:
                await conn.execute(
                    """UPDATE routes SET lat_min=$1, lat_max=$2, lon_min=$3, lon_max=$4 WHERE code=$5""",
                    r["lat_min"], r["lat_max"], r["lon_min"], r["lon_max"], r["code"],
                )

    # Теперь привязываем АЗС (bulk insert — намного быстрее)
    logger.info(f"\nПривязываю АЗС к трассам (bulk)...")

    # Получаем все станции одним запросом
    if db.USE_SQLITE:
        all_stations = await db._fetch(
            """SELECT s.id, s.lat, s.lon FROM stations s
               WHERE s.is_active = 1"""
        )
    else:
        async with db._db.acquire() as conn:
            all_stations = await conn.fetch(
                """SELECT s.id, s.lat, s.lon FROM stations s
                   WHERE s.is_active = TRUE"""
            )

    logger.info(f"  Загружено {len(all_stations)} станций")

    # Собираем все пары (station_id, route_id) по bbox
    all_links = []
    if db.USE_SQLITE:
        route_rows = await db._fetch("SELECT id, code, lat_min, lat_max, lon_min, lon_max FROM routes")
    else:
        async with db._db.acquire() as conn:
            route_rows = await conn.fetch("SELECT id, code, lat_min, lat_max, lon_min, lon_max FROM routes")

    # Build bbox lookup
    route_by_id = {r["id"]: r for r in route_rows}

    for s in all_stations:
        sid = s["id"] if isinstance(s, dict) else s[0]
        slat = s["lat"] if isinstance(s, dict) else s[1]
        slon = s["lon"] if isinstance(s, dict) else s[2]
        for route in route_rows:
            if route["lat_min"] <= slat <= route["lat_max"] and route["lon_min"] <= slon <= route["lon_max"]:
                all_links.append((sid, route["id"]))

    logger.info(f"  Подготовлено {len(all_links)} связей")

    # Bulk insert батчами по 5000
    BATCH_SIZE = 5000
    for i in range(0, len(all_links), BATCH_SIZE):
        batch = all_links[i:i+BATCH_SIZE]
        if db.USE_SQLITE:
            for sid, rid in batch:
                try:
                    await db._execute(
                        """INSERT OR IGNORE INTO station_routes (station_id, route_id, direction)
                           VALUES (?, ?, 'both')""",
                        sid, rid,
                    )
                    links_total += 1
                except Exception:
                    pass
        else:
            values = ",".join([f"({sid},{rid},'both')" for sid, rid in batch])
            async with db._db.acquire() as conn:
                try:
                    await conn.execute(f"""
                        INSERT INTO station_routes (station_id, route_id, direction)
                        VALUES {values}
                        ON CONFLICT (station_id, route_id) DO NOTHING
                    """)
                    links_total += len(batch)
                except Exception as e:
                    logger.warning(f"  batch {i}: {e}")
        logger.info(f"  Обработано {min(i+BATCH_SIZE, len(all_links))}/{len(all_links)} связей")

    logger.info(f"\n=== ИТОГО ===")
    logger.info(f"  Трасс: {total}")
    logger.info(f"  Связей АЗС-трасса: {links_total}")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
