#!/usr/bin/env python3
"""Backfill expires_at для старых отчётов от парсеров.

У парсеров старые отчёты (до добавления expires_at) не имели TTL.
Этот скрипт ставит expires_at = created_at + 2 hours для всех NULL.

Использование:
  python3 scripts/backfill_expires_at.py
"""
import asyncio
import asyncpg
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))


async def main():
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    if not DATABASE_URL:
        print("❌ DATABASE_URL не задан")
        return 1

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        # Парсеры с TTL 2 часа
        for source in ("gdebenz", "fuelprice_ru", "ishubenzin", "tg", "vk", "2gis",
                       "rss", "parser", "azslive", "benzinmap", "benzin_status_tech",
                       "yandex_fuel", "vk_groups", "networks"):
            r = await conn.execute(f"""
                UPDATE reports
                SET expires_at = created_at + INTERVAL '2 hours'
                WHERE source = '{source}' AND expires_at IS NULL
            """)
            count = r.split()[-1] if r else "0"
            if int(count) > 0:
                print(f"  {source}: {count} обновлено")

        # Пользовательские — 7 дней
        for source in ("user", "miniapp", "vk_user"):
            r = await conn.execute(f"""
                UPDATE reports
                SET expires_at = created_at + INTERVAL '7 days'
                WHERE source = '{source}' AND expires_at IS NULL
            """)
            count = r.split()[-1] if r else "0"
            if int(count) > 0:
                print(f"  {source}: {count} обновлено (7 дн)")

        # Проверяем
        r = await conn.fetchval("SELECT COUNT(*) FROM reports WHERE expires_at IS NULL")
        print(f"\nОсталось NULL expires_at: {r}")
        r = await conn.fetchval("SELECT COUNT(*) FROM reports WHERE expires_at > NOW()")
        print(f"Live отчётов после backfill: {r}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
