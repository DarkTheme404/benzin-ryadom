"""
Пул соединений с БД + хелперы.
Поддержка SQLite (локальная разработка) и PostgreSQL (production).
"""
import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import asyncpg
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)

# Переключатель: SQLite или PostgreSQL
USE_SQLITE = os.getenv("USE_SQLITE", "true").lower() == "true"
DB_PATH = Path(__file__).parent / "benzin.db"
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Флаг: API процесс управляет пулом. Парсеры НЕ должны вызывать close_db().
# Заменяет костыль с os.environ["_API_MODE"].
API_MODE = False

_db: Any = None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в км (формула Гаверсинуса)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# === Инициализация ===
async def init_db():
    """Инициализирует БД. Идемпотентна — если уже инициализирована, ничего не делает."""
    global _db
    if _db is not None:
        # Уже инициализирована (например, API держит пул)
        return
    if USE_SQLITE:
        _db = await aiosqlite.connect(str(DB_PATH))
        _db.row_factory = aiosqlite.Row
        # PRAGMA оптимизации для скорости
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.execute("PRAGMA busy_timeout=5000")  # 5 сек max wait на блокировку
        await _db.execute("PRAGMA cache_size=-20000")  # 20MB кеш
        await _db.execute("PRAGMA temp_store=MEMORY")  # temp таблицы в RAM
        await _db.execute("PRAGMA synchronous=NORMAL")  # чуть быстрее WAL
        # Регистрируем Python-функцию lower() — корректно работает с кириллицей
        # (встроенный SQLite LOWER() её не понимает).
        await _db.create_function("py_lower", 1, _ru_lower)
        await _create_schema_sqlite(_db)
        await _db.commit()
    else:
        _db = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=4,         # больше соединений
            max_size=20,
            command_timeout=30, # быстрее fail при проблемах
            ssl="require",
            # Supabase free tier использует pgbouncer в Transaction mode
            # который не поддерживает named prepared statements.
            # statement_cache_size=0 отключает кэш → безопасно для pgbouncer.
            statement_cache_size=0,
        )
        await _create_schema_pg(_db)


async def close_db():
    """Закрывает БД. Не закрывает, если API_MODE=True (вызвано из API сервера)."""
    global _db
    if API_MODE:
        # API держит пул, не закрываем
        return
    if _db:
        await _db.close()
        _db = None


# === Создание схемы ===
async def _create_schema_sqlite(db):
    """Создаёт схему в SQLite (CREATE IF NOT EXISTS) + миграции."""
    schema_path = Path(__file__).parent.parent / "db" / "schema_sqlite.sql"
    if not schema_path.exists():
        return

    # Сначала добавляем недостающие колонки в существующие таблицы
    await _migrate_sqlite(db)

    # Потом выполняем schema (CREATE IF NOT EXISTS пропустит существующие)
    sql = schema_path.read_text(encoding="utf-8")
    await db.executescript(sql)

    # Создаём индексы, которые зависят от миграций
    await _create_indexes_sqlite(db)
    await db.commit()


async def _migrate_sqlite(db):
    """Добавляет недостающие колонки в существующие таблицы (для уже созданных БД)."""
    async with db.execute("PRAGMA table_info(subscriptions)") as cur:
        cols = {row[1] for row in await cur.fetchall()}

    if "center_lat" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN center_lat REAL")
    if "center_lon" not in cols:
        await db.execute("ALTER TABLE subscriptions ADD COLUMN center_lon REAL")

    # Миграция: reports.next_delivery_at
    async with db.execute("PRAGMA table_info(reports)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "next_delivery_at" not in cols:
        await db.execute("ALTER TABLE reports ADD COLUMN next_delivery_at TEXT")

    # Миграция: расширенные поля reports
    new_report_cols = [
        ("octane_rating", "REAL"),
        ("cetane_number", "REAL"),
        ("additives", "TEXT"),
        ("quality_score", "REAL"),
        ("fuel_standard", "TEXT"),
        ("certification", "TEXT"),
        ("queue_wait_minutes", "INTEGER"),
        ("queue_trend", "TEXT"),
        ("limit_per_visit", "INTEGER"),
        ("limit_daily", "INTEGER"),
        ("limit_weekly", "INTEGER"),
        ("canister_ban", "INTEGER"),
        ("review_text", "TEXT"),
        ("rating", "REAL"),
        ("photos_count", "INTEGER"),
        ("has_car_wash", "INTEGER"),
        ("has_shop", "INTEGER"),
        ("has_restaurant", "INTEGER"),
        ("has_atm", "INTEGER"),
        ("has_parking", "INTEGER"),
        ("has_ev_charging", "INTEGER"),
        ("accessibility", "TEXT"),
        ("opening_hours", "TEXT"),
        ("phone", "TEXT"),
        ("website", "TEXT"),
    ]
    for col_name, col_type in new_report_cols:
        if col_name not in cols:
            await db.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_type}")

    # Миграция: owner_stations — платное размещение
    async with db.execute("PRAGMA table_info(owner_stations)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "is_promoted" not in cols:
        await db.execute("ALTER TABLE owner_stations ADD COLUMN is_promoted INTEGER DEFAULT 0")
    if "promoted_until" not in cols:
        await db.execute("ALTER TABLE owner_stations ADD COLUMN promoted_until TEXT")

    # Миграция: users — привязка аккаунтов TG ↔ VK ↔ MiniApp
    async with db.execute("PRAGMA table_info(users)") as cur:
        user_cols = {row[1] for row in await cur.fetchall()}
    if "linked_telegram_id" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN linked_telegram_id INTEGER")
    if "vk_id" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN vk_id INTEGER")
    if "link_code" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN link_code TEXT")
    if "link_code_expires_at" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN link_code_expires_at TEXT")
    if "linked_user_id" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN linked_user_id INTEGER")
    if "screen_name" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN screen_name TEXT")
    if "vk_profile_link" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN vk_profile_link TEXT")
    if "tg_profile_link" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN tg_profile_link TEXT")
    if "password_hash" not in user_cols:
        await db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    # Миграция: убираем UNIQUE constraint с telegram_id (для VK юзеров с telegram_id=0)
    # Создаём partial unique index — telegram_id уникален только когда > 0
    try:
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id_positive
            ON users (telegram_id) WHERE telegram_id > 0
        """)
    except Exception:
        pass
    # Миграция: vk_id должен быть UNIQUE (для поиска VK юзеров)
    try:
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_vk_id_unique
            ON users (vk_id) WHERE vk_id IS NOT NULL
        """)
    except Exception:
        pass

    # SQLite: пересоздаём таблицу без UNIQUE constraint на telegram_id
    # SQLite не поддерживает DROP CONSTRAINT, нужно пересоздать таблицу
    # Но проще: создать уникальный partial index (он заменяет UNIQUE constraint)
    try:
        # Проверяем, есть ли уже UNIQUE constraint на telegram_id
        cursor = await db.execute("PRAGMA index_list(users)")
        rows = await cursor.fetchall()
        # Если есть autoindex на telegram_id — удаляем его
        for row in rows:
            idx_name = row[1] if isinstance(row, tuple) else row["name"]
            if idx_name and "telegram_id" in idx_name.lower() and "autoindex" in idx_name.lower():
                # Это auto UNIQUE index — нужно пересоздать таблицу
                # Для SQLite просто создаём partial unique index который заменит поведение
                await db.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id_positive
                    ON users (telegram_id) WHERE telegram_id > 0
                """)
                break
    except Exception:
        pass

    # Создаём owner_stations если её нет
    await db.execute(
        """CREATE TABLE IF NOT EXISTS owner_stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
            inn TEXT,
            role TEXT DEFAULT 'owner',
            is_verified INTEGER DEFAULT 0,
            moderator_id INTEGER REFERENCES users(id),
            rejection_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            verified_at TEXT,
            UNIQUE(user_id, station_id)
        )"""
    )

    # Добавляем UNIQUE на subscriptions (если ещё нет) — защита от дублей
    try:
        # Сначала удаляем дубли (если есть)
        await db.execute(
            """DELETE FROM subscriptions
               WHERE id NOT IN (
                   SELECT MIN(id) FROM subscriptions
                   WHERE user_id IS NOT NULL AND station_id IS NOT NULL
                   GROUP BY user_id, station_id
               )
               AND station_id IS NOT NULL"""
        )
        # Создаём UNIQUE index (в SQLite это и есть constraint)
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_unique "
            "ON subscriptions (user_id, station_id) WHERE station_id IS NOT NULL"
        )
    except Exception as e:
        logger.warning(f"Could not add UNIQUE to subscriptions: {e}")

    # Premium tables (13.07.2026)
    await db.execute(
        """CREATE TABLE IF NOT EXISTS premium_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tier TEXT NOT NULL,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            payment_id TEXT,
            payment_amount INTEGER,
            payment_method TEXT,
            is_active INTEGER DEFAULT 1,
            cancelled_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_premium_users_user_id ON premium_users (user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_premium_users_active ON premium_users (user_id, is_active, expires_at)")

    # Миграция: убираем CHECK constraint на tier в premium_users (нужен для 'founder' tier)
    # SQLite не поддерживает DROP CONSTRAINT, пересоздаём таблицу
    try:
        cursor = await db.execute("PRAGMA table_info(premium_users)")
        pragma_cols = await cursor.fetchall()
        has_check = any(
            (row[1] if isinstance(row, tuple) else row.get("name")) == "tier"
            for row in pragma_cols
        )
        if has_check:
            # Проверяем, есть ли CHECK constraint через sql
            cursor = await db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='premium_users'")
            create_sql = await cursor.fetchone()
            create_text = create_sql[0] if isinstance(create_sql, tuple) else (create_sql or {}).get("sql", "")
            if "CHECK" in create_text.upper():
                await db.execute("BEGIN TRANSACTION")
                await db.execute("ALTER TABLE premium_users RENAME TO premium_users_old")
                await db.execute("""
                    CREATE TABLE premium_users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        tier TEXT NOT NULL,
                        started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        expires_at TEXT NOT NULL,
                        payment_id TEXT,
                        payment_amount INTEGER,
                        payment_method TEXT,
                        is_active INTEGER DEFAULT 1,
                        cancelled_at TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await db.execute("""
                    INSERT INTO premium_users
                    SELECT id, user_id, tier, started_at, expires_at, payment_id,
                           payment_amount, payment_method, is_active, cancelled_at,
                           created_at, updated_at
                    FROM premium_users_old
                """)
                await db.execute("DROP TABLE premium_users_old")
                await db.execute("COMMIT")
    except Exception as e:
        logger.warning(f"Migration premium_users CHECK constraint: {e}")
        try:
            await db.execute("ROLLBACK")
        except Exception:
            pass

    # premium_trials — трекинг trial активаций (1 раз на юзера)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS premium_trials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tier TEXT NOT NULL,
            days INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_premium_trials_user_id ON premium_trials (user_id)")

    await db.execute(
        """CREATE TABLE IF NOT EXISTS premium_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tier TEXT NOT NULL,
            amount INTEGER NOT NULL,
            currency TEXT DEFAULT 'RUB',
            status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'failed', 'refunded')),
            payment_method TEXT,
            external_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT,
            metadata TEXT
        )"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_premium_payments_user_id ON premium_payments (user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_premium_payments_external_id ON premium_payments (external_id)")

    # === fuel_alarms — подписки на появление топлива (Premium) ===
    await db.execute(
        """CREATE TABLE IF NOT EXISTS fuel_alarms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
            fuel_type TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            triggered_at TEXT,
            last_notified_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, station_id, fuel_type)
        )"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_fuel_alarms_active ON fuel_alarms (is_active, station_id, fuel_type)")

    # === referrals — Реферальная программа ===
    await db.execute(
        """CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            referred_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            referral_code TEXT NOT NULL UNIQUE,
            referred_telegram_id INTEGER,
            status TEXT DEFAULT 'pending',
            premium_granted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        )"""
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals (referral_code)")

    # === referral_discounts — 50% скидка за реферала (вместо бесплатного месяца) ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS referral_discounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            discount_percent INTEGER NOT NULL DEFAULT 50,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_referral_discounts_user ON referral_discounts (user_id, expires_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals (referrer_user_id)")

    # === referral_relationships — permanent referral tracking ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS referral_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            referred_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(referred_user_id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ref_rel_referrer ON referral_relationships (referrer_user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ref_rel_referred ON referral_relationships (referred_user_id)")

    # === referral_balances — баланс реферера ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS referral_balances (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            total_earned INTEGER DEFAULT 0,
            total_withdrawn INTEGER DEFAULT 0,
            balance INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # === referral_earnings — история начислений ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS referral_earnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            referred_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            payment_id INTEGER,
            payment_amount INTEGER NOT NULL,
            commission_percent INTEGER NOT NULL DEFAULT 50,
            commission_amount INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ref_earn_referrer ON referral_earnings (referrer_user_id)")

    # === referral_withdrawals — заявки на вывод ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS referral_withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount INTEGER NOT NULL,
            method TEXT NOT NULL DEFAULT 'card',
            details TEXT,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'paid')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            processed_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_ref_wd_user ON referral_withdrawals (user_id)")

    # === Миграция: rate limit привязки аккаунтов ===
    async with db.execute("PRAGMA table_info(users)") as cur:
        link_cols = {row[1] for row in await cur.fetchall()}
    if "link_ops_count" not in link_cols:
        await db.execute("ALTER TABLE users ADD COLUMN link_ops_count INTEGER DEFAULT 0")
    if "last_link_change_at" not in link_cols:
        await db.execute("ALTER TABLE users ADD COLUMN last_link_change_at TEXT")

    # === pending_link_confirmations — запросы на привязку, ожидающие подтверждения ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS pending_link_confirmations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_tg_id INTEGER NOT NULL,
            to_vk_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_pending_link_tg ON pending_link_confirmations (to_tg_id, status)")

    # === link_groups — единая связка аккаунтов (1 VK + 1 TG) ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS link_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    async with db.execute("PRAGMA table_info(users)") as cur:
        all_cols = {row[1] for row in await cur.fetchall()}
    if "link_group_id" not in all_cols:
        await db.execute("ALTER TABLE users ADD COLUMN link_group_id INTEGER REFERENCES link_groups(id)")

    # === founder_purchases — Founder Pack покупки ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS founder_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount INTEGER NOT NULL DEFAULT 1990,
            payment_token TEXT,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_founder_purchases_user ON founder_purchases (user_id)")


async def _create_indexes_sqlite(db):
    """Создаёт индексы (можно безопасно вызывать повторно)."""
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_geo "
        "ON subscriptions (center_lat, center_lon)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_owner_stations_user "
        "ON owner_stations (user_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_owner_stations_station "
        "ON owner_stations (station_id)"
    )
    # Составной индекс для get_station_current_status (фильтр по station + время)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_station_created "
        "ON reports (station_id, created_at DESC)"
    )
    # Индекс для get_recent_fuel_reports (по времени)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_reports_created "
        "ON reports (created_at DESC)"
    )
    # Бейджи пользователей
    await db.execute(
        "CREATE TABLE IF NOT EXISTS user_badges ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "badge_code TEXT NOT NULL, "
        "awarded_at TEXT DEFAULT (datetime('now')), "
        "UNIQUE(user_id, badge_code))"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_badges_user "
        "ON user_badges (user_id)"
    )
    # Premium-подписки (Telegram Stars)
    await db.execute(
        """CREATE TABLE IF NOT EXISTS premium_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_payment_charge_id TEXT,
            stars_amount INTEGER,
            started_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )"""
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_premium_user "
        "ON premium_subscriptions (user_id, is_active)"
    )


async def _create_schema_pg(pool):
    """Создаёт все таблицы в PostgreSQL (CREATE IF NOT EXISTS).

    Выполняет schema.sql + недостающие миграции.
    Безопасно вызывать повторно — IF NOT EXISTS пропустит существующие.
    """
    async with pool.acquire() as conn:
        # 1. Полная схема из schema.sql
        schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
        if schema_path.exists():
            sql = schema_path.read_text(encoding="utf-8")
            # Разбиваем на statements, но не ломаем $$..$$ блоки (PL/pgSQL)
            import re as _re
            protected = []
            def _protect(m):
                protected.append(m.group(0))
                return f"__PROTECTED_{len(protected)-1}__"
            sql_safe = _re.sub(r"\$\$.*?\$\$", _protect, sql, flags=_re.DOTALL)
            for stmt in sql_safe.split(";"):
                stmt = stmt.strip()
                if not stmt or stmt.startswith("--"):
                    continue
                # Восстанавливаем $$ блоки
                for i, p in enumerate(protected):
                    stmt = stmt.replace(f"__PROTECTED_{i}__", p)
                # Пропускаем VIEW, COMMENT и индексы — не критичны для бота, могут зависать через пуллер
                upper = stmt.upper().strip()
                if upper.startswith("CREATE OR REPLACE VIEW") or upper.startswith("COMMENT ON"):
                    continue
                if "CREATE INDEX" in upper:
                    continue
                try:
                    await asyncio.wait_for(conn.execute(stmt), timeout=30)
                except asyncio.TimeoutError:
                    logger.warning(f"PG schema stmt timed out (30s): {stmt[:80]}...")
                except Exception as e:
                    logger.warning(f"PG schema stmt: {e} | {stmt[:80]}...")
            logger.info("PG schema.sql applied")

        # 2. owner_stations: платное размещение (если таблица уже есть без этих колонок)
        try:
            await conn.execute("ALTER TABLE owner_stations ADD COLUMN IF NOT EXISTS is_promoted BOOLEAN DEFAULT FALSE")
            await conn.execute("ALTER TABLE owner_stations ADD COLUMN IF NOT EXISTS promoted_until TIMESTAMPTZ")
        except Exception as e:
            logger.warning(f"PG migration owner_stations promoted: {e}")

        # 3. users: привязка аккаунтов TG ↔ VK ↔ MiniApp
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_telegram_id BIGINT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS vk_id BIGINT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS link_code TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS link_code_expires_at TIMESTAMPTZ")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS linked_user_id BIGINT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS screen_name TEXT")
        except Exception as e:
            logger.warning(f"PG migration users link fields: {e}")

        # 3b. Partial unique indexes (для VK юзеров с telegram_id=0)
        try:
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id_positive
                ON users (telegram_id) WHERE telegram_id > 0
            """)
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_vk_id_unique
                ON users (vk_id) WHERE vk_id IS NOT NULL
            """)
        except Exception as e:
            logger.warning(f"PG migration partial indexes: {e}")

        # 3c. Drop old UNIQUE constraint on telegram_id (он блокирует VK юзеров с telegram_id=0)
        try:
            await conn.execute("""
                ALTER TABLE users DROP CONSTRAINT IF EXISTS users_telegram_id_key
            """)
        except Exception as e:
            logger.warning(f"PG drop telegram_id constraint: {e}")

        # 3d. Rate limit для привязки аккаунтов
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS link_ops_count INTEGER DEFAULT 0")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_link_change_at TIMESTAMPTZ")
        except Exception as e:
            logger.warning(f"PG migration link rate limit: {e}")

        # 3f. password_hash, vk_profile_link, tg_profile_link
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS vk_profile_link TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS tg_profile_link TEXT")
        except Exception as e:
            logger.warning(f"PG migration password_hash/profile_links: {e}")

        # 3e. pending_link_confirmations
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_link_confirmations (
                    id SERIAL PRIMARY KEY,
                    from_user_id INTEGER NOT NULL,
                    to_tg_id INTEGER NOT NULL,
                    to_vk_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pending_link_tg
                ON pending_link_confirmations (to_tg_id, status)
            """)
        except Exception as e:
            logger.warning(f"PG migration pending_link_confirmations: {e}")

        # 3g. link_groups — единая связка аккаунтов
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS link_groups (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS link_group_id INTEGER REFERENCES link_groups(id)")
        except Exception as e:
            logger.warning(f"PG migration link_groups: {e}")

        # 1f. Founder Pack purchases
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS founder_purchases (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    amount INTEGER NOT NULL DEFAULT 1990,
                    payment_token TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    paid_at TIMESTAMPTZ
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_founder_purchases_user
                ON founder_purchases (user_id)
            """)
        except Exception as e:
            logger.warning(f"PG migration founder_purchases: {e}")

        # 2b. Reports: новые колонки для качеств/очередей/лимитов
        new_report_cols = [
            ("octane_rating", "REAL"),
            ("cetane_number", "REAL"),
            ("additives", "TEXT"),
            ("quality_score", "REAL"),
            ("fuel_standard", "TEXT"),
            ("certification", "TEXT"),
            ("queue_wait_minutes", "INTEGER"),
            ("queue_trend", "TEXT"),
            ("limit_per_visit", "INTEGER"),
            ("limit_daily", "INTEGER"),
            ("limit_weekly", "INTEGER"),
            ("canister_ban", "BOOLEAN DEFAULT FALSE"),
            ("review_text", "TEXT"),
            ("rating", "REAL"),
            ("photos_count", "INTEGER DEFAULT 0"),
            ("has_car_wash", "BOOLEAN DEFAULT FALSE"),
            ("has_shop", "BOOLEAN DEFAULT FALSE"),
            ("has_restaurant", "BOOLEAN DEFAULT FALSE"),
            ("has_atm", "BOOLEAN DEFAULT FALSE"),
            ("has_parking", "BOOLEAN DEFAULT FALSE"),
            ("has_ev_charging", "BOOLEAN DEFAULT FALSE"),
            ("accessibility", "TEXT"),
            ("opening_hours", "TEXT"),
            ("phone", "TEXT"),
            ("website", "TEXT"),
        ]
        for col_name, col_type in new_report_cols:
            try:
                await conn.execute(f"ALTER TABLE reports ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
            except Exception as e:
                if "already exists" not in str(e):
                    logger.warning(f"PG migration reports.{col_name}: {e}")

        # 3. Premium подписки (13.07.2026)
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS premium_users (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    tier TEXT NOT NULL,
                    started_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    payment_id TEXT,
                    payment_amount INTEGER,
                    payment_method TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    cancelled_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_users_user_id ON premium_users (user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_users_active ON premium_users (user_id, is_active, expires_at)")

            # Миграция: убираем CHECK constraint на tier (нужен для 'founder' tier)
            try:
                await conn.execute("ALTER TABLE premium_users DROP CONSTRAINT IF EXISTS premium_users_tier_check")
            except Exception:
                pass  # Constraint may not exist

            # premium_trials — трекинг trial активаций (1 раз на юзера)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS premium_trials (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                    tier TEXT NOT NULL,
                    days INTEGER NOT NULL,
                    started_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS premium_payments (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    tier TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT DEFAULT 'RUB',
                    status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'failed', 'refunded')),
                    payment_method TEXT,
                    external_id TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    paid_at TIMESTAMPTZ,
                    metadata JSONB
                )
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_payments_user_id ON premium_payments (user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_payments_external_id ON premium_payments (external_id)")
            logger.info("PG migration premium: tables created")
        except Exception as e:
            logger.warning(f"PG migration premium: {e}")

        # 3.5. fuel_alarms — подписки на появление топлива (Premium)
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS fuel_alarms (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
                    fuel_type TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    triggered_at TIMESTAMPTZ,
                    last_notified_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, station_id, fuel_type)
                )"""
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_fuel_alarms_active ON fuel_alarms (is_active, station_id, fuel_type)")
        except Exception as e:
            logger.warning(f"PG migration fuel_alarms: {e}")

        # 3.6. referrals — Реферальная программа
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    referred_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    referral_code TEXT NOT NULL UNIQUE,
                    referred_telegram_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    premium_granted BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )"""
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals (referral_code)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals (referrer_user_id)")
        except Exception as e:
            logger.warning(f"PG migration referrals: {e}")

        # 3.7. referral_discounts — 50% скидка за приглашение (вместо бесплатного месяца)
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS referral_discounts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    discount_percent INTEGER NOT NULL DEFAULT 50,
                    expires_at TIMESTAMPTZ NOT NULL,
                    used BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )"""
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_referral_discounts_user "
                "ON referral_discounts (user_id, expires_at)"
            )
        except Exception as e:
            logger.warning(f"PG migration referral_discounts: {e}")

        # 3.8. referral_relationships — permanent referral tracking
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS referral_relationships (
                    id SERIAL PRIMARY KEY,
                    referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    referred_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(referred_user_id)
                )"""
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_rel_referrer ON referral_relationships (referrer_user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_rel_referred ON referral_relationships (referred_user_id)")
        except Exception as e:
            logger.warning(f"PG migration referral_relationships: {e}")

        # 3.9. referral_balances — баланс реферера
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS referral_balances (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    total_earned INTEGER DEFAULT 0,
                    total_withdrawn INTEGER DEFAULT 0,
                    balance INTEGER DEFAULT 0,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )"""
            )
        except Exception as e:
            logger.warning(f"PG migration referral_balances: {e}")

        # 3.10. referral_earnings — история начислений
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS referral_earnings (
                    id SERIAL PRIMARY KEY,
                    referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    referred_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    payment_id INTEGER,
                    payment_amount INTEGER NOT NULL,
                    commission_percent INTEGER NOT NULL DEFAULT 50,
                    commission_amount INTEGER NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )"""
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_earn_referrer ON referral_earnings (referrer_user_id)")
        except Exception as e:
            logger.warning(f"PG migration referral_earnings: {e}")

        # 3.11. referral_withdrawals — заявки на вывод
        try:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS referral_withdrawals (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    amount INTEGER NOT NULL,
                    method TEXT NOT NULL DEFAULT 'card',
                    details TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    processed_at TIMESTAMPTZ
                )"""
            )
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ref_wd_user ON referral_withdrawals (user_id)")
        except Exception as e:
            logger.warning(f"PG migration referral_withdrawals: {e}")

        # 4. Автоимпорт из SQLite если PG пуста
        try:
            cnt = await conn.fetchval("SELECT COUNT(*) FROM stations")
            if cnt == 0 and DB_PATH.exists():
                await _import_from_sqlite_pg(conn)
        except Exception as e:
            logger.warning(f"PG auto-import: {e}")


