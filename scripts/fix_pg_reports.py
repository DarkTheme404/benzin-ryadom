"""Step 2: Import reports with proper types."""
import asyncio, os, sys, sqlite3
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / "bot" / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = Path(__file__).parent.parent / "bot" / "benzin.db"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"

def _parse_ts(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s))
    except: return None

async def main():
    import asyncpg
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5, command_timeout=120, ssl="require", statement_cache_size=0)
    sq = sqlite3.connect(str(SQLITE_PATH))
    sq.row_factory = sqlite3.Row

    pg_reports = await (await pool.acquire()).fetchval("SELECT COUNT(*) FROM reports")
    sq_reports = sq.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    print(f"PG: {pg_reports}, SQLite: {sq_reports}", flush=True)

    if pg_reports >= sq_reports:
        print("Reports OK, skipping", flush=True)
        await pool.close()
        sq.close()
        return

    rows = sq.execute("SELECT * FROM reports").fetchall()
    print(f"Loading {len(rows)} reports...", flush=True)
    batch_size = 3000
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        data = []
        for r in batch:
            avail = r["available"]
            if avail == 1: avail_bool = True
            elif avail == 0: avail_bool = False
            else: avail_bool = None

            keys = r.keys()
            data.append((
                r["id"], r["station_id"], r["user_id"], r["fuel_type"],
                avail_bool, r["price"], r["queue_size"],
                bool(r["has_limit"]), r["limit_liters"],
                r["comment"], r["confidence"], r["confirmations"], r["disputes"],
                r["source"],
                _parse_ts(r["expires_at"]),
                _parse_ts(r["next_delivery_at"]) if "next_delivery_at" in keys and r["next_delivery_at"] else None,
                _parse_ts(r["created_at"]),
            ))
        async with pool.acquire() as conn:
            await conn.executemany(
                """INSERT INTO reports
                    (id,station_id,user_id,fuel_type,available,price,queue_size,
                     has_limit,limit_liters,comment,confidence,confirmations,disputes,
                     source,expires_at,next_delivery_at,created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                   ON CONFLICT (id) DO NOTHING""",
                data,
            )
        total += len(batch)
        print(f"  {total}/{len(rows)}", flush=True)

    final = await (await pool.acquire()).fetchval("SELECT COUNT(*) FROM reports")
    print(f"Done: {final} reports", flush=True)
    await pool.close()
    sq.close()

asyncio.run(main())
