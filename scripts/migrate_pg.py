"""
Быстрая миграция SQLite → PostgreSQL.
Запуск: python scripts/migrate_pg.py
Требует: DATABASE_URL в переменных окружения (уже есть на Render).
"""
import os
import sys
import sqlite3
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("pip install psycopg2-binary")
    sys.exit(1)

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "bot", "benzin.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    print("DATABASE_URL не задан")
    sys.exit(1)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if "sslmode" not in DATABASE_URL:
    DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"

print(f"SQLite: {SQLITE_PATH}")
print(f"PG: {DATABASE_URL.split('@')[-1][:50]}")

if not os.path.exists(SQLITE_PATH):
    print(f"SQLite файл не найден: {SQLITE_PATH}")
    sys.exit(1)

sq = sqlite3.connect(SQLITE_PATH)
sq.row_factory = sqlite3.Row

pg = psycopg2.connect(DATABASE_URL)
pg.autocommit = False
cur = pg.cursor()

# === 1. Создаём таблицы (если нет) ===
print("\n1. Проверяем/создаём таблицы...")

cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
cur.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

cur.execute("""CREATE TABLE IF NOT EXISTS stations (
    id BIGSERIAL PRIMARY KEY,
    osm_id BIGINT UNIQUE,
    name TEXT NOT NULL,
    operator TEXT, brand TEXT, network TEXT,
    country TEXT DEFAULT 'RU', region TEXT, city TEXT, address TEXT,
    lat DOUBLE PRECISION NOT NULL DEFAULT 0,
    lon DOUBLE PRECISION NOT NULL DEFAULT 0,
    fuel_types TEXT[],
    has_24_7 BOOLEAN DEFAULT FALSE,
    phone TEXT, website TEXT,
    is_verified BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)""")

cur.execute("""CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username TEXT, first_name TEXT, last_name TEXT,
    language_code TEXT DEFAULT 'ru',
    reputation INTEGER DEFAULT 50,
    total_reports INTEGER DEFAULT 0,
    confirmed_reports INTEGER DEFAULT 0,
    badge TEXT, region TEXT, city TEXT,
    is_owner BOOLEAN DEFAULT FALSE,
    is_blocked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW()
)""")

cur.execute("""CREATE TABLE IF NOT EXISTS reports (
    id BIGSERIAL PRIMARY KEY,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    fuel_type TEXT NOT NULL,
    available BOOLEAN NOT NULL,
    price NUMERIC(6, 2),
    queue_size INTEGER,
    has_limit BOOLEAN DEFAULT FALSE,
    limit_liters INTEGER,
    comment TEXT,
    confidence REAL DEFAULT 0.5,
    confirmations INTEGER DEFAULT 0,
    disputes INTEGER DEFAULT 0,
    source TEXT DEFAULT 'user',
    expires_at TIMESTAMPTZ,
    next_delivery_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""")