async def _import_from_sqlite_pg(conn):
    """Импорт данных из локальной SQLite в PostgreSQL (одноразово).

    Конвертирует типы: int→bool для has_24_7/is_verified/is_active/has_limit/available,
    json-string→list для fuel_types, string→datetime для timestamps.
    """
    import sqlite3 as _sq3
    from datetime import datetime as _dt
    logger.info(f"Importing from SQLite: {DB_PATH}")
    sq = _sq3.connect(str(DB_PATH))
    sq.row_factory = _sq3.Row

    def _ts(s):
        if not s: return None
        try: return _dt.fromisoformat(str(s))
        except: return None

    # Stations
    rows = sq.execute("SELECT * FROM stations").fetchall()
    if rows:
        data = []
        for r in rows:
            ft = r["fuel_types"]
            if isinstance(ft, str):
                try: ft = json.loads(ft)
                except: ft = []
            if not isinstance(ft, list): ft = []
            data.append((r["id"],r["osm_id"],r["name"],r["operator"],r["brand"],r["network"],
                r["country"],r["region"],r["city"],r["address"],r["lat"],r["lon"],ft,
                bool(r["has_24_7"]),r["phone"],r["website"],
                bool(r["is_verified"]),bool(r["is_active"]),
                _ts(r["created_at"]),_ts(r["updated_at"])))
        await conn.executemany('''INSERT INTO stations
            (id,osm_id,name,operator,brand,network,country,region,city,address,lat,lon,fuel_types,
             has_24_7,phone,website,is_verified,is_active,created_at,updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                    $14,$15,$16,$17,$18,$19,$20)
            ON CONFLICT (id) DO NOTHING''', data)
        logger.info(f"  stations: {len(data)}")

    # Users
    rows = sq.execute("SELECT * FROM users").fetchall()
    if rows:
        data = [(r["id"],r["telegram_id"],r["username"],r["first_name"],r["last_name"],
            r["language_code"],r["reputation"],r["total_reports"],r["confirmed_reports"],
            r["badge"],r["region"],r["city"],
            bool(r["is_owner"]),bool(r["is_blocked"]),
            _ts(r["created_at"]),_ts(r["last_active_at"])) for r in rows]
        await conn.executemany('''INSERT INTO users
            (id,telegram_id,username,first_name,last_name,language_code,reputation,
             total_reports,confirmed_reports,badge,region,city,is_owner,is_blocked,
             created_at,last_active_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (id) DO NOTHING''', data)
        logger.info(f"  users: {len(data)}")

    # Reports (батчами)
    rows = sq.execute("SELECT * FROM reports").fetchall()
    total = 0
    for i in range(0, len(rows), 3000):
        chunk = rows[i:i+3000]
        data = []
        for r in chunk:
            avail = r["available"]
            if avail == 1: avail_b = True
            elif avail == 0: avail_b = False
            else: avail_b = None
            nd = r["next_delivery_at"]
            data.append((r["id"],r["station_id"],r["user_id"],r["fuel_type"],
                avail_b,r["price"],r["queue_size"],bool(r["has_limit"]),
                r["limit_liters"],r["comment"],r["confidence"],r["confirmations"],
                r["disputes"],r["source"],_ts(r["expires_at"]),
                _ts(nd),_ts(r["created_at"])))
        await conn.executemany('''INSERT INTO reports
            (id,station_id,user_id,fuel_type,available,price,queue_size,has_limit,
             limit_liters,comment,confidence,confirmations,disputes,source,expires_at,
             next_delivery_at,created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
            ON CONFLICT (id) DO NOTHING''', data)
        total += len(data)
    logger.info(f"  reports: {total}")

    # Reset sequences
    for t in ["stations","users","reports"]:
        try:
            await conn.execute(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}),1))")
        except: pass

    sq.close()
    logger.info("SQLite → PG import done")


from contextlib import asynccontextmanager

@asynccontextmanager
async def get_connection():
    """Async context manager: yield connection (aiosqlite или asyncpg)."""
    if USE_SQLITE:
        yield _db
    else:
        async with _db.acquire() as conn:
            yield conn


# === Универсальные хелперы ===
def _sqlite_sql(sql: str) -> str:
    """Конвертирует PG-style → SQLite-style для совместимости."""
    import re
    sql = re.sub(r"\$\d+", "?", sql)
    # NOW() → datetime('now')
    sql = sql.replace("NOW()", "datetime('now')")
    # INTERVAL 'N hours' → '-N hours'
    sql = re.sub(r"INTERVAL\s+'(\d+)\s+(\w+)'", r"'-\1 \2'", sql)
    return sql


async def _fetch(sql: str, *args, one: bool = False):
    """Универсальный fetch. Возвращает dict (SQLite) или list[dict] (PostgreSQL)."""
    if USE_SQLITE:
        # SQLite использует ? вместо $1, $2, ...; автоматически конвертируем
        sql = _sqlite_sql(sql)
        async with _db.execute(sql, args) as cur:
            if one:
                row = await cur.fetchone()
                return dict(row) if row else None
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        # PostgreSQL — asyncpg
        async with _db.acquire() as conn:
            # Конвертируем ? обратно в $1, $2, ...
            import re
            pg_sql = sql
            idx = 1
            while "?" in pg_sql:
                pg_sql = pg_sql.replace("?", f"${idx}", 1)
                idx += 1
            if one:
                row = await conn.fetchrow(pg_sql, *args)
                return dict(row) if row else None
            rows = await conn.fetch(pg_sql, *args)
        return [dict(r) for r in rows]


# === Продвижение АЗС (платное размещение) ===

PROMO_PRICE_STARS = 299  #Stars за 30 дней продвижения
PROMO_DURATION_DAYS = 30


async def promote_station(owner_station_id: int, days: int = PROMO_DURATION_DAYS) -> None:
    """Активировать продвижение АЗС на N дней."""
    from datetime import datetime, timedelta
    expires = (datetime.now() + timedelta(days=days)).isoformat()
    if USE_SQLITE:
        await _db.execute(
            "UPDATE owner_stations SET is_promoted = 1, promoted_until = ? WHERE id = ?",
            expires, owner_station_id,
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE owner_stations SET is_promoted = TRUE, promoted_until = $1 WHERE id = $2",
                expires, owner_station_id,
            )


async def is_station_promoted(station_id: int) -> bool:
    """Проверяет, продвигается ли АЗС (и не истёк ли срок."""
    from datetime import datetime
    if USE_SQLITE:
        row = await _fetch(
            """SELECT is_promoted, promoted_until FROM owner_stations
               WHERE station_id = ? AND is_verified = 1 AND is_promoted = 1
               LIMIT 1""",
            station_id, one=True,
        )
    else:
        row = await _fetch(
            """SELECT is_promoted, promoted_until FROM owner_stations
               WHERE station_id = $1 AND is_verified = TRUE AND is_promoted = TRUE
               LIMIT 1""",
            station_id, one=True,
        )
    if not row:
        return False
    until = row.get("promoted_until")
    if not until:
        return True
    try:
        if isinstance(until, str):
            until_dt = datetime.fromisoformat(until.replace(" ", "T"))
        else:
            until_dt = until
        return until_dt > datetime.now(timezone.utc) if until_dt.tzinfo else until_dt > datetime.now()
    except Exception:
        return True


async def get_promoted_station_ids(city: str) -> list[int]:
    """Возвращает ID продвинутых АЗС в городе."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT os.station_id FROM owner_stations os
               JOIN stations s ON s.id = os.station_id
               WHERE s.city = ? AND os.is_verified = 1 AND os.is_promoted = 1
                 AND (os.promoted_until IS NULL OR os.promoted_until > datetime('now'))""",
            city,
        )
    else:
        rows = await _fetch(
            """SELECT os.station_id FROM owner_stations os
               JOIN stations s ON s.id = os.station_id
               WHERE s.city = $1 AND os.is_verified = TRUE AND os.is_promoted = TRUE
                 AND (os.promoted_until IS NULL OR os.promoted_until > NOW())""",
            city,
        )
    return [r["station_id"] for r in rows]


async def get_owner_station_by_user_and_station(user_id: int, station_id: int) -> dict | None:
    """Получить owner_stations запись по user_id + station_id."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT * FROM owner_stations WHERE user_id = ? AND station_id = ?",
            user_id, station_id, one=True,
        )
    else:
        row = await _fetch(
            "SELECT * FROM owner_stations WHERE user_id = $1 AND station_id = $2",
            user_id, station_id, one=True,
        )
    return row


# === Бейджи пользователей ===
BADGE_CATALOG = {
    "newcomer": {"name": "Новичок", "emoji": "🥉", "desc": "Первый отчёт"},
    "active": {"name": "Активный", "emoji": "🥈", "desc": "10+ отчётов"},
    "expert": {"name": "Эксперт", "emoji": "🥇", "desc": "100+ отчётов"},
    "top_region": {"name": "Топ региона", "emoji": "👑", "desc": "Самый активный в своём городе"},
    "pioneer": {"name": "Первопроходец", "emoji": "🔍", "desc": "Первый отчёт о новой АЗС"},
    "verified_owner": {"name": "Verified", "emoji": "✅", "desc": "Подтверждённый владелец АЗС"},
}


async def award_badge(user_id: int, badge_code: str) -> bool:
    """Выдаёт бейдж пользователю. Возвращает True если новый, False если уже был."""
    if badge_code not in BADGE_CATALOG:
        return False
    if USE_SQLITE:
        try:
            async with _db.execute(
                "INSERT INTO user_badges (user_id, badge_code) VALUES (?, ?)",
                (user_id, badge_code),
            ) as cur:
                await cur.fetchone()
            await _db.commit()
            return True
        except Exception:
            await _db.rollback()
            return False  # уже есть (UNIQUE constraint)
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO user_badges (user_id, badge_code)
                   VALUES ($1, $2)
                   ON CONFLICT (user_id, badge_code) DO NOTHING
                   RETURNING id""",
                user_id, badge_code,
            )
            return row is not None


async def get_user_badges(user_id: int) -> list:
    """Возвращает список бейджей пользователя с метаданными."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT badge_code, awarded_at FROM user_badges "
            "WHERE user_id = ? ORDER BY awarded_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {**BADGE_CATALOG.get(r["badge_code"], {"name": r["badge_code"], "emoji": "🏅", "desc": ""}),
             "code": r["badge_code"],
             "awarded_at": r["awarded_at"]}
            for r in rows
        ]
    async with _db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT badge_code, awarded_at FROM user_badges "
            "WHERE user_id = $1 ORDER BY awarded_at",
            user_id,
        )
    return [
        {**BADGE_CATALOG.get(r["badge_code"], {"name": r["badge_code"], "emoji": "🏅", "desc": ""}),
         "code": r["badge_code"],
         "awarded_at": r["awarded_at"].isoformat() if r["awarded_at"] else None}
        for r in rows
    ]


async def check_and_award_badges(user_id: int) -> list:
    """Проверяет и выдаёт бейджи по текущей статистике. Возвращает список новых бейджей."""
    if USE_SQLITE:
        # total_reports
        async with _db.execute(
            "SELECT total_reports FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return []
        total = row["total_reports"] or 0
        # is_owner + verified
        async with _db.execute(
            "SELECT COUNT(*) as c FROM owner_stations "
            "WHERE user_id = ? AND is_verified = 1",
            (user_id,),
        ) as cur:
            v = await cur.fetchone()
        has_verified_station = (v["c"] or 0) > 0
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT total_reports FROM users WHERE id = $1", user_id
            )
            if not row:
                return []
            total = row["total_reports"] or 0
            v = await conn.fetchrow(
                "SELECT COUNT(*) as c FROM owner_stations "
                "WHERE user_id = $1 AND is_verified = TRUE",
                user_id,
            )
            has_verified_station = (v["c"] or 0) > 0

    new_badges = []
    if total >= 1:
        if await award_badge(user_id, "newcomer"):
            new_badges.append("newcomer")
    if total >= 10:
        if await award_badge(user_id, "active"):
            new_badges.append("active")
    if total >= 100:
        if await award_badge(user_id, "expert"):
            new_badges.append("expert")
    if has_verified_station:
        if await award_badge(user_id, "verified_owner"):
            new_badges.append("verified_owner")

    return new_badges


async def get_user_stats_summary(user_id: int) -> dict:
    """Возвращает репутацию, отчёты и список бейджей для /profile."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT reputation, total_reports, confirmed_reports, region, city "
            "FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {}
        stats = dict(row)
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reputation, total_reports, confirmed_reports, region, city "
                "FROM users WHERE id = $1",
                user_id,
            )
            if not row:
                return {}
            stats = dict(row)
    stats["badges"] = await get_user_badges(user_id)
    return stats





async def get_premium_info(user_id: int) -> dict | None:
    """Возвращает инфо о premium-подписке или None."""
    sub = await get_user_premium(user_id)
    if not sub:
        return None
    result = dict(sub) if hasattr(sub, 'keys') else dict(sub)
    expires = result.get("expires_at")
    if isinstance(expires, str):
        try:
            from datetime import datetime as _dt
            result["expires_at"] = _dt.fromisoformat(expires)
        except (ValueError, TypeError):
            pass
    return result


async def _execute(sql: str, *args, returning: bool = False):
    """Универсальный execute.

    При returning=True: для SQLite возвращает cursor.lastrowid, для PG — результат RETURNING.
    Если в SQL нет RETURNING, автоматически добавляет RETURNING id.
    """
    if USE_SQLITE:
        sql = _sqlite_sql(sql)
        async with _db.execute(sql, args) as cur:
            await _db.commit()
            if returning:
                return cur.lastrowid
        return None
    async with _db.acquire() as conn:
        import re
        pg_sql = sql
        idx = 1
        while "?" in pg_sql:
            pg_sql = pg_sql.replace("?", f"${idx}", 1)
            idx += 1
        if returning and "RETURNING" not in pg_sql.upper():
            pg_sql += " RETURNING id"
        if returning:
            row = await conn.fetchrow(pg_sql, *args)
            return row[0] if row else None
        await conn.execute(pg_sql, *args)


