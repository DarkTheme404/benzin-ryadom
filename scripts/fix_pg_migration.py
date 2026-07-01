"""
Fix PG migration: proper type conversion from SQLite → PostgreSQL.
Runs via asyncio with asyncpg (works with pgbouncer).
"""
import asyncio
import json
import os
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "bot" / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = Path(__file__).parent.parent / "bot" / "benzin.db"

if not DATABASE_URL:
    print("DATABASE_URL не задан")
    sys.exit(1)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"

print(f"SQLite: {SQLITE_PATH} ({SQLITE_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
print(f"PG: ...{DATABASE_URL.split('@')[-1][:40]}", flush=True)


def _parse_ts(s):
    """Parse SQLite timestamp string → datetime (for asyncpg timestamptz)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return None


async def main():
    import asyncpg

    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60,
        ssl="require",
        statement_cache_size=0,
    )

    sq = sqlite3.connect(str(SQLITE_PATH))
    sq.row_factory = sqlite3.Row

    # Check current PG state
    async with pool.acquire() as conn:
        pg_stations = await conn.fetchval("SELECT COUNT(*) FROM stations")
        pg_reports = await conn.fetchval("SELECT COUNT(*) FROM reports")
    print(f"\nТекущее состояние PG: {pg_stations} станций, {pg_reports} отчётов")

    # Count SQLite
    sq_stations = sq.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    sq_reports = sq.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    print(f"SQLite: {sq_stations} станций, {sq_reports} отчётов")

    # === 1. Import stations with proper types ===
    if pg_stations < sq_stations:
        print(f"\n=== Импорт станций ({sq_stations}) ===")
        rows = sq.execute("SELECT * FROM stations").fetchall()
        batch = []
        for r in rows:
            ft = r["fuel_types"]
            if isinstance(ft, str):
                try:
                    ft = json.loads(ft)
                except:
                    ft = []
            if not isinstance(ft, list):
                ft = []
            batch.append((
                r["id"], r["osm_id"], r["name"], r["operator"], r["brand"], r["network"],
                r["country"], r["region"], r["city"], r["address"],
                r["lat"], r["lon"], ft,
                bool(r["has_24_7"]), r["phone"], r["website"],
                bool(r["is_verified"]), bool(r["is_active"]),
                _parse_ts(r["created_at"]),
                _parse_ts(r["updated_at"]),
            ))

            async with pool.acquire() as conn:
                await conn.executemany(
                    """INSERT INTO stations
                        (id, osm_id, name, operator, brand, network,
                         country, region, city, address,
                         lat, lon, fuel_types,
                         has_24_7, phone, website, is_verified, is_active,
                         created_at, updated_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                               $14,$15,$16,$17,$18,
                               $19,$20)
                       ON CONFLICT (id) DO UPDATE SET
                         name=EXCLUDED.name, operator=EXCLUDED.operator,
                         region=EXCLUDED.region, city=EXCLUDED.city,
                         address=EXCLUDED.address, fuel_types=EXCLUDED.fuel_types,
                         has_24_7=EXCLUDED.has_24_7, is_active=EXCLUDED.is_active,
                         updated_at=EXCLUDED.updated_at""",
                    batch,
                )
        pg_stations_new = await (await pool.acquire()).fetchval("SELECT COUNT(*) FROM stations") if False else 0
        async with pool.acquire() as conn:
            pg_stations_new = await conn.fetchval("SELECT COUNT(*) FROM stations")
        print(f"  → {pg_stations_new} станций в PG")
    else:
        print(f"\n=== Станции уже загружены ({pg_stations}) ===")

    # === 2. Import reports with proper types ===
    if pg_reports < sq_reports:
        print(f"\n=== Импорт отчётов ({sq_reports}) ===")
        rows = sq.execute("SELECT * FROM reports").fetchall()
        batch_size = 5000
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            data = []
            for r in batch:
                # available: 0 → False, 1 → True, 2 → None
                avail = r["available"]
                if avail == 1:
                    avail_bool = True
                elif avail == 0:
                    avail_bool = False
                else:
                    avail_bool = None  # "кончается"

                data.append((
                    r["id"], r["station_id"], r["user_id"], r["fuel_type"],
                    avail_bool,
                    r["price"], r["queue_size"],
                    bool(r["has_limit"]), r["limit_liters"],
                    r["comment"], r["confidence"], r["confirmations"], r["disputes"],
                    r["source"],
                    _parse_ts(r["expires_at"]),
                    _parse_ts(r["next_delivery_at"]) if "next_delivery_at" in r.keys() else None,
                    _parse_ts(r["created_at"]),
                ))

            async with pool.acquire() as conn:
                await conn.executemany(
                    """INSERT INTO reports
                        (id, station_id, user_id, fuel_type, available, price,
                         queue_size, has_limit, limit_liters, comment, confidence,
                         confirmations, disputes, source, expires_at,
                         next_delivery_at, created_at)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                               $15,$16,$17)
                       ON CONFLICT (id) DO NOTHING""",
                    data,
                )
            total += len(data)
            print(f"  ... {total}/{len(rows)}")

        async with pool.acquire() as conn:
            pg_reports_new = await conn.fetchval("SELECT COUNT(*) FROM reports")
        print(f"  → {pg_reports_new} отчётов в PG")
    else:
        print(f"\n=== Отчёты уже загружены ({pg_reports}) ===")

    # === 3. Reset sequences ===
    print("\n=== Reset sequences ===")
    async with pool.acquire() as conn:
        for t in ["stations", "users", "reports", "subscriptions", "owner_stations", "events", "user_badges", "premium_subscriptions"]:
            try:
                await conn.execute(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 1))"
                )
            except:
                pass
    print("  OK")

    # === 4. Final count ===
    async with pool.acquire() as conn:
        final_stations = await conn.fetchval("SELECT COUNT(*) FROM stations")
        final_reports = await conn.fetchval("SELECT COUNT(*) FROM reports")
        iv_stations = await conn.fetchval(
            "SELECT COUNT(*) FROM stations WHERE LOWER(city) LIKE '%иваново%'"
        )
        iv_reports = await conn.fetchval(
            """SELECT COUNT(*) FROM reports r
               JOIN stations s ON s.id = r.station_id
               WHERE LOWER(s.city) LIKE '%иваново%'"""
        )

    print(f"\n✅ Итого:")
    print(f"   Stations: {final_stations} (ожидалось {sq_stations})")
    print(f"   Reports:  {final_reports} (ожидалось {sq_reports})")
    print(f"   Ivanovo stations: {iv_stations}")
    print(f"   Ivanovo reports:  {iv_reports}")

    sq.close()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
