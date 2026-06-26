"""
Оркестратор всех парсеров цен.

Запускает все доступные парсеры, объединяет результаты, обновляет БД.

Источники (в порядке приоритета):
  1. User Reports     — бот (1.0)
  2. TG-каналы        — Telethon (0.85) — нужен auth
  3. fuelprice.ru     — крупный агрегатор (0.75) ✅
  4. Сети АЗС         — официальные сайты (0.75)
  5. benzin-price.ru  — JS-challenge (нужен headless)
  6. 2ГИС paid        — полные цены (0.80)
  7. 2ГИС demo        — координаты (0.40)
  8. OSM              — fallback (0.30)

Запуск:
  python scripts/orchestrator.py --once
  python scripts/orchestrator.py --schedule    # каждые 6 часов

Расписание (по умолчанию):
  - fuelprice.ru: раз в сутки
  - сети: каждые 6 часов
  - TG: каждые 2 часа
  - benzin-price.ru: раз в сутки (когда будет headless)
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

# Импортируем парсеры
from parse_fuelprice import main as parse_fuelprice


# Топ-12 городов России по населению
TOP_CITIES = [
    "moskva",
    "sankt-peterburg",
    "novosibirsk",
    "ekaterinburg",
    "kazan",
    "krasnodar",
    "chelyabinsk",
    "nizhniy-novgorod",
    "samara",
    "rostov-na-donu",
    "ufa",
    "krasnoyarsk",
]


async def parse_fuelprice_all_cities():
    """Запускает fuelprice.ru по всем крупным городам."""
    print(f"\n[fuelprice.ru] {len(TOP_CITIES)} городов")
    total = {"matched": 0, "created": 0, "saved": 0}
    for city in TOP_CITIES:
        try:
            # Прямой вызов с аргументами
            sys.argv = ["parse_fuelprice.py", "--city", city, "--create-new"]
            from parse_fuelprice import main as fp_main
            await fp_main()
        except SystemExit:
            pass
        except Exception as e:
            print(f"  ⚠ {city}: {e}")
        await asyncio.sleep(2)
    return total


SOURCES = {
    "fuelprice": {
        "name": "fuelprice.ru (60+ городов, координаты + цены)",
        "function": parse_fuelprice_all_cities,
        "interval_hours": 24,
        "enabled": True,
    },
}


async def run_source(name: str, source: dict) -> bool:
    """Запускает один источник."""
    print(f"\n>>> {source['name']}")
    print(f"    Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        result = await source["function"]()
        print(f"    ✓ Завершено")
        return True
    except Exception as e:
        print(f"    ❌ Ошибка: {e}")
        return False


async def run_once():
    """Запускает все источники один раз."""
    print("=" * 60)
    print(f"ОРКЕСТРАТОР ПАРСЕРОВ — однократный запуск")
    print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    await db.init_db()

    results = {}
    for name, source in SOURCES.items():
        if not source["enabled"]:
            continue
        results[name] = await run_source(name, source)

    print()
    print("=" * 60)
    print("ИТОГО")
    print("=" * 60)
    for name, ok in results.items():
        status = "✓" if ok else "❌"
        print(f"  {status} {SOURCES[name]['name']}")

    await db.close_db()


async def run_schedule():
    """Запускает парсеры по расписанию."""
    print("=" * 60)
    print("ОРКЕСТРАТОР ПАРСЕРОВ — режим расписания")
    print(f"Старт: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    await db.init_db()

    last_run = {name: None for name in SOURCES}

    while True:
        now = datetime.now()
        for name, source in SOURCES.items():
            if not source["enabled"]:
                continue
            interval = timedelta(hours=source["interval_hours"])
            last = last_run[name]
            if last is None or (now - last) >= interval:
                await run_source(name, source)
                last_run[name] = now

        # Спим 10 минут между проверками
        await asyncio.sleep(600)

    await db.close_db()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Однократный запуск")
    parser.add_argument("--schedule", action="store_true", help="Запуск по расписанию")
    args = parser.parse_args()

    if args.schedule:
        asyncio.run(run_schedule())
    else:
        # По умолчанию — однократный запуск
        asyncio.run(run_once())


if __name__ == "__main__":
    main()