# === Пользователи ===
async def upsert_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    language_code: str | None = None,
) -> int:
    """Создаёт или обновляет пользователя. Возвращает его id."""
    if USE_SQLITE:
        # Сначала проверяем, есть ли уже
        async with _db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            user_id = row[0]
            await _db.execute(
                """UPDATE users SET username=?, first_name=?, last_name=?, language_code=?, last_active_at=datetime('now')
                   WHERE id=?""",
                (username, first_name, last_name, language_code, user_id),
            )
            await _db.commit()
            return user_id
        # Создаём нового
        async with _db.execute(
            """INSERT INTO users (telegram_id, username, first_name, last_name, language_code, last_active_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (telegram_id, username, first_name, last_name, language_code),
        ) as cur:
            user_id = cur.lastrowid
        await _db.commit()
        return user_id
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE telegram_id = $1", telegram_id
            )
            if row:
                await conn.execute(
                    """UPDATE users SET username=$1, first_name=$2, last_name=$3, language_code=$4, last_active_at=NOW()
                       WHERE id=$5""",
                    username, first_name, last_name, language_code, row["id"],
                )
                return row["id"]
            new_row = await conn.fetchrow(
                """INSERT INTO users (telegram_id, username, first_name, last_name, language_code, last_active_at)
                   VALUES ($1, $2, $3, $4, $5, NOW()) RETURNING id""",
                telegram_id, username, first_name, last_name, language_code,
            )
            return new_row["id"]


async def mark_user_blocked(telegram_id: int) -> None:
    """Помечает пользователя заблокированным (если он заблокировал бота)."""
    if USE_SQLITE:
        await _db.execute(
            "UPDATE users SET is_blocked = 1 WHERE telegram_id = ?",
            (telegram_id,),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_blocked = TRUE WHERE telegram_id = $1",
                telegram_id,
            )


async def get_all_tg_user_ids() -> list[int]:
    """Возвращает список всех Telegram ID активных пользователей."""
    rows = await _fetch(
        "SELECT telegram_id FROM users WHERE telegram_id > 0 AND (is_blocked IS NULL OR is_blocked = 0)"
    )
    return [r["telegram_id"] if isinstance(r, dict) else r[0] for r in rows]


async def get_all_vk_user_ids() -> list[int]:
    """Возвращает список всех VK peer_id активных пользователей."""
    rows = await _fetch(
        "SELECT vk_id FROM users WHERE vk_id > 0 AND (is_blocked IS NULL OR is_blocked = 0)"
    )
    return [r["vk_id"] if isinstance(r, dict) else r[0] for r in rows]


async def get_broadcast_stats() -> dict:
    """Возвращает статистику для рассылки."""
    tg_rows = await _fetch("SELECT COUNT(*) as cnt FROM users WHERE telegram_id > 0 AND (is_blocked IS NULL OR is_blocked = 0)")
    vk_rows = await _fetch("SELECT COUNT(*) as cnt FROM users WHERE vk_id > 0 AND (is_blocked IS NULL OR is_blocked = 0)")
    total_rows = await _fetch("SELECT COUNT(*) as cnt FROM users WHERE (is_blocked IS NULL OR is_blocked = 0)")
    tg_count = tg_rows[0]["cnt"] if tg_rows else 0
    vk_count = vk_rows[0]["cnt"] if vk_rows else 0
    total_count = total_rows[0]["cnt"] if total_rows else 0
    return {"tg": tg_count, "vk": vk_count, "total": total_count}


async def get_or_create_user(message) -> int:
    """Создаёт/обновляет пользователя из сообщения (Telegram или VK)."""
    # Telegram
    if hasattr(message, "from_user") and message.from_user is not None:
        user = message.from_user
        return await upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )
    # VK
    if hasattr(message, "peer_id"):
        return await upsert_user(
            telegram_id=message.peer_id,
            username=f"vk_{message.peer_id}",
            first_name=None,
            last_name=None,
            language_code="ru",
        )
    return 0


# === АЗС и поиск ===
def _ru_lower(s: str | None) -> str | None:
    """Python-lower, корректно работает с кириллицей (в отличие от SQLite LOWER())."""
    return s.lower() if s else s


async def find_nearest_stations(
    lat: float, lon: float,
    fuel_type: str | None = None,
    limit: int = 5, radius_km: int = 50,
) -> list:
    """Ищет ближайшие АЗС к точке (в SQLite — простой фильтр по bbox + haversine)."""
    lat_delta = radius_km / 111.0
    lon_delta = radius_km / (111.0 * math.cos(math.radians(lat)))

    if USE_SQLITE:
        # SQLite: грубый bbox фильтр, потом haversine в Python
        if fuel_type:
            sql = """
                SELECT id, name, operator, city, address, lat, lon, fuel_types, is_verified
                FROM stations
                WHERE is_active = 1
                  AND lat BETWEEN ? AND ?
                  AND lon BETWEEN ? AND ?
                  AND fuel_types LIKE ?
            """
            params = (lat - lat_delta, lat + lat_delta,
                      lon - lon_delta, lon + lon_delta, f'%"{fuel_type}"%')
        else:
            sql = """
                SELECT id, name, operator, city, address, lat, lon, fuel_types, is_verified
                FROM stations
                WHERE is_active = 1
                  AND lat BETWEEN ? AND ?
                  AND lon BETWEEN ? AND ?
            """
            params = (lat - lat_delta, lat + lat_delta,
                      lon - lon_delta, lon + lon_delta)

        async with _db.execute(sql, params) as cur:
            rows = await cur.fetchall()

        # Haversine
        results = []
        for row in rows:
            d = dict(row)
            dist = _haversine_km(lat, lon, d["lat"], d["lon"])
            if dist <= radius_km:
                d["distance_km"] = dist
                results.append(d)
        results.sort(key=lambda x: x["distance_km"])
        return results[:limit]
    else:
        # PostgreSQL: точный запрос с haversine в SQL
        if fuel_type:
            sql = """
                WITH nearest AS (
                    SELECT
                        id, name, operator, city, address, lat, lon, fuel_types, is_verified,
                        (
                            6371 * acos(
                                GREATEST(-1, LEAST(1,
                                    cos(radians($1)) * cos(radians(lat)) *
                                    cos(radians(lon) - radians($2)) +
                                    sin(radians($1)) * sin(radians(lat))
                                ))
                            )
                        ) AS distance_km
                    FROM stations
                    WHERE is_active = TRUE
                      AND lat BETWEEN $1 - $4 AND $1 + $4
                      AND lon BETWEEN $2 - $5 AND $2 + $5
                      AND $3 = ANY(fuel_types)
                )
                SELECT *
                FROM nearest
                WHERE distance_km <= $6
                ORDER BY distance_km ASC
                LIMIT $7
            """
        else:
            sql = """
                WITH nearest AS (
                    SELECT
                        id, name, operator, city, address, lat, lon, fuel_types, is_verified,
                        (
                            6371 * acos(
                                GREATEST(-1, LEAST(1,
                                    cos(radians($1)) * cos(radians(lat)) *
                                    cos(radians(lon) - radians($2)) +
                                    sin(radians($1)) * sin(radians(lat))
                                ))
                            )
                        ) AS distance_km
                    FROM stations
                    WHERE is_active = TRUE
                      AND lat BETWEEN $1 - $3 AND $1 + $3
                      AND lon BETWEEN $2 - $4 AND $2 + $4
                )
                SELECT *
                FROM nearest
                WHERE distance_km <= $5
                ORDER BY distance_km ASC
                LIMIT $6
            """
        async with _db.acquire() as conn:
            if fuel_type:
                rows = await conn.fetch(
                    sql, lat, lon, fuel_type, lat_delta, lon_delta, radius_km, limit
                )
            else:
                rows = await conn.fetch(
                    sql, lat, lon, lat_delta, lon_delta, radius_km, limit
                )
        return [dict(r) for r in rows]


async def find_stations_by_city(
    city: str,
    region: str | None = None,
    fuel_type: str | None = None,
    network: str | None = None,
    max_price: float | None = None,
    has_stock: bool = True,
    include_nearby_regions: bool = True,
    with_coords: bool = False,
    limit: int = 50,
) -> list:
    """Ищет АЗС по городу (а не геолокации).

    Фильтры:
      - city: название города (LIKE, fuzzy match)
      - region: регион (если None и include_nearby_regions=True — ищем во всех)
      - fuel_type: 92/95/98/diesel/lpg
      - network: оператор (Лукойл, Газпром, etc) — LIKE
      - max_price: максимальная цена за литр
      - has_stock: True = только АЗС с подтверждённым наличием (отчёт за 4 часа)
      - include_nearby_regions: True = включаем соседние регионы
    Возвращает АЗС с:
      - расстояние от центра города (если есть координаты)
      - последняя цена (если есть)
      - наличие (если есть)
      - source, source_priority
    """
    if USE_SQLITE:
        # === Сбор параметров строго в порядке появления `?` в SQL ===
        # SQL: ... FROM stations s {join} WHERE ... LIMIT ?
        # join идёт ПЕРЕД where, поэтому JOIN-параметры добавляем первыми.
        params = []
        join_params: list = []  # параметры для JOIN (идут первыми в SQL)
        where_params: list = []  # параметры для WHERE
        where = ["is_active = 1"]
        join = ""

        # === Подзапрос: есть отчёт с наличием за последние 4 часа ===
        if has_stock:
            if fuel_type:
                join = """
                    JOIN (
                        SELECT station_id,
                               MAX(CASE WHEN available = 1 THEN 1 ELSE 0 END) as has_stock,
                               MIN(price) FILTER (WHERE fuel_type = ? AND price IS NOT NULL) as min_price_recent
                        FROM reports
                        WHERE created_at > datetime('now', '-4 hours')
                          AND fuel_type != 'all'
                          AND fuel_type = ?
                        GROUP BY station_id
                    ) r ON r.station_id = s.id
                """
                join_params.extend([fuel_type, fuel_type])
            else:
                join = """
                    JOIN (
                        SELECT station_id,
                               MAX(CASE WHEN available = 1 THEN 1 ELSE 0 END) as has_stock
                        FROM reports
                        WHERE created_at > datetime('now', '-4 hours')
                          AND fuel_type != 'all'
                        GROUP BY station_id
                    ) r ON r.station_id = s.id
                """
            where.append("r.has_stock = 1")

        # === Фильтр по цене (свежие отчёты за 7 дней) ===
        if max_price is not None and fuel_type:
            if has_stock:
                # min_price_recent уже доступен через join выше
                where.append("r.min_price_recent <= ?")
                where_params.append(max_price)
            else:
                join = """
                    JOIN (
                        SELECT station_id, MIN(price) as min_price_recent
                        FROM reports
                        WHERE fuel_type = ? AND created_at > datetime('now', '-7 days')
                          AND price IS NOT NULL
                        GROUP BY station_id
                    ) r ON r.station_id = s.id
                """
                join_params.append(fuel_type)
                where.append("r.min_price_recent <= ?")
                where_params.append(max_price)

        # === Город (fuzzy) — py_lower() корректно работает с кириллицей ===
        if city:
            where.append("(py_lower(s.city) LIKE ? OR py_lower(s.address) LIKE ? OR py_lower(s.name) LIKE ?)")
            c = f"%{city.lower()}%"
            where_params.extend([c, c, c])

        # === Регион ===
        if region and not include_nearby_regions:
            where.append("py_lower(s.region) LIKE ?")
            where_params.append(f"%{region.lower()}%")

        # === Сеть (operator/network) ===
        if network:
            where.append("(py_lower(s.operator) LIKE ? OR py_lower(s.network) LIKE ? OR py_lower(s.name) LIKE ?)")
            n = f"%{network.lower()}%"
            where_params.extend([n, n, n])

        # === Тип топлива (в fuel_types массиве) ===
        if fuel_type:
            where.append("s.fuel_types LIKE ?")
            where_params.append(f'%"{fuel_type}"%')

        # === Только с координатами (для карты) ===
        if with_coords:
            where.append("s.lat IS NOT NULL AND s.lon IS NOT NULL AND s.lat != 0 AND s.lon != 0")

        sql = f"""
            SELECT s.id, s.name, s.operator, s.city, s.region, s.address, s.lat, s.lon,
                   s.fuel_types, s.is_verified,
                   {("r.has_stock," if has_stock else "")}
                   {("r.min_price_recent as min_price," if max_price is not None and fuel_type else "")}
                   0 as distance_km
            FROM stations s {join}
            WHERE {' AND '.join(where)}
            ORDER BY s.is_verified DESC, s.name
            LIMIT ?
        """
        # Собираем финальный список: join_params + where_params + limit
        params = join_params + where_params + [limit]
        async with _db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # === PostgreSQL ===
    params = []
    where = ["s.is_active = TRUE"]
    join = ""

    if city:
        where.append("(LOWER(s.city) LIKE $1 OR LOWER(s.address) LIKE $1 OR LOWER(s.name) LIKE $1)")
        params.append(f"%{city.lower()}%")

    if region and not include_nearby_regions:
        where.append("LOWER(s.region) LIKE $" + str(len(params) + 1))
        params.append(f"%{region.lower()}%")

    if network:
        n_idx = len(params) + 1
        where.append(f"(LOWER(s.operator) LIKE ${n_idx} OR LOWER(s.network) LIKE ${n_idx} OR LOWER(s.name) LIKE ${n_idx})")
        params.append(f"%{network.lower()}%")

    if fuel_type:
        f_idx = len(params) + 1
        where.append(f"${f_idx} = ANY(s.fuel_types)")
        params.append(fuel_type)

    if with_coords:
        where.append("s.lat IS NOT NULL AND s.lon IS NOT NULL AND s.lat != 0 AND s.lon != 0")

    if has_stock:
        if fuel_type:
            join = f"""
                JOIN (
                    SELECT station_id,
                           BOOL_OR(available = TRUE) as has_stock,
                           MIN(price) FILTER (WHERE price IS NOT NULL) as min_price_recent
                    FROM reports
                    WHERE created_at > NOW() - INTERVAL '4 hours'
                      AND fuel_type != 'all'
                      AND fuel_type = ${len(params) + 1}
                    GROUP BY station_id
                ) r ON r.station_id = s.id
                """
            params.append(fuel_type)
        else:
            join = """
                JOIN (
                    SELECT station_id,
                           BOOL_OR(available = TRUE) as has_stock
                    FROM reports
                    WHERE created_at > NOW() - INTERVAL '4 hours'
                      AND fuel_type != 'all'
                    GROUP BY station_id
                ) r ON r.station_id = s.id
                """
        where.append("r.has_stock = TRUE")

    if max_price is not None and fuel_type:
        if not has_stock:
            join = """
                JOIN (
                    SELECT station_id, MIN(price) as min_price_recent
                    FROM reports
                    WHERE fuel_type = $X AND created_at > NOW() - INTERVAL '7 days'
                      AND price IS NOT NULL
                    GROUP BY station_id
                ) r ON r.station_id = s.id
            """.replace("$X", f"${len(params) + 1}")
            params.append(fuel_type)
        where.append("r.min_price_recent <= $" + str(len(params) + 1))
        params.append(max_price)

    sql = f"""
        SELECT s.id, s.name, s.operator, s.city, s.region, s.address, s.lat, s.lon,
               s.fuel_types, s.is_verified,
               {("r.has_stock," if has_stock else "")}
               {("r.min_price_recent as min_price," if max_price is not None and fuel_type else "")}
               0 as distance_km
        FROM stations s {join}
        WHERE {' AND '.join(where)}
        ORDER BY s.is_verified DESC, s.name
        LIMIT ${len(params) + 1}
    """
    params.append(limit)

    async with _db.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


async def get_station_by_id(station_id: int) -> dict | None:
    """Получает АЗС по id."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT * FROM stations WHERE id = ?", (station_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM stations WHERE id = $1", station_id)
        return dict(row) if row else None


async def upsert_station_for_import(
    name: str,
    region: str,
    city: str = "",
    operator: str = "",
    lat: float | None = None,
    lon: float | None = None,
) -> int:
    """Находит существующую АЗС по (name+region+city) или создаёт новую.
    
    Используется при импорте от внешних парсеров (benzin-price.ru и т.д.),
    когда у нас нет надёжного external_id, но есть name и регион.
    Возвращает station_id.
    """
    name_norm = (name or "").strip()
    region_norm = (region or "").strip()
    city_norm = (city or "").strip()
    operator_norm = (operator or "").strip()
    if not name_norm or not region_norm:
        return 0
    if lat is None or lon is None:
        # Без координат АЗС не имеет смысла — ставим дефолт (Москва)
        lat = 55.7558
        lon = 37.6173
    
    if USE_SQLITE:
        # 1) Ищем точное совпадение по name+region
        row = await (
            await _db.execute(
                """SELECT id FROM stations 
                   WHERE py_lower(name) = py_lower(?)
                     AND py_lower(COALESCE(region, '')) = py_lower(?)
                     AND is_active = 1
                   LIMIT 1""",
                (name_norm, region_norm),
            )
        ).fetchone()
        if row:
            return row[0]
        # 2) Мягкий поиск — по name + region (содержит)
        row = await (
            await _db.execute(
                """SELECT id FROM stations 
                   WHERE py_lower(name) = py_lower(?)
                     AND py_lower(COALESCE(region, '')) LIKE ?
                     AND is_active = 1
                   LIMIT 1""",
                (name_norm, f"%{region_norm.lower()}%"),
            )
        ).fetchone()
        if row:
            return row[0]
        # 3) Создаём новую запись
        async with _db.execute(
            """INSERT INTO stations (name, operator, region, city, lat, lon, fuel_types, is_verified, is_active)
               VALUES (?, ?, ?, ?, ?, ?, '[]', 0, 1)""",
            (name_norm, operator_norm or None, region_norm, city_norm or None, lat, lon),
        ) as cur:
            new_id = cur.lastrowid
        await _db.commit()
        return new_id
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id FROM stations 
                   WHERE LOWER(name) = LOWER($1)
                     AND LOWER(COALESCE(region, '')) = LOWER($2)
                     AND is_active = TRUE
                   LIMIT 1""",
                name_norm, region_norm,
            )
            if row:
                return row["id"]
            row = await conn.fetchrow(
                """SELECT id FROM stations 
                   WHERE LOWER(name) = LOWER($1)
                     AND LOWER(COALESCE(region, '')) LIKE LOWER($2)
                     AND is_active = TRUE
                   LIMIT 1""",
                name_norm, f"%{region_norm}%",
            )
            if row:
                return row["id"]
            new_id = await conn.fetchval(
                """INSERT INTO stations (name, operator, region, city, lat, lon, fuel_types, is_verified, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, '{}', FALSE, TRUE)
                   RETURNING id""",
                name_norm, operator_norm or None, region_norm, city_norm or None, lat, lon,
            )
            return new_id


async def update_station_address(station_id: int, address: str, city: str, region: str) -> None:
    """Обновляет адрес, город и регион АЗС (используется при обогащении через reverse geocoding)."""
    if USE_SQLITE:
        await _db.execute(
            """UPDATE stations
               SET address = COALESCE(NULLIF(?, ''), address),
                   city = COALESCE(NULLIF(?, ''), city),
                   region = COALESCE(NULLIF(?, ''), region),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (address, city, region, station_id),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                """UPDATE stations
                   SET address = COALESCE(NULLIF($1, ''), address),
                       city = COALESCE(NULLIF($2, ''), city),
                       region = COALESCE(NULLIF($3, ''), region),
                       updated_at = NOW()
                   WHERE id = $4""",
                address, city, region, station_id,
            )


async def get_stations_without_address(
    city: str | None = None, limit: int | None = None
) -> list:
    """Возвращает АЗС без адреса (для обогащения через reverse geocoding)."""
    if USE_SQLITE:
        sql = """SELECT id, name, lat, lon, address, city, region
                 FROM stations
                 WHERE is_active = 1
                   AND (address IS NULL OR address = '' OR city IS NULL OR city = '')"""
        params: list = []
        if city:
            sql += " AND (city LIKE ? OR name LIKE ?)"
            like = f"%{city}%"
            params.extend([like, like])
        sql += " ORDER BY id LIMIT ?"
        params.append(limit or 1000)
        async with _db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    async with _db.acquire() as conn:
        sql = """SELECT id, name, lat, lon, address, city, region
                 FROM stations
                 WHERE is_active = TRUE
                   AND (address IS NULL OR address = '' OR city IS NULL OR city = '')"""
        params = []
        if city:
            sql += " AND (city ILIKE $1 OR name ILIKE $1)"
            params.append(f"%{city}%")
        sql += f" ORDER BY id LIMIT {limit or 1000}"
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]


async def get_user_id_by_telegram_id(telegram_id: int) -> int | None:
    """Возвращает внутренний id пользователя по telegram_id."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE telegram_id = $1", telegram_id
            )
        return row["id"] if row else None


async def get_user_id_by_vk_id(vk_id: int) -> int | None:
    """Возвращает внутренний id пользователя по vk_id."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT id FROM users WHERE vk_id = ?", (vk_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE vk_id = $1", vk_id
            )
        return row["id"] if row else None


async def upsert_user_vk(vk_id: int, first_name: str = "", last_name: str = "", screen_name: str = "") -> int:
    """Создаёт/обновляет пользователя по VK ID. Возвращает user_id."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT id FROM users WHERE vk_id = ?", vk_id, one=True,
        )
        if row:
            uid = row["id"] if isinstance(row, dict) else row[0]
            if screen_name:
                await _execute(
                    "UPDATE users SET first_name = ?, screen_name = ?, last_active_at = datetime('now') WHERE id = ?",
                    first_name, screen_name, uid,
                )
            else:
                await _execute(
                    "UPDATE users SET first_name = ?, last_active_at = datetime('now') WHERE id = ?",
                    first_name, uid,
                )
            return uid
        # Fallback: может быть старая запись с telegram_id=peer_id (до фикса)
        row = await _fetch(
            "SELECT id FROM users WHERE telegram_id = ? AND vk_id IS NULL", vk_id, one=True,
        )
        if row:
            uid = row["id"] if isinstance(row, dict) else row[0]
            await _execute(
                "UPDATE users SET vk_id = ?, first_name = ?, last_active_at = datetime('now') WHERE id = ?",
                vk_id, first_name, uid,
            )
            return uid
        try:
            await _execute(
                """INSERT INTO users (vk_id, telegram_id, first_name, last_name, last_active_at)
                   VALUES (?, 0, ?, ?, datetime('now'))""",
                vk_id, first_name, last_name,
            )
        except Exception:
            # Race condition — другой запрос уже создал запись
            row = await _fetch(
                "SELECT id FROM users WHERE vk_id = ?", vk_id, one=True,
            )
            if row:
                uid = row["id"] if isinstance(row, dict) else row[0]
                await _execute(
                    "UPDATE users SET first_name = ?, last_active_at = datetime('now') WHERE id = ?",
                    first_name, uid,
                )
                return uid
            raise
        new_row = await _fetch("SELECT last_insert_rowid() as id", one=True)
        return new_row["id"] if isinstance(new_row, dict) else new_row[0]
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE vk_id = $1", vk_id,
            )
            if row:
                if screen_name:
                    await conn.execute(
                        "UPDATE users SET first_name = $1, screen_name = $2, last_active_at = NOW() WHERE id = $3",
                        first_name, screen_name, row["id"],
                    )
                else:
                    await conn.execute(
                        "UPDATE users SET first_name = $1, last_active_at = NOW() WHERE id = $2",
                        first_name, row["id"],
                    )
                return row["id"]
            # Fallback: старая запись с telegram_id=peer_id (до фикса)
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE telegram_id = $1 AND vk_id IS NULL", vk_id,
            )
            if row:
                await conn.execute(
                    "UPDATE users SET vk_id = $1, first_name = $2, last_active_at = NOW() WHERE id = $3",
                    vk_id, first_name, row["id"],
                )
                return row["id"]
            try:
                new_row = await conn.fetchrow(
                    """INSERT INTO users (vk_id, telegram_id, first_name, last_name, last_active_at)
                       VALUES ($1, 0, $2, $3, NOW())
                       RETURNING id""",
                    vk_id, first_name, last_name,
                )
                if new_row:
                    return new_row["id"]
                # INSERT не вернул id — ищем запись
                row = await conn.fetchrow(
                    "SELECT id FROM users WHERE vk_id = $1", vk_id,
                )
                if row:
                    await conn.execute(
                        "UPDATE users SET first_name = $1, last_active_at = NOW() WHERE id = $2",
                        first_name, row["id"],
                    )
                    return row["id"]
                raise Exception("upsert_user_vk: row not found after insert")
            except Exception as e:
                err_str = str(e).lower()
                # Race: telegram_id=0 уже занят другим VK юзером
                if "unique" in err_str or "duplicate" in err_str or "constraint" in err_str:
                    row = await conn.fetchrow(
                        "SELECT id FROM users WHERE vk_id = $1", vk_id,
                    )
                    if row:
                        await conn.execute(
                            "UPDATE users SET first_name = $1, last_active_at = NOW() WHERE id = $2",
                            first_name, row["id"],
                        )
                        return row["id"]
                    # Последний fallback: обновляем telegram_id=0 запись → ставим уникальный telegram_id
                    placeholder_id = -abs(vk_id) % 2147483647
                    row = await conn.fetchrow(
                        "SELECT id FROM users WHERE telegram_id = $1", placeholder_id,
                    )
                    if row:
                        await conn.execute(
                            "UPDATE users SET vk_id = $1, first_name = $2, telegram_id = $3, last_active_at = NOW() WHERE id = $4",
                            vk_id, first_name, placeholder_id, row["id"],
                        )
                        return row["id"]
                raise


async def add_report(
    station_id: int,
    fuel_type: str,
    available: bool | None,
    user_id: int | None = None,
    price: float | None = None,
    queue_size: int | None = None,
    has_limit: bool = False,
    limit_liters: int | None = None,
    comment: str | None = None,
    source: str = "user",
    next_delivery_at: datetime | None = None,
    octane_rating: float | None = None,
    cetane_number: float | None = None,
    additives: str | None = None,
    quality_score: float | None = None,
    queue_wait_minutes: int | None = None,
    queue_trend: str | None = None,
    limit_per_visit: int | None = None,
    limit_daily: int | None = None,
    limit_weekly: int | None = None,
    canister_ban: bool = False,
    fuel_standard: str | None = None,
    certification: str | None = None,
    review_text: str | None = None,
    rating: float | None = None,
    photos_count: int | None = None,
    has_car_wash: bool | None = None,
    has_shop: bool | None = None,
    has_restaurant: bool | None = None,
    has_atm: bool | None = None,
    has_parking: bool | None = None,
    has_ev_charging: bool | None = None,
    accessibility: str | None = None,
    opening_hours: str | None = None,
    phone: str | None = None,
    website: str | None = None,
) -> int:
    """Добавляет отчёт о наличии топлива.

    available: True / False / None (None = "кончается").
    next_delivery_at: прогноз следующего завоза (если известен, None если нет).
    octane_rating: октановое число (92, 95, 98, 100)
    cetane_number: цетановое число для дизеля (40-60)
    additives: добавки (метилтретбутиловый эфир и т.д.)
    quality_score: оценка качества 0-10
    queue_wait_minutes: время ожидания в очереди (минуты)
    queue_trend: тренд очереди (growing/shrinking/stable)
    limit_per_visit: лимит на одну заправку (литры)
    limit_daily: дневной лимит (литры)
    limit_weekly: недельный лимит (литры)
    fuel_standard: стандарт топлива (ТУ, ГОСТ, Евро-5)
    certification: сертификат качества
    review_text: текст отзыва
    rating: оценка 0-5
    photos_count: количество фото
    has_car_wash: автомойка
    has_shop: магазин
    has_restaurant: кафе/ресторан
    has_atm: банкомат
    has_parking: парковка
    has_ev_charging: зарядка для ЭТС
    accessibility: доступность (пандус, широкие проезды)
    opening_hours: часы работы
    phone: телефон
    website: сайт
    В SQLite available NOT NULL, поэтому None хранится как 2.
    Также инкрементит users.total_reports и last_active_at.
    """
    expires_at_dt = datetime.now() + timedelta(hours=1)
    if USE_SQLITE:
        expires_at = expires_at_dt.isoformat()
        next_delivery_iso = next_delivery_at.isoformat() if next_delivery_at else None
    else:
        expires_at = expires_at_dt  # asyncpg требует datetime, не строку
        next_delivery_iso = next_delivery_at  # asyncpg принимает datetime

    if USE_SQLITE:
        # SQLite: True=1, False=0, None=2 ("кончается")
        if available is True:
            avail_int = 1
        elif available is False:
            avail_int = 0
        else:
            avail_int = 2
        has_limit_int = 1 if has_limit else 0
        canister_ban_int = 1 if canister_ban else 0

        async with _db.execute(
            """INSERT INTO reports (
                station_id, user_id, fuel_type, available, price,
                queue_size, has_limit, limit_liters, comment, source, expires_at, next_delivery_at,
                octane_rating, cetane_number, additives, quality_score,
                queue_wait_minutes, queue_trend,
                limit_per_visit, limit_daily, limit_weekly, canister_ban,
                fuel_standard, certification, review_text, rating, photos_count,
                has_car_wash, has_shop, has_restaurant, has_atm, has_parking, has_ev_charging,
                accessibility, opening_hours, phone, website
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (station_id, user_id, fuel_type, avail_int, price,
             queue_size, has_limit_int, limit_liters, comment, source, expires_at, next_delivery_iso,
             octane_rating, cetane_number, additives, quality_score,
             queue_wait_minutes, queue_trend,
             limit_per_visit, limit_daily, limit_weekly, canister_ban_int,
             fuel_standard, certification, review_text, rating, photos_count,
             has_car_wash, has_shop, has_restaurant, has_atm, has_parking, has_ev_charging,
             accessibility, opening_hours, phone, website),
        ) as cur:
            report_id = cur.lastrowid
        if user_id:
            await _db.execute(
                "UPDATE users SET total_reports = total_reports + 1, last_active_at = datetime('now') WHERE id = ?",
                (user_id,),
            )
        await _db.commit()
        return report_id
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO reports (
                    station_id, user_id, fuel_type, available, price,
                    queue_size, has_limit, limit_liters, comment, source, expires_at, next_delivery_at,
                    octane_rating, cetane_number, additives, quality_score,
                    queue_wait_minutes, queue_trend,
                    limit_per_visit, limit_daily, limit_weekly, canister_ban,
                    fuel_standard, certification, review_text, rating, photos_count,
                    has_car_wash, has_shop, has_restaurant, has_atm, has_parking, has_ev_charging,
                    accessibility, opening_hours, phone, website
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                          $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27,
                          $28, $29, $30, $31, $32, $33, $34, $35, $36, $37)
                RETURNING id
                """,
                station_id, user_id, fuel_type, available, price,
                queue_size, has_limit, limit_liters, comment, source, expires_at, next_delivery_iso,
                octane_rating, cetane_number, additives, quality_score,
                queue_wait_minutes, queue_trend,
                limit_per_visit, limit_daily, limit_weekly, canister_ban,
                fuel_standard, certification, review_text, rating, photos_count,
                has_car_wash, has_shop, has_restaurant, has_atm, has_parking, has_ev_charging,
                accessibility, opening_hours, phone, website,
            )
            if user_id:
                await conn.execute(
                    "UPDATE users SET total_reports = total_reports + 1, last_active_at = NOW() WHERE id = $1",
                    user_id,
                )
            return row["id"]


async def stale_old_reports(source: str, older_than_hours: int = 1) -> int:
    """Удаляет старые отчёты от конкретного источника.
    
    Вызывается перед началом нового цикла парсинга, чтобы станции,
    которые НЕ появились в новых данных, не оставались 'available' 
    со старыми отчётоми.
    Пользовательские отчёты (source='user') НЕ удаляются — они живут
    пока не появится противоречащие данные.
    Возвращает количество удалённых записей.
    """
    if source == "user":
        return 0  # never delete user reports
    if USE_SQLITE:
        cursor = await _db.execute(
            """DELETE FROM reports 
               WHERE source = ? 
               AND created_at < datetime('now', ? || ' hours')""",
            (source, f"-{older_than_hours}"),
        )
        deleted = cursor.rowcount
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            result = await conn.execute(
                """DELETE FROM reports 
                   WHERE source = $1 
                   AND created_at < NOW() - ($2 || ' hours')::interval""",
                source, str(older_than_hours),
            )
            # result is like "DELETE 123"
            deleted = int(result.split()[-1]) if result and "DELETE" in result else 0
    if deleted:
        logger.info("stale_old_reports(%s): удалено %d старых отчётов", source, deleted)
    return deleted


async def add_subscription(
    user_id: int,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: int = 5,
    fuel_type: str | None = None,
    station_id: int | None = None,
) -> int:
    """Создаёт подписку: либо гео (lat/lon), либо на конкретную АЗС (station_id)."""
    fuel = fuel_type or "92"
    if USE_SQLITE:
        async with _db.execute(
            """INSERT INTO subscriptions
                (user_id, station_id, fuel_type, radius_km, center_lat, center_lon)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, station_id, fuel, radius_km, lat, lon),
        ) as cur:
            sub_id = cur.lastrowid
        await _db.commit()
        return sub_id
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO subscriptions
                    (user_id, station_id, fuel_type, radius_km, center_lat, center_lon)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id""",
                user_id, station_id, fuel, radius_km, lat, lon,
            )
            return row["id"]


async def find_stations_by_name(query: str, limit: int = 5, priority_city: str | None = None) -> list:
    """Ищет АЗС по имени, оператору, городу или адресу.

    Разбивает запрос на слова. Каждое слово должно совпасть хотя бы с одним полем
    (name, operator, city, address). Все слова должны совпасть одновременно (AND).

    priority_city: город пользователя — АЗС из этого города показываются первыми.
    """
    words = [w.strip() for w in query.split() if w.strip()]
    if not words:
        return []

    if USE_SQLITE:
        # Каждое слово — условие AND. Внутри слова — OR по полям.
        word_conditions = []
        word_params = []
        for w in words:
            like = f"%{w.lower()}%"
            word_conditions.append(
                "(py_lower(s.name) LIKE ? OR py_lower(s.operator) LIKE ?"
                " OR py_lower(s.city) LIKE ? OR py_lower(s.address) LIKE ?)"
            )
            word_params.extend([like, like, like, like])

        where_words = " AND ".join(word_conditions)

        city_priority_expr = ""
        city_priority_params = []
        if priority_city:
            city_priority_expr = "CASE WHEN py_lower(s.city) LIKE ? THEN 0 ELSE 1 END,"
            city_priority_params = [f"%{priority_city.lower()}%"]

        # Релевантность: точное совпадение имени > оператора > адреса
        # Берём первое слово для оценки релевантности
        first_like = f"%{words[0].lower()}%"
        sql = f"""
            SELECT s.id, s.name, s.operator, s.city, s.address, s.lat, s.lon, s.is_verified
            FROM stations s
            WHERE s.is_active = 1
              AND {where_words}
            ORDER BY
                {city_priority_expr}
                CASE WHEN py_lower(s.name) LIKE ? THEN 0
                     WHEN py_lower(s.operator) LIKE ? THEN 1
                     WHEN py_lower(s.address) LIKE ? THEN 2
                     ELSE 3 END,
                s.operator,
                s.name
            LIMIT ?
        """
        params = city_priority_params + word_params + [first_like, first_like, first_like, limit]
        async with _db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        # PostgreSQL: каждое слово — AND, внутри — ILIKE по полям
        word_clauses = []
        params = []
        for w in words:
            idx = len(params) + 1
            word_clauses.append(
                f"(s.name ILIKE ${idx} OR s.operator ILIKE ${idx}"
                f" OR s.city ILIKE ${idx} OR s.address ILIKE ${idx})"
            )
            params.append(f"%{w}%")

        where_words = " AND ".join(word_clauses)

        city_order = ""
        if priority_city:
            city_idx = len(params) + 1
            city_order = f"CASE WHEN LOWER(s.city) LIKE ${city_idx} THEN 0 ELSE 1 END,"
            params.append(f"%{priority_city.lower()}%")

        # Релевантность — используем первое слово
        params.append(words[0])  # для $first_idx
        first_idx = len(params)
        params.append(limit)     # для $limit_idx
        limit_idx = len(params)
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT s.id, s.name, s.operator, s.city, s.address, s.lat, s.lon, s.is_verified
                FROM stations s
                WHERE s.is_active = TRUE
                  AND {where_words}
                ORDER BY
                    {city_order}
                    CASE WHEN s.name ILIKE ${first_idx} THEN 0
                         WHEN s.operator ILIKE ${first_idx} THEN 1
                         WHEN s.address ILIKE ${first_idx} THEN 2
                         ELSE 3 END,
                    s.operator NULLS LAST,
                    s.name
                LIMIT ${limit_idx}
                """,
                *params,
            )
        return [dict(r) for r in rows]


