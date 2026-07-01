"""Import only MISSING stations (553) with proper types. ON CONFLICT DO NOTHING."""
import asyncio, json, os, sys, sqlite3
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

    async with pool.acquire() as conn:
        pg_ids = set(r[0] for r in await conn.fetch("SELECT id FROM stations"))
    sq_rows = sq.execute("SELECT * FROM stations").fetchall()
    missing = [r for r in sq_rows if r["id"] not in pg_ids]
    print(f"PG has {len(pg_ids)}, SQLite has {len(sq_rows)}, missing: {len(missing)}", flush=True)

    if not missing:
        print("All stations imported!", flush=True)
        await pool.close()
        sq.close()
        return

    data = []
    for r in missing:
        ft = r["fuel_types"]
        if isinstance(ft, str):
            try: ft = json.loads(ft)
            except: ft = []
        if not isinstance(ft, list): ft = []
        data.append((
            r["id"], r["osm_id"], r["name"], r["operator"], r["brand"], r["network"],
            r["country"], r["region"], r["city"], r["address"],
            r["lat"], r["lon"], ft,
            bool(r["has_24_7"]), r["phone"], r["website"],
            bool(r["is_verified"]), bool(r["is_active"]),
            _parse_ts(r["created_at"]), _parse_ts(r["updated_at"]),
        ))

    async with pool.acquire() as conn:
        await conn.executemany(
            """INSERT INTO stations
                (id,osm_id,name,operator,brand,network,country,region,city,address,
                 lat,lon,fuel_types,has_24_7,phone,website,is_verified,is_active,
                 created_at,updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                       $14,$15,$16,$17,$18,$19,$20)
               ON CONFLICT (id) DO NOTHING""",
            data,
        )
    final = await (await pool.acquire()).fetchval("SELECT COUNT(*) FROM stations")
    print(f"Done: {final} stations (imported {len(missing)} missing)", flush=True)

    # Also update existing stations with correct types (has_24_7, fuel_types)
    print("\nUpdating existing stations with correct types...", flush=True)
    updates = []
    for r in sq_rows:
        if r["id"] in pg_ids:
            ft = r["fuel_types"]
            if isinstance(ft, str):
                try: ft = json.loads(ft)
                except: ft = []
            if not isinstance(ft, list): ft = []
            updates.append((
                ft, bool(r["has_24_7"]), bool(r["is_verified"]), bool(r["is_active"]),
                _parse_ts(r["updated_at"]), r["id"],
            ))
    
    batch = 5000
    for i in range(0, len(updates), batch):
        async with pool.acquire() as conn:
            await conn.executemany(
                """UPDATE stations SET fuel_types=$1, has_24_7=$2, is_verified=$3, is_active=$4, updated_at=$5
                   WHERE id=$6""",
                updates[i:i+batch],
            )
        print(f"  Updated {min(i+batch, len(updates))}/{len(updates)}", flush=True)

    await pool.close()
    sq.close()

asyncio.run(main())