cur.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id BIGINT REFERENCES stations(id) ON DELETE CASCADE,
    city TEXT, region TEXT, fuel_type TEXT,
    radius_km REAL DEFAULT 5,
    center_lat DOUBLE PRECISION,
    center_lon DOUBLE PRECISION,
    is_active BOOLEAN DEFAULT TRUE,
    last_notified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""")

cur.execute("""CREATE TABLE IF NOT EXISTS owner_stations (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    inn TEXT, role TEXT DEFAULT 'owner',
    is_verified BOOLEAN DEFAULT FALSE,
    moderator_id BIGINT REFERENCES users(id),
    rejection_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    verified_at TIMESTAMPTZ,
    is_promoted BOOLEAN DEFAULT FALSE,
    promoted_until TIMESTAMPTZ,
    UNIQUE(user_id, station_id)
)""")

cur.execute("""CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""")

cur.execute("""CREATE TABLE IF NOT EXISTS user_badges (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    badge_code TEXT NOT NULL,
    awarded_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, badge_code)
)""")

cur.execute("""CREATE TABLE IF NOT EXISTS premium_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    telegram_payment_charge_id TEXT,
    stars_amount INTEGER,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
)""")

pg.commit()
print("  Таблицы готовы")

# === 2. Очищаем PG (полная замена) ===
print("\n2. Очищаем PG...")
for t in ["premium_subscriptions", "user_badges", "events", "owner_stations", "subscriptions", "reports", "users", "stations"]:
    cur.execute(f"TRUNCATE {t} RESTART IDENTITY CASCADE")
pg.commit()
print("  Очищено")

# === 3. Импорт stations ===
print("\n3. Импорт stations...")
sq_st = sq.execute("SELECT * FROM stations").fetchall()
if sq_st:
    rows = []
    for r in sq_st:
        ft = r["fuel_types"]
        if isinstance(ft, str):
            try:
                ft = json.loads(ft)
            except:
                ft = []
        rows.append((
            r["id"], r["osm_id"], r["name"], r["operator"], r["brand"], r["network"],
            r["country"], r["region"], r["city"], r["address"],
            r["lat"], r["lon"], ft,
            r["has_24_7"], r["phone"], r["website"], r["is_verified"], r["is_active"],
            r["created_at"], r["updated_at"],
        ))
    execute_values(cur, """INSERT INTO stations
        (id, osm_id, name, operator, brand, network, country, region, city, address,
         lat, lon, fuel_types, has_24_7, phone, website, is_verified, is_active,
         created_at, updated_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  {len(rows)} станций")
pg.commit()

# === 4. Импорт users ===
print("\n4. Импорт users...")
sq_users = sq.execute("SELECT * FROM users").fetchall()
if sq_users:
    rows = []
    for r in sq_users:
        rows.append((
            r["id"], r["telegram_id"], r["username"], r["first_name"], r["last_name"],
            r["language_code"], r["reputation"], r["total_reports"], r["confirmed_reports"],
            r["badge"], r["region"], r["city"], r["is_owner"], r["is_blocked"],
            r["created_at"], r["last_active_at"],
        ))
    execute_values(cur, """INSERT INTO users
        (id, telegram_id, username, first_name, last_name, language_code, reputation,
         total_reports, confirmed_reports, badge, region, city, is_owner, is_blocked,
         created_at, last_active_at)
        VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
    print(f"  {len(rows)} пользователей")
pg.commit()

# === 5. Импорт reports (пачками по 5000) ===
print("\n5. Импорт reports...")
sq_rep = sq.execute("SELECT * FROM reports").fetchall()
if sq_rep:
    batch_size = 5000
    total = 0
    for i in range(0, len(sq_rep), batch_size):
        batch = sq_rep[i:i+batch_size]
        rows = []
        for r in batch:
            rows.append((
                r["id"], r["station_id"], r["user_id"], r["fuel_type"], r["available"],
                r["price"], r["queue_size"], r["has_limit"], r["limit_liters"],
                r["comment"], r["confidence"], r["confirmations"], r["disputes"],
                r["source"], r["expires_at"], r.get("next_delivery_at"), r["created_at"],
            ))
        execute_values(cur, """INSERT INTO reports
            (id, station_id, user_id, fuel_type, available, price, queue_size,
             has_limit, limit_liters, comment, confidence, confirmations, disputes,
             source, expires_at, next_delivery_at, created_at)
            VALUES %s ON CONFLICT (id) DO NOTHING""", rows)
        total += len(rows)
        pg.commit()
        print(f"  ... {total}/{len(sq_rep)}")
    print(f"  {total} отчётов")

# === 6. Импорт остальных таблиц ===
print("\n6. Остальные таблицы...")
for table_name, columns in [
    ("subscriptions", ("id","user_id","station_id","city","region","fuel_type","radius_km","center_lat","center_lon","is_active","last_notified_at","created_at")),
    ("owner_stations", ("id","user_id","station_id","inn","role","is_verified","moderator_id","rejection_reason","created_at","verified_at")),
    ("events", ("id","user_id","event_type","payload","created_at")),
    ("user_badges", ("id","user_id","badge_code","awarded_at")),
    ("premium_subscriptions", ("id","user_id","telegram_payment_charge_id","stars_amount","started_at","expires_at","is_active")),
]:
    sq_rows = sq.execute(f"SELECT * FROM {table_name}").fetchall()
    if sq_rows:
        rows = [tuple(r[c] for c in columns) for r in sq_rows]
        placeholders = ", ".join(["%s"] * len(columns))
        cols = ", ".join(columns)
        execute_values(cur, f"INSERT INTO {table_name} ({cols}) VALUES %s ON CONFLICT (id) DO NOTHING", rows)
        print(f"  {table_name}: {len(rows)}")
    else:
        print(f"  {table_name}: пусто")
pg.commit()

# === 7. Сброс sequences ===
print("\n7. Сброс sequences...")
for table in ["users", "stations", "reports", "subscriptions", "owner_stations", "events", "user_badges", "premium_subscriptions"]:
    cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 1))")
pg.commit()
print("  OK")

# === 8. Индексы ===
print("\n8. Индексы...")
for idx_sql in [
    "CREATE INDEX IF NOT EXISTS idx_stations_geo ON stations (lat, lon)",
    "CREATE INDEX IF NOT EXISTS idx_stations_operator ON stations (operator)",
    "CREATE INDEX IF NOT EXISTS idx_stations_region ON stations (region)",
    "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users (telegram_id)",
    "CREATE INDEX IF NOT EXISTS idx_users_reputation ON users (reputation DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reports_station ON reports (station_id, fuel_type)",
    "CREATE INDEX IF NOT EXISTS idx_reports_created ON reports (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reports_confidence ON reports (confidence DESC)",
    "CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type)",
]:
    cur.execute(idx_sql)
pg.commit()
print("  OK")

# === Итого ===
cur.execute("SELECT COUNT(*) FROM stations")
st_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM reports")
rep_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM users")
us_count = cur.fetchone()[0]

print(f"\n✅ Миграция завершена!")
print(f"   Stations: {st_count}")
print(f"   Reports:  {rep_count}")
print(f"   Users:    {us_count}")

sq.close()
pg.close()