async def find_stations_by_address(query: str, limit: int = 10) -> list:
    """Ищет АЗС по адресу (название + улица).

    Разбивает запрос на слова и ищет каждое слово отдельно:
      - «Газпром Минская» → operator/name содержит "Газпром" И address/city содержит "Минская"
      - «Лукойл Мира 42» → operator/name содержит "Лукойл" И address содержит "Мира 42"
    """
    words = [w.strip() for w in query.split() if w.strip()]
    if not words:
        return []

    if USE_SQLITE:
        # Каждое слово должно совпасть хотя бы с одним полем
        word_conditions = []
        params = []
        for w in words:
            like = f"%{w.lower()}%"
            word_conditions.append(
                "(py_lower(name) LIKE ? OR py_lower(operator) LIKE ?"
                " OR py_lower(address) LIKE ? OR py_lower(city) LIKE ?)"
            )
            params.extend([like, like, like, like])

        where = " AND ".join(word_conditions)
        sql = f"""
            SELECT id, name, operator, city, address, lat, lon, is_verified
            FROM stations
            WHERE is_active = 1 AND {where}
            ORDER BY
                CASE WHEN py_lower(name) LIKE ? THEN 0 ELSE 1 END,
                CASE WHEN py_lower(address) LIKE ? THEN 0 ELSE 1 END,
                operator, name
            LIMIT ?
        """
        # Для сортировки — ищем совпадение по первому слову
        first_like = f"%{words[0].lower()}%"
        params.extend([first_like, first_like, limit])
        async with _db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        # PostgreSQL: каждое слово в отдельном ILIKE
        word_clauses = []
        params = []
        idx = 1
        for w in words:
            word_clauses.append(
                f"(name ILIKE ${idx} OR operator ILIKE ${idx}"
                f" OR address ILIKE ${idx} OR city ILIKE ${idx})"
            )
            params.append(f"%{w}%")
            idx += 1

        where = " AND ".join(word_clauses)
        params.append(f"%{words[0]}%")  # for sorting
        params.append(f"%{words[0]}%")  # for sorting
        params.append(limit)

        async with _db.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, name, operator, city, address, lat, lon, is_verified
                FROM stations
                WHERE is_active = TRUE AND {where}
                ORDER BY
                    CASE WHEN name ILIKE ${idx} THEN 0 ELSE 1 END,
                    CASE WHEN address ILIKE ${idx+1} THEN 0 ELSE 1 END,
                    operator NULLS LAST, name
                LIMIT ${idx+2}
                """,
                *params,
            )
        return [dict(r) for r in rows]


async def add_review(
    station_id: int,
    user_id: int,
    fuel_type: str,
    rating: int,
    comment: str | None = None,
) -> int:
    """Добавляет отзыв о качестве бензина на АЗС.

    rating: 0-5 звёзд (0 = ужасно, 5 = отлично).
    """
    if rating < 0 or rating > 5:
        raise ValueError("Rating must be 0-5")

    if USE_SQLITE:
        async with _db.execute(
            """INSERT INTO reviews (station_id, user_id, fuel_type, rating, comment)
               VALUES (?, ?, ?, ?, ?)""",
            (station_id, user_id, fuel_type, rating, comment),
        ) as cur:
            review_id = cur.lastrowid
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO reviews (station_id, user_id, fuel_type, rating, comment)
                   VALUES ($1, $2, $3, $4, $5)
                   RETURNING id""",
                station_id, user_id, fuel_type, rating, comment,
            )
            review_id = row["id"]
    return review_id


async def get_station_rating(station_id: int) -> dict:
    """Возвращает рейтинг АЗС на основе отзывов.

    Возвращает: {avg_rating, total_reviews, by_fuel: {fuel: avg}}
    """
    if USE_SQLITE:
        async with _db.execute(
            """SELECT fuel_type, AVG(rating) as avg_rating, COUNT(*) as cnt
               FROM reviews
               WHERE station_id = ?
               GROUP BY fuel_type""",
            (station_id,),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT fuel_type, AVG(rating) as avg_rating, COUNT(*) as cnt
                   FROM reviews
                   WHERE station_id = $1
                   GROUP BY fuel_type""",
                station_id,
            )

    by_fuel = {}
    total_reviews = 0
    total_sum = 0.0
    for row in rows:
        fuel = row["fuel_type"]
        avg = float(row["avg_rating"])
        cnt = row["cnt"]
        by_fuel[fuel] = {"avg": round(avg, 1), "count": cnt}
        total_reviews += cnt
        total_sum += avg * cnt

    avg_rating = round(total_sum / total_reviews, 1) if total_reviews > 0 else 0.0
    return {
        "avg_rating": avg_rating,
        "total_reviews": total_reviews,
        "by_fuel": by_fuel,
    }


async def get_station_recent_reviews(station_id: int, limit: int = 5) -> list:
    """Возвращает последние отзывы об АЗС."""
    if USE_SQLITE:
        async with _db.execute(
            """SELECT r.rating, r.fuel_type, r.comment, r.created_at,
                      u.username, u.first_name
               FROM reviews r
               LEFT JOIN users u ON r.user_id = u.id
               WHERE r.station_id = ?
               ORDER BY r.created_at DESC
               LIMIT ?""",
            (station_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT r.rating, r.fuel_type, r.comment, r.created_at,
                          u.username, u.first_name
                   FROM reviews r
                   LEFT JOIN users u ON r.user_id = u.id
                   WHERE r.station_id = $1
                   ORDER BY r.created_at DESC
                   LIMIT $2""",
                station_id, limit,
            )
    return [dict(r) for r in rows]


async def get_station_current_status(station_id: int) -> list:
    """Возвращает текущий статус АЗС по всем видам топлива (свежие < 24ч).

    available: True / False / None ("кончается")
    next_delivery_at: datetime или None — прогноз следующего завоза.
    """
    if USE_SQLITE:
        async with _db.execute(
            """SELECT fuel_type, available, price, queue_size, has_limit, limit_liters, canister_ban,
                      limit_per_visit, limit_daily, limit_weekly,
                      confidence, created_at, next_delivery_at, source
               FROM reports
               WHERE station_id = ?
                 AND (
                   (source != 'user' AND created_at > datetime('now', '-2 hours'))
                   OR
                   (source = 'user' AND created_at > datetime('now', '-7 days'))
                 )
               ORDER BY fuel_type, 
                 CASE WHEN source = 'user' THEN 0 ELSE 1 END,
                 confidence DESC, created_at DESC""",
            (station_id,)
        ) as cur:
            rows = await cur.fetchall()
        # Возвращаем ВСЕ отчёты (format_station_card сам группирует и выбирает лучший)
        result = []
        for row in rows:
            r = dict(row)
            if r.get("available") == 1:
                r["available"] = True
            elif r.get("available") == 0:
                r["available"] = False
            elif r.get("available") == 2:
                r["available"] = None
            nd = r.get("next_delivery_at")
            if nd and isinstance(nd, str):
                try:
                    r["next_delivery_at"] = datetime.fromisoformat(nd)
                except ValueError:
                    r["next_delivery_at"] = None
            result.append(r)
        return result
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    fuel_type, available, price, queue_size, has_limit,
                    limit_liters, canister_ban,
                    limit_per_visit, limit_daily, limit_weekly,
                    confidence, created_at,
                    next_delivery_at, source
                FROM reports
                WHERE station_id = $1
                  AND (
                    (source != 'user' AND created_at > NOW() - INTERVAL '2 hours')
                    OR
                    (source = 'user' AND created_at > NOW() - INTERVAL '7 days')
                  )
                ORDER BY fuel_type, 
                  CASE WHEN source = 'user' THEN 0 ELSE 1 END,
                  confidence DESC, created_at DESC
                """,
                station_id,
            )
        return [dict(r) for r in rows]


async def get_stations_with_statuses(stations: list) -> list:
    """Bulk-получение статусов для списка АЗС одним запросом (избегаем N+1).

    Возвращает тот же список stations, но с добавленным полем 'statuses' и 'has_data'.
    Возвращает ВСЕ отчёты за 24 часа по каждой АЗС (не только последний),
    чтобы показывать данные из разных источников (fuelprice ✅ + gdebenz ❌).
    """
    if not stations:
        return stations

    station_ids = [s["id"] for s in stations]
    placeholders = ",".join("?" for _ in station_ids)

    if USE_SQLITE:
        async with _db.execute(
            f"""SELECT station_id, fuel_type, available, price, queue_size,
                       has_limit, limit_liters, canister_ban,
                       limit_per_visit, limit_daily, limit_weekly,
                       confidence, created_at, next_delivery_at, source
                FROM reports
                WHERE station_id IN ({placeholders})
                  AND (
                    (source != 'user' AND created_at > datetime('now', '-2 hours'))
                    OR
                    (source = 'user' AND created_at > datetime('now', '-7 days'))
                  )
                ORDER BY station_id, fuel_type, 
                  CASE WHEN source = 'user' THEN 0 ELSE 1 END,
                  confidence DESC, created_at DESC""",
            station_ids,
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT station_id, fuel_type, available, price, queue_size,
                        has_limit, limit_liters, canister_ban,
                        limit_per_visit, limit_daily, limit_weekly,
                        confidence,
                        created_at, next_delivery_at, source
                    FROM reports
                    WHERE station_id = ANY($1)
                      AND (
                        (source != 'user' AND created_at > NOW() - INTERVAL '2 hours')
                        OR
                        (source = 'user' AND created_at > NOW() - INTERVAL '7 days')
                      )
                    ORDER BY station_id, fuel_type, 
                      CASE WHEN source = 'user' THEN 0 ELSE 1 END,
                      confidence DESC, created_at DESC""",
                station_ids,
            )

    by_station: dict[int, list] = {}
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else r
        if USE_SQLITE:
            if d.get("available") == 1:
                d["available"] = True
            elif d.get("available") == 0:
                d["available"] = False
            elif d.get("available") == 2:
                d["available"] = None
            nd = d.get("next_delivery_at")
            if nd and isinstance(nd, str):
                try:
                    d["next_delivery_at"] = datetime.fromisoformat(nd)
                except ValueError:
                    d["next_delivery_at"] = None
        sid = d["station_id"]
        by_station.setdefault(sid, []).append(d)

    for s in stations:
        sid = s["id"]
        statuses = by_station.get(sid, [])
        s["statuses"] = statuses
        s["has_data"] = len(statuses) > 0

    return stations


# === Аналитика ===
async def log_event(user_id: int | None, event_type: str, payload: dict | None = None):
    """Логирует событие. user_id — это internal id из users.id. Если None, не пишет user_id."""
    if USE_SQLITE:
        await _db.execute(
            "INSERT INTO events (user_id, event_type, payload) VALUES (?, ?, ?)",
            (user_id, event_type, json.dumps(payload or {})),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "INSERT INTO events (user_id, event_type, payload) VALUES ($1, $2, $3::jsonb)",
                user_id, event_type, json.dumps(payload or {}),
            )


# === Приоритизация источников ===
# Чем выше priority, тем больше доверия к источнику.
# При конфликте цен берётся источник с max(priority × confidence).
# НОВОЕ: recency_bonus — доп. балл за свежесть (< 1ч = +0.15, < 4ч = +0.10, < 12ч = +0.05)
SOURCE_PRIORITY = {
    # === НАЛИЧИЕ (самые точные для наличия) ===
    "user":                1.00,  # отчёт водителя на АЗС — самый доверенный
    "owner":               1.00,  # владелец АЗС
    "benzin_status_tech":  0.95,  # crowdsourced наличие (мини-аппа)
    "benzin_status_bot":   0.90,  # интерактивный бот
    "gdebenzru":           0.88,  # ГдеБЕНЗ карта наличия (27K+ АЗС)
    "agdebenzinmlt":       0.85,  # А где бензин? (9.5K подписчиков)
    "rusfuel":             0.82,  # АЗС России
    # === РЕГИОНАЛЬНЫЕ ЧАТЫ НАЛИЧИЯ (очень точные!) ===
    "gde_zalit":           0.87,  # Все "Где залить?" чаты (краудсорсинг)
    "benzin_kholod":       0.84,  # Все "Бензин холодный" чаты
    "benzin_est_chat":     0.86,  # Бензин есть чат
    "toplivo_est_chat":    0.86,  # Топливо есть чат
    "azs_status_chat":     0.85,  # Статус АЗС чат
    "fuel_alert_chat":     0.84,  # Топливные оповещения чат
    "benzin_check_chat":   0.83,  # Проверка бензина чат
    "gde_benz_chat":       0.85,  # Где бензин чат
    # === ТЕЛЕГРАМ (наличие + цены) ===
    "tg":                  0.80,  # Telegram-каналы (общий)
    # === ОБЩЕРОССИЙСКИЕ КАНАЛЫ ===
    "benzin_price":        0.80,  # Ежедневные цены
    "benzup_ru":           0.78,  # BenzUp.ru
    "fuelprice_ru":        0.77,  # FuelPrice.ru
    "azs_prices_omt_bot":  0.76,  # OMT (18K+ АЗС)
    "benzoopt":            0.75,  # Биржевые цены
    "Neftexpert":          0.74,  # Нефтяной рынок
    # === ВНЕШНИЕ API ===
    "benzin_status_tech":  0.95,  # benzin-status.tech
    "yandex":              0.80,  # Яндекс.Заправки
    # === ОФИЦИАЛЬНЫЕ СЕТИ АЗС ===
    "toplivo_rosneft":     0.73,  # Роснефть
    "toplivo_lukoil":      0.73,  # Лукойл
    "toplivo_gpn":         0.73,  # Газпромнефть
    "azstatneft":          0.72,  # Татнефть
    "azs_bashneft":        0.72,  # Башнефть
    "azs_surgut":          0.71,  # Сургутнефтегаз
    "azs_taif":            0.71,  # ТАИФ
    "azs_tneft":           0.71,  # Тнефтепродукт
    "azs_neftmagistral":   0.70,  # Нефтьмагистраль
    # === ОСТАЛЬНЫЕ ===
    "okolo_AZS":           0.60,
    "toplivo_gsm_ru":      0.60,
    "toplivo_chat":        0.60,
    "azsdiller":           0.60,
    "azs_price":           0.60,
    "russiabase_ru":       0.60,
    "gde_benz_rf":         0.60,
    "toplivo_rf":          0.60,
    "toplivo_poisk":       0.60,
    "pro_zapravki":        0.60,
    "benzinmap":           0.60,
    "mapfuel":             0.60,
    "toplivo_online":      0.60,
    "benzinru":            0.60,
    "fuel_monitoring":     0.60,
    "gas_station_prices":  0.60,
    "shopot_nefti":        0.55,
    "benzinstatus":        0.55,
    "lukoil":              0.75,  # сайт сети
    "gazprom":             0.75,
    "rosneft":             0.75,
    "tatneft":             0.75,
    "bashneft":            0.75,
    "2gis":                0.65,  # 2ГИС
    "osm":                 0.30,  # OSM (нет цен, только мета)
    "default":             0.50,
}

# Бонус за свежесть: чем свежее, тем выше приоритет
RECENCY_BONUS = [
    (1,   0.15),  # < 1 часа:  +0.15
    (4,   0.10),  # < 4 часов: +0.10
    (12,  0.05),  # < 12 часов: +0.05
    (24,  0.00),  # < 24 часов: без бонуса
    (999, -0.10), # > 24 часов: штраф -0.10
]


def get_source_priority(source: str) -> float:
    return SOURCE_PRIORITY.get(source, SOURCE_PRIORITY["default"])


def get_recency_bonus(age_hours: float) -> float:
    """Бонус за свежесть данных: чем свежее, тем выше."""
    for max_hours, bonus in RECENCY_BONUS:
        if age_hours < max_hours:
            return bonus
    return -0.10


# === Confidence модель ===
# Чем больше подтверждений и свежее данные — тем выше уверенность.
def calculate_confidence(
    source: str,
    age_hours: float,
    agreement_count: int = 1,
    base_confidence: float = 0.7,
) -> float:
    """Рассчитывает confidence (0..1) для отчёта.

    source: источник данных
    age_hours: сколько часов назад
    agreement_count: сколько других источников согласны с этой ценой
    base_confidence: базовая уверенность источника

    Улучшено: добавлен recency_bonus за свежесть данных.
    """
    # Свежесть: экспоненциальный спад
    freshness = max(0.1, 1.0 - (age_hours / 24.0) ** 0.5)
    # Бонус за свежесть (отдельно от freshness decay)
    recency = get_recency_bonus(age_hours)
    # Согласие: +0.2 за каждый согласный источник
    agreement = min(0.4, agreement_count * 0.2)
    # Базовый confidence от источника
    base = base_confidence * get_source_priority(source)
    return min(1.0, base * freshness + agreement + recency)


