"""
Seed-данные: добавляет реалистичные отчёты по 6 АЗС в Иваново,
чтобы в боте сразу была видна информация (наличие + цены + адреса).

Идемпотентно: если у АЗС уже есть свежие отчёты (< 4 часов) — пропускает.

Запуск:
    cd bot && python3 -m scripts.seed_ivanovo
    # или
    python3 scripts/seed_ivanovo.py
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Пути
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))

from db import (  # noqa: E402
    add_report,
    init_db,
    close_db,
    get_station_current_status,
)


# === Реалистичные данные по 6 АЗС Иваново (июнь 2026) ===
# Основано на текущих рыночных ценах Ивановской области:
#   АИ-92: 58-62₽, АИ-95: 62-67₽, АИ-98: 75-80₽, Дизель: 70-78₽
# source = "seed" — чтобы можно было отличить от реальных отчётов
SEED_STATIONS = [
    {
        "id": 12236,  # Опти, пл. Генкиной, 2
        "name": "Опти",
        "address": "Иваново, площадь Генкиной, 2",
        "reports": [
            # (fuel_type, available, price, queue, source)
            ("92", True, 59.40, None, "seed"),
            ("95", True, 64.10, None, "seed"),
            ("98", True, 77.50, None, "seed"),
            ("diesel", True, 72.30, None, "seed"),
        ],
    },
    {
        "id": 18573,  # Ивойл, Поселковая, 8
        "name": "Ивойл",
        "address": "Иваново, Поселковая улица, 8",
        "reports": [
            ("92", True, 58.90, None, "seed"),
            ("95", True, 63.80, 3, "seed"),  # небольшая очередь
            ("diesel", True, 71.50, None, "seed"),
        ],
    },
    {
        "id": 20048,  # Газпромнефть, пр. Строителей, 23
        "name": "Газпромнефть",
        "address": "Иваново, проспект Строителей, 23",
        "reports": [
            ("92", True, 60.20, None, "seed"),
            ("95", True, 64.90, None, "seed"),
            ("98", True, 78.10, None, "seed"),
            ("diesel", True, 73.40, None, "seed"),
        ],
    },
    {
        "id": 20087,  # АГНКС Иваново-1, ул. Станкостроителей, 41
        "name": "АГНКС Иваново-1",
        "address": "Иваново, улица Станкостроителей, 41",
        "reports": [
            # Газ — этот тип пока не в списке, дадим дизель
            ("92", None, 59.10, None, "seed"),  # кончается
            ("diesel", True, 72.00, None, "seed"),
        ],
    },
    {
        "id": 22848,  # АЗС, ул. Домостроителей, 5
        "name": "АЗС",
        "address": "Иваново, улица Домостроителей, 5",
        "reports": [
            ("92", True, 58.70, None, "seed"),
            ("95", True, 63.50, None, "seed"),
            ("diesel", True, 71.20, None, "seed"),
        ],
    },
]


async def seed_one_station(station: dict, *, force: bool = False) -> list[int]:
    """Добавляет отчёты для одной АЗС. Возвращает список report_id.

    force=True — добавит даже если уже есть свежие отчёты.
    """
    station_id = station["id"]
    report_ids = []

    # Проверка: если уже есть свежие отчёты — пропускаем
    if not force:
        existing = await get_station_current_status(station_id)
        if existing:
            print(
                f"  ⏭  {station['name']} (id={station_id}) — "
                f"уже есть {len(existing)} свежих отчётов, пропускаю"
            )
            return report_ids

    for fuel, available, price, queue, source in station["reports"]:
        rid = await add_report(
            station_id=station_id,
            fuel_type=fuel,
            available=available,
            user_id=None,  # seed — без пользователя
            price=price,
            queue_size=queue,
            source=source,
        )
        report_ids.append(rid)

    print(
        f"  ✅ {station['name']} (id={station_id}, {station['address']}) — "
        f"добавлено {len(report_ids)} отчётов"
    )
    return report_ids


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="Добавить отчёты даже если уже есть свежие",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Seed: реалистичные отчёты по АЗС Иваново")
    print("=" * 60)

    await init_db()
    try:
        total = 0
        for s in SEED_STATIONS:
            ids = await seed_one_station(s, force=args.force)
            total += len(ids)
        print()
        print(f"Итого добавлено: {total} отчётов")
        if total == 0 and not args.force:
            print(
                "(используй --force чтобы добавить ещё, "
                "если у АЗС уже есть свежие отчёты)"
            )
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
