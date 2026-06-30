"""
Мониторинг прогресса обогащения адресов.

Показывает:
- Сколько АЗС с адресом/городом сейчас
- Сколько осталось
- Текущая скорость (по последнему checkpoint)
- ETA до завершения

Запуск:
  python scripts/enrich_status.py
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402


async def main():
    await db.init_db()

    # === Статистика БД ===
    cur = await db._fetch("SELECT COUNT(*) as c FROM stations", one=True)
    total = cur["c"] if cur else 0
    cur = await db._fetch("SELECT COUNT(*) as c FROM stations WHERE address IS NOT NULL AND address != ''", one=True)
    with_address = cur["c"] if cur else 0
    cur = await db._fetch("SELECT COUNT(*) as c FROM stations WHERE city IS NOT NULL AND city != ''", one=True)
    with_city = cur["c"] if cur else 0
    cur = await db._fetch("SELECT COUNT(*) as c FROM stations WHERE (address IS NULL OR address = '')", one=True)
    without_address = cur["c"] if cur else 0

    print("=" * 60)
    print("📊 Статус обогащения адресов")
    print("=" * 60)
    print(f"  Всего АЗС:              {total:>6}")
    print(f"  ✅ С адресом:            {with_address:>6}  ({with_address/total*100:.1f}%)")
    print(f"  ✅ С городом:            {with_city:>6}  ({with_city/total*100:.1f}%)")
    print(f"  ⏳ Без адреса:           {without_address:>6}  ({without_address/total*100:.1f}%)")

    # === Checkpoint ===
    cp_path = Path(__file__).parent / ".enrich_checkpoint"
    if cp_path.exists():
        cp = cp_path.read_text().strip()
        try:
            cp_id = int(cp)
            # Сколько АЗС с id <= cp_id имеют address
            still_missing_row = await db._fetch(
                "SELECT COUNT(*) as c FROM stations WHERE id <= ? AND (address IS NULL OR address = '')",
                cp_id, one=True,
            )
            still_missing = still_missing_row["c"] if still_missing_row else 0
            processed_row = await db._fetch(
                "SELECT COUNT(*) as c FROM stations WHERE id <= ?", cp_id, one=True,
            )
            processed = processed_row["c"] if processed_row else 0
            print()
            print(f"  📌 Checkpoint: id={cp_id}  обработано={processed}, осталось в этом диапазоне={still_missing}")
        except (ValueError, OSError):
            pass
    else:
        print()
        print("  📌 Checkpoint: не установлен")

    # === Скорость (по timestamp файла лога) ===
    log_path = Path(__file__).parent.parent / "enrich_nominatim5.log"
    if not log_path.exists():
        # Ищем любой enrich*.log
        for f in Path(__file__).parent.parent.glob("enrich*.log"):
            log_path = f
            break

    if log_path.exists():
        mtime = log_path.stat().st_mtime
        age_sec = time.time() - mtime
        print()
        print(f"  📄 Лог: {log_path.name}  (модифицирован {age_sec/60:.1f} мин назад)")
        if age_sec > 300:
            print(f"     ⚠️ Простаивает >5 мин — возможно процесс упал")

    # === Рекомендация ===
    print()
    print("=" * 60)
    if without_address == 0:
        print("🎉 Все АЗС обогащены!")
    else:
        hours = without_address / 0.7 / 3600
        print(f"📋 Осталось обогатить: {without_address} АЗС")
        print(f"   При 0.7 req/s (Nominatim): ~{hours:.1f} ч")
        print(f"   При 1.5 req/s (Yandex):    ~{without_address/1.5/3600:.1f} ч")
        print(f"   При 2.0 req/s (Yandex ×2): ~{without_address/2.0/3600:.1f} ч")

    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