async def get_station_analytics(station_id: int, days: int = 30) -> dict:
    """Аналитика для владельца АЗС: просмотры, отчёты, подписчики, цены."""
    result = {
        "station_id": station_id,
        "period_days": days,
        "views": 0,
        "reports_30d": 0,
        "reports_by_fuel": {},
        "subscribers": 0,
        "avg_price": None,
        "last_price": None,
        "last_report_at": None,
        "views_chart": [],  # [{date, count}]
    }
    if USE_SQLITE:
        # Просмотры
        async with _db.execute(
            """SELECT DATE(created_at) as d, COUNT(*) as c FROM events
               WHERE event_type = 'station_viewed'
                 AND json_extract(payload, '$.station_id') = ?
                 AND created_at > datetime('now', ?)
               GROUP BY d ORDER BY d""",
            (station_id, f"-{days} days"),
        ) as cur:
            for r in await cur.fetchall():
                result["views_chart"].append({"date": r["d"], "count": r["c"]})
            result["views"] = sum(v["count"] for v in result["views_chart"])
        # Отчёты
        async with _db.execute(
            """SELECT fuel_type, COUNT(*) as c, AVG(price) as avg_p, MAX(price) as max_p, MIN(price) as min_p
               FROM reports
               WHERE station_id = ? AND created_at > datetime('now', ?)
               GROUP BY fuel_type""",
            (station_id, f"-{days} days"),
        ) as cur:
            total_avg = []
            for r in await cur.fetchall():
                result["reports_by_fuel"][r["fuel_type"]] = {
                    "count": r["c"],
                    "avg_price": float(r["avg_p"]) if r["avg_p"] else None,
                }
                if r["avg_p"]:
                    total_avg.append(float(r["avg_p"]))
            result["reports_30d"] = sum(v["count"] for v in result["reports_by_fuel"].values())
            result["avg_price"] = sum(total_avg) / len(total_avg) if total_avg else None
        # Подписчики
        async with _db.execute(
            "SELECT COUNT(*) as c FROM subscriptions WHERE station_id = ? AND is_active = 1",
            (station_id,),
        ) as cur:
            r = await cur.fetchone()
            result["subscribers"] = r["c"] if r else 0
        # Последний отчёт
        async with _db.execute(
            """SELECT fuel_type, available, price, created_at FROM reports
               WHERE station_id = ? ORDER BY created_at DESC LIMIT 1""",
            (station_id,),
        ) as cur:
            last = await cur.fetchone()
        if last:
            result["last_report_at"] = last["created_at"]
            result["last_price"] = float(last["price"]) if last["price"] else None
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT DATE(created_at) as d, COUNT(*) as c FROM events
                   WHERE event_type = 'station_viewed'
                     AND (payload->>'station_id')::int = $1
                     AND created_at > NOW() - ($2 || ' days')::interval
                   GROUP BY d ORDER BY d""",
                station_id, str(days),
            )
            for r in rows:
                result["views_chart"].append({"date": r["d"].isoformat(), "count": r["c"]})
            result["views"] = sum(v["count"] for v in result["views_chart"])
            rows = await conn.fetch(
                """SELECT fuel_type, COUNT(*) as c, AVG(price) as avg_p
                   FROM reports
                   WHERE station_id = $1 AND created_at > NOW() - ($2 || ' days')::interval
                   GROUP BY fuel_type""",
                station_id, str(days),
            )
            total_avg = []
            for r in rows:
                result["reports_by_fuel"][r["fuel_type"]] = {
                    "count": r["c"],
                    "avg_price": float(r["avg_p"]) if r["avg_p"] else None,
                }
                if r["avg_p"]:
                    total_avg.append(float(r["avg_p"]))
            result["reports_30d"] = sum(v["count"] for v in result["reports_by_fuel"].values())
            result["avg_price"] = sum(total_avg) / len(total_avg) if total_avg else None
            row = await conn.fetchrow(
                "SELECT COUNT(*) as c FROM subscriptions WHERE station_id = $1 AND is_active = TRUE",
                station_id,
            )
            result["subscribers"] = row["c"] if row else 0
            row = await conn.fetchrow(
                """SELECT fuel_type, available, price, created_at FROM reports
                   WHERE station_id = $1 ORDER BY created_at DESC LIMIT 1""",
                station_id,
            )
            if row:
                result["last_report_at"] = row["created_at"].isoformat()
                result["last_price"] = float(row["price"]) if row["price"] else None
    return result


async def get_best_price_for_station(
    station_id: int, fuel_type: str
) -> dict | None:
    """Возвращает лучшую цену для (station, fuel) по приоритету × свежести.

    Учитывает все источники, отдаёт отчёт с максимальным weighted_score.
    """
    if USE_SQLITE:
        cur = await _db.execute(
            """SELECT id, fuel_type, available, price, source, confidence, created_at
               FROM reports
               WHERE station_id = ? AND fuel_type = ? AND price IS NOT NULL
                 AND created_at > datetime('now', '-7 days')
               ORDER BY created_at DESC LIMIT 20""",
            (station_id, fuel_type),
        )
        rows = await cur.fetchall()
        rows = [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, fuel_type, available, price, source, confidence, created_at
                   FROM reports
                   WHERE station_id = $1 AND fuel_type = $2 AND price IS NOT NULL
                     AND created_at > NOW() - INTERVAL '7 days'
                   ORDER BY created_at DESC LIMIT 20""",
                station_id, fuel_type,
            )
            rows = [dict(r) for r in rows]

    if not rows:
        return None

    # Для каждого отчёта считаем score
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    scored = []
    for r in rows:
        created = r["created_at"]
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (now - created).total_seconds() / 3600.0
        source = r.get("source") or "default"
        # Считаем сколько других отчётов согласны (в пределах ±2₽)
        agreement = sum(
            1 for other in rows
            if other["id"] != r["id"]
            and other.get("price") is not None
            and abs(float(other["price"]) - float(r["price"])) <= 2.0
        )
        score = calculate_confidence(source, age_h, agreement)
        r["weighted_score"] = score
        scored.append(r)

    # Лучший по score
    scored.sort(key=lambda x: x["weighted_score"], reverse=True)
    return scored[0]


async def get_all_prices_for_station(station_id: int) -> dict:
    """Возвращает все цены по всем источникам для станции.

    Формат: {fuel_type: [{source, price, age_hours, confidence, weighted_score, is_best}]}
    """
    if USE_SQLITE:
        cur = await _db.execute(
            """SELECT id, fuel_type, available, price, source, confidence, created_at
               FROM reports
               WHERE station_id = ? AND price IS NOT NULL
                 AND created_at > datetime('now', '-7 days')
               ORDER BY fuel_type, created_at DESC""",
            (station_id,),
        )
        rows = await cur.fetchall()
        rows = [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, fuel_type, available, price, source, confidence, created_at
                   FROM reports
                   WHERE station_id = $1 AND price IS NOT NULL
                     AND created_at > NOW() - INTERVAL '7 days'
                   ORDER BY fuel_type, created_at DESC""",
                station_id,
            )
            rows = [dict(r) for r in rows]

    from datetime import datetime, timezone
    from decimal import Decimal
    now = datetime.now(timezone.utc)
    by_fuel: dict[str, list] = {}
    for r in rows:
        created = r["created_at"]
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_h = (now - created).total_seconds() / 3600.0
        r["age_hours"] = round(age_h, 1)
        # Конвертируем Decimal → float (asyncpg для NUMERIC)
        if r.get("price") is not None:
            r["price"] = float(r["price"]) if not isinstance(r["price"], Decimal) else float(r["price"])
        else:
            r["price"] = None
        r["confidence"] = float(r["confidence"]) if r.get("confidence") and not isinstance(r["confidence"], Decimal) else (float(r["confidence"]) if isinstance(r["confidence"], Decimal) else 0.5)
        source = r.get("source") or "default"
        r["source_priority"] = get_source_priority(source)
        # Считаем agreement
        fuel = r["fuel_type"]
        # Конвертируем ВСЕ цены других в float чтобы избежать Decimal/float mix
        others = []
        for x in rows:
            if x["fuel_type"] != fuel or x["id"] == r["id"] or not x.get("price"):
                continue
            if isinstance(x["price"], Decimal):
                x["price"] = float(x["price"])
            others.append(x)
        r["agreement"] = sum(1 for x in others if abs(x["price"] - r["price"]) <= 2.0) if r["price"] else 0
        r["weighted_score"] = round(
            calculate_confidence(source, age_h, r["agreement"]), 3
        )
        # Конвертируем datetime в ISO для JSON
        r["created_at"] = created.isoformat()
        by_fuel.setdefault(fuel, []).append(r)

    # Помечаем лучший
    for fuel, items in by_fuel.items():
        if items:
            items.sort(key=lambda x: x["weighted_score"], reverse=True)
            items[0]["is_best"] = True
            for it in items[1:]:
                it["is_best"] = False

    return by_fuel


async def get_stats() -> dict:
    """Глобальная статистика."""
    if USE_SQLITE:
        stats = {}
        async with _db.execute("SELECT COUNT(*) as c FROM stations WHERE is_active = 1") as cur:
            stats["stations_count"] = (await cur.fetchone())[0]
        async with _db.execute("SELECT COUNT(*) as c FROM users") as cur:
            stats["users_count"] = (await cur.fetchone())[0]
        async with _db.execute("SELECT COUNT(*) as c FROM reports WHERE created_at > datetime('now', '-1 day')") as cur:
            stats["reports_24h"] = (await cur.fetchone())[0]
        async with _db.execute("SELECT COUNT(DISTINCT city) as c FROM stations WHERE city IS NOT NULL") as cur:
            stats["cities_count"] = (await cur.fetchone())[0]
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    (SELECT COUNT(*) FROM stations WHERE is_active) AS stations_count,
                    (SELECT COUNT(*) FROM users) AS users_count,
                    (SELECT COUNT(*) FROM reports WHERE created_at > NOW() - INTERVAL '24 hours') AS reports_24h,
                    (SELECT COUNT(DISTINCT city) FROM stations WHERE city IS NOT NULL) AS cities_count
            """)
        return dict(row)


# === Referral Balance & Commission System ===

async def get_referrer_for_user(user_id: int) -> int | None:
    """Returns referrer_user_id for a referred user, or None."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT referrer_user_id FROM referral_relationships WHERE referred_user_id = ?",
            user_id, one=True,
        )
    else:
        row = await _fetch(
            "SELECT referrer_user_id FROM referral_relationships WHERE referred_user_id = $1",
            user_id, one=True,
        )
    if not row:
        return None
    return row["referrer_user_id"] if isinstance(row, dict) else row[0]


async def record_referral_commission(user_id: int, payment_id: int, payment_amount: int) -> bool:
    """Records 50% commission for the referrer of this user. Only Elite/Founder referrers earn commission. Returns True if commission was recorded."""
    referrer_id = await get_referrer_for_user(user_id)
    if not referrer_id:
        return False

    # Only Elite/Founder referrers earn commission
    referrer_sub = await get_user_premium(referrer_id)
    referrer_tier = (referrer_sub.get("tier") or "").lower() if referrer_sub else ""
    is_founder = await is_founder(referrer_id)
    if referrer_tier not in ("elite",) and not is_founder:
        return False

    commission = round(payment_amount * 0.50)
    if commission <= 0:
        return False

    if USE_SQLITE:
        await _execute(
            """INSERT INTO referral_earnings (referrer_user_id, referred_user_id, payment_id, payment_amount, commission_percent, commission_amount)
               VALUES (?, ?, ?, ?, 50, ?)""",
            referrer_id, user_id, payment_id, payment_amount, commission,
        )
        await _execute(
            """INSERT INTO referral_balances (user_id, total_earned, balance, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                   total_earned = total_earned + excluded.total_earned,
                   balance = balance + excluded.balance,
                   updated_at = datetime('now')""",
            referrer_id, commission, commission,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                """INSERT INTO referral_earnings (referrer_user_id, referred_user_id, payment_id, payment_amount, commission_percent, commission_amount)
                   VALUES ($1, $2, $3, $4, 50, $5)""",
                referrer_id, user_id, payment_id, payment_amount, commission,
            )
            await conn.execute(
                """INSERT INTO referral_balances (user_id, total_earned, balance, updated_at)
                   VALUES ($1, $2, $3, NOW())
                   ON CONFLICT(user_id) DO UPDATE SET
                       total_earned = referral_balances.total_earned + EXCLUDED.total_earned,
                       balance = referral_balances.balance + EXCLUDED.balance,
                       updated_at = NOW()""",
                referrer_id, commission, commission,
            )
    return True


async def get_referral_balance(user_id: int) -> dict:
    """Returns referral balance info for the user."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT * FROM referral_balances WHERE user_id = ?",
            user_id, one=True,
        )
    else:
        row = await _fetch(
            "SELECT * FROM referral_balances WHERE user_id = $1",
            user_id, one=True,
        )
    if not row:
        return {"total_earned": 0, "total_withdrawn": 0, "balance": 0}
    d = dict(row) if isinstance(row, dict) else {
        "total_earned": row[1], "total_withdrawn": row[2], "balance": row[3],
    }
    return d


async def get_referral_earnings(user_id: int, limit: int = 50) -> list[dict]:
    """Returns referral earnings history for the user."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT re.*, u.first_name, u.username
               FROM referral_earnings re
               LEFT JOIN users u ON re.referred_user_id = u.id
               WHERE re.referrer_user_id = ?
               ORDER BY re.created_at DESC LIMIT ?""",
            user_id, limit,
        )
    else:
        rows = await _fetch(
            """SELECT re.*, u.first_name, u.username
               FROM referral_earnings re
               LEFT JOIN users u ON re.referred_user_id = u.id
               WHERE re.referrer_user_id = $1
               ORDER BY re.created_at DESC LIMIT $2""",
            user_id, limit,
        )
    return [dict(r) if isinstance(r, dict) else r for r in (rows or [])]


async def get_referred_users_list(referrer_user_id: int) -> list[dict]:
    """Returns list of users this person referred with earnings."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT rr.referred_user_id, rr.created_at, u.first_name, u.username,
                      COALESCE(SUM(re.commission_amount), 0) as total_commission,
                      COUNT(re.id) as payment_count
               FROM referral_relationships rr
               LEFT JOIN users u ON rr.referred_user_id = u.id
               LEFT JOIN referral_earnings re ON re.referred_user_id = rr.referred_user_id
               WHERE rr.referrer_user_id = ?
               GROUP BY rr.id
               ORDER BY rr.created_at DESC""",
            referrer_user_id,
        )
    else:
        rows = await _fetch(
            """SELECT rr.referred_user_id, rr.created_at, u.first_name, u.username,
                      COALESCE(SUM(re.commission_amount), 0) as total_commission,
                      COUNT(re.id) as payment_count
               FROM referral_relationships rr
               LEFT JOIN users u ON rr.referred_user_id = u.id
               LEFT JOIN referral_earnings re ON re.referred_user_id = rr.referred_user_id
               WHERE rr.referrer_user_id = $1
               GROUP BY rr.id, rr.referred_user_id, rr.created_at, u.first_name, u.username
               ORDER BY rr.created_at DESC""",
            referrer_user_id,
        )
    return [dict(r) if isinstance(r, dict) else r for r in (rows or [])]


async def request_withdrawal(user_id: int, amount: int, method: str, details: str) -> dict:
    """Creates a withdrawal request."""
    balance = await get_referral_balance(user_id)
    if balance["balance"] < amount:
        return {"ok": False, "error": "insufficient_balance"}
    if amount < 100:
        return {"ok": False, "error": "minimum_withdrawal_100"}

    if USE_SQLITE:
        await _execute(
            "UPDATE referral_balances SET balance = balance - ?, updated_at = datetime('now') WHERE user_id = ?",
            amount, user_id,
        )
        await _execute(
            """INSERT INTO referral_withdrawals (user_id, amount, method, details)
               VALUES (?, ?, ?, ?)""",
            user_id, amount, method, details,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE referral_balances SET balance = balance - $1, updated_at = NOW() WHERE user_id = $2",
                amount, user_id,
            )
            await conn.execute(
                """INSERT INTO referral_withdrawals (user_id, amount, method, details)
                   VALUES ($1, $2, $3, $4)""",
                user_id, amount, method, details,
            )
    return {"ok": True, "message": "Заявка на вывод создана"}


async def get_pending_withdrawals() -> list[dict]:
    """Returns pending withdrawal requests for admin."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT rw.*, u.telegram_id, u.username, u.first_name
               FROM referral_withdrawals rw
               LEFT JOIN users u ON rw.user_id = u.id
               WHERE rw.status = 'pending'
               ORDER BY rw.created_at ASC"""
        )
    else:
        rows = await _fetch(
            """SELECT rw.*, u.telegram_id, u.username, u.first_name
               FROM referral_withdrawals rw
               LEFT JOIN users u ON rw.user_id = u.id
               WHERE rw.status = 'pending'
               ORDER BY rw.created_at ASC"""
        )
    return [dict(r) if isinstance(r, dict) else r for r in (rows or [])]


async def process_withdrawal(withdrawal_id: int, status: str) -> bool:
    """Approves or rejects a withdrawal. status = 'approved' | 'rejected' | 'paid'."""
    if status not in ("approved", "rejected", "paid"):
        return False

    if USE_SQLITE:
        row = await _fetch(
            "SELECT * FROM referral_withdrawals WHERE id = ? AND status = 'pending'",
            withdrawal_id, one=True,
        )
        if not row:
            return False
        withdrawal = dict(row) if isinstance(row, dict) else {"user_id": row[1], "amount": row[2]}

        if status == "rejected":
            # Возвращаем деньги на баланс
            await _execute(
                "UPDATE referral_balances SET balance = balance + ?, updated_at = datetime('now') WHERE user_id = ?",
                withdrawal["amount"], withdrawal["user_id"],
            )

        await _execute(
            "UPDATE referral_withdrawals SET status = ?, processed_at = datetime('now') WHERE id = ?",
            status, withdrawal_id,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM referral_withdrawals WHERE id = $1 AND status = 'pending'",
                withdrawal_id,
            )
            if not row:
                return False
            withdrawal = dict(row)

            if status == "rejected":
                await conn.execute(
                    "UPDATE referral_balances SET balance = balance + $1, updated_at = NOW() WHERE user_id = $2",
                    withdrawal["amount"], withdrawal["user_id"],
                )

            await conn.execute(
                "UPDATE referral_withdrawals SET status = $1, processed_at = NOW() WHERE id = $2",
                status, withdrawal_id,
            )
    return True


async def get_user_withdrawals(user_id: int, limit: int = 20) -> list[dict]:
    """Returns withdrawal history for a user."""
    if USE_SQLITE:
        rows = await _fetch(
            "SELECT * FROM referral_withdrawals WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            user_id, limit,
        )
    else:
        rows = await _fetch(
            "SELECT * FROM referral_withdrawals WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
            user_id, limit,
        )
    return [dict(r) if isinstance(r, dict) else r for r in (rows or [])]


# === Push-уведомления ===
async def get_recent_fuel_reports(minutes: int = 5) -> list:
    """Возвращает свежие отчёты о наличии топлива (за последние N минут).

    Каждый отчёт дополнен prev_available и prev_price — предыдущим состоянием
    той же АЗС+топлива (нужно для push-сценариев "появилось" и "цена упала").
    """
    if USE_SQLITE:
        async with _db.execute(
            """SELECT r.id, r.station_id, r.fuel_type, r.available, r.queue_size, r.price,
                      s.name, s.lat, s.lon, s.city, s.address,
                      (SELECT r2.available FROM reports r2
                         WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                           AND r2.id < r.id ORDER BY r2.id DESC LIMIT 1) AS prev_available,
                      (SELECT r2.price FROM reports r2
                         WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                           AND r2.id < r.id AND r2.price IS NOT NULL
                           ORDER BY r2.id DESC LIMIT 1) AS prev_price
               FROM reports r
               JOIN stations s ON s.id = r.station_id
               WHERE r.created_at > datetime('now', ?)
                 AND r.available IN (1, 2)
                 AND s.is_active = 1
               ORDER BY r.created_at DESC""",
            (f"-{minutes} minutes",),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT r.id, r.station_id, r.fuel_type, r.available, r.queue_size, r.price,
                         s.name, s.lat, s.lon, s.city, s.address,
                         (SELECT r2.available FROM reports r2
                            WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                              AND r2.id < r.id ORDER BY r2.id DESC LIMIT 1) AS prev_available,
                         (SELECT r2.price FROM reports r2
                            WHERE r2.station_id = r.station_id AND r2.fuel_type = r.fuel_type
                              AND r2.id < r.id AND r2.price IS NOT NULL
                              ORDER BY r2.id DESC LIMIT 1) AS prev_price
                  FROM reports r
                  JOIN stations s ON s.id = r.station_id
                  WHERE r.created_at > NOW() - ($1 || ' minutes')::interval
                    AND r.available IN (TRUE, NULL)
                    AND s.is_active = TRUE
                  ORDER BY r.created_at DESC""",
                str(minutes),
            )
        return [dict(r) for r in rows]


async def get_subscribers_for_station(
    station_id: int,
    station_lat: float,
    station_lon: float,
    fuel_type: str,
    radius_km: int = 10,
) -> list:
    """Возвращает подписчиков, которых надо уведомить о наличии на АЗС.

    Возвращает [{user_id, telegram_id, distance_km, last_notified_at}].
    """
    if USE_SQLITE:
        async with _db.execute(
            """SELECT s.id AS sub_id, s.user_id, s.station_id, s.center_lat, s.center_lon,
                      s.radius_km, s.fuel_type, s.last_notified_at,
                      u.telegram_id
               FROM subscriptions s
               JOIN users u ON u.id = s.user_id
               WHERE s.is_active = 1
                 AND u.is_blocked = 0
                 AND (
                     s.station_id = ?
                     OR (s.center_lat IS NOT NULL
                         AND ABS(? - s.center_lat) < 1
                         AND ABS(? - s.center_lon) < 1)
                 )""",
            (station_id, station_lat, station_lon),
        ) as cur:
            rows = await cur.fetchall()
        results = []
        for row in rows:
            r = dict(row)
            # Точная подписка на АЗС
            if r.get("station_id") == station_id:
                r["distance_km"] = 0
                results.append(r)
                continue
            # Гео-подписка
            if r.get("center_lat") is not None and r.get("center_lon") is not None:
                d = _haversine_km(station_lat, station_lon, r["center_lat"], r["center_lon"])
                sub_radius = r.get("radius_km") or 5
                if d <= sub_radius:
                    r["distance_km"] = d
                    results.append(r)
        return results
    else:
        # Для PostgreSQL используем PostGIS или упрощённый bbox
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT s.id AS sub_id, s.user_id, s.station_id, s.center_lat, s.center_lon,
                         s.radius_km, s.fuel_type, s.last_notified_at,
                         u.telegram_id
                  FROM subscriptions s
                  JOIN users u ON u.id = s.user_id
                  WHERE s.is_active = TRUE
                    AND u.is_blocked = FALSE
                    AND (
                        s.station_id = $1
                        OR (s.center_lat IS NOT NULL
                            AND ABS($2 - s.center_lat) < 1
                            AND ABS($3 - s.center_lon) < 1)
                    )""",
                station_id, station_lat, station_lon,
            )
        results = []
        for row in rows:
            r = dict(row)
            if r.get("station_id") == station_id:
                r["distance_km"] = 0
                results.append(r)
                continue
            if r.get("center_lat") is not None and r.get("center_lon") is not None:
                d = _haversine_km(station_lat, station_lon, r["center_lat"], r["center_lon"])
                sub_radius = r.get("radius_km") or 5
                if d <= sub_radius:
                    r["distance_km"] = d
                    results.append(r)
        return results


async def mark_subscription_notified(sub_id: int) -> None:
    """Обновляет last_notified_at подписки."""
    now_iso = datetime.now().isoformat()
    if USE_SQLITE:
        await _db.execute(
            "UPDATE subscriptions SET last_notified_at = ? WHERE id = ?",
            (now_iso, sub_id),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE subscriptions SET last_notified_at = NOW() WHERE id = $1",
                sub_id,
            )


# === Owner stations ===
async def add_owner_station(
    user_id: int,
    station_id: int,
    inn: str | None = None,
    role: str = "owner",
) -> int:
    """Регистрирует пользователя как владельца/работника АЗС.

    Создаёт запись с is_verified=False и помечает user.is_owner=1 в одной транзакции.
    Бейдж Verified появится только после модерации (set_owner_station_verified).
    Возвращает -1 если пользователь уже зарегистрирован на эту АЗС.
    """
    if USE_SQLITE:
        try:
            # BEGIN ... COMMIT — одна транзакция
            await _db.execute("BEGIN")
            async with _db.execute(
                """INSERT INTO owner_stations (user_id, station_id, inn, role, is_verified)
                   VALUES (?, ?, ?, ?, 0)""",
                (user_id, station_id, inn, role),
            ) as cur:
                row_id = cur.lastrowid
            await _db.execute(
                "UPDATE users SET is_owner = 1 WHERE id = ?",
                (user_id,),
            )
            await _db.commit()
            return row_id
        except Exception as e:
            await _db.rollback()
            if "UNIQUE" in str(e):
                return -1
            raise
    async with _db.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    """INSERT INTO owner_stations (user_id, station_id, inn, role, is_verified)
                       VALUES ($1, $2, $3, $4, FALSE)
                       RETURNING id""",
                    user_id, station_id, inn, role,
                )
                await conn.execute(
                    "UPDATE users SET is_owner = TRUE WHERE id = $1",
                    user_id,
                )
                return row["id"]
            except Exception as e:
                if "unique" in str(e).lower():
                    return -1
                raise


async def get_owner_stations(user_id: int) -> list:
    """Возвращает АЗС, на которые зарегистрирован пользователь как владелец/работник."""
    if USE_SQLITE:
        async with _db.execute(
            """SELECT os.id, os.station_id, os.role, os.is_verified, os.inn,
                      s.name, s.operator, s.city, s.address, s.lat, s.lon
               FROM owner_stations os
               JOIN stations s ON s.id = os.station_id
               WHERE os.user_id = ?
               ORDER BY s.name""",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT os.id, os.station_id, os.role, os.is_verified, os.inn,
                         s.name, s.operator, s.city, s.address, s.lat, s.lon
                  FROM owner_stations os
                  JOIN stations s ON s.id = os.station_id
                  WHERE os.user_id = $1
                  ORDER BY s.name""",
                user_id,
            )
        return [dict(r) for r in rows]


async def is_owner_of_station(user_id: int, station_id: int) -> bool:
    """Проверяет, является ли пользователь владельцем/работником АЗС."""
    if USE_SQLITE:
        async with _db.execute(
            "SELECT 1 FROM owner_stations WHERE user_id = ? AND station_id = ? LIMIT 1",
            (user_id, station_id),
        ) as cur:
            return (await cur.fetchone()) is not None
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM owner_stations WHERE user_id = $1 AND station_id = $2 LIMIT 1",
                user_id, station_id,
            )
            return row is not None


async def set_owner_station_verified(owner_station_id: int, moderator_id: int | None = None) -> None:
    """Модератор одобряет заявку. Также ставит is_verified на АЗС."""
    now_iso = datetime.now().isoformat()
    # Проверяем, что moderator_id существует (если передан)
    if moderator_id is not None:
        if USE_SQLITE:
            async with _db.execute(
                "SELECT 1 FROM users WHERE id = ?", (moderator_id,)
            ) as cur:
                if (await cur.fetchone()) is None:
                    moderator_id = None
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow("SELECT 1 FROM users WHERE id = $1", moderator_id)
                if not row:
                    moderator_id = None

    if USE_SQLITE:
        async with _db.execute(
            "SELECT station_id FROM owner_stations WHERE id = ?",
            (owner_station_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return
        station_id = row[0]
        await _db.execute(
            """UPDATE owner_stations
               SET is_verified = 1, moderator_id = ?, verified_at = ?
               WHERE id = ?""",
            (moderator_id, now_iso, owner_station_id),
        )
        await _db.execute(
            "UPDATE stations SET is_verified = 1 WHERE id = ?",
            (station_id,),
        )
        await _db.commit()
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT station_id FROM owner_stations WHERE id = $1",
                owner_station_id,
            )
            if not row:
                return
            await conn.execute(
                """UPDATE owner_stations
                   SET is_verified = TRUE, moderator_id = $1, verified_at = NOW()
                   WHERE id = $2""",
                moderator_id, owner_station_id,
            )
            await conn.execute(
                "UPDATE stations SET is_verified = TRUE WHERE id = $1",
                row["station_id"],
            )


async def get_pending_owner_applications() -> list:
    """Заявки на модерацию (is_verified=0, ожидают одобрения)."""
    if USE_SQLITE:
        async with _db.execute(
            """SELECT os.id, os.user_id, os.station_id, os.inn, os.role, os.created_at,
                      u.telegram_id, u.first_name, u.username,
                      s.name AS station_name, s.city
               FROM owner_stations os
               JOIN users u ON u.id = os.user_id
               JOIN stations s ON s.id = os.station_id
               WHERE os.is_verified = 0
               ORDER BY os.created_at DESC""",
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT os.id, os.user_id, os.station_id, os.inn, os.role, os.created_at,
                         u.telegram_id, u.first_name, u.username,
                         s.name AS station_name, s.city
                  FROM owner_stations os
                  JOIN users u ON u.id = os.user_id
                  JOIN stations s ON s.id = os.station_id
                  WHERE os.is_verified = FALSE
                  ORDER BY os.created_at DESC""",
            )
        return [dict(r) for r in rows]


# === Маршруты (трассы) ===

def _normalize_query(q: str) -> tuple[str, str]:
    """Нормализует запрос: возвращает (cyrillic, latin) варианты для поиска.
    Например "м-4" → ("м-4", "m-4") — ищем по обоим.
    """
    if not q:
        return "", ""
    q_lower = q.strip().lower()
    # Маппинг кириллица → латиница для типичных замен
    # Полный маппинг: визуально одинаковые кириллические и латинские буквы
    cyr_to_lat = {
        # Транслитерация (русский → английский, по звукам)
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
    lat_variant = "".join(cyr_to_lat.get(c, c) for c in q_lower)
    # Также обратный маппинг
    lat_to_cyr = {v: k for k, v in cyr_to_lat.items() if v}
    cyr_variant = "".join(lat_to_cyr.get(c, c) for c in q_lower)
    return cyr_variant, lat_variant


async def search_routes(query: str, limit: int = 10) -> list[dict]:
    """Ищет трассы по коду или названию. Поиск по подстроке.

    Поддерживает кириллицу и латиницу: "М-4" и "M-4" дают одинаковый результат.
    """
    if not query or len(query.strip()) < 2:
        return []
    cyr_q, lat_q = _normalize_query(query)
    queries = list({cyr_q, lat_q, query.strip().lower()})  # уникальные
    # Строим LIKE patterns
    like_patterns = [f"%{q}%" for q in queries]
    # Строим точные и prefix patterns для ранжирования
    exact = queries[0]
    prefix = [f"{q}%" for q in queries]

    if USE_SQLITE:
        # SQLite LOWER() не работает с кириллицей.
        # Решение: получаем ВСЕ маршруты и фильтруем в Python.
        all_rows = await _fetch(
            "SELECT id, code, name, aliases, type, length_km, start_point, end_point, description "
            "FROM routes WHERE is_active = 1"
        )
        rows = []
        for r in all_rows:
            r_dict = dict(r)
            # Нормализуем все текстовые поля в Python
            text = (r_dict["code"] + " " + r_dict["name"] + " " + (r_dict["aliases"] or "")).lower()
            if any(q.lower() in text for q in queries):
                rows.append(r_dict)
        # Сортируем: точные совпадения кода первыми
        rows.sort(key=lambda r: 0 if r["code"].lower() == exact else 1)
        rows = rows[:limit]
    else:
        # PG — используем ILIKE для case-insensitive
        # ILIKE работает с кириллицей
        conditions = " OR ".join(
            f"(code ILIKE ${i+1} OR name ILIKE ${i+1} OR aliases ILIKE ${i+1})"
            for i in range(len(queries))
        )
        params = [f"%{q}%" for q in queries]
        rows = await _fetch(
            f"""SELECT id, code, name, aliases, type, length_km, start_point, end_point, description
                FROM routes
                WHERE is_active = TRUE AND ({conditions})
                ORDER BY
                  CASE WHEN LOWER(code) = ${len(queries)+1} THEN 0
                       ELSE 1
                  END, code
                LIMIT ${len(queries)+2}""",
            *params, exact, limit,
        )
    return [dict(r) for r in rows]


async def find_stations_by_route(
    route_id: int,
    limit: int = 50,
    fuel: str | None = None,
    has_live: bool = True,
) -> list[dict]:
    """Возвращает АЗС на трассе с координатами и текущим статусом."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT s.id, s.name, s.operator, s.brand, s.city, s.address,
                      s.lat, s.lon, s.fuel_types, sr.km_marker,
                      EXISTS(SELECT 1 FROM reports r WHERE r.station_id = s.id
                             AND r.expires_at > datetime('now')
                             AND r.available = 1) AS has_fuel
               FROM station_routes sr
               JOIN stations s ON s.id = sr.station_id
               WHERE sr.route_id = ? AND s.is_active = 1
               ORDER BY sr.km_marker NULLS LAST, s.id
               LIMIT ?""",
            route_id, limit,
        ) if "NULLS LAST" in "?" else await _fetch(
            """SELECT s.id, s.name, s.operator, s.brand, s.city, s.address,
                      s.lat, s.lon, s.fuel_types, sr.km_marker,
                      EXISTS(SELECT 1 FROM reports r WHERE r.station_id = s.id
                             AND r.expires_at > datetime('now')
                             AND r.available = 1) AS has_fuel
               FROM station_routes sr
               JOIN stations s ON s.id = sr.station_id
               WHERE sr.route_id = ? AND s.is_active = 1
               ORDER BY s.id
               LIMIT ?""",
            route_id, limit,
        )
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT s.id, s.name, s.operator, s.brand, s.city, s.address,
                          s.lat, s.lon, s.fuel_types, sr.km_marker,
                          EXISTS(SELECT 1 FROM reports r WHERE r.station_id = s.id
                                 AND r.expires_at > NOW()
                                 AND r.available = TRUE) AS has_fuel
                   FROM station_routes sr
                   JOIN stations s ON s.id = sr.station_id
                   WHERE sr.route_id = $1 AND s.is_active = TRUE
                   ORDER BY sr.km_marker NULLS LAST, s.id
                   LIMIT $2""",
                route_id, limit,
            )
    return [dict(r) for r in rows]


async def search_cities(query: str = "", limit: int = 200) -> list[dict]:
    """Ищет города из БД. Поддерживает кириллицу/латиницу.
    Возвращает [{name, region, stations_count, has_live, lat, lon}, ...]

    Быстрый GROUP BY + Python-фильтр (т.к. кириллица/латиница не матчатся в SQL).
    with_fuel НЕ считается здесь (только city+region+lat+lon) — для скорости.
    """
    base_sql = """
        SELECT s.city,
               MAX(s.region) as region,
               COUNT(*) as total,
               AVG(s.lat) as avg_lat,
               AVG(s.lon) as avg_lon
          FROM stations s
         WHERE s.city IS NOT NULL AND s.city != ''
           AND s.lat IS NOT NULL AND s.lon IS NOT NULL
           AND COALESCE(s.is_active, TRUE) = TRUE
    """

    if not query or len(query.strip()) < 2:
        # Без запроса — топ городов по числу АЗС
        if USE_SQLITE:
            sql = base_sql + " GROUP BY s.city ORDER BY total DESC LIMIT ?"
            rows = await _fetch(sql, limit)
        else:
            sql = base_sql + " GROUP BY s.city ORDER BY COUNT(*) DESC LIMIT $1"
            async with _db.acquire() as conn:
                rows = await conn.fetch(sql, limit)
        return [
            {
                "name": (r["city"] if isinstance(r, dict) else r[0]),
                "region": (r["region"] if isinstance(r, dict) else r[1]) or "",
                "stations_count": (r["total"] if isinstance(r, dict) else r[2]) or 0,
                "with_fuel": 0,
                "lat": float(r["avg_lat"]) if (r.get("avg_lat") if isinstance(r, dict) else r[3]) else None,
                "lon": float(r["avg_lon"]) if (r.get("avg_lon") if isinstance(r, dict) else r[4]) else None,
            }
            for r in rows
        ]

    # С запросом — LIKE/ILIKE фильтр в БД
    q = query.strip().lower()
    fetch_limit = max(limit * 10, 500)
    # Дополнительно: транслитерированный вариант запроса (Ivanovo → иваново)
    cyr_to_lat = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
        "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
    # LAT → CYR: расширенный (включая ь, х, й, мягкий/твёрдый знаки)
    lat_to_cyr = {
        "a": "а", "b": "б", "c": "с", "d": "д", "e": "е", "f": "ф",
        "g": "г", "h": "х", "i": "и", "j": "й", "k": "к", "l": "л",
        "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р",
        "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "х",
        "y": "й", "z": "з",
    }

    # Транслитерируем в обе стороны
    q_cyr_from_lat = "".join(lat_to_cyr.get(c, c) for c in q)
    q_lat_from_cyr = "".join(cyr_to_lat.get(c, c) for c in q)

    # Уникальные паттерны для SQL
    patterns = list({f"%{q}%", f"%{q_cyr_from_lat}%", f"%{q_lat_from_cyr}%"} - {""})

    if USE_SQLITE:
        # SQLite: OR для всех паттернов
        like_clauses = " OR ".join(["py_lower(s.city) LIKE ?"] * len(patterns))
        sql = base_sql + f" AND ({like_clauses}) GROUP BY s.city LIMIT ?"
        rows = await _fetch(sql, *patterns, fetch_limit)
    else:
        # PG: ILIKE OR
        like_clauses = " OR ".join([f"LOWER(s.city) ILIKE ${i+1}" for i in range(len(patterns))])
        sql = base_sql + f" AND ({like_clauses}) GROUP BY s.city LIMIT ${len(patterns)+1}"
        async with _db.acquire() as conn:
            rows = await conn.fetch(sql, *patterns, fetch_limit)

    # Build result (SQL уже отфильтровал)
    result = []
    seen_cities = set()

    for r in rows:
        r_dict = r if isinstance(r, dict) else dict(r)
        city_name = r_dict.get("city")
        if not city_name or city_name in seen_cities:
            continue

        seen_cities.add(city_name)
        result.append({
            "name": city_name,
            "region": r_dict.get("region") or "",
            "stations_count": r_dict.get("total", 0) or 0,
            "with_fuel": 0,
            "lat": float(r_dict["avg_lat"]) if r_dict.get("avg_lat") else None,
            "lon": float(r_dict["avg_lon"]) if r_dict.get("avg_lon") else None,
        })

        if len(result) >= limit:
            break

    return result


# =====================================================
# Premium подписки
# =====================================================

PREMIUM_PLANS = {
    "economy": {
        "code": "economy",
        "name": "Эконом",
        "price": 100,
        "period_days": 30,
        "features": [
            "price_history",
            "export_csv",
            "offline_map",
        ],
        "icon": "📊",
        "tagline": "История цен + оффлайн-карта",
    },
    "standard": {
        "code": "standard",
        "name": "Стандарт",
        "price": 250,
        "period_days": 30,
        "features": [
            "price_history", "export_csv", "offline_map",  # экономи
            "route_fuel", "forecast_7d", "fuel_alarm",    # свои
        ],
        "icon": "🗺️",
        "tagline": "Маршрут с гарантией топлива",
    },
    "elite": {
        "code": "elite",
        "name": "Элит",
        "price": 500,
        "period_days": 30,
        "features": [
            "price_history", "export_csv", "offline_map",
            "route_fuel", "forecast_7d", "fuel_alarm",
            "anti_traffic", "sos_elite",
        ],
        "icon": "👑",
        "tagline": "Анти-пробка и SOS-режим",
    },
    "founder": {
        "code": "founder",
        "name": "Founder Pack",
        "price": 1990,
        "period_days": 36500,
        "features": [
            "price_history", "export_csv", "offline_map",
            "route_fuel", "forecast_7d", "fuel_alarm",
            "anti_traffic", "sos_elite",
        ],
        "icon": "🏆",
        "tagline": "Пожизненный Элит + Founder-бейдж",
    },
}

FEATURE_TIER = {
    "price_history": "economy",
    "export_csv": "economy",
    "offline_map": "economy",
    "route_fuel": "standard",
    "forecast_7d": "standard",
    "fuel_alarm": "standard",
    "anti_traffic": "elite",
    "sos_elite": "elite",
}

TIER_RANK = {"economy": 1, "standard": 2, "elite": 3, "founder": 4}


def get_plan(tier: str) -> dict | None:
    return PREMIUM_PLANS.get(tier)


def all_plans() -> list[dict]:
    return list(PREMIUM_PLANS.values())


def has_feature(user_tier: str | None, feature: str) -> bool:
    """Проверяет, доступна ли фича для тарифа пользователя."""
    if not user_tier:
        return False
    required = FEATURE_TIER.get(feature)
    if not required:
        return False
    return TIER_RANK.get(user_tier, 0) >= TIER_RANK.get(required, 99)


async def get_user_premium(user_id: int) -> dict | None:
    """Возвращает активную подписку пользователя или None."""
    if USE_SQLITE:
        row = await _fetch(
            """SELECT * FROM premium_users
               WHERE user_id = ? AND is_active = 1
                 AND datetime(expires_at) > datetime('now')
               ORDER BY expires_at DESC LIMIT 1""",
            user_id, one=True,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM premium_users
                   WHERE user_id = $1 AND is_active = TRUE
                     AND expires_at > NOW()
                   ORDER BY expires_at DESC LIMIT 1""",
                user_id,
            )
    if not row:
        return None
    return dict(row) if not USE_SQLITE else row


async def activate_premium(user_id: int, tier: str, days: int = 30, payment_id: str = "manual", amount: int | None = None) -> dict:
    """Активирует/продлевает премиум подписку."""
    plan = get_plan(tier)
    if not plan:
        raise ValueError(f"Unknown tier: {tier}")
    if amount is None:
        amount = plan["price"]

    current = await get_user_premium(user_id)
    if current and current.get("tier") == tier:
        # Продлеваем
        if USE_SQLITE:
            await _execute(
                """UPDATE premium_users
                   SET expires_at = datetime(expires_at, '+' || ? || ' days'),
                       payment_id = ?, payment_amount = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                days, payment_id, amount, current["id"],
            )
        else:
            async with _db.acquire() as conn:
                await conn.execute(
                    """UPDATE premium_users
                       SET expires_at = expires_at + ($2 || ' days')::INTERVAL,
                           payment_id = $3, payment_amount = $4, updated_at = NOW()
                       WHERE id = $1""",
                    current["id"], str(days), payment_id, amount,
                )
    else:
        # Создаём новую (деактивируем старые)
        if USE_SQLITE:
            await _execute("UPDATE premium_users SET is_active = 0 WHERE user_id = ?", user_id)
            await _execute(
                """INSERT INTO premium_users
                   (user_id, tier, started_at, expires_at, payment_id, payment_amount, payment_method, is_active)
                   VALUES (?, ?, datetime('now'), datetime('now', '+' || ? || ' days'), ?, ?, ?, 1)""",
                user_id, tier, days, payment_id, amount, "manual",
            )
        else:
            async with _db.acquire() as conn:
                await conn.execute("UPDATE premium_users SET is_active = FALSE WHERE user_id = $1", user_id)
                await conn.execute(
                    """INSERT INTO premium_users
                       (user_id, tier, started_at, expires_at, payment_id, payment_amount, payment_method, is_active)
                       VALUES ($1, $2, NOW(), NOW() + ($3 || ' days')::INTERVAL, $4, $5, $6, TRUE)""",
                    user_id, tier, str(days), payment_id, amount, "manual",
                )

    # Логируем платёж
    if USE_SQLITE:
        await _execute(
            """INSERT INTO premium_payments (user_id, tier, amount, status, payment_method, external_id, paid_at)
               VALUES (?, ?, ?, 'paid', 'manual', ?, datetime('now'))""",
            user_id, tier, amount, payment_id,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                """INSERT INTO premium_payments (user_id, tier, amount, status, payment_method, external_id, paid_at)
                   VALUES ($1, $2, $3, 'paid', 'manual', $4, NOW())""",
                user_id, tier, amount, payment_id,
            )

    return await get_user_premium(user_id) or {}


async def has_used_trial(user_id: int) -> bool:
    """Проверяет, использовал ли юзер trial раньше."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT id FROM premium_trials WHERE user_id = ?",
            user_id, one=True,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM premium_trials WHERE user_id = $1",
                user_id,
            )
    return row is not None


async def activate_trial(user_id: int, tier: str = "standard", days: int = 3) -> dict:
    """Активирует trial Premium (1 раз на юзера).

    Returns:
        {"ok": True, "expires_at": "2026-07-17", "tier": "standard"}
        или {"ok": False, "error": "already_used" | "unknown_tier"}
    """
    if get_plan(tier) is None:
        return {"ok": False, "error": "unknown_tier"}

    # Проверяем, использовал ли trial раньше
    if await has_used_trial(user_id):
        return {"ok": False, "error": "already_used"}

    # Сначала записываем trial (UNIQUE constraint защитит от race)
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=days)

    try:
        if USE_SQLITE:
            await _execute(
                """INSERT INTO premium_trials (user_id, tier, days, started_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                user_id, tier, days, now.isoformat(), expires.isoformat(),
            )
        else:
            async with _db.acquire() as conn:
                await conn.execute(
                    """INSERT INTO premium_trials (user_id, tier, days, started_at, expires_at)
                       VALUES ($1, $2, $3, $4, $5)""",
                    user_id, tier, days, now, expires,
                )
    except Exception as e:
        # Race: другой запрос уже создал trial
        err_str = str(e).lower()
        if "unique" in err_str or "duplicate" in err_str:
            return {"ok": False, "error": "already_used"}
        raise

    # Активируем premium (тот же механизм что и обычная активация)
    sub = await activate_premium(
        user_id=user_id,
        tier=tier,
        days=days,
        payment_id=f"trial_{tier}_{days}d",
        amount=0,
    )
    return {
        "ok": True,
        "tier": tier,
        "days": days,
        "expires_at": sub.get("expires_at", ""),
    }


async def cancel_premium(user_id: int) -> bool:
    """Отменяет активную подписку."""
    if USE_SQLITE:
        cur = await _execute(
            "UPDATE premium_users SET is_active = 0, cancelled_at = datetime('now') WHERE user_id = ? AND is_active = 1",
            user_id,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE premium_users SET is_active = FALSE, cancelled_at = NOW() WHERE user_id = $1 AND is_active = TRUE",
                user_id,
            )
    return True


async def is_premium(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя активный премиум."""
    sub = await get_user_premium(user_id)
    return sub is not None


async def is_founder(user_id: int) -> bool:
    """Проверяет, является ли пользователем Founder."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT 1 FROM founder_purchases WHERE user_id = ? AND status = 'paid' LIMIT 1",
            user_id, one=True,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM founder_purchases WHERE user_id = $1 AND status = 'paid' LIMIT 1",
                user_id,
            )
    return row is not None


async def get_founder_info(user_id: int) -> dict | None:
    """Возвращает инфо о Founder-покупке или None."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT * FROM founder_purchases WHERE user_id = ? AND status = 'paid' LIMIT 1",
            user_id, one=True,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM founder_purchases WHERE user_id = $1 AND status = 'paid' LIMIT 1",
                user_id,
            )
    return dict(row) if row else None


async def create_founder_purchase(user_id: int, amount: int = 1990, payment_token: str = "") -> dict:
    """Создаёт запись о Founder-покупке."""
    if USE_SQLITE:
        await _execute(
            """INSERT INTO founder_purchases (user_id, amount, payment_token, status, created_at)
               VALUES (?, ?, ?, 'pending', datetime('now'))""",
            user_id, amount, payment_token,
        )
        row = await _fetch(
            "SELECT * FROM founder_purchases WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            user_id, one=True,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                """INSERT INTO founder_purchases (user_id, amount, payment_token, status, created_at)
                   VALUES ($1, $2, $3, 'pending', NOW())""",
                user_id, amount, payment_token,
            )
            row = await conn.fetchrow(
                "SELECT * FROM founder_purchases WHERE user_id = $1 ORDER BY id DESC LIMIT 1",
                user_id,
            )
    return dict(row) if row else {}


async def confirm_founder_purchase(payment_token: str) -> bool:
    """Подтверждает Founder-покупку и активирует пожизненный Elite."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT * FROM founder_purchases WHERE payment_token = ? AND status = 'pending' LIMIT 1",
            payment_token, one=True,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM founder_purchases WHERE payment_token = $1 AND status = 'pending' LIMIT 1",
                payment_token,
            )
    if not row:
        return False

    user_id = row["user_id"]

    # Обновляем статус покупки
    if USE_SQLITE:
        await _execute(
            "UPDATE founder_purchases SET status = 'paid', paid_at = datetime('now') WHERE payment_token = ?",
            payment_token,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE founder_purchases SET status = 'paid', paid_at = NOW() WHERE payment_token = $1",
                payment_token,
            )

    # Активируем пожизненный Elite
    await activate_premium(user_id, "founder", days=36500, payment_id=f"founder_{payment_token}", amount=1990)

    # Record referral commission (50% to referrer)
    try:
        await record_referral_commission(user_id, 0, 1990)
    except Exception as e:
        logger.warning(f"Failed to record founder referral commission: {e}")

    return True


async def get_founders_list() -> list[dict]:
    """Возвращает список всех Founder-пользователей."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT fp.user_id, fp.created_at, u.first_name, u.username
               FROM founder_purchases fp
               LEFT JOIN users u ON fp.user_id = u.id
               WHERE fp.status = 'paid'
               ORDER BY fp.created_at ASC"""
        )
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT fp.user_id, fp.created_at, u.first_name, u.username
                   FROM founder_purchases fp
                   LEFT JOIN users u ON fp.user_id = u.id
                   WHERE fp.status = 'paid'
                   ORDER BY fp.created_at ASC"""
            )
    return [dict(r) for r in rows]


# === Запросы на оплату (для VK Pay) ===

import secrets as _secrets
from datetime import timedelta as _timedelta


async def create_payment_request(user_id: int, tier: str, payment_method: str = "vk_pay") -> str:
    """Создаёт pending-платёж, возвращает токен для оплаты.

    Токен используется в VK Pay ссылке. После оплаты вызывается
    confirm_payment() чтобы активировать подписку.
    """
    plan = get_plan(tier)
    if not plan:
        raise ValueError(f"Unknown tier: {tier}")
    token = _secrets.token_urlsafe(24)

    if USE_SQLITE:
        await _execute(
            """INSERT INTO premium_payments
               (user_id, tier, amount, status, payment_method, external_id, created_at)
               VALUES (?, ?, ?, 'pending', ?, ?, datetime('now'))""",
            user_id, tier, plan["price"], payment_method, token,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                """INSERT INTO premium_payments
                   (user_id, tier, amount, status, payment_method, external_id, created_at)
                   VALUES ($1, $2, $3, 'pending', $4, $5, NOW())""",
                user_id, tier, plan["price"], payment_method, token,
            )
    return token


async def get_payment_by_token(token: str) -> dict | None:
    """Получает платёж по токену."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT * FROM premium_payments WHERE external_id = ?",
            token, one=True,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM premium_payments WHERE external_id = $1",
                token,
            )
    if not row:
        return None
    return dict(row) if not USE_SQLITE else row


async def confirm_payment(token: str) -> dict | None:
    """Подтверждает оплату и активирует премиум.

    Вызывается после успешной оплаты через VK Pay callback.
    Также записывает комиссию 50% рефереру (если есть).
    """
    payment = await get_payment_by_token(token)
    if not payment:
        return None
    if payment.get("status") == "paid":
        # Уже подтверждён
        return await get_user_premium(payment["user_id"])

    if USE_SQLITE:
        await _execute(
            "UPDATE premium_payments SET status = 'paid', paid_at = datetime('now') WHERE external_id = ?",
            token,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE premium_payments SET status = 'paid', paid_at = NOW() WHERE external_id = $1",
                token,
            )

    plan = get_plan(payment["tier"])
    sub = await activate_premium(
        payment["user_id"],
        payment["tier"],
        days=plan["period_days"] if plan else 30,
        payment_id=token,
        amount=payment["amount"],
    )

    # Record referral commission (50% to referrer)
    try:
        await record_referral_commission(payment["user_id"], payment.get("id", 0), payment["amount"])
    except Exception as e:
        logger.warning(f"Failed to record referral commission: {e}")

    return sub


async def get_pending_payments(limit: int = 50) -> list[dict]:
    """Возвращает список ожидающих оплаты (для админ-панели)."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT pp.*, u.telegram_id, u.username, u.first_name
               FROM premium_payments pp
               LEFT JOIN users u ON u.id = pp.user_id
               WHERE pp.status = 'pending'
               ORDER BY pp.created_at DESC LIMIT ?""",
            limit,
        )
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT pp.*, u.telegram_id, u.username, u.first_name
                   FROM premium_payments pp
                   LEFT JOIN users u ON u.id = pp.user_id
                   WHERE pp.status = 'pending'
                   ORDER BY pp.created_at DESC LIMIT $1""",
                limit,
            )
    result = []
    for r in (rows if not USE_SQLITE else rows):
        d = dict(r) if not USE_SQLITE else r
        result.append(d)
    return result


# === Привязка аккаунтов TG ↔ VK ↔ MiniApp ===

import secrets
from datetime import datetime, timedelta, timezone


def _gen_link_code() -> str:
    """Генерирует 6-значный код для привязки аккаунтов."""
    return f"{secrets.randbelow(1000000):06d}"


async def create_link_code(telegram_id: int) -> str:
    """Создаёт/обновляет код привязки для пользователя.

    Принимает telegram_id ИЛИ vk_user_id — функция сама найдёт пользователя.
    Возвращает 6-значный код, действующий 10 минут.
    """
    code = _gen_link_code()
    expires_dt = datetime.now(timezone.utc) + timedelta(minutes=10)
    expires_str = expires_dt.isoformat()  # для SQLite (TEXT)

    uid = await get_user_id_by_any(telegram_id)
    if not uid:
        raise ValueError("User not found")

    if USE_SQLITE:
        await _execute(
            "UPDATE users SET link_code = ?, link_code_expires_at = ? WHERE id = ?",
            code, expires_str, uid,
        )
    else:
        # PG: TIMESTAMPTZ требует datetime объект, не строку
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE users SET link_code = $1, link_code_expires_at = $2 WHERE id = $3",
                code, expires_dt, uid,
            )
    return code


async def get_link_code_info(code: str) -> dict | None:
    """Возвращает инфо о коде привязки (кто создал, когда истекает)."""
    if USE_SQLITE:
        row = await _fetch(
            """SELECT id, telegram_id, vk_id, username, first_name, link_code_expires_at
               FROM users WHERE link_code = ?""",
            code, one=True,
        )
        if not row:
            return None
        result = dict(row) if hasattr(row, "keys") else row
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id, telegram_id, vk_id, username, first_name, link_code_expires_at
                   FROM users WHERE link_code = $1""",
                code,
            )
            if not row:
                return None
            result = dict(row)
    # Проверяем что не истёк
    exp = result.get("link_code_expires_at")
    if exp:
        try:
            if isinstance(exp, str):
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            else:
                exp_dt = exp
            if datetime.now(timezone.utc) > exp_dt:
                return None
        except Exception:
            pass
    return result


async def link_accounts(telegram_id: int, link_code: str) -> dict:
    """Привязывает текущий аккаунт к аккаунту, который создал link_code.

    Принимает telegram_id ИЛИ vk_user_id — функция сама найдёт пользователя.
    Возвращает {"ok": True, "linked_to": ..., "username": ...}
    или {"ok": False, "error": "..."}
    """
    info = await get_link_code_info(link_code)
    if not info:
        return {"ok": False, "error": "Код не найден или истёк"}

    target_uid = info.get("id")
    if not target_uid:
        return {"ok": False, "error": "Аккаунт с этим кодом не найден"}

    current_uid = await get_user_id_by_any(telegram_id)
    if not current_uid:
        return {"ok": False, "error": "Сначала запусти бота"}

    if target_uid == current_uid:
        return {"ok": False, "error": "Нельзя привязать аккаунт к самому себе"}

    rate = await check_link_rate_limit(current_uid)
    if not rate.get("ok"):
        return rate

    linked_tg_id = info.get("telegram_id")

    if USE_SQLITE:
        try:
            await _execute(
                "UPDATE users SET linked_user_id = ?, link_code = NULL, link_code_expires_at = NULL WHERE id = ?",
                target_uid, current_uid,
            )
            await _execute(
                "UPDATE users SET linked_user_id = ? WHERE id = ?",
                current_uid, target_uid,
            )
        except Exception:
            # Fallback если linked_user_id колонки нет
            await _execute(
                "UPDATE users SET linked_telegram_id = ?, link_code = NULL, link_code_expires_at = NULL WHERE id = ?",
                linked_tg_id, current_uid,
            )
    else:
        async with _db.acquire() as conn:
            try:
                await conn.execute(
                    """UPDATE users
                       SET linked_user_id = $1, link_code = NULL, link_code_expires_at = NULL
                       WHERE id = $2""",
                    target_uid, current_uid,
                )
                await conn.execute(
                    "UPDATE users SET linked_user_id = $1 WHERE id = $2",
                    current_uid, target_uid,
                )
            except Exception:
                # Fallback если linked_user_id колонки нет
                await conn.execute(
                    """UPDATE users
                       SET linked_telegram_id = $1, link_code = NULL, link_code_expires_at = NULL
                       WHERE id = $2""",
                    linked_tg_id, current_uid,
                )

    await record_link_operation(current_uid)
    return {
        "ok": True,
        "linked_to_telegram_id": linked_tg_id,
        "linked_to_username": info.get("username"),
        "linked_to_name": info.get("first_name"),
    }


async def link_accounts_by_vk(vk_id: int, telegram_id: int) -> dict:
    """Привязывает VK аккаунт к TG по deep link (one-click link).

    Вызывается из /start link_vk_VKID deep link.
    """
    # Находим VK пользователя
    vk_uid = await get_user_id_by_vk_id(vk_id)
    if not vk_uid:
        # Создаём VK пользователя
        from db import upsert_user_vk
        vk_uid = await upsert_user_vk(vk_id)
        if not vk_uid:
            return {"ok": False, "error": "Не удалось создать VK аккаунт"}

    # Находим TG пользователя
    tg_uid = await get_user_id_by_telegram_id(telegram_id)
    if not tg_uid:
        # Создаём TG пользователя
        from db import upsert_user
        tg_uid = await upsert_user(telegram_id)
        if not tg_uid:
            return {"ok": False, "error": "Не удалось создать TG аккаунт"}

    if vk_uid == tg_uid:
        return {"ok": False, "error": "Это один и тот же аккаунт"}

    rate = await check_link_rate_limit(vk_uid)
    if not rate.get("ok"):
        return rate

    # Привязываем VK → TG (bidirectional)
    try:
        if USE_SQLITE:
            await _execute(
                "UPDATE users SET linked_user_id = ?, link_code = NULL, link_code_expires_at = NULL WHERE id = ?",
                tg_uid, vk_uid,
            )
            await _execute(
                "UPDATE users SET linked_user_id = ? WHERE id = ?",
                vk_uid, tg_uid,
            )
        else:
            async with _db.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET linked_user_id = $1, link_code = NULL, link_code_expires_at = NULL WHERE id = $2",
                    tg_uid, vk_uid,
                )
                await conn.execute(
                    "UPDATE users SET linked_user_id = $1 WHERE id = $2",
                    vk_uid, tg_uid,
                )
    except Exception:
        # Fallback
        if USE_SQLITE:
            await _execute(
                "UPDATE users SET linked_telegram_id = ?, link_code = NULL, link_code_expires_at = NULL WHERE id = ?",
                telegram_id, vk_uid,
            )
        else:
            async with _db.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET linked_telegram_id = $1, link_code = NULL, link_code_expires_at = NULL WHERE id = $2",
                    telegram_id, vk_uid,
                )

    await record_link_operation(vk_uid)
    return {"ok": True, "vk_id": vk_id, "telegram_id": telegram_id}


async def link_accounts_direct(current_uid: int, target_uid: int) -> dict:
    """Прямая привязка двух аккаунтов по профилю (без подтверждения).

    Bidirectional: оба аккаунта получают linked_user_id друг друга.
    """
    if current_uid == target_uid:
        return {"ok": False, "error": "Нельзя привязать аккаунт к самому себе"}

    rate = await check_link_rate_limit(current_uid)
    if not rate.get("ok"):
        return rate

    # Проверяем, не привязан ли уже кто-то из них
    if USE_SQLITE:
        s_row = await _fetch("SELECT linked_user_id, linked_telegram_id, link_group_id FROM users WHERE id = ?", current_uid, one=True)
        t_row = await _fetch("SELECT linked_user_id, linked_telegram_id, link_group_id FROM users WHERE id = ?", target_uid, one=True)
    else:
        async with _db.acquire() as conn:
            s_row = await conn.fetchrow("SELECT linked_user_id, linked_telegram_id, link_group_id FROM users WHERE id = $1", current_uid)
            t_row = await conn.fetchrow("SELECT linked_user_id, linked_telegram_id, link_group_id FROM users WHERE id = $1", target_uid)

    def _get_linked(row):
        if not row:
            return None
        if isinstance(row, dict):
            return row.get("linked_user_id") or row.get("linked_telegram_id")
        return row[0] or (row[1] if len(row) > 1 else None)

    def _get_group(row):
        if not row:
            return None
        if isinstance(row, dict):
            return row.get("link_group_id")
        return row[2] if len(row) > 2 else None

    s_linked = _get_linked(s_row)
    t_linked = _get_linked(t_row)
    s_group = _get_group(s_row)
    t_group = _get_group(t_row)

    if s_linked:
        return {"ok": False, "error": "У тебя уже есть привязанный аккаунт. Сначала отвяжи текущий."}
    if t_linked:
        return {"ok": False, "error": "Этот пользователь уже привязан к другому аккаунту."}

    # Создаём или берём существующую группу
    group_id = s_group or t_group
    if not group_id:
        group_id = await create_link_group()

    # Bidirectional linking (старая логика + группа)
    try:
        if USE_SQLITE:
            await _execute(
                "UPDATE users SET linked_user_id = ?, link_group_id = ? WHERE id = ?",
                target_uid, group_id, current_uid,
            )
            await _execute(
                "UPDATE users SET linked_user_id = ?, link_group_id = ? WHERE id = ?",
                current_uid, group_id, target_uid,
            )
            await _db.commit()
        else:
            async with _db.acquire() as conn:
                await conn.execute("UPDATE users SET linked_user_id = $1, link_group_id = $2 WHERE id = $3", target_uid, group_id, current_uid)
                await conn.execute("UPDATE users SET linked_user_id = $1, link_group_id = $2 WHERE id = $3", current_uid, group_id, target_uid)
    except Exception as e:
        logger.warning(f"link_accounts_direct error: {e}")
        return {"ok": False, "error": "Ошибка привязки"}

    await record_link_operation(current_uid)
    return {"ok": True, "group_id": group_id}


async def unlink_user(uid: int) -> dict:
    """Отвязывает аккаунт: очищает linked_user_id, linked_telegram_id, link_group_id."""
    try:
        if USE_SQLITE:
            row = await _fetch("SELECT linked_user_id, linked_telegram_id FROM users WHERE id = ?", uid, one=True)
            if not row:
                return {"ok": True, "message": "Аккаунт не был привязан."}
            linked_uid = (row.get("linked_user_id") if row else None) if isinstance(row, dict) else (row[0] if row else None)
            linked_tg = (row.get("linked_telegram_id") if row else None) if isinstance(row, dict) else (row[1] if len(row) > 1 else None)
            target_id = linked_uid or linked_tg
            if target_id:
                await _execute("UPDATE users SET linked_user_id = NULL, linked_telegram_id = NULL, link_group_id = NULL WHERE id = ?", uid)
                await _execute("UPDATE users SET linked_user_id = NULL, linked_telegram_id = NULL, link_group_id = NULL WHERE id = ?", target_id)
                await _db.commit()
            else:
                await _execute("UPDATE users SET link_group_id = NULL WHERE id = ?", uid)
                await _db.commit()
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow("SELECT linked_user_id, linked_telegram_id FROM users WHERE id = $1", uid)
                if not row:
                    return {"ok": True, "message": "Аккаунт не был привязан."}
                linked_uid = row["linked_user_id"]
                linked_tg = row["linked_telegram_id"]
                target_id = linked_uid or linked_tg
                if target_id:
                    await conn.execute("UPDATE users SET linked_user_id = NULL, linked_telegram_id = NULL, link_group_id = NULL WHERE id = $1", uid)
                    await conn.execute("UPDATE users SET linked_user_id = NULL, linked_telegram_id = NULL, link_group_id = NULL WHERE id = $1", target_id)
                else:
                    await conn.execute("UPDATE users SET link_group_id = NULL WHERE id = $1", uid)
        await record_link_operation(uid)
        return {"ok": True, "message": "Аккаунт отвязан."}
    except Exception as e:
        logger.warning(f"unlink_user error for uid={uid}: {e}")
        return {"ok": False, "error": f"Ошибка отвязки: {e}"}


async def create_link_group() -> int:
    """Создаёт новую группу привязки. Возвращает group_id."""
    if USE_SQLITE:
        await _execute("INSERT INTO link_groups (created_at) VALUES (datetime('now'))")
        row = await _fetch("SELECT last_insert_rowid() as id", one=True)
        return row["id"] if isinstance(row, dict) else row[0]
    else:
        async with _db.acquire() as conn:
            gid = await conn.fetchval("INSERT INTO link_groups DEFAULT VALUES RETURNING id")
            return gid


async def join_link_group(uid: int, group_id: int) -> dict:
    """Добавляет пользователя в группу привязки. Проверяет: макс 1 VK + 1 TG в группе."""
    try:
        if USE_SQLITE:
            me = await _fetch("SELECT vk_id, telegram_id FROM users WHERE id = ?", uid, one=True)
            members = await _fetch("SELECT vk_id, telegram_id FROM users WHERE link_group_id = ?", group_id)
        else:
            async with _db.acquire() as conn:
                me = await conn.fetchrow("SELECT vk_id, telegram_id FROM users WHERE id = $1", uid)
                members = await conn.fetch("SELECT vk_id, telegram_id FROM users WHERE link_group_id = $1", group_id)
        if not me:
            return {"ok": False, "error": "Пользователь не найден"}
        me_v = me.get("vk_id") if isinstance(me, dict) else me[0]
        me_t = me.get("telegram_id") if isinstance(me, dict) else me[1]
        has_vk = bool(me_v)
        has_tg = bool(me_t and me_t > 0)
        group_vk = sum(1 for m in (members or []) if (m.get("vk_id") if isinstance(m, dict) else m[0]))
        group_tg = sum(1 for m in (members or []) if (m.get("telegram_id") if isinstance(m, dict) else m[1]) and ((m.get("telegram_id") if isinstance(m, dict) else m[1]) or 0) > 0)
        if has_vk and group_vk >= 1:
            return {"ok": False, "error": "В этой группе уже есть VK аккаунт"}
        if has_tg and group_tg >= 1:
            return {"ok": False, "error": "В этой группе уже есть TG аккаунт"}
        if USE_SQLITE:
            await _execute("UPDATE users SET link_group_id = ? WHERE id = ?", group_id, uid)
            await _db.commit()
        else:
            async with _db.acquire() as conn:
                await conn.execute("UPDATE users SET link_group_id = $1 WHERE id = $2", group_id, uid)
        return {"ok": True, "group_id": group_id}
    except Exception as e:
        logger.warning(f"join_link_group error: {e}")
        return {"ok": False, "error": f"Ошибка: {e}"}


async def get_link_group_id(uid: int) -> int | None:
    """Возвращает link_group_id пользователя."""
    try:
        if USE_SQLITE:
            row = await _fetch("SELECT link_group_id FROM users WHERE id = ?", uid, one=True)
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow("SELECT link_group_id FROM users WHERE id = $1", uid)
        if not row:
            return None
        return (row.get("link_group_id") if isinstance(row, dict) else row[0])
    except Exception:
        return None


async def get_link_group_members(group_id: int) -> list[dict]:
    """Возвращает всех участников группы привязки."""
    try:
        if USE_SQLITE:
            rows = await _fetch("SELECT id, first_name, vk_id, telegram_id FROM users WHERE link_group_id = ?", group_id)
        else:
            async with _db.acquire() as conn:
                rows = await conn.fetch("SELECT id, first_name, vk_id, telegram_id FROM users WHERE link_group_id = $1", group_id)
        result = []
        for r in (rows or []):
            if isinstance(r, dict):
                result.append(r)
            else:
                result.append({"id": r[0], "first_name": r[1], "vk_id": r[2], "telegram_id": r[3]})
        return result
    except Exception as e:
        logger.warning(f"get_link_group_members error: {e}")
        return []


async def get_linked_account_info(uid: int) -> dict | None:
    """Возвращает информацию о привязанном аккаунте."""
    try:
        if USE_SQLITE:
            row = await _fetch("SELECT linked_user_id FROM users WHERE id = ?", uid, one=True)
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow("SELECT linked_user_id FROM users WHERE id = $1", uid)
        if not row:
            return None
        linked_uid = (row.get("linked_user_id") if isinstance(row, dict) else row[0]) if row else None
        if not linked_uid:
            return None
        if USE_SQLITE:
            t_row = await _fetch("SELECT first_name, vk_id, telegram_id FROM users WHERE id = ?", linked_uid, one=True)
        else:
            async with _db.acquire() as conn:
                t_row = await conn.fetchrow("SELECT first_name, vk_id, telegram_id FROM users WHERE id = $1", linked_uid)
        if not t_row:
            return None
        if isinstance(t_row, dict):
            return {
                "first_name": t_row.get("first_name", ""),
                "vk_id": t_row.get("vk_id"),
                "telegram_id": t_row.get("telegram_id"),
                "platform": "vk" if t_row.get("vk_id") else "telegram",
            }
        return {
            "first_name": t_row[0] or "",
            "vk_id": t_row[1],
            "telegram_id": t_row[2],
            "platform": "vk" if t_row[1] else "telegram",
        }
    except Exception as e:
        logger.warning(f"get_linked_account_info error: {e}")
        return None


async def find_tg_user_by_username(username: str) -> int | None:
    """Находит telegram_id по username (без @). Возвращает None если не найден."""
    clean = username.strip().lstrip("@").lower()
    if not clean:
        return None
    try:
        if USE_SQLITE:
            row = await _fetch(
                "SELECT telegram_id FROM users WHERE LOWER(username) = ? AND telegram_id > 0",
                clean, one=True,
            )
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT telegram_id FROM users WHERE LOWER(username) = $1 AND telegram_id > 0",
                    clean,
                )
        return row["telegram_id"] if row else None
    except Exception as e:
        logger.warning(f"find_tg_user_by_username error: {e}")
        return None


async def find_vk_user_by_screen_name(screen_name: str) -> int | None:
    """Находит vk_id по screen_name (VK username). Возвращает None если не найден."""
    clean = screen_name.strip().lstrip("@").lower()
    if not clean:
        return None
    try:
        if USE_SQLITE:
            row = await _fetch(
                "SELECT vk_id FROM users WHERE LOWER(screen_name) = ? AND vk_id IS NOT NULL",
                clean, one=True,
            )
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT vk_id FROM users WHERE LOWER(screen_name) = $1 AND vk_id IS NOT NULL",
                    clean,
                )
        return row["vk_id"] if row else None
    except Exception as e:
        logger.warning(f"find_vk_user_by_screen_name error: {e}")
        return None


# === Rate limit привязки аккаунтов ===

MAX_LINK_OPS_PER_MONTH = 3
LINK_COOLDOWN_DAYS = 7

async def check_link_rate_limit(user_id: int) -> dict:
    """Проверяет лимит привязок. Возвращает {"ok": bool, "error": str?}"""
    try:
        if USE_SQLITE:
            row = await _fetch(
                "SELECT link_ops_count, last_link_change_at FROM users WHERE id = ?",
                user_id, one=True,
            )
        else:
            async with _db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT link_ops_count, last_link_change_at FROM users WHERE id = $1",
                    user_id,
                )
    except Exception:
        return {"ok": True}
    if not row:
        return {"ok": False, "error": "Пользователь не найден"}

    ops = (row["link_ops_count"] if isinstance(row, dict) else row[0]) or 0
    last_change = row["last_link_change_at"] if isinstance(row, dict) else row[1]

    if ops >= MAX_LINK_OPS_PER_MONTH:
        return {"ok": False, "error": f"Лимит привязок: {MAX_LINK_OPS_PER_MONTH} в месяц. Попробуй позже."}

    if last_change:
        if isinstance(last_change, str):
            last_dt = datetime.fromisoformat(last_change.replace("Z", "+00:00"))
        else:
            last_dt = last_change
        if datetime.now(timezone.utc) - last_dt < timedelta(days=LINK_COOLDOWN_DAYS):
            remaining = timedelta(days=LINK_COOLDOWN_DAYS) - (datetime.now(timezone.utc) - last_dt)
            hours = int(remaining.total_seconds() // 3600)
            return {"ok": False, "error": f"Можно сменить привязку через {hours} ч."}

    return {"ok": True}


async def reset_link_ops(user_id: int) -> dict:
    """Сбрасывает лимит привязок для пользователя."""
    try:
        if USE_SQLITE:
            await _execute(
                "UPDATE users SET link_ops_count = 0, last_link_change_at = NULL WHERE id = ?",
                user_id,
            )
            await _db.commit()
        else:
            async with _db.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET link_ops_count = 0, last_link_change_at = NULL WHERE id = $1",
                    user_id,
                )
        return {"ok": True, "message": "Лимит сброшен"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def list_all_links() -> list:
    """Возвращает все активные привязки (для отладки)."""
    try:
        if USE_SQLITE:
            rows = await _fetch(
                "SELECT id, first_name, telegram_id, vk_id, linked_user_id, linked_telegram_id, link_group_id FROM users WHERE linked_user_id IS NOT NULL OR linked_telegram_id IS NOT NULL"
            )
        else:
            async with _db.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, first_name, telegram_id, vk_id, linked_user_id, linked_telegram_id, link_group_id FROM users WHERE linked_user_id IS NOT NULL OR linked_telegram_id IS NOT NULL"
                )
        result = []
        for r in (rows or []):
            if isinstance(r, dict):
                result.append(r)
            else:
                result.append({
                    "id": r[0], "first_name": r[1], "telegram_id": r[2],
                    "vk_id": r[3], "linked_user_id": r[4],
                    "linked_telegram_id": r[5], "link_group_id": r[6],
                })
        return result
    except Exception as e:
        logger.warning(f"list_all_links error: {e}")
        return []


async def force_unlink_all(user_id: int) -> dict:
    """Принудительно очищает ВСЕ связи пользователя."""
    try:
        if USE_SQLITE:
            await _execute(
                "UPDATE users SET linked_user_id = NULL, linked_telegram_id = NULL, link_group_id = NULL WHERE id = ?",
                user_id,
            )
            await _db.commit()
        else:
            async with _db.acquire() as conn:
                await conn.execute(
                    "UPDATE users SET linked_user_id = NULL, linked_telegram_id = NULL, link_group_id = NULL WHERE id = $1",
                    user_id,
                )
        return {"ok": True, "message": f"Связки пользователя {user_id} очищены"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def record_link_operation(user_id: int) -> None:
    """Увеличивает счётчик операций и обновляет last_link_change_at."""
    now = datetime.now(timezone.utc)
    if USE_SQLITE:
        await _execute(
            "UPDATE users SET link_ops_count = COALESCE(link_ops_count, 0) + 1, last_link_change_at = ? WHERE id = ?",
            now.isoformat(), user_id,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE users SET link_ops_count = COALESCE(link_ops_count, 0) + 1, last_link_change_at = $1 WHERE id = $2",
                now, user_id,
            )


async def reset_link_ops_monthly() -> None:
    """Сброс лимитов раз в месяц (вызывать из cron/worker)."""
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    if USE_SQLITE:
        await _execute(
            "UPDATE users SET link_ops_count = 0 WHERE last_link_change_at < ? OR last_link_change_at IS NULL",
            month_ago,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE users SET link_ops_count = 0 WHERE last_link_change_at < $1 OR last_link_change_at IS NULL",
                month_ago,
            )


# === Pending Link Confirmations (TG подтверждение привязки) ===

async def create_pending_confirmation(from_user_id: int, to_tg_id: int, to_vk_id: int | None = None) -> int | None:
    """Создаёт запрос на привязку. Возвращает confirmation_id."""
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    if USE_SQLITE:
        await _execute(
            """INSERT INTO pending_link_confirmations (from_user_id, to_tg_id, to_vk_id, expires_at)
               VALUES (?, ?, ?, ?)""",
            from_user_id, to_tg_id, to_vk_id, expires.isoformat(),
        )
        row = await _fetch("SELECT last_insert_rowid() as id", one=True)
        return (row["id"] if isinstance(row, dict) else row[0])
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO pending_link_confirmations (from_user_id, to_tg_id, to_vk_id, expires_at)
                   VALUES ($1, $2, $3, $4) RETURNING id""",
                from_user_id, to_tg_id, to_vk_id, expires,
            )
            return row["id"] if row else None


async def get_pending_confirmation(confirm_id: int) -> dict | None:
    """Получает pending confirmation по ID."""
    if USE_SQLITE:
        row = await _fetch(
            """SELECT * FROM pending_link_confirmations WHERE id = ? AND status = 'pending'""",
            confirm_id, one=True,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM pending_link_confirmations WHERE id = $1 AND status = 'pending'""",
                confirm_id,
            )
    if not row:
        return None
    result = dict(row) if not USE_SQLITE else row
    exp = result.get("expires_at")
    if exp:
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00")) if isinstance(exp, str) else exp
            if datetime.now(timezone.utc) > exp_dt:
                return None
        except Exception:
            pass
    return result


async def get_pending_confirmation_for_tg(tg_id: int) -> list:
    """Получает все pending confirmations для TG юзера."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT plc.*, u.first_name as from_name, u.username as from_username
               FROM pending_link_confirmations plc
               JOIN users u ON u.id = plc.from_user_id
               WHERE plc.to_tg_id = ? AND plc.status = 'pending'
               ORDER BY plc.created_at DESC""",
            tg_id,
        )
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT plc.*, u.first_name as from_name, u.username as from_username
                   FROM pending_link_confirmations plc
                   JOIN users u ON u.id = plc.from_user_id
                   WHERE plc.to_tg_id = $1 AND plc.status = 'pending'
                   ORDER BY plc.created_at DESC""",
                tg_id,
            )
    result = []
    for r in (rows or []):
        d = dict(r) if not USE_SQLITE else r
        exp = d.get("expires_at")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00")) if isinstance(exp, str) else exp
                if datetime.now(timezone.utc) > exp_dt:
                    continue
            except Exception:
                pass
        result.append(d)
    return result


async def get_pending_confirmation_for_vk(vk_id: int) -> list:
    """Получает все pending confirmations для VK юзера."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT plc.*, u.first_name as from_name, u.username as from_username
               FROM pending_link_confirmations plc
               JOIN users u ON u.id = plc.from_user_id
               WHERE plc.to_vk_id = ? AND plc.status = 'pending'
               ORDER BY plc.created_at DESC""",
            vk_id,
        )
    else:
        async with _db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT plc.*, u.first_name as from_name, u.username as from_username
                   FROM pending_link_confirmations plc
                   JOIN users u ON u.id = plc.from_user_id
                   WHERE plc.to_vk_id = $1 AND plc.status = 'pending'
                   ORDER BY plc.created_at DESC""",
                vk_id,
            )
    result = []
    for r in (rows or []):
        d = dict(r) if not USE_SQLITE else r
        exp = d.get("expires_at")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00")) if isinstance(exp, str) else exp
                if datetime.now(timezone.utc) > exp_dt:
                    continue
            except Exception:
                pass
        result.append(d)
    return result


async def confirm_linking(confirm_id: int) -> dict:
    """Подтверждает привязку. Возвращает {"ok": True, "from_vk_id": ...}."""
    info = await get_pending_confirmation(confirm_id)
    if not info:
        return {"ok": False, "error": "Запрос не найден или истёк"}

    from_user_id = info["from_user_id"]
    to_tg_id = info["to_tg_id"]

    to_uid = await get_user_id_by_telegram_id(to_tg_id)
    if not to_uid:
        to_uid = await upsert_user(to_tg_id)

    if from_user_id == to_uid:
        return {"ok": False, "error": "Нельзя привязать к самому себе"}

    rate = await check_link_rate_limit(from_user_id)
    if not rate.get("ok"):
        return rate

    if USE_SQLITE:
        await _execute(
            "UPDATE users SET linked_user_id = ?, link_code = NULL, link_code_expires_at = NULL WHERE id = ?",
            to_uid, from_user_id,
        )
        await _execute(
            "UPDATE users SET linked_user_id = ? WHERE id = ?",
            from_user_id, to_uid,
        )
        await _execute(
            "UPDATE pending_link_confirmations SET status = 'confirmed' WHERE id = ?",
            confirm_id,
        )
        from_row = await _fetch("SELECT vk_id FROM users WHERE id = ?", from_user_id, one=True)
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE users SET linked_user_id = $1, link_code = NULL, link_code_expires_at = NULL WHERE id = $2",
                to_uid, from_user_id,
            )
            await conn.execute(
                "UPDATE users SET linked_user_id = $1 WHERE id = $2",
                from_user_id, to_uid,
            )
            await conn.execute(
                "UPDATE pending_link_confirmations SET status = 'confirmed' WHERE id = $1",
                confirm_id,
            )
            from_row = await conn.fetchrow("SELECT vk_id FROM users WHERE id = $1", from_user_id)

    from_vk_id = from_row["vk_id"] if from_row and from_row.get("vk_id") else None

    try:
        await record_link_operation(from_user_id)
    except Exception as e:
        logger.warning(f"record_link_operation failed (non-critical): {e}")
    return {"ok": True, "from_vk_id": from_vk_id}


async def reject_linking(confirm_id: int) -> dict:
    """Отклоняет привязку."""
    if USE_SQLITE:
        await _execute(
            "UPDATE pending_link_confirmations SET status = 'rejected' WHERE id = ?",
            confirm_id,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE pending_link_confirmations SET status = 'rejected' WHERE id = $1",
                confirm_id,
            )
    return {"ok": True}


async def get_user_id_by_any(telegram_id: int) -> int | None:
    """Ищет user_id по telegram_id, vk_id, id ИЛИ по linked_telegram_id/linked_user_id."""
    if USE_SQLITE:
        try:
            row = await _fetch(
                """SELECT id FROM users
                   WHERE telegram_id = ? OR vk_id = ? OR id = ? OR linked_telegram_id = ?
                      OR linked_user_id = (
                        SELECT id FROM users WHERE telegram_id = ? OR vk_id = ? LIMIT 1
                      )
                   ORDER BY (telegram_id = ?) DESC, (vk_id = ?) DESC, (id = ?) DESC
                   LIMIT 1""",
                telegram_id, telegram_id, telegram_id, telegram_id,
                telegram_id, telegram_id,
                telegram_id, telegram_id, telegram_id,
                one=True,
            )
        except Exception:
            row = await _fetch(
                """SELECT id FROM users
                   WHERE telegram_id = ? OR vk_id = ? OR id = ? OR linked_telegram_id = ?
                   ORDER BY (telegram_id = ?) DESC, (vk_id = ?) DESC, (id = ?) DESC
                   LIMIT 1""",
                telegram_id, telegram_id, telegram_id, telegram_id,
                telegram_id, telegram_id,
                telegram_id,
                one=True,
            )
        if not row:
            return None
        return row["id"] if isinstance(row, dict) else row[0]
    else:
        async with _db.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """SELECT id FROM users
                       WHERE telegram_id = $1 OR vk_id = $1 OR id = $1 OR linked_telegram_id = $1
                          OR linked_user_id = (
                            SELECT id FROM users WHERE telegram_id = $1 OR vk_id = $1 LIMIT 1
                          )
                       ORDER BY (telegram_id = $1) DESC, (vk_id = $1) DESC, (id = $1) DESC
                       LIMIT 1""",
                    telegram_id,
                )
            except Exception:
                row = await conn.fetchrow(
                    """SELECT id FROM users
                       WHERE telegram_id = $1 OR vk_id = $1 OR id = $1 OR linked_telegram_id = $1
                       ORDER BY (telegram_id = $1) DESC, (vk_id = $1) DESC, (id = $1) DESC
                       LIMIT 1""",
                    telegram_id,
                )
            return row["id"] if row else None


# === Fuel Alarms (Premium топливный будильник) ===

async def create_fuel_alarm(user_id: int, station_id: int, fuel_type: str) -> int | None:
    """Создаёт/обновляет alarm. Возвращает alarm_id."""
    if USE_SQLITE:
        row = await _fetch(
            """SELECT id FROM fuel_alarms
               WHERE user_id = ? AND station_id = ? AND fuel_type = ?""",
            user_id, station_id, fuel_type, one=True,
        )
        if row:
            await _execute(
                "UPDATE fuel_alarms SET is_active = 1, triggered_at = NULL WHERE id = ?",
                row["id"] if isinstance(row, dict) else row[0],
            )
            return row["id"] if isinstance(row, dict) else row[0]
        await _execute(
            """INSERT INTO fuel_alarms (user_id, station_id, fuel_type, is_active)
               VALUES (?, ?, ?, 1)""",
            user_id, station_id, fuel_type,
        )
        new_row = await _fetch(
            "SELECT last_insert_rowid() as id", one=True,
        )
        return new_row["id"] if isinstance(new_row, dict) else new_row[0]
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT id FROM fuel_alarms
                   WHERE user_id = $1 AND station_id = $2 AND fuel_type = $3""",
                user_id, station_id, fuel_type,
            )
            if row:
                await conn.execute(
                    "UPDATE fuel_alarms SET is_active = TRUE, triggered_at = NULL WHERE id = $1",
                    row["id"],
                )
                return row["id"]
            new_row = await conn.fetchrow(
                """INSERT INTO fuel_alarms (user_id, station_id, fuel_type, is_active)
                   VALUES ($1, $2, $3, TRUE)
                   RETURNING id""",
                user_id, station_id, fuel_type,
            )
            return new_row["id"]


async def delete_fuel_alarm(user_id: int, station_id: int, fuel_type: str) -> bool:
    """Удаляет alarm."""
    if USE_SQLITE:
        await _execute(
            "DELETE FROM fuel_alarms WHERE user_id = ? AND station_id = ? AND fuel_type = ?",
            user_id, station_id, fuel_type,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "DELETE FROM fuel_alarms WHERE user_id = $1 AND station_id = $2 AND fuel_type = $3",
                user_id, station_id, fuel_type,
            )
    return True


async def get_fuel_alarms_for_user(user_id: int) -> list:
    """Возвращает все активные alarms юзера."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT fa.id, fa.station_id, fa.fuel_type, fa.created_at, fa.triggered_at,
                      s.name, s.operator, s.address, s.city, s.lat, s.lon
               FROM fuel_alarms fa
               JOIN stations s ON s.id = fa.station_id
               WHERE fa.user_id = ? AND fa.is_active = 1
               ORDER BY fa.created_at DESC""",
            user_id,
        )
    else:
        rows = await _fetch(
            """SELECT fa.id, fa.station_id, fa.fuel_type, fa.created_at, fa.triggered_at,
                      s.name, s.operator, s.address, s.city, s.lat, s.lon
               FROM fuel_alarms fa
               JOIN stations s ON s.id = fa.station_id
               WHERE fa.user_id = $1 AND fa.is_active = TRUE
               ORDER BY fa.created_at DESC""",
            user_id,
        )
    return [dict(r) if not USE_SQLITE else r for r in (rows or [])]


async def get_fuel_alarms_for_station(station_id: int, fuel_type: str) -> list:
    """Возвращает активные alarms для конкретной АЗС + топлива."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT fa.id, fa.user_id, u.telegram_id
               FROM fuel_alarms fa
               JOIN users u ON u.id = fa.user_id
               WHERE fa.station_id = ? AND fa.fuel_type = ? AND fa.is_active = 1
                 AND (fa.triggered_at IS NULL OR datetime(fa.triggered_at) < datetime('now', '-1 hour'))""",
            station_id, fuel_type,
        )
    else:
        rows = await _fetch(
            """SELECT fa.id, fa.user_id, u.telegram_id
               FROM fuel_alarms fa
               JOIN users u ON u.id = fa.user_id
               WHERE fa.station_id = $1 AND fa.fuel_type = $2 AND fa.is_active = TRUE
                 AND (fa.triggered_at IS NULL OR fa.triggered_at < NOW() - INTERVAL '1 hour')""",
            station_id, fuel_type,
        )
    return [dict(r) if not USE_SQLITE else r for r in (rows or [])]


async def mark_fuel_alarm_triggered(alarm_id: int) -> None:
    """Помечает alarm как сработавший (чтобы не спамить)."""
    if USE_SQLITE:
        await _execute(
            "UPDATE fuel_alarms SET triggered_at = datetime('now') WHERE id = ?",
            alarm_id,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                "UPDATE fuel_alarms SET triggered_at = NOW() WHERE id = $1",
                alarm_id,
            )


# === Referral Program (Реферальная программа) ===

import secrets
import string

def _generate_referral_code(length: int = 8) -> str:
    """Генерирует уникальный реферальный код."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def create_referral_code(user_id: int) -> str:
    """Создаёт реферальный код для юзера. Возвращает код."""
    if USE_SQLITE:
        # Проверяем есть ли уже код
        row = await _fetch(
            "SELECT referral_code FROM referrals WHERE referrer_user_id = ? AND status = 'active' LIMIT 1",
            user_id, one=True,
        )
        if row:
            return row["referral_code"] if isinstance(row, dict) else row[0]
        code = _generate_referral_code()
        await _execute(
            """INSERT INTO referrals (referrer_user_id, referral_code, status)
               VALUES (?, ?, 'active')""",
            user_id, code,
        )
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT referral_code FROM referrals WHERE referrer_user_id = $1 AND status = 'active' LIMIT 1",
                user_id,
            )
            if row:
                return row["referral_code"]
            code = _generate_referral_code()
            await conn.execute(
                """INSERT INTO referrals (referrer_user_id, referral_code, status)
                   VALUES ($1, $2, 'active')""",
                user_id, code,
            )
    return code


async def get_referral_by_code(code: str) -> dict | None:
    """Находит реферера по коду."""
    if USE_SQLITE:
        row = await _fetch(
            """SELECT id, referrer_user_id, referral_code, status
               FROM referrals WHERE referral_code = ? LIMIT 1""",
            code, one=True,
        )
    else:
        row = await _fetch(
            """SELECT id, referrer_user_id, referral_code, status
               FROM referrals WHERE referral_code = $1 LIMIT 1""",
            code, one=True,
        )
    return dict(row) if row and isinstance(row, dict) else ({"id": row[0], "referrer_user_id": row[1], "referral_code": row[2], "status": row[3]} if row else None)


async def grant_referral_discount(user_id: int, percent: int = 50, days: int = 30) -> bool:
    """Выдаёт скидку % на premium-подписку. Действует days дней."""
    from datetime import datetime, timedelta
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    if USE_SQLITE:
        await _execute(
            """INSERT INTO referral_discounts (user_id, discount_percent, expires_at, used)
               VALUES (?, ?, ?, 0)""",
            user_id, percent, expires,
        )
    else:
        async with _db.acquire() as conn:
            await conn.execute(
                """INSERT INTO referral_discounts (user_id, discount_percent, expires_at, used)
                   VALUES ($1, $2, $3, FALSE)""",
                user_id, percent, expires,
            )
    return True


async def get_active_discount(user_id: int) -> dict | None:
    """Возвращает активную реферальную скидку (не использованную и не истёкшую) или None."""
    if USE_SQLITE:
        row = await _fetch(
            """SELECT id, discount_percent, expires_at FROM referral_discounts
               WHERE user_id = ? AND used = 0 AND datetime(expires_at) > datetime('now')
               ORDER BY created_at DESC LIMIT 1""",
            user_id, one=True,
        )
    else:
        row = await _fetch(
            """SELECT id, discount_percent, expires_at FROM referral_discounts
               WHERE user_id = $1 AND used = FALSE AND expires_at > NOW()
               ORDER BY created_at DESC LIMIT 1""",
            user_id, one=True,
        )
    if not row:
        return None
    return dict(row) if isinstance(row, dict) else {"id": row[0], "discount_percent": row[1], "expires_at": row[2]}


async def use_discount(discount_id: int) -> bool:
    """Помечает скидку как использованную."""
    if USE_SQLITE:
        await _execute("UPDATE referral_discounts SET used = 1 WHERE id = ?", discount_id)
    else:
        async with _db.acquire() as conn:
            await conn.execute("UPDATE referral_discounts SET used = TRUE WHERE id = $1", discount_id)
    return True


async def complete_referral(code: str, referred_user_id: int, referred_telegram_id: int) -> bool:
    """Завершает реферал: создаёт связь + 15% скидка приглашённому. Реферер получает 50% комиссии с оплат."""
    if USE_SQLITE:
        row = await _fetch(
            "SELECT referrer_user_id FROM referrals WHERE referral_code = ? AND status = 'active'",
            code, one=True,
        )
        if not row:
            return False
        referrer_id = row["referrer_user_id"] if isinstance(row, dict) else row[0]
        await _execute(
            """UPDATE referrals SET referred_user_id = ?, referred_telegram_id = ?,
               status = 'completed', completed_at = datetime('now')
               WHERE referral_code = ?""",
            referred_user_id, referred_telegram_id, code,
        )
        # Create permanent relationship
        await _execute(
            """INSERT OR IGNORE INTO referral_relationships (referrer_user_id, referred_user_id)
               VALUES (?, ?)""",
            referrer_id, referred_user_id,
        )
        # 15% скидка только приглашённому (первый платёж)
        await grant_referral_discount(referred_user_id, percent=15, days=90)
        return True
    else:
        async with _db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT referrer_user_id FROM referrals WHERE referral_code = $1 AND status = 'active'",
                code,
            )
            if not row:
                return False
            referrer_id = row["referrer_user_id"]
            await conn.execute(
                """UPDATE referrals SET referred_user_id = $1, referred_telegram_id = $2,
                   status = 'completed', completed_at = NOW()
                   WHERE referral_code = $3""",
                referred_user_id, referred_telegram_id, code,
            )
            await conn.execute(
                """INSERT INTO referral_relationships (referrer_user_id, referred_user_id)
                   VALUES ($1, $2) ON CONFLICT (referred_user_id) DO NOTHING""",
                referrer_id, referred_user_id,
            )
            await grant_referral_discount(referred_user_id, percent=15, days=90)
            return True


async def get_referral_stats(user_id: int) -> dict:
    """Возвращает статистику рефералов юзера."""
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT status, COUNT(*) as cnt FROM referrals
               WHERE referrer_user_id = ? GROUP BY status""",
            user_id,
        )
        total = await _fetch(
            "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_user_id = ?",
            user_id, one=True,
        )
    else:
        rows = await _fetch(
            """SELECT status, COUNT(*) as cnt FROM referrals
               WHERE referrer_user_id = $1 GROUP BY status""",
            user_id,
        )
        total = await _fetch(
            "SELECT COUNT(*) as cnt FROM referrals WHERE referrer_user_id = $1",
            user_id, one=True,
        )

    stats = {"total": 0, "completed": 0, "pending": 0}
    if total:
        stats["total"] = total["cnt"] if isinstance(total, dict) else total[0]
    for r in (rows or []):
        s = r["status"] if isinstance(r, dict) else r[0]
        c = r["cnt"] if isinstance(r, dict) else r[1]
        if s == "completed":
            stats["completed"] = c
        elif s == "pending":
            stats["pending"] = c
    return stats
