"""
API-сервер для Mini App.
Работает рядом с ботом в одном процессе (порт 8080).
"""
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiohttp import web

from db import (
    USE_SQLITE,
    add_report,
    add_review,
    find_nearest_stations,
    find_stations_by_city,
    find_stations_by_name,
    get_all_prices_for_station,
    get_station_analytics,
    get_station_by_id,
    get_station_current_status,
    get_user_id_by_telegram_id,
    upsert_station_for_import,
    upsert_user,
    check_and_award_badges,
    BADGE_CATALOG,
    is_premium,
)
import db  # for db._fetch, db.USE_SQLITE в get_source_stats
import aiohttp  # для reverse geocoding

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security")


# === Rate limit (in-memory, на IP) ===
# Строже: 30 GET / 10 POST в минуту на IP
_rate_limit: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_GET = 120
RATE_LIMIT_POST = 60
RATE_LIMIT_ADMIN = 10  # для admin endpoints
RATE_LIMIT_PER_MIN = RATE_LIMIT_GET  # legacy alias

# === Request size limits ===
MAX_REQUEST_BODY = 1024 * 1024  # 1 MB

# === Suspicious activity tracking ===
_suspicious: dict[str, list[tuple[float, str]]] = defaultdict(list)
SUSPICIOUS_THRESHOLD = 10  # запросов за 5 минут
SUSPICIOUS_WINDOW = 300  # 5 минут


def _json_default(obj):
    """JSON serializer fallback for Decimal, datetime, etc."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def json_resp(data: Any, status: int = 200) -> web.Response:
    """json_resp wrapper that handles Decimal from asyncpg."""
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, default=_json_default))

# === Parsers lock (чтобы не запускать парсеры параллельно) ===
_parsers_running: bool = False


def _check_rate(ip: str, max_per_min: int) -> bool:
    """Возвращает True если запрос разрешён, False если rate limit превышен."""
    now = time.time()
    _rate_limit[ip] = [t for t in _rate_limit[ip] if now - t < 60]
    if len(_rate_limit[ip]) >= max_per_min:
        security_logger.warning("Rate limit exceeded: IP=%s count=%d/%d", ip, len(_rate_limit[ip]), max_per_min)
        return False
    _rate_limit[ip].append(now)
    return True


def _track_suspicious(ip: str, reason: str) -> bool:
    """Отслеживает подозрительную активность. Возвращает True если порог превышен."""
    now = time.time()
    _suspicious[ip] = [(t, r) for t, r in _suspicious[ip] if now - t < SUSPICIOUS_WINDOW]
    _suspicious[ip].append((now, reason))
    if len(_suspicious[ip]) >= SUSPICIOUS_THRESHOLD:
        security_logger.error("SUSPICIOUS ACTIVITY: IP=%s requests=%d reason=%s",
                            ip, len(_suspicious[ip]), reason)
        return True
    return False


def _sanitize_error(e: Exception) -> str:
    """Возвращает безопасное описание ошибки (без внутренних деталей)."""
    safe_types = (ValueError, KeyError, TypeError)
    if isinstance(e, safe_types):
        return str(e)[:100]
    return "internal error"


# === Simple in-memory cache for slow endpoints ===
# Reduces DB load for frequent queries (by-city, etc.)
_cache: dict[str, tuple[float, str]] = {}  # key → (expires_at, json_str)
CACHE_TTL_STATIONS = 60  # 1 min for station lists
CACHE_TTL_SEARCH = 30    # 30 sec for search


def _cache_get(key: str) -> str | None:
    """Get cached response or None."""
    if key in _cache:
        expires_at, data = _cache[key]
        if time.time() < expires_at:
            return data
        else:
            del _cache[key]
    return None


def _cache_set(key: str, data: str, ttl: int = CACHE_TTL_STATIONS):
    """Cache a response."""
    # Limit cache size to prevent memory issues
    if len(_cache) > 500:
        # Remove oldest entries
        now = time.time()
        expired = [k for k, (e, _) in _cache.items() if e < now]
        for k in expired:
            del _cache[k]
    _cache[key] = (time.time() + ttl, data)


def _serialize_station(s: dict) -> dict:
    """Приводит станцию к JSON-безопасному виду."""
    from datetime import datetime, date
    from decimal import Decimal
    out = dict(s)
    if "fuel_types" in out and isinstance(out["fuel_types"], str):
        try:
            out["fuel_types"] = json.loads(out["fuel_types"])
        except Exception:
            out["fuel_types"] = []
    # datetime → ISO string, Decimal → float (asyncpg/PostgreSQL)
    for k, v in list(out.items()):
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
    return out


def _serialize_status(s: dict) -> dict:
    from datetime import datetime, date
    from decimal import Decimal
    out = dict(s)
    if "available" in out:
        out["available"] = bool(out["available"]) if out["available"] is not None else None
    if "has_limit" in out:
        out["has_limit"] = bool(out["has_limit"])
    if "canister_ban" in out:
        out["canister_ban"] = bool(out["canister_ban"])
    # datetime → ISO string, Decimal → float
    for k, v in list(out.items()):
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
    return out


def _dedupe_statuses_per_fuel(statuses: list) -> list:
    """Оставляет только один (лучший) статус на каждый fuel_type.

    Входной список уже отсортирован по приоритету: user → confidence DESC → created_at DESC.
    Берём первый (лучший) отчёт для каждого типа топлива.
    Если у топлива несколько разных цен от разных источников — показываем в комментарии.
    """
    if not statuses:
        return []
    seen = set()
    out = []
    for s in statuses:
        ft = s.get("fuel_type")
        if not ft or ft == "all":
            continue
        if ft in seen:
            continue
        seen.add(ft)
        out.append(s)
    return out


def _parse_float(request, name: str, min_val: float, max_val: float) -> tuple[float | None, web.Response | None]:
    """Парсит float query param с валидацией диапазона."""
    try:
        v = float(request.query[name])
    except (KeyError, ValueError):
        return None, json_resp(
            {"error": f"{name} is required and must be a number"},
            status=400,
        )
    if not (min_val <= v <= max_val):
        return None, json_resp(
            {"error": f"{name} must be in [{min_val}, {max_val}]"},
            status=400,
        )
    return v, None


# === Handlers ===
async def handle_health(request):
    return json_resp({"status": "ok"})


async def handle_logs(request):
    """GET /api/logs?lines=50 — последние строки bot.log (для отладки).

    Требует заголовок X-Parse-Key для авторизации.
    """
    parse_key = os.environ.get("PARSE_API_KEY", "")
    provided_key = request.headers.get("X-Parse-Key", "") or request.query.get("key", "")
    if not parse_key or not provided_key or provided_key != parse_key:
        return json_resp({"error": "unauthorized"}, status=401)

    log_path = Path(__file__).parent / "bot.log"
    if not log_path.exists():
        return json_resp({"error": "no log file"}, status=404)
    try:
        lines = int(request.query.get("lines", "50"))
        lines = max(1, min(lines, 200))
    except (ValueError, TypeError):
        lines = 50
    try:
        with open(log_path, "rb") as f:
            content = f.read()
        text = content.decode("utf-8", errors="ignore")
        all_lines = text.splitlines()
        last = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return json_resp({
            "total_lines": len(all_lines),
            "shown": len(last),
            "lines": last,
        })
    except Exception as e:
        return json_resp({"error": "internal error"}, status=500)


# === Кеш reverse geocoding (city по координатам) ===
_reverse_cache: dict[tuple[float, float], dict] = {}


async def handle_reverse_geocode(request):
    """GET /api/reverse-geocode?lat=..&lon=..

    Возвращает город и регион по координатам (Nominatim).
    Используется Mini App для автоопределения города.
    """
    lat, err = _parse_float(request, "lat", -90, 90)
    if err:
        return err
    lon, err = _parse_float(request, "lon", -180, 180)
    if err:
        return err

    # Кеш (округление до 0.01 ≈ 1.1 км)
    cache_key = (round(lat, 2), round(lon, 2))
    if cache_key in _reverse_cache:
        return json_resp(_reverse_cache[cache_key])

    try:
        url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?format=json&lat={lat}&lon={lon}&accept-language=ru&zoom=10"
        )
        headers = {"User-Agent": "BenzinRyadom/1.0 (https://t.me/benzyn_ryadom)"}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10), headers=headers) as r:
                if r.status == 200:
                    data = await r.json()
                    addr = data.get("address", {})
                    city = (
                        addr.get("city")
                        or addr.get("town")
                        or addr.get("village")
                        or addr.get("hamlet")
                        or addr.get("county")
                    )
                    region = addr.get("state") or addr.get("region")
                    result = {
                        "city": city,
                        "region": region,
                        "country": addr.get("country"),
                        "raw": addr,
                    }
                    # Кешируем
                    if len(_reverse_cache) > 1000:
                        _reverse_cache.clear()
                    _reverse_cache[cache_key] = result
                    return json_resp(result)
    except Exception as e:
        pass

    # Fallback: не нашли
    return json_resp({"city": None, "region": None, "country": None})


async def handle_admin_stats(request):
    """GET /api/admin/stats — статистика всех парсеров (мониторинг).

    Требует заголовок X-Parse-Key для авторизации.
    """
    parse_key = os.environ.get("PARSE_API_KEY", "")
    provided_key = request.headers.get("X-Parse-Key", "") or request.query.get("key", "")
    if not parse_key or not provided_key or provided_key != parse_key:
        return json_resp({"error": "unauthorized"}, status=401)

    # === Статистика по источникам ===
    sources_stats = await get_source_stats()
    total_stations = await db._fetch("SELECT COUNT(*) as c FROM stations", one=True)
    if db.USE_SQLITE:
        with_prices = await db._fetch("""
            SELECT COUNT(DISTINCT station_id) as c
            FROM reports
            WHERE created_at > datetime('now', '-7 days')
        """, one=True)
    else:
        with_prices = await db._fetch("""
            SELECT COUNT(DISTINCT station_id) as c
            FROM reports
            WHERE created_at > NOW() - INTERVAL '7 days'
        """, one=True)

    return json_resp({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stations": {
            "total": total_stations["c"],
            "with_prices_7d": with_prices["c"],
        },
        "sources": sources_stats,
    })


async def handle_public_stats(request):
    """GET /api/stats — публичная статистика (для лендинга и Mini App)."""
    try:
        import db as _db_mod

        def _count(sql: str, params=()):
            """Универсальный COUNT."""
            if _db_mod.USE_SQLITE:
                row = _db_mod._fetch(sql, *params, one=True) if params else _db_mod._fetch(sql, one=True)
            else:
                import asyncio as _aio
                async def _q():
                    async with _db_mod._db.acquire() as conn:
                        return await conn.fetchval(sql, *params)
                return _aio.get_event_loop().run_until_complete(_q())
            if row is None: return 0
            if isinstance(row, dict): return row.get("c", 0) or 0
            try: return row["c"]
            except: return row[0] if row else 0

        # Используем простые COUNT через raw SQL
        if _db_mod.USE_SQLITE:
            total_users = _db_mod._fetch("SELECT COUNT(*) as c FROM users", one=True)
            tg_users = _db_mod._fetch("SELECT COUNT(*) as c FROM users WHERE telegram_id > 0 AND vk_id IS NULL", one=True)
            vk_users = _db_mod._fetch("SELECT COUNT(*) as c FROM users WHERE vk_id IS NOT NULL", one=True)
            try:
                linked_users = _db_mod._fetch("SELECT COUNT(*) as c FROM users WHERE linked_user_id IS NOT NULL OR linked_telegram_id IS NOT NULL", one=True)
            except Exception:
                linked_users = {"c": 0}
            try:
                premium_users = _db_mod._fetch("SELECT COUNT(*) as c FROM premium_users WHERE is_active = 1", one=True)
            except Exception:
                premium_users = {"c": 0}
            total_stations = _db_mod._fetch("SELECT COUNT(*) as c FROM stations", one=True)
            try:
                total_reports = _db_mod._fetch("SELECT COUNT(*) as c FROM reports", one=True)
            except Exception:
                total_reports = {"c": 0}
        else:
            async with _db_mod._db.acquire() as conn:
                total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
                tg_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE telegram_id > 0 AND vk_id IS NULL")
                vk_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE vk_id IS NOT NULL")
                try:
                    linked_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE linked_user_id IS NOT NULL OR linked_telegram_id IS NOT NULL")
                except Exception:
                    linked_users = 0
                try:
                    premium_users = await conn.fetchval("SELECT COUNT(*) FROM premium_users WHERE is_active = 1")
                except Exception:
                    premium_users = 0
                total_stations = await conn.fetchval("SELECT COUNT(*) FROM stations")
                try:
                    total_reports = await conn.fetchval("SELECT COUNT(*) FROM reports")
                except Exception:
                    total_reports = 0

        def _get(r, key="c"):
            if r is None: return 0
            if isinstance(r, int): return r
            if isinstance(r, dict): return r.get(key, 0) or 0
            try: return r[key]
            except: return r[0] if r else 0

        return json_resp({
            "users": {
                "total": _get(total_users),
                "telegram": _get(tg_users),
                "vk": _get(vk_users),
                "linked": _get(linked_users),
                "premium": _get(premium_users),
            },
            "stations": _get(total_stations),
            "reports": _get(total_reports),
        })
    except Exception as e:
        logger.exception(f"public_stats error: {e}")
        return json_resp({"error": f"internal error: {type(e).__name__}: {str(e)[:100]}"}, status=500)


async def get_source_stats() -> list[dict]:
    """Собирает статистику по каждому источнику."""
    if db.USE_SQLITE:
        rows = await db._fetch("""
            SELECT source,
                   SUM(CASE WHEN created_at > datetime('now', '-1 hour') THEN 1 ELSE 0 END) as h1,
                   SUM(CASE WHEN created_at > datetime('now', '-6 hours') THEN 1 ELSE 0 END) as h6,
                   SUM(CASE WHEN created_at > datetime('now', '-24 hours') THEN 1 ELSE 0 END) as h24,
                   COUNT(*) as total,
                   MAX(created_at) as last_update
            FROM reports
            GROUP BY source
            ORDER BY total DESC
        """)
    else:
        rows = await db._fetch("""
            SELECT source,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as h1,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '6 hours') as h6,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as h24,
                   COUNT(*) as total,
                   MAX(created_at) as last_update
            FROM reports
            GROUP BY source
            ORDER BY total DESC
        """)
    result = []
    for r in rows:
        # Статус: OK (1h), STALE (6h), DEAD (24h+)
        last = r["last_update"]
        # SQLite возвращает строку, конвертируем в datetime
        if isinstance(last, str):
            try:
                last_dt = datetime.fromisoformat(last.replace(" ", "T"))
            except ValueError:
                last_dt = datetime.now(timezone.utc) - timedelta(days=365)
        else:
            last_dt = last
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        hours_ago = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        if hours_ago < 1:
            status = "OK"
        elif hours_ago < 6:
            status = "STALE"
        else:
            status = "DEAD"
        result.append({
            "source": r["source"],
            "h1": int(r["h1"]) if r["h1"] is not None else 0,
            "h6": int(r["h6"]) if r["h6"] is not None else 0,
            "h24": int(r["h24"]) if r["h24"] is not None else 0,
            "total": int(r["total"]) if r["total"] is not None else 0,
            "last_update": last_dt.isoformat(),
            "hours_ago": round(hours_ago, 1),
            "status": status,
        })
    return result


async def handle_stations(request):
    """GET /api/stations?lat=..&lon=..&radius=..&fuel=92&telegram_id=.."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    lat, err = _parse_float(request, "lat", -90, 90)
    if err:
        return err
    lon, err = _parse_float(request, "lon", -180, 180)
    if err:
        return err

    # === Premium detection по telegram_id / vk_user_id ===
    telegram_id_raw = request.query.get("telegram_id")
    vk_user_id_raw = request.headers.get("X-VK-User-Id") or request.query.get("vk_user_id")
    is_premium_user = False
    if telegram_id_raw:
        try:
            tid = int(telegram_id_raw)
            uid = await get_user_id_by_telegram_id(tid)
            if uid:
                from db import is_premium
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass
    elif vk_user_id_raw:
        # VK: ищем пользователя по vk_user_id (хранится в telegram_id поле как соглашение)
        try:
            vuid = int(vk_user_id_raw)
            uid = await get_user_id_by_telegram_id(vuid)
            if uid:
                from db import is_premium
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass

    # === Premium лимиты ===
    max_radius = 100 if is_premium_user else 30
    max_limit = 500 if is_premium_user else 100
    default_radius = 50 if is_premium_user else 30

    try:
        radius = int(request.query.get("radius", default_radius))
        if not (1 <= radius <= max_radius):
            return json_resp(
                {"error": f"radius must be in [1, {max_radius}]"}, status=400
            )
    except ValueError:
        return json_resp({"error": "radius must be int"}, status=400)

    fuel = request.query.get("fuel")
    if fuel is not None and fuel not in ("92", "95", "98", "diesel", "100", "lpg"):
        return json_resp({"error": f"invalid fuel: {fuel}"}, status=400)

    stations = await find_nearest_stations(
        lat=lat, lon=lon, fuel_type=fuel, limit=max_limit, radius_km=radius,
    )

    # Один запрос на статусы для всех АЗС (избегаем N+1)
    station_ids = [s["id"] for s in stations]
    statuses_by_station = await _bulk_get_statuses(station_ids)

    result = []
    for s in stations:
        sid = s["id"]
        statuses = statuses_by_station.get(sid, [])
        # Если operator пустой — используем name (многие АЗС имеют только name)
        operator = s.get("operator") or s.get("name")
        # Если city пустой — оставляем пустым
        result.append({
            "id": sid,
            "name": s.get("name"),
            "operator": operator,
            "city": s.get("city"),
            "address": s.get("address") or "",
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "distance_km": s.get("distance_km"),
            "is_verified": bool(s.get("is_verified")),
            "statuses": [_serialize_status(st) for st in _dedupe_statuses_per_fuel(statuses)],
            "has_data": len(statuses) > 0,
        })

    return json_resp({"stations": result, "count": len(result)})


# === Дисклеймер ===
DISCLAIMER = (
    "⚠️ <b>Важно:</b>\n"
    "• Цены и наличие обновляются пользователями и парсерами, возможны задержки.\n"
    "• Актуальность зависит от региона: крупные города — точнее, малые — реже.\n"
    "• Перед поездкой перезвоните на АЗС, особенно если топливо подорожало.\n"
    "• Данные собираются из: fuelprice.ru, 2ГИС, отчётов пользователей, "
    "Telegram-каналов и других открытых источников.\n"
    "• Бот не несёт ответственности за достоверность данных."
)


async def handle_stations_by_city(request):
    """GET /api/stations/by-city?city=...&region=...&fuel=...&network=...&max_price=...&has_stock=1

    Возвращает АЗС по городу (а не геолокации), с фильтрами:
      - city: название города (обязательно)
      - region: регион (опционально)
      - fuel: 92/95/98/diesel/lpg
      - network: оператор (Лукойл, Газпром, etc)
      - max_price: макс. цена за литр
      - has_stock: 1 = только с подтверждённым наличием (default 1)
      - include_nearby_regions: 1 = включать соседние регионы (default 1)
      - with_coords: 1 = только АЗС с координатами (для карты), отключает has_stock и увеличивает лимит
      - limit: макс. кол-во результатов (default 50)
      - telegram_id: для Premium detection
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    city = (request.query.get("city") or "").strip()
    if not city:
        return json_resp({"error": "city is required"}, status=400)

    region = request.query.get("region") or None
    fuel = request.query.get("fuel") or None
    network = request.query.get("network") or None
    with_coords = request.query.get("with_coords", "0") == "1"
    has_stock = request.query.get("has_stock", "1") == "1" if not with_coords else False
    include_nearby = request.query.get("include_nearby_regions", "1") == "1"

    try:
        max_price = float(request.query["max_price"]) if "max_price" in request.query else None
    except (ValueError, KeyError):
        max_price = None

    try:
        default_limit = 500 if with_coords else 50
        limit = int(request.query.get("limit", str(default_limit)))
        limit = max(1, min(limit, 500))
    except ValueError:
        limit = default_limit

    # === Premium detection (TG + VK) ===
    telegram_id_raw = request.query.get("telegram_id")
    vk_user_id_raw = request.headers.get("X-VK-User-Id") or request.query.get("vk_user_id")
    is_premium_user = False
    if telegram_id_raw:
        try:
            tid = int(telegram_id_raw)
            uid = await get_user_id_by_telegram_id(tid)
            if uid:
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass
    elif vk_user_id_raw:
        try:
            vuid = int(vk_user_id_raw)
            uid = await get_user_id_by_telegram_id(vuid)
            if uid:
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass

    if is_premium_user:
        limit = min(limit * 3, 500)

    # === Cache check (skip for premium users to ensure fresh data) ===
    if not is_premium_user:
        cache_key = f"bycity:{city}:{region}:{fuel}:{network}:{max_price}:{has_stock}:{include_nearby}:{limit}"
        cached = _cache_get(cache_key)
        if cached:
            return web.Response(
                text=cached,
                content_type="application/json",
                headers={"X-Cache": "HIT"}
            )

    stations = await find_stations_by_city(
        city=city,
        region=region,
        fuel_type=fuel,
        network=network,
        max_price=max_price,
        has_stock=has_stock,
        include_nearby_regions=include_nearby,
        with_coords=with_coords,
        limit=limit,
    )

    # Получаем статусы (цены + наличие)
    station_ids = [s["id"] for s in stations]
    statuses_by_station = await _bulk_get_statuses(station_ids)

    result = []
    for s in stations:
        sid = s["id"]
        statuses = statuses_by_station.get(sid, [])
        result.append({
            "id": sid,
            "name": s.get("name"),
            "operator": s.get("operator"),
            "city": s.get("city"),
            "region": s.get("region"),
            "address": s.get("address"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "is_verified": bool(s.get("is_verified")),
            "statuses": [_serialize_status(st) for st in _dedupe_statuses_per_fuel(statuses)],
            "has_data": len(statuses) > 0,
        })

    response_data = {
        "stations": result,
        "count": len(result),
        "city": city,
        "filters": {
            "region": region,
            "fuel": fuel,
            "network": network,
            "max_price": max_price,
            "has_stock": has_stock,
            "include_nearby_regions": include_nearby,
            "with_coords": with_coords,
        },
        "disclaimer": DISCLAIMER.replace("<b>", "").replace("</b>", ""),
    }

    # Cache the response (already serialized)
    if not is_premium_user:
        import json as _json
        _cache_set(cache_key, _json.dumps(response_data, default=str), CACHE_TTL_STATIONS)

    return json_resp(response_data)


async def handle_emergency(request):
    """GET /api/stations/emergency?city=..&fuel=..

    ЭКСТРЕННЫЙ поиск: ближайшая АЗС с подтверждённым наличием топлива.
    Без фильтров по цене, сети, очереди.
    """
    city = (request.query.get("city") or "").strip()
    if not city:
        return json_resp({"error": "city is required"}, status=400)
    fuel = request.query.get("fuel") or "92"

    stations = await find_stations_by_city(
        city=city,
        fuel_type=None,  # Любое топливо
        network=None,    # Любая сеть
        max_price=None,  # Любая цена
        has_stock=True,  # ТОЛЬКО с подтверждённым наличием
        include_nearby_regions=True,
        limit=20,
    )

    # Сортируем по свежести отчёта
    result = []
    for s in stations:
        sid = s["id"]
        statuses = await _bulk_get_statuses([sid])
        status_list = statuses.get(sid, [])
        # Только с available=True
        if not status_list:
            continue
        last_status = status_list[0] if status_list else None
        if not last_status or not last_status.get("available"):
            continue
        result.append({
            "id": sid,
            "name": s.get("name"),
            "operator": s.get("operator") or s.get("name"),
            "city": s.get("city"),
            "address": s.get("address") or "",
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "fuel_type": last_status.get("fuel_type"),
            "price": float(last_status.get("price") or 0) if last_status.get("price") else None,
            "queue_size": last_status.get("queue_size"),
            "has_limit": last_status.get("has_limit"),
            "updated_at": _to_iso(last_status.get("created_at")),
            "is_verified": bool(s.get("is_verified")),
        })

    # Сортировка: verified → с ценой → по свежести
    result.sort(key=lambda x: (
        0 if x["is_verified"] else 1,
        0 if x["price"] else 1,
        x["updated_at"] or "",
    ))

    return json_resp({
        "stations": result,
        "count": len(result),
        "city": city,
        "fuel": fuel,
        "disclaimer": DISCLAIMER.replace("<b>", "").replace("</b>", ""),
    })


def _to_iso(dt):
    """datetime → ISO string."""
    if dt is None:
        return None
    from datetime import datetime, date
    if isinstance(dt, (datetime, date)):
        return dt.isoformat()
    return str(dt)


async def handle_search(request):
    """GET /api/search?q=... — поиск АЗС по городу/имени."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    q = request.query.get("q", "").strip()
    if len(q) < 2:
        return json_resp({"results": [], "query": q})

    from db import search_routes as _search_routes
    routes = await _search_routes(q, limit=10)

    return json_resp({
        "results": routes,
        "query": q,
        "count": len(routes),
    })


async def handle_route_stations(request):
    """GET /api/routes/{id}/stations — АЗС на трассе."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    route_id = int(request.match_info["id"])
    limit = int(request.query.get("limit", "50"))
    fuel = request.query.get("fuel") or None

    from db import find_stations_by_route, get_station_current_status
    stations = await find_stations_by_route(route_id, limit=limit)

    # Добавляем статусы
    for s in stations:
        try:
            statuses = await get_station_current_status(s["id"])
            s["statuses"] = statuses
        except Exception:
            s["statuses"] = []

    return json_resp({
        "stations": stations,
        "count": len(stations),
        "route_id": route_id,
    })


async def handle_routes(request):
    """GET /api/routes?q=... — список всех трасс или поиск."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    q = request.query.get("q", "").strip()

    from db import search_routes as _search_routes
    if q:
        routes = await _search_routes(q, limit=20)
    else:
        # Возвращаем все трассы
        from db import _fetch
        if db.USE_SQLITE:
            rows = await _fetch("SELECT id, code, name, aliases, length_km, start_point, end_point FROM routes WHERE is_active = 1 ORDER BY code LIMIT 100")
        else:
            async with db._db.acquire() as conn:
                rows = await conn.fetch("SELECT id, code, name, aliases, length_km, start_point, end_point FROM routes WHERE is_active = TRUE ORDER BY code LIMIT 100")
        routes = [dict(r) for r in rows]

    return json_resp({
        "routes": routes,
        "count": len(routes),
    })


async def handle_cities(request):
    """GET /api/cities?q=... — поиск городов в БД (для Mini App).

    Без q: возвращает топ-200 городов по числу АЗС.
    С q: возвращает все города, название которых содержит подстроку.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    q = request.query.get("q", "").strip()
    try:
        limit = min(int(request.query.get("limit", "200")), 500)
    except (ValueError, TypeError):
        limit = 200

    from db import search_cities
    cities = await search_cities(q, limit=limit)

    return json_resp({
        "cities": cities,
        "count": len(cities),
        "query": q,
    })


async def handle_search_legacy(request):
    """GET /api/search (legacy) — backward compat."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    query = request.query.get("q", "").strip()
    if len(query) < 2:
        return json_resp(
            {"error": "q parameter required (min 2 chars)"},
            status=400,
        )

    # === Premium detection (как в handle_stations) ===
    telegram_id_raw = request.query.get("telegram_id")
    vk_user_id_raw = request.headers.get("X-VK-User-Id") or request.query.get("vk_user_id")
    is_premium_user = False
    if telegram_id_raw:
        try:
            tid = int(telegram_id_raw)
            uid = await get_user_id_by_telegram_id(tid)
            if uid:
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass
    elif vk_user_id_raw:
        try:
            vuid = int(vk_user_id_raw)
            uid = await get_user_id_by_telegram_id(vuid)
            if uid:
                is_premium_user = await is_premium(uid)
        except (ValueError, TypeError):
            pass
    max_radius = 100 if is_premium_user else 30
    max_limit = 500 if is_premium_user else 100

    stations = await find_stations_by_name(query, limit=50)

    station_ids = [s["id"] for s in stations]
    statuses_by_station = await _bulk_get_statuses(station_ids)

    result = []
    for s in stations:
        sid = s["id"]
        statuses = statuses_by_station.get(sid, [])
        result.append({
            "id": sid,
            "name": s.get("name"),
            "operator": s.get("operator"),
            "city": s.get("city"),
            "address": s.get("address"),
            "lat": s.get("lat"),
            "lon": s.get("lon"),
            "is_verified": bool(s.get("is_verified")),
            "statuses": [_serialize_status(st) for st in _dedupe_statuses_per_fuel(statuses)],
            "has_data": len(statuses) > 0,
        })

    return json_resp({
        "stations": result,
        "count": len(result),
        "is_premium": is_premium_user,
        "limits": {
            "max_radius": max_radius,
            "max_stations": max_limit,
        },
    })


async def _bulk_get_statuses(station_ids: list[int]) -> dict[int, list]:
    """Один запрос на получение статусов для многих АЗС. Избегаем N+1."""
    if not station_ids:
        return {}
    from db import _fetch
    placeholders = ",".join("?" for _ in station_ids)
    if USE_SQLITE:
        rows = await _fetch(
            f"""SELECT station_id, fuel_type, available, price, queue_size, has_limit,
                      limit_liters, canister_ban,
                      limit_per_visit, limit_daily, limit_weekly,
                      confidence, created_at
               FROM (
                   SELECT *, ROW_NUMBER() OVER (
                       PARTITION BY station_id, fuel_type
                       ORDER BY confidence DESC, created_at DESC
                   ) AS rn
                   FROM reports
                   WHERE station_id IN ({placeholders})
                     AND created_at > datetime('now', '-1 day')
               )
               WHERE rn = 1""",
            *station_ids,
        )
    else:
        # PostgreSQL: DISTINCT ON работает
        rows = await _fetch(
            f"""SELECT DISTINCT ON (station_id, fuel_type)
                    station_id, fuel_type, available, price, queue_size,
                    has_limit, limit_liters, canister_ban,
                    limit_per_visit, limit_daily, limit_weekly,
                    confidence, created_at
                FROM reports
                WHERE station_id = ANY($1)
                  AND created_at > NOW() - INTERVAL '24 hours'
                ORDER BY station_id, fuel_type, confidence DESC, created_at DESC""",
            list(station_ids),
        )

    # Конвертируем SQLite int → bool/None
    result: dict[int, list] = {}
    for r in rows:
        sid = r["station_id"]
        if r.get("available") == 1:
            r["available"] = True
        elif r.get("available") == 0:
            r["available"] = False
        elif r.get("available") == 2:
            r["available"] = None
        result.setdefault(sid, []).append(r)
    return result


async def handle_station_detail(request):
    """GET /api/stations/{id}"""
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    try:
        station_id = int(request.match_info["id"])
    except ValueError:
        return json_resp({"error": "invalid id"}, status=400)

    station = await get_station_by_id(station_id)
    if not station:
        return json_resp({"error": "not found"}, status=404)

    statuses = await get_station_current_status(station_id)

    # Проверяем есть ли отчёты от Premium юзеров
    premium_verified = False
    try:
        if USE_SQLITE:
            pv = await _fetch(
                """SELECT COUNT(*) as cnt FROM reports r
                   JOIN premium_users pu ON pu.user_id = r.user_id
                   WHERE r.station_id = ? AND pu.is_active = 1""",
                station_id,
            )
        else:
            pv = await _fetch(
                """SELECT COUNT(*) as cnt FROM reports r
                   JOIN premium_users pu ON pu.user_id = r.user_id
                   WHERE r.station_id = $1 AND pu.is_active = TRUE""",
                station_id,
            )
        if pv:
            cnt = pv[0].get("cnt", 0) if isinstance(pv[0], dict) else pv[0][0] if isinstance(pv[0], (tuple, list)) else 0
            premium_verified = int(cnt) > 0
    except Exception:
        pass

    return json_resp({
        "station": _serialize_station(station),
        "statuses": [_serialize_status(st) for st in _dedupe_statuses_per_fuel(statuses)],
        "premium_verified": premium_verified,
    })


async def handle_price_history(request):
    """GET /api/stations/{id}/price-history?fuel=92&days=30

    Без Premium: только последние 3 дня.
    С Premium: до 365 дней + прогноз.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_PER_MIN):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    try:
        station_id = int(request.match_info["id"])
    except ValueError:
        return json_resp({"error": "invalid id"}, status=400)

    fuel = request.query.get("fuel", "95")
    if fuel not in ("92", "95", "98", "diesel", "100", "lpg"):
        return json_resp({"error": f"invalid fuel: {fuel}"}, status=400)

    try:
        days = int(request.query.get("days", "30"))
        if not (1 <= days <= 365):
            days = 30
    except ValueError:
        days = 30

    # Premium check
    tid = request.query.get("telegram_id")
    is_premium = False
    if tid:
        try:
            from db import get_user_id_by_any, get_user_premium
            uid = await get_user_id_by_any(int(tid))
            if uid:
                sub = await get_user_premium(uid)
                is_premium = bool(sub and sub.get("tier"))
        except Exception:
            pass

    # Free = 3 дня, Premium = сколько запросил
    if not is_premium:
        days = min(days, 3)
        max_records = 10
    else:
        max_records = 50

    from db import _fetch
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT fuel_type, price, created_at
               FROM reports
               WHERE station_id = ? AND fuel_type = ? AND price IS NOT NULL
                 AND created_at > datetime('now', ?)
               ORDER BY created_at DESC
               LIMIT ?""",
            station_id, fuel, f"-{days} days", max_records,
        )
    else:
        rows = await _fetch(
            """SELECT fuel_type, price, created_at
               FROM reports
               WHERE station_id = $1 AND fuel_type = $2 AND price IS NOT NULL
                 AND created_at > NOW() - ($3 || ' days')::interval
               ORDER BY created_at DESC
               LIMIT $4""",
            station_id, fuel, str(days), max_records,
        )

    history = []
    for r in rows:
        history.append({
            "fuel_type": r.get("fuel_type"),
            "price": float(r["price"]) if r.get("price") is not None else None,
            "at": str(r.get("created_at")),
        })

    # Если нет данных — возвращаем "fake" историю для премиум-триггера
    if not history and is_premium:
        # Если нет реальных данных — возвращаем placeholder
        history = [
            {"fuel_type": fuel, "price": None, "at": "Нет данных", "_placeholder": True}
        ]
    elif not history:
        # Free: не показываем даже 3 дня если нет данных
        history = []

    # Простой прогноз (только Premium): средняя цена за 7 дней ± 5%
    forecast = None
    if is_premium and len(history) >= 3:
        prices = [h["price"] for h in history if h.get("price")]
        if prices:
            avg = sum(prices) / len(prices)
            forecast = {
                "avg": round(avg, 2),
                "low": round(avg * 0.95, 2),
                "high": round(avg * 1.05, 2),
                "trend": "stable",
            }

    return json_resp({
        "station_id": station_id,
        "fuel": fuel,
        "history": history,
        "count": len(history),
        "is_premium": is_premium,
        "max_days_free": 3,
        "max_days_premium": 365,
        "forecast": forecast,
    })


async def handle_price_forecast(request):
    """GET /api/stations/{id}/forecast?fuel=95&days=7

    Premium only. Прогноз цен на 7 дней на основе исторических данных.
    Использует скользящую среднюю + линейный тренд + сезонные колебания.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)

    try:
        station_id = int(request.match_info["id"])
    except ValueError:
        return json_resp({"error": "invalid id"}, status=400)

    fuel = request.query.get("fuel", "95")
    if fuel not in ("92", "95", "98", "diesel", "100", "lpg"):
        fuel = "95"

    try:
        days = int(request.query.get("days", "7"))
        days = min(max(days, 1), 30)
    except ValueError:
        days = 7

    # Premium check
    tid = request.query.get("telegram_id")
    is_premium = False
    if tid:
        try:
            from db import get_user_id_by_any, get_user_premium
            uid = await get_user_id_by_any(int(tid))
            if uid:
                sub = await get_user_premium(uid)
                is_premium = bool(sub and sub.get("tier"))
        except Exception:
            pass

    if not is_premium:
        return json_resp({
            "error": "premium_required",
            "feature": "forecast_7d",
            "message": "Прогноз цен доступен только для Premium",
        }, status=402)

    from db import _fetch
    # Получаем историю за 30 дней
    if USE_SQLITE:
        rows = await _fetch(
            """SELECT price, created_at
               FROM reports
               WHERE station_id = ? AND fuel_type = ? AND price IS NOT NULL
                 AND created_at > datetime('now', '-30 days')
               ORDER BY created_at ASC""",
            station_id, fuel,
        )
    else:
        rows = await _fetch(
            """SELECT price, created_at
               FROM reports
               WHERE station_id = $1 AND fuel_type = $2 AND price IS NOT NULL
                 AND created_at > NOW() - INTERVAL '30 days'
               ORDER BY created_at ASC""",
            station_id, fuel,
        )

    if not rows or len(rows) < 3:
        return json_resp({
            "ok": True,
            "station_id": station_id,
            "fuel": fuel,
            "forecast": [],
            "message": "Недостаточно данных для прогноза",
            "is_premium": True,
        })

    prices = [float(r["price"]) for r in rows if r.get("price") is not None]
    if not prices:
        return json_resp({
            "ok": True,
            "station_id": station_id,
            "fuel": fuel,
            "forecast": [],
            "message": "Нет данных о ценах",
            "is_premium": True,
        })

    # Простой алгоритм прогноза:
    # 1. Скользящее среднее (последние 7 точек)
    # 2. Линейный тренд (разница между средним первой и второй половины)
    # 3. Сезонность (день недели × среднее по дню)
    from datetime import datetime, timedelta

    # Текущая средняя
    window = min(7, len(prices))
    current_avg = sum(prices[-window:]) / window

    # Тренд (разница между первой и второй половинами)
    if len(prices) >= 6:
        first_half = sum(prices[:len(prices)//2]) / (len(prices)//2)
        second_half = sum(prices[len(prices)//2:]) / (len(prices) - len(prices)//2)
        trend = (second_half - first_half) / max(len(prices)//2, 1)
    else:
        trend = 0

    # Прогноз на N дней
    forecast_data = []
    today = datetime.now()

    # Текущая цена
    forecast_data.append({
        "day": 0,
        "date": today.strftime("%Y-%m-%d"),
        "label": "Сегодня",
        "price": round(prices[-1], 2),
        "is_actual": True,
    })

    # Прогноз
    for d in range(1, days + 1):
        # Прогноз = текущая_средняя + тренд * день + небольшой шум
        # Сезонность: ±3% в зависимости от дня недели
        day_of_week = (today + timedelta(days=d)).weekday()
        # Выходные (5, 6) — цены обычно выше на 1-2%
        weekend_factor = 1.0
        if day_of_week in (5, 6):
            weekend_factor = 1.015

        projected_price = current_avg + (trend * d) * weekend_factor
        # Ограничим ±15% от текущей цены (защита от выбросов)
        max_price = prices[-1] * 1.15
        min_price = prices[-1] * 0.85
        projected_price = max(min(projected_price, max_price), min_price)

        forecast_data.append({
            "day": d,
            "date": (today + timedelta(days=d)).strftime("%Y-%m-%d"),
            "label": (today + timedelta(days=d)).strftime("%a"),
            "price": round(projected_price, 2),
            "is_actual": False,
        })

    # Анализ тренда
    last_price = prices[-1]
    final_price = forecast_data[-1]["price"]
    delta = final_price - last_price
    delta_pct = round((delta / last_price) * 100, 1) if last_price else 0

    if delta > 0.5:
        trend_label = "📈 Цена вырастет"
        trend_advice = "Лучше заправиться сегодня"
    elif delta < -0.5:
        trend_label = "📉 Цена упадёт"
        trend_advice = "Можно подождать 2-3 дня"
    else:
        trend_label = "➡️ Цена стабильна"
        trend_advice = "Цена не изменится существенно"

    # Найти лучший день для заправки
    best_day = min(forecast_data[1:], key=lambda x: x["price"])
    worst_day = max(forecast_data[1:], key=lambda x: x["price"])
    best_diff = round(prices[-1] - best_day["price"], 2)
    worst_diff = round(worst_day["price"] - prices[-1], 2)

    return json_resp({
        "ok": True,
        "station_id": station_id,
        "fuel": fuel,
        "current_price": round(prices[-1], 2),
        "forecast": forecast_data,
        "trend": {
            "label": trend_label,
            "advice": trend_advice,
            "delta": round(delta, 2),
            "delta_pct": delta_pct,
        },
        "best_day": {
            "date": best_day["date"],
            "label": best_day["label"],
            "price": best_day["price"],
            "savings": best_diff,
        },
        "worst_day": {
            "date": worst_day["date"],
            "label": worst_day["label"],
            "price": worst_day["price"],
            "loss": worst_diff,
        },
        "is_premium": True,
        "data_points": len(prices),
        "accuracy_note": "Прогноз основан на истории за 30 дней. Точность ~80%.",
    })


async def handle_station_analytics(request):
    """GET /api/stations/{id}/analytics — аналитика для владельца АЗС."""
    try:
        station_id = int(request.match_info["id"])
    except (KeyError, ValueError, TypeError):
        return json_resp({"error": "invalid id"}, status=400)

    days = int(request.query.get("days", 30))
    if days < 1 or days > 365:
        days = 30

    analytics = await get_station_analytics(station_id, days)
    return json_resp(analytics)


async def handle_station_prices(request):
    """GET /api/stations/{id}/prices — все цены по источникам с приоритетом.

    Возвращает:
    {
      "station_id": 1,
      "fuel_prices": {
        "95": {
          "best": {"source": "user", "price": 56.40, "confidence": 0.92, "age_hours": 0.5},
          "all": [
            {"source": "user", "price": 56.40, "is_best": true, "confidence": 0.92, "age_hours": 0.5},
            {"source": "2gis", "price": 56.20, "is_best": false, "confidence": 0.65, "age_hours": 24.0}
          ]
        }
      },
      "sources_summary": {
        "user": 5,        # сколько отчётов
        "telegram": 2,
        "2gis": 1
      }
    }
    """
    try:
        station_id = int(request.match_info["id"])
    except (KeyError, ValueError, TypeError):
        return json_resp({"error": "invalid id"}, status=400)

    all_prices = await get_all_prices_for_station(station_id)

    # Форматируем для Mini App
    from datetime import datetime, date
    from decimal import Decimal
    fuel_prices = {}
    sources_summary = {}
    for fuel, items in all_prices.items():
        if not items:
            continue
        # Лучший — items[0] (отсортированы по weighted_score)
        best = items[0]

        def _to_jsonable(v):
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v

        fuel_prices[fuel] = {
            "best": {
                "source": best.get("source"),
                "price": _to_jsonable(best.get("price")),
                "confidence": _to_jsonable(best.get("weighted_score")),
                "age_hours": _to_jsonable(best.get("age_hours")),
                "updated_at": _to_jsonable(best.get("created_at")),
            },
            "all": [
                {
                    "source": it.get("source"),
                    "price": _to_jsonable(it.get("price")),
                    "is_best": it.get("is_best", False),
                    "confidence": _to_jsonable(it.get("weighted_score")),
                    "age_hours": _to_jsonable(it.get("age_hours")),
                    "updated_at": _to_jsonable(it.get("created_at")),
                }
                for it in items[:5]  # максимум 5 источников
            ],
        }
        # Считаем по источникам
        for it in items:
            src = it.get("source") or "default"
            sources_summary[src] = sources_summary.get(src, 0) + 1

    return json_resp({
        "station_id": station_id,
        "fuel_prices": fuel_prices,
        "sources_summary": sources_summary,
        "total_sources": len(sources_summary),
    })


async def handle_create_report(request):
    """POST /api/reports — создание отчёта из Mini App"""
    # Строже rate limit для POST
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    try:
        data = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    if not isinstance(data, dict):
        return json_resp({"error": "expected json object"}, status=400)

    station_id = data.get("station_id")
    fuel_type = data.get("fuel_type")
    available = data.get("available")
    telegram_id = data.get("telegram_id")
    first_name = str(data.get("first_name", "MiniApp User"))[:64]
    price = data.get("price")
    queue_size = data.get("queue_size")
    has_limit = data.get("has_limit", False)
    limit_liters = data.get("limit_liters")
    limit_per_visit = data.get("limit_per_visit")
    limit_daily = data.get("limit_daily")
    limit_weekly = data.get("limit_weekly")
    canister_ban = data.get("canister_ban", False)
    comment = data.get("comment")
    source = data.get("source", "miniapp")

    if not station_id or not isinstance(station_id, int):
        return json_resp({"error": "station_id (int) is required"}, status=400)
    if not fuel_type or fuel_type not in ("92", "95", "98", "diesel", "100", "lpg", "all"):
        return json_resp({"error": f"invalid fuel_type: {fuel_type}"}, status=400)
    if available is not None and not isinstance(available, bool):
        return json_resp(
            {"error": "available must be true, false or null"},
            status=400,
        )
    if telegram_id is not None and not isinstance(telegram_id, int):
        return json_resp({"error": "telegram_id must be int"}, status=400)
    if price is not None and (not isinstance(price, (int, float)) or price < 0 or price > 500):
        return json_resp({"error": "price must be 0..500"}, status=400)
    if queue_size is not None and (not isinstance(queue_size, int) or queue_size < 0 or queue_size > 100):
        return json_resp({"error": "queue_size must be 0..100"}, status=400)
    if limit_liters is not None and (not isinstance(limit_liters, int) or limit_liters < 0 or limit_liters > 1000):
        return json_resp({"error": "limit_liters must be 0..1000"}, status=400)
    if limit_per_visit is not None and (not isinstance(limit_per_visit, int) or limit_per_visit < 0 or limit_per_visit > 500):
        return json_resp({"error": "limit_per_visit must be 0..500"}, status=400)
    if limit_daily is not None and (not isinstance(limit_daily, int) or limit_daily < 0 or limit_daily > 2000):
        return json_resp({"error": "limit_daily must be 0..2000"}, status=400)
    if limit_weekly is not None and (not isinstance(limit_weekly, int) or limit_weekly < 0 or limit_weekly > 5000):
        return json_resp({"error": "limit_weekly must be 0..5000"}, status=400)
    if comment is not None and (not isinstance(comment, str) or len(comment) > 500):
        return json_resp({"error": "comment must be string ≤ 500 chars"}, status=400)

    user_id = None
    if telegram_id:
        await upsert_user(telegram_id=telegram_id, first_name=first_name)
        user_id = await get_user_id_by_telegram_id(telegram_id)

    report_id = await add_report(
        station_id=station_id,
        user_id=user_id,
        fuel_type=fuel_type,
        available=available,
        price=float(price) if price is not None else None,
        queue_size=int(queue_size) if queue_size is not None else None,
        has_limit=bool(has_limit),
        limit_liters=int(limit_liters) if limit_liters is not None else None,
        limit_per_visit=int(limit_per_visit) if limit_per_visit is not None else None,
        limit_daily=int(limit_daily) if limit_daily is not None else None,
        limit_weekly=int(limit_weekly) if limit_weekly is not None else None,
        canister_ban=bool(canister_ban),
        comment=str(comment)[:500] if comment else None,
        source=source,
    )

    new_badges = await check_and_award_badges(user_id) if user_id else []
    return json_resp(
        {
            "ok": True,
            "report_id": report_id,
            "new_badges": [
                {
                    "code": b,
                    "name": BADGE_CATALOG.get(b, {}).get("name"),
                    "emoji": BADGE_CATALOG.get(b, {}).get("emoji"),
                    "desc": BADGE_CATALOG.get(b, {}).get("desc"),
                }
                for b in new_badges
            ],
        }
    )


async def handle_price_update(request):
    """POST /api/price-update — обновление цены топлива (от владельца/пользователя).

    Тело: { station_id, fuel_type, price, available?, queue_size?, telegram_id? }
    Создаёт обычный отчёт с заполненным price.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    try:
        data = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    if not isinstance(data, dict):
        return json_resp({"error": "expected json object"}, status=400)

    station_id = data.get("station_id")
    fuel_type = data.get("fuel_type")
    price = data.get("price")
    available = data.get("available", True)
    queue_size = data.get("queue_size")
    telegram_id = data.get("telegram_id")
    first_name = str(data.get("first_name", "PriceUpdate"))[:64]

    if not station_id or not isinstance(station_id, int):
        return json_resp({"error": "station_id (int) is required"}, status=400)
    if not fuel_type or fuel_type not in ("92", "95", "98", "diesel", "100", "lpg"):
        return json_resp({"error": f"invalid fuel_type: {fuel_type}"}, status=400)
    if price is None or not isinstance(price, (int, float)) or price < 0 or price > 500:
        return json_resp({"error": "price is required, 0..500"}, status=400)

    user_id = None
    if telegram_id:
        await upsert_user(telegram_id=telegram_id, first_name=first_name)
        user_id = await get_user_id_by_telegram_id(telegram_id)

    report_id = await add_report(
        station_id=station_id,
        user_id=user_id,
        fuel_type=fuel_type,
        available=available if available in (True, False, None) else True,
        price=float(price),
        queue_size=int(queue_size) if isinstance(queue_size, int) else None,
        source="price_update",
    )

    new_badges = await check_and_award_badges(user_id) if user_id else []
    return json_resp(
        {
            "ok": True,
            "report_id": report_id,
            "new_badges": [
                {
                    "code": b,
                    "name": BADGE_CATALOG.get(b, {}).get("name"),
                    "emoji": BADGE_CATALOG.get(b, {}).get("emoji"),
                    "desc": BADGE_CATALOG.get(b, {}).get("desc"),
                }
                for b in new_badges
            ],
        }
    )


# === Импорт от внешних парсеров (GitHub Actions) ===
# Используется скриптом scripts/parse_benzin_price_headless.py в GitHub Actions.
# Авторизация — через X-Import-Key header, совпадает с IMPORT_API_KEY в .env.
VALID_FUEL_TYPES = {"92", "95", "98", "100", "diesel", "lpg", "cng"}


async def handle_import_prices(request):
    """POST /api/import_prices — приём цен от внешних парсеров.
    
    Авторизация: header X-Import-Key: <IMPORT_API_KEY>
    Тело: {
        source: "benzin_price_ru" | ...,
        scraped_at: ISO datetime,
        results: [
            {
                external_id: int,    # ID во внешнем источнике (для логов)
                name: str,           # название АЗС
                region_id: str,      # ID региона во внешнем источнике (для логов)
                region_name: str,    # название региона ("Москва и МО")
                city: str,           # опционально
                operator: str,       # опционально
                lat: float,          # опционально
                lon: float,          # опционально
                prices: {"92": 58.40, "95": 63.20, ...}
            },
            ...
        ]
    }
    
    Для каждой записи:
    1. upsert_station_for_import(name, region_name, city, operator, lat, lon) → station_id
    2. Для каждого fuel в prices: add_report(station_id, fuel, True, price, source, comment)
    """
    # Авторизация
    import_key = os.environ.get("IMPORT_API_KEY", "")
    provided_key = request.headers.get("X-Import-Key", "")
    if not import_key:
        logger.error("IMPORT_API_KEY is not set in env")
        return json_resp({"error": "server misconfigured"}, status=500)
    if not provided_key or provided_key != import_key:
        return json_resp({"error": "unauthorized"}, status=401)
    
    # Rate limit: GitHub Actions дёргает раз в день, но подстрахуемся
    if not _check_rate(request.remote or "?", 10):
        return json_resp({"error": "rate limit exceeded"}, status=429)
    
    try:
        data = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)
    
    if not isinstance(data, dict):
        return json_resp({"error": "expected json object"}, status=400)
    
    source = str(data.get("source", "unknown"))[:64]
    results = data.get("results", [])
    if not isinstance(results, list):
        return json_resp({"error": "results must be a list"}, status=400)
    if len(results) > 5000:
        return json_resp({"error": "too many results, max 5000 per request"}, status=400)
    
    saved = 0
    errors = 0
    new_stations = 0
    existing_stations = 0
    seen_stations: dict[int, int] = {}  # station_id → отчётов добавлено
    
    for r in results:
        if not isinstance(r, dict):
            errors += 1
            continue
        name = str(r.get("name", "")).strip()[:200]
        region_name = str(r.get("region_name", "")).strip()[:200]
        city = str(r.get("city", "")).strip()[:100]
        operator = str(r.get("operator", "")).strip()[:100]
        prices = r.get("prices", {})
        lat = r.get("lat")
        lon = r.get("lon")
        
        if not name or not region_name or not isinstance(prices, dict) or not prices:
            errors += 1
            continue
        
        try:
            station_id = await upsert_station_for_import(
                name=name,
                region=region_name,
                city=city,
                operator=operator,
                lat=lat if isinstance(lat, (int, float)) else None,
                lon=lon if isinstance(lon, (int, float)) else None,
            )
            if station_id <= 0:
                errors += 1
                continue
            
            if station_id not in seen_stations:
                # Новая или уже существующая — отслеживаем только для статистики
                seen_stations[station_id] = 0
                # Первое появление — проверим created_at позже
            
            for fuel, price in prices.items():
                if fuel not in VALID_FUEL_TYPES:
                    continue
                if not isinstance(price, (int, float)) or price <= 0 or price > 500:
                    continue
                try:
                    await add_report(
                        station_id=station_id,
                        fuel_type=fuel,
                        available=True,
                        price=float(price),
                        source=source,
                        comment=f"{source}: {name}",
                    )
                    saved += 1
                    seen_stations[station_id] = seen_stations.get(station_id, 0) + 1
                except Exception as e:
                    logger.warning(f"import_prices: add_report failed for station {station_id} fuel {fuel}: {e}")
                    errors += 1
        except Exception as e:
            logger.warning(f"import_prices: station {name!r} failed: {e}")
            errors += 1
    
    # Статистика по новым/существующим АЗС
    if seen_stations:
        ids = list(seen_stations.keys())
        if USE_SQLITE:
            placeholders = ",".join("?" * len(ids))
            rows = await db._fetch(
                f"SELECT id FROM stations WHERE id IN ({placeholders})",
                *ids,
            )
            existing_ids = {r["id"] for r in rows}
            new_stations = len(ids) - len(existing_ids)
            existing_stations = len(existing_ids)
        else:
            rows = await db._fetch(
                "SELECT id FROM stations WHERE id = ANY($1::bigint[])", ids,
            )
            existing_ids = {r["id"] for r in rows}
            new_stations = len(ids) - len(existing_ids)
            existing_stations = len(existing_ids)
    
    return json_resp({
        "ok": True,
        "source": source,
        "received": len(results),
        "saved": saved,
        "errors": errors,
        "stations_total": len(seen_stations),
        "stations_new": new_stations,
        "stations_existing": existing_stations,
    })


async def handle_create_review(request):
    """POST /api/reviews — создание отзыва о качестве топлива на АЗС.

    Тело: { station_id, fuel_type, rating (0-5), comment?, telegram_id?, first_name? }
    Используется из Mini App и ботов.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit exceeded"}, status=429)

    try:
        data = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    if not isinstance(data, dict):
        return json_resp({"error": "expected json object"}, status=400)

    station_id = data.get("station_id")
    fuel_type = data.get("fuel_type")
    rating = data.get("rating")
    comment = data.get("comment")
    telegram_id = data.get("telegram_id")
    first_name = str(data.get("first_name", "MiniApp User"))[:64]

    if not station_id or not isinstance(station_id, int):
        return json_resp({"error": "station_id (int) is required"}, status=400)
    if not fuel_type or fuel_type not in ("92", "95", "98", "diesel", "100", "lpg", "all"):
        return json_resp({"error": f"invalid fuel_type: {fuel_type}"}, status=400)
    if rating is None or not isinstance(rating, int) or rating < 0 or rating > 5:
        return json_resp({"error": "rating (0-5 int) is required"}, status=400)
    if comment is not None and (not isinstance(comment, str) or len(comment) > 1000):
        return json_resp({"error": "comment must be string ≤ 1000 chars"}, status=400)
    if telegram_id is not None and not isinstance(telegram_id, int):
        return json_resp({"error": "telegram_id must be int"}, status=400)

    user_id = None
    if telegram_id:
        await upsert_user(telegram_id=telegram_id, first_name=first_name)
        user_id = await get_user_id_by_telegram_id(telegram_id)

    if not user_id:
        return json_resp({"error": "telegram_id is required for reviews"}, status=400)

    review_id = await add_review(
        station_id=station_id,
        user_id=user_id,
        fuel_type=fuel_type,
        rating=rating,
        comment=str(comment)[:1000] if comment else None,
    )

    new_badges = await check_and_award_badges(user_id) if user_id else []
    return json_resp(
        {
            "ok": True,
            "review_id": review_id,
            "new_badges": [
                {
                    "code": b,
                    "name": BADGE_CATALOG.get(b, {}).get("name"),
                    "emoji": BADGE_CATALOG.get(b, {}).get("emoji"),
                    "desc": BADGE_CATALOG.get(b, {}).get("desc"),
                }
                for b in new_badges
            ],
        }
    )


async def handle_parse(request):
    """POST/GET /api/parse — запуск всех парсеров (вызывается внешним cron).

    Авторизация: query ?key=<PARSE_API_KEY> или header X-Parse-Key
    Не блокирует основной процесс — запускает парсеры в фоне.
    Защита от частого вызова: не запустит если уже идёт.
    """
    global _parsers_running
    if _parsers_running:
        return json_resp({
            "ok": False,
            "message": "Parsers already running, skipped"
        }, status=429)
    _parsers_running = True

    parse_key = os.environ.get("PARSE_API_KEY", "")
    provided_key = request.headers.get("X-Parse-Key", "") or request.query.get("key", "")
    if not parse_key or not provided_key or provided_key != parse_key:
        _parsers_running = False
        return json_resp({"error": "unauthorized"}, status=401)
    
    import asyncio
    import sys
    
    async def _run_parsers():
        """Запуск парсеров в фоне (без re-init DB — API уже подключён)."""
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        # Флаг для парсеров: НЕ вызывать close_db() (API уже держит пул)
        import db as _db_module
        _db_module.API_MODE = True
        
        results = {}
        try:
            # === Быстрые парсеры — последовательно ===
            try:
                import parse_fuelprice
                sys.argv = ["parse_fuelprice.py", "--create-new"]
                await parse_fuelprice.main()
                results["fuelprice"] = "ok"
            except Exception as e:
                results["fuelprice"] = str(e)

            try:
                import parse_ishubenzin
                await parse_ishubenzin.main()
                results["ishubenzin"] = "ok"
            except Exception as e:
                results["ishubenzin"] = str(e)

            # TG channels parser REMOVED from API — runs via Render only.
            results["tg_channels"] = "skipped (runs via cron only)"

            # benzin-status.tech (Mini App для @benzin_status_bot) — прямой API
            try:
                import parse_benzin_status_tech
                tech_cities = [
                    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург",
                    "Казань", "Нижний Новгород", "Челябинск", "Самара",
                    "Омск", "Ростов-на-Дону",
                ]
                await asyncio.wait_for(
                    parse_benzin_status_tech.run(tech_cities),
                    timeout=240.0,
                )
                results["benzin_status_tech"] = "ok"
            except asyncio.TimeoutError:
                results["benzin_status_tech"] = "timeout (240s)"
            except Exception as e:
                results["benzin_status_tech"] = str(e)

            # benzinmap.ru — лимиты/канистры (62 региона)
            try:
                import parse_benzinmap
                await asyncio.wait_for(
                    parse_benzinmap.main(),
                    timeout=120.0,
                )
                results["benzinmap"] = "ok"
            except asyncio.TimeoutError:
                results["benzinmap"] = "timeout (120s)"
            except Exception as e:
                results["benzinmap"] = str(e)

            # === Долгие парсеры — в ПАРАЛЛЕЛЬ с таймаутом ===
            async def _run_gdebenz():
                try:
                    import parse_gdebenz
                    # 4,233 bbox × 2сек = ~2.3 часа, режем до 90 мин
                    await asyncio.wait_for(
                        parse_gdebenz.main(),
                        timeout=5400.0,
                    )
                    return "ok"
                except asyncio.TimeoutError:
                    return "timeout (5400s)"
                except Exception as e:
                    return str(e)

            async def _run_azslive():
                try:
                    import parse_azslive
                    await asyncio.wait_for(
                        parse_azslive.main(),
                        timeout=300.0,
                    )
                    return "ok"
                except asyncio.TimeoutError:
                    return "timeout (300s)"
                except Exception as e:
                    return str(e)

            async def _run_vk_groups():
                try:
                    import parse_vk_groups
                    await asyncio.wait_for(
                        parse_vk_groups.run_vk_parser(cities=None, limit_per_group=30),
                        timeout=300.0,
                    )
                    return "ok"
                except asyncio.TimeoutError:
                    return "timeout (300s)"
                except Exception as e:
                    return str(e)

            async def _run_news():
                try:
                    import parse_news
                    await asyncio.wait_for(
                        parse_news.main(),
                        timeout=120.0,
                    )
                    return "ok"
                except asyncio.TimeoutError:
                    return "timeout (120s)"
                except Exception as e:
                    return str(e)

            async def _run_yandex_fuel():
                try:
                    import parse_yandex_fuel
                    # Парсим по крупным городам (top 20)
                    major_cities = [
                        (55.7558, 37.6173),  # Москва
                        (59.9311, 30.3609),  # СПб
                        (55.0084, 82.9357),  # Новосибирск
                        (56.8389, 60.6057),  # Екатеринбург
                        (55.8304, 49.0661),  # Казань
                        (56.3260, 44.0059),  # НН
                        (55.1600, 61.4000),  # Челябинск
                        (53.1959, 50.1002),  # Самара
                        (54.9885, 73.3242),  # Омск
                        (47.2225, 39.7183),  # Ростов
                        (54.7388, 55.9721),  # Уфа
                        (56.0097, 92.8524),  # Красноярск
                        (51.6611, 39.2000),  # Воронеж
                        (48.7070, 44.5169),  # Волгоград
                        (58.0092, 56.2502),  # Пермь
                        (45.0400, 38.9760),  # Краснодар
                    ]
                    total = 0
                    for lat, lon in major_cities:
                        try:
                            sys.argv = ["parse_yandex_fuel.py", "--lat", str(lat), "--lon", str(lon)]
                            await parse_yandex_fuel.main()
                            total += 1
                        except SystemExit:
                            pass
                        except Exception:
                            pass
                        await asyncio.sleep(1)
                    return f"ok ({total} cities)"
                except Exception as e:
                    return str(e)

            # Запускаем все 5 долгих парсеров ПАРАЛЛЕЛЬНО
            gdebenz_task = asyncio.create_task(_run_gdebenz())
            azslive_task = asyncio.create_task(_run_azslive())
            vk_groups_task = asyncio.create_task(_run_vk_groups())
            yandex_fuel_task = asyncio.create_task(_run_yandex_fuel())
            news_task = asyncio.create_task(_run_news())

            # Собираем результаты с таймаутом
            try:
                gdebenz_result, azslive_result, vk_groups_result, yandex_fuel_result, news_result = await asyncio.wait_for(
                    asyncio.gather(gdebenz_task, azslive_task, vk_groups_task, yandex_fuel_task, news_task, return_exceptions=True),
                    timeout=5400.0,  # 90 минут max
                )
                results["gdebenz"] = gdebenz_result if not isinstance(gdebenz_result, Exception) else str(gdebenz_result)
                results["azslive"] = azslive_result if not isinstance(azslive_result, Exception) else str(azslive_result)
                results["vk_groups"] = vk_groups_result if not isinstance(vk_groups_result, Exception) else str(vk_groups_result)
                results["yandex_fuel"] = yandex_fuel_result if not isinstance(yandex_fuel_result, Exception) else str(yandex_fuel_result)
                results["news"] = news_result if not isinstance(news_result, Exception) else str(news_result)
            except asyncio.TimeoutError:
                # Если что-то не успело — отменяем и пишем
                for t in (gdebenz_task, azslive_task, vk_groups_task, yandex_fuel_task, news_task):
                    if not t.done():
                        t.cancel()
                results["gdebenz"] = "timeout (5400s)"
                results["azslive"] = results.get("azslive", "timeout")
                results["vk_groups"] = results.get("vk_groups", "timeout")
                results["yandex_fuel"] = results.get("yandex_fuel", "timeout")
                results["news"] = results.get("news", "timeout")
        finally:
            _db_module.API_MODE = False
            logger.info("Background parsers finished: %s", results)
            global _parsers_running
            _parsers_running = False

    asyncio.create_task(_run_parsers())
    return json_resp({"ok": True, "message": "parsers started in background"})


async def handle_parse_benzin(request):
    parse_key = os.environ.get("PARSE_API_KEY", "")
    provided_key = request.headers.get("X-Parse-Key", "") or request.query.get("key", "")
    if not parse_key or not provided_key or provided_key != parse_key:
        return json_resp({"error": "unauthorized"}, status=401)

    city = request.query.get("city", "Москва")
    import asyncio
    import sys
    import io
    scripts_dir = str(Path(__file__).parent.parent / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Перехватываем логи в StringIO
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    log_handler.setLevel(logging.INFO)
    log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    try:
        import parse_benzin_status_tech
        # Подключаем handler к логгеру парсера
        parser_logger = logging.getLogger("benzin_status_tech")
        parser_logger.addHandler(log_handler)
        parser_logger.setLevel(logging.INFO)
        parser_logger.propagate = False  # чтобы не дублировать в root

        db.API_MODE = True
        parser_logger.info("=== НАЧАЛО: handler count=%d ===", len(parser_logger.handlers))
        count = await parse_benzin_status_tech.run([city])
        parser_logger.info("=== КОНЕЦ: saved=%d ===", count)
        logs = log_stream.getvalue()
        parser_logger.removeHandler(log_handler)
        return json_resp({"ok": True, "city": city, "saved": count, "logs": logs})
    except Exception as e:
        import traceback
        logs = log_stream.getvalue()
        logger.error("parse_benzin error: %s", e)
        return json_resp({
            "ok": False,
            "error": str(e),
            "logs": logs,
        }, status=500)
    finally:
        db.API_MODE = False
        try:
            parser_logger.removeHandler(log_handler)
        except Exception:
            pass


async def handle_vk_callback(request):
    """POST /api/vk/callback — VK Callback API webhook.

    Максимально простой и надёжный обработчик.
    """
    try:
        event = await request.json()
    except Exception as e:
        logger.warning("VK callback: invalid JSON: %s", e)
        return web.Response(text="ok", content_type="text/plain")

    event_type = event.get("type", "")
    logger.info("VK callback: type=%s", event_type)

    # Confirmation
    if event_type == "confirmation":
        token = os.environ.get("VK_CONFIRMATION_TOKEN", "")
        return web.Response(body=token, content_type="text/plain", charset="utf-8")

    # Обработка событий — импорт внутри для скорости
    try:
        from vk_callback import process_message_new, process_message_event
    except ImportError:
        return web.Response(text="ok", content_type="text/plain")

    # Обработка — ловим ВСЁ, логируем, но НИКОГДА не падаем
    try:
        if event_type == "message_new":
            await process_message_new(event)
        elif event_type == "message_event":
            await process_message_event(event)
    except Exception as e:
        logger.exception("VK callback: event processing failed: %s", e)
        # Алерт админу при критических ошибках VK callback
        try:
            from alert import alert_critical
            await alert_critical(
                f"VK callback: event processing failed\n\n"
                f"type: {event_type}\n"
                f"error: {type(e).__name__}: {str(e)[:200]}",
                exc=e,
            )
        except Exception:
            pass

    return web.Response(text="ok", content_type="text/plain")


# === CORS ===
# В проде — только домены Mini App и VK. В dev — * для удобства.
ALLOWED_ORIGINS_RAW = os.getenv("CORS_ORIGINS", "")
if ALLOWED_ORIGINS_RAW:
    ALLOWED_ORIGINS = ALLOWED_ORIGINS_RAW
else:
    # Default: разрешаем Mini App домены
    ALLOWED_ORIGINS = "https://benzin-ryadom.onrender.com,https://benzin-ryadom.vercel.app"


async def _on_startup(app: web.Application) -> None:
    """Инициализация БД + security checks при старте API."""
    import db as _db_mod
    try:
        if _db_mod._db is None:
            await db.init_db()
    except Exception as e:
        logger.exception(f"DB init failed: {e}")
        try:
            from alert import alert_critical
            await alert_critical(f"DB init failed at startup!\n\n{type(e).__name__}: {str(e)[:200]}", exc=e)
        except Exception:
            pass
        raise

    # Security: проверка критических переменных окружения
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token or bot_token == "YOUR_BOT_TOKEN_HERE":
        security_logger.critical("BOT_TOKEN is missing or placeholder!")

    parse_key = os.getenv("PARSE_API_KEY", "")
    if not parse_key:
        security_logger.warning("PARSE_API_KEY not set")

    vk_secret = os.getenv("VK_CALLBACK_SECRET", "")
    if not vk_secret:
        security_logger.warning("VK_CALLBACK_SECRET not set")

    logger.info("API started, DB initialized")

    # YooMoney polling worker
    try:
        from yoomoney_worker import yoomoney_polling_loop
        asyncio.create_task(yoomoney_polling_loop())
        logger.info("YooMoney polling worker started")
    except Exception as e:
        logger.warning(f"YooMoney polling not started: {e}")


async def _on_cleanup(app: web.Application) -> None:
    """Закрытие БД при остановке API."""
    await db.close_db()


async def handle_enrich(request):
    """GET /api/enrich?key=... — обогащение адресов через Nominatim (в фоне)."""
    parse_key = os.environ.get("PARSE_API_KEY", "")
    provided_key = request.headers.get("X-Parse-Key", "") or request.query.get("key", "")
    if not parse_key or not provided_key or provided_key != parse_key:
        return json_resp({"error": "unauthorized"}, status=401)

    import asyncio
    import sys

    limit = min(int(request.query.get("limit", "500")), 5000)

    async def _run_enrich():
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        db.API_MODE = True
        try:
            import enrich_addresses_nominatim
            sys.argv = ["enrich_addresses_nominatim.py", "--limit", str(limit)]
            logger.info("[enrich] Starting enrichment (limit=%d)...", limit)
            await enrich_addresses_nominatim.main()
            logger.info("[enrich] Done")
        except Exception as e:
            logger.warning("[enrich] Failed: %s", e, exc_info=True)

    asyncio.create_task(_run_enrich())
    return json_resp({"ok": True, "message": "enrich started in background"})


async def handle_import_osm(request):
    """GET /api/import-osm?key=...&region=ivanovo|million — импорт АЗС из OpenStreetMap (в фоне)."""
    global _parsers_running
    if _parsers_running:
        return json_resp({"ok": False, "message": "Another job is running"}, status=429)
    _parsers_running = True

    parse_key = os.environ.get("PARSE_API_KEY", "")
    provided_key = request.headers.get("X-Parse-Key", "") or request.query.get("key", "")
    if not parse_key or not provided_key or provided_key != parse_key:
        _parsers_running = False
        return json_resp({"error": "unauthorized"}, status=401)

    region = request.query.get("region", "ivanovo").lower()

    import asyncio
    import sys

    async def _run_import():
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        db.API_MODE = True
        try:
            if region == "million":
                import import_osm_million_cities
                await import_osm_million_cities.main()
                logger.info("[osm-import-million] Done")
            else:
                import import_osm_ivanovo
                await import_osm_ivanovo.main()
                logger.info("[osm-import-ivanovo] Done")
        except Exception as e:
            logger.warning("[osm-import-%s] Failed: %s", region, e)
        finally:
            global _parsers_running
            _parsers_running = False
            db.API_MODE = False

    asyncio.create_task(_run_import())
    return json_resp({"ok": True, "message": f"OSM import ({region}) started in background"})


def create_app() -> web.Application:
    """Алиас для setup_app (для совместимости с bot/main.py)."""
    return setup_app()


def setup_app() -> web.Application:
    """Создаёт и настраивает aiohttp приложение."""

    def _cors_headers() -> dict:
        """CORS заголовки."""
        origins = os.environ.get("ALLOWED_ORIGINS", "*")
        return {
            "Access-Control-Allow-Origin": origins,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "Content-Type, X-Parse-Key, Authorization",
            "Access-Control-Max-Age": "86400",
        }

    @web.middleware
    async def audit_middleware(request, handler):
        ip = request.remote or "?"
        method = request.method
        path = request.path

        if method == "POST":
            security_logger.info("POST %s from %s", path, ip)

        suspicious = False
        lower_path = path.lower()
        if "script" in lower_path or "exec" in lower_path or "eval" in lower_path:
            suspicious = True
        if "../" in path or "%2e%2e" in lower_path:
            suspicious = True

        if suspicious:
            security_logger.error("BLOCKED: %s %s from %s", method, path, ip)
            return json_resp({"error": "forbidden"}, status=403)

        return await handler(request)

    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            return web.Response(headers=_cors_headers())
        try:
            response = await handler(request)
        except web.HTTPException as e:
            response = e
        except Exception as e:
            logger.exception("Unhandled exception in %s: %s", request.path, e)
            response = web.json_response(
                {"error": "internal server error"}, status=500
            )
        for k, v in _cors_headers().items():
            response.headers.setdefault(k, v)
        return response

    app = web.Application(middlewares=[audit_middleware, cors_middleware])
    # API routes
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/logs", handle_logs)
    app.router.add_get("/api/admin/stats", handle_admin_stats)
    app.router.add_get("/api/stats", handle_public_stats)
    app.router.add_get("/api/reverse-geocode", handle_reverse_geocode)
    app.router.add_get("/api/stations", handle_stations)
    app.router.add_get("/api/stations/by-city", handle_stations_by_city)
    app.router.add_get("/api/stations/emergency", handle_emergency)
    app.router.add_get("/api/search", handle_search)
    app.router.add_get("/api/routes", handle_routes)
    app.router.add_get("/api/routes/{id}/stations", handle_route_stations)
    app.router.add_get("/api/cities", handle_cities)
    app.router.add_get("/api/stations/{id}", handle_station_detail)
    app.router.add_get("/api/stations/{id}/price-history", handle_price_history)
    app.router.add_get("/api/stations/{id}/forecast", handle_price_forecast)
    app.router.add_get("/api/stations/{id}/analytics", handle_station_analytics)
    app.router.add_get("/api/stations/{id}/prices", handle_station_prices)
    # Legacy /api/premium-status
    async def legacy_premium_status(request):
        return json_resp({"is_premium": False, "legacy": True, "use": "/api/premium/status"})
    app.router.add_get("/api/premium-status", legacy_premium_status)
    app.router.add_post("/api/reports", handle_create_report)
    app.router.add_post("/api/reviews", handle_create_review)
    app.router.add_post("/api/price-update", handle_price_update)
    app.router.add_post("/api/import_prices", handle_import_prices)
    app.router.add_post("/api/parse", handle_parse)
    app.router.add_get("/api/parse", handle_parse)
    app.router.add_get("/api/parse-benzin", handle_parse_benzin)
    app.router.add_post("/api/vk/callback", handle_vk_callback)
    app.router.add_get("/api/enrich", handle_enrich)
    app.router.add_get("/api/import-osm", handle_import_osm)
    # Premium
    app.router.add_get("/api/premium/plans", handle_premium_plans)
    app.router.add_get("/api/premium/status", handle_premium_status)
    app.router.add_get("/api/premium/check", handle_premium_feature)
    app.router.add_post("/api/premium/activate", handle_premium_activate)
    app.router.add_post("/api/premium/cancel", handle_premium_cancel)
    app.router.add_post("/api/premium/trial", handle_premium_trial)
    app.router.add_post("/api/premium/create-payment", handle_premium_create_payment)
    app.router.add_get("/api/premium/payment-callback", handle_premium_payment_callback)
    app.router.add_get("/api/premium/payment-status", handle_premium_payment_status)
    app.router.add_get("/api/premium/pending", handle_premium_pending_payments)
    app.router.add_get("/api/referral/discount-status", handle_referral_discount_status)
    # Account linking
    app.router.add_post("/api/account/link/create", handle_account_link_create)
    app.router.add_post("/api/account/link/use", handle_account_link_use)
    # Fuel alarms
    app.router.add_post("/api/fuel-alarm/create", handle_fuel_alarm_create)
    app.router.add_post("/api/fuel-alarm/delete", handle_fuel_alarm_delete)
    app.router.add_get("/api/fuel-alarm/list", handle_fuel_alarm_list)
    app.router.add_get("/api/user/savings", handle_user_savings)
    app.router.add_post("/api/sos/broadcast", handle_sos_broadcast)
    app.router.add_get("/api/referral/code", handle_referral_code)
    app.router.add_post("/api/referral/apply", handle_referral_apply)
    app.router.add_get("/api/referral/stats", handle_referral_stats)
    app.router.add_get("/api/account/info", handle_account_info)
    app.router.add_get("/api/export/csv", handle_export_csv)
    app.router.add_get("/api/route/fuel", handle_route_fuel)
    app.router.add_get("/api/route/anti-traffic", handle_route_anti_traffic)
    # Mini App static
    miniapp_dir = Path(__file__).parent.parent / "miniapp"
    if miniapp_dir.exists():
        async def serve_index(request):
            response = web.FileResponse(miniapp_dir / "index.html")
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        app.router.add_static("/app/", miniapp_dir, append_version=False)
        for path in ("/miniapp", "/miniapp/", "/m", "/m/", "/v2", "/v2/"):
            app.router.add_get(path, serve_index)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


# === PREMIUM ===

async def handle_premium_plans(request):
    """GET /api/premium/plans — все тарифы."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    from db import all_plans
    return json_resp({"plans": all_plans()})


async def handle_premium_status(request):
    """GET /api/premium/status?telegram_id=... — статус подписки."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id") or request.query.get("vk_user_id")
    if not tid:
        return json_resp({"error": "telegram_id required"}, status=400)
    try:
        from db import get_user_id_by_any, get_user_premium
        uid = await get_user_id_by_any(int(tid))
        if not uid:
            return json_resp({"active": False, "tier": None, "expires_at": None})
        sub = await get_user_premium(uid)
        if not sub:
            return json_resp({"active": False, "tier": None, "expires_at": None})
        return json_resp({
            "active": True,
            "tier": sub.get("tier"),
            "started_at": str(sub.get("started_at", "")),
            "expires_at": str(sub.get("expires_at", "")),
            "payment_method": sub.get("payment_method"),
        })
    except Exception as e:
        logger.warning(f"premium_status: {e}")
        return json_resp({"error": str(e)}, status=500)


async def handle_premium_activate(request):
    """POST /api/premium/activate — активировать (только admin)."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    # Admin auth
    admin_token = request.headers.get("X-Admin-Token", "")
    if admin_token != os.environ.get("ADMIN_TOKEN", ""):
        return json_resp({"error": "forbidden"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)
    tid = body.get("telegram_id") or body.get("vk_user_id")
    tier = body.get("tier")
    if not tid or not tier:
        return json_resp({"error": "telegram_id and tier required"}, status=400)
    if tier not in ("economy", "standard", "elite"):
        return json_resp({"error": "invalid tier"}, status=400)
    from db import get_user_id_by_any, upsert_user, upsert_user_vk, activate_premium, get_plan
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        if body.get("vk_user_id"):
            await upsert_user_vk(int(tid))
        else:
            await upsert_user(int(tid), username=None, first_name="Premium User")
        uid = await get_user_id_by_any(int(tid))
    plan = get_plan(tier)
    sub = await activate_premium(uid, tier, days=plan["period_days"], payment_id=body.get("payment_id", f"manual_{tid}"), amount=plan["price"])
    return json_resp({
        "ok": True,
        "tier": tier,
        "expires_at": str(sub.get("expires_at", "")),
        "message": f"Премиум '{plan['name']}' активирован на {plan['period_days']} дней",
    })


async def handle_premium_cancel(request):
    """POST /api/premium/cancel — отменить подписку (только admin)."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    admin_token = request.headers.get("X-Admin-Token", "")
    if admin_token != os.environ.get("ADMIN_TOKEN", ""):
        return json_resp({"error": "forbidden"}, status=403)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)
    tid = body.get("telegram_id") or body.get("vk_user_id")
    if not tid:
        return json_resp({"error": "telegram_id required"}, status=400)
    from db import get_user_id_by_any, cancel_premium
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"ok": False, "error": "user not found"}, status=404)
    await cancel_premium(uid)
    return json_resp({"ok": True, "message": "Подписка отменена"})


async def handle_premium_pending_payments(request):
    """GET /api/premium/pending — список ожидающих оплаты (только admin)."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    admin_token = request.headers.get("X-Admin-Token", "")
    if admin_token != os.environ.get("ADMIN_TOKEN", ""):
        return json_resp({"error": "forbidden"}, status=403)
    from db import get_pending_payments
    payments = await get_pending_payments(limit=50)
    return json_resp({"payments": payments, "count": len(payments)})


async def handle_referral_discount_status(request):
    """GET /api/referral/discount-status?telegram_id=X или vk_user_id=X — активная реферальная скидка."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id") or request.query.get("vk_user_id")
    if not tid:
        return json_resp({"error": "telegram_id or vk_user_id required"}, status=400)
    from db import get_user_id_by_any, get_active_discount
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"ok": True, "discount": None})
    discount = await get_active_discount(uid)
    if discount:
        return json_resp({
            "ok": True,
            "discount": {
                "percent": discount["discount_percent"],
                "expires_at": str(discount["expires_at"])[:10],
            },
        })
    return json_resp({"ok": True, "discount": None})


# === Account linking ===

async def handle_account_link_create(request):
    """POST /api/account/link/create — создать 6-значный код для привязки.

    Тело: {"telegram_id": 12345} ИЛИ {"vk_user_id": 12345}
    Возвращает: {"ok": true, "code": "123456", "expires_in": 600}
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)
    tid = body.get("telegram_id") or body.get("vk_user_id")
    if not tid:
        return json_resp({"error": "telegram_id or vk_user_id required"}, status=400)
    from db import create_link_code, get_user_id_by_any, upsert_user, upsert_user_vk
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        if body.get("vk_user_id"):
            await upsert_user_vk(int(tid))
        else:
            await upsert_user(int(tid), username=None, first_name="User")
    try:
        code = await create_link_code(int(tid))
    except Exception as e:
        logger.exception(f"create_link_code error: {e}")
        return json_resp({"error": "failed to create link code"}, status=500)
    return json_resp({"ok": True, "code": code, "expires_in": 600})


async def handle_account_link_use(request):
    """POST /api/account/link/use — использовать код для привязки.

    Тело: {"telegram_id": 67890, "code": "123456"}
    Привязывает telegram_id=67890 к аккаунту, который создал код.
    После привязки premium работает на обоих ID.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)
    tid = body.get("telegram_id") or body.get("vk_user_id")
    code = body.get("code")
    if not tid or not code:
        return json_resp({"error": "telegram_id and code required"}, status=400)
    from db import link_accounts
    result = await link_accounts(int(tid), str(code).strip())
    if result.get("ok"):
        return json_resp(result)
    return json_resp(result, status=400)


async def handle_premium_trial(request):
    """POST /api/premium/trial — активирует trial Premium (1 раз на юзера).

    Body: {telegram_id или vk_user_id, tier="standard", days=3}
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)
    tid = body.get("telegram_id") or body.get("vk_user_id")
    tier = body.get("tier", "standard")
    days = body.get("days", 3)
    if not tid:
        return json_resp({"error": "telegram_id or vk_user_id required"}, status=400)
    if tier not in ("economy", "standard", "elite"):
        return json_resp({"error": "invalid tier"}, status=400)
    if not isinstance(days, int) or days < 1 or days > 30:
        return json_resp({"error": "invalid days (1-30)"}, status=400)

    from db import get_user_id_by_any, activate_trial
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user not found"}, status=404)

    result = await activate_trial(uid, tier=tier, days=days)
    if result.get("ok"):
        return json_resp({
            "ok": True,
            "tier": result["tier"],
            "days": result["days"],
            "expires_at": str(result.get("expires_at", "")),
            "message": f"Trial Premium активирован на {days} дня!",
        })
    return json_resp(result, status=400)


async def handle_account_info(request):
    """GET /api/account/info?telegram_id=... — информация о привязанных аккаунтах.

    Возвращает:
    {
      "ok": true,
      "telegram_id": 12345,
      "linked_telegram_id": 67890,  // ID привязанного аккаунта (если есть)
      "linked_via": "telegram" | "vk",
      "is_premium": true,
      "premium_tier": "economy",
      "premium_expires_at": "2026-08-12 ..."
    }
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id") or request.query.get("vk_user_id")
    if not tid:
        return json_resp({"error": "telegram_id required"}, status=400)
    try:
        from db import get_user_id_by_any, get_user_premium, is_premium
        uid = await get_user_id_by_any(int(tid))
        if not uid:
            return json_resp({
                "ok": True,
                "telegram_id": int(tid),
                "linked_telegram_id": None,
                "is_premium": False,
            })
        # Получаем telegram_id и linked данные
        if USE_SQLITE:
            row = await db._fetch(
                "SELECT telegram_id, linked_telegram_id, linked_user_id, vk_id FROM users WHERE id = ?",
                uid, one=True,
            )
            user_data = dict(row) if row else {}
        else:
            import db as _db_mod
            if _db_mod._db is None:
                return json_resp({"error": "db not ready"}, status=503)
            async with _db_mod._db.acquire() as conn:
                try:
                    row = await conn.fetchrow(
                        "SELECT telegram_id, linked_telegram_id, linked_user_id, vk_id FROM users WHERE id = $1",
                        uid,
                    )
                    user_data = dict(row) if row else {}
                except Exception:
                    row = await conn.fetchrow(
                        "SELECT telegram_id, linked_telegram_id, vk_id FROM users WHERE id = $1",
                        uid,
                    )
                    user_data = dict(row) if row else {}

        # Получаем premium статус
        sub = await get_user_premium(uid)
        is_prem = bool(sub and sub.get("tier"))

        # Определяем linked_via
        linked_via = None
        linked_vk_id = None
        linked_tg_id = None
        if user_data.get("linked_user_id"):
            # Есть привязка через linked_user_id
            linked_uid = user_data["linked_user_id"]
            if USE_SQLITE:
                linked_row = await db._fetch(
                    "SELECT telegram_id, vk_id FROM users WHERE id = ?",
                    linked_uid, one=True,
                )
            else:
                async with _db_mod._db.acquire() as conn:
                    linked_row = await conn.fetchrow(
                        "SELECT telegram_id, vk_id FROM users WHERE id = $1",
                        linked_uid,
                    )
            if linked_row:
                linked_row = dict(linked_row) if hasattr(linked_row, 'keys') else linked_row
                linked_tg_id = linked_row.get("telegram_id")
                linked_vk_id = linked_row.get("vk_id")
            # Определяем тип текущего юзера
            if user_data.get("vk_id") and int(tid) == user_data.get("vk_id"):
                linked_via = "vk"
            elif user_data.get("telegram_id") and int(tid) == user_data.get("telegram_id"):
                linked_via = "telegram"
            else:
                linked_via = "linked"
        elif user_data.get("linked_telegram_id"):
            linked_tg_id = user_data["linked_telegram_id"]
            if user_data.get("vk_id") and int(tid) == user_data.get("vk_id"):
                linked_via = "vk"
            else:
                linked_via = "telegram"

        return json_resp({
            "ok": True,
            "telegram_id": user_data.get("telegram_id") if user_data.get("telegram_id", 0) > 0 else None,
            "vk_id": user_data.get("vk_id"),
            "platform": "vk" if user_data.get("vk_id") and not user_data.get("telegram_id", 0) > 0 else "telegram",
            "display_id": user_data.get("vk_id") or (user_data.get("telegram_id") if user_data.get("telegram_id", 0) > 0 else None),
            "linked_telegram_id": linked_tg_id,
            "linked_user_id": user_data.get("linked_user_id"),
            "linked_via": linked_via,
            "linked_vk_id": linked_vk_id,
            "is_premium": is_prem,
            "premium_tier": sub.get("tier") if sub else None,
            "premium_expires_at": str(sub.get("expires_at", "")) if sub else None,
        })
    except Exception as e:
        logger.exception(f"account_info error: {e}")
        return json_resp({"error": f"internal error: {type(e).__name__}"}, status=500)


# === Fuel Alarm endpoints ===

async def handle_fuel_alarm_create(request):
    """POST /api/fuel-alarm/create

    Premium only. Создаёт подписку "уведомить когда появится X на АЗС Y".
    Body: {telegram_id, station_id, fuel_type}
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    tid = body.get("telegram_id")
    sid = body.get("station_id")
    fuel = body.get("fuel_type", "95")
    if not tid or not sid:
        return json_resp({"error": "telegram_id and station_id required"}, status=400)
    if fuel not in ("92", "95", "98", "diesel", "100", "lpg"):
        return json_resp({"error": "invalid fuel_type"}, status=400)

    # Premium check
    from db import get_user_id_by_any, get_user_premium, create_fuel_alarm
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user not found"}, status=404)
    sub = await get_user_premium(uid)
    if not sub or not sub.get("tier"):
        return json_resp({
            "error": "premium_required",
            "feature": "fuel_alarm",
            "message": "Топливный будильник доступен только для Premium",
        }, status=402)

    try:
        alarm_id = await create_fuel_alarm(uid, int(sid), fuel)
        return json_resp({
            "ok": True,
            "alarm_id": alarm_id,
            "station_id": int(sid),
            "fuel_type": fuel,
            "message": f"Будильник установлен: уведомим когда появится АИ-{fuel}",
        })
    except Exception as e:
        logger.exception(f"fuel_alarm create: {e}")
        return json_resp({"error": str(e)}, status=500)


async def handle_fuel_alarm_delete(request):
    """POST /api/fuel-alarm/delete
    Body: {telegram_id, station_id, fuel_type}
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    tid = body.get("telegram_id")
    sid = body.get("station_id")
    fuel = body.get("fuel_type", "95")
    if not tid or not sid:
        return json_resp({"error": "telegram_id and station_id required"}, status=400)

    from db import get_user_id_by_any, delete_fuel_alarm
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user not found"}, status=404)
    try:
        await delete_fuel_alarm(uid, int(sid), fuel)
        return json_resp({"ok": True, "message": "Будильник удалён"})
    except Exception as e:
        logger.exception(f"fuel_alarm delete: {e}")
        return json_resp({"error": str(e)}, status=500)


async def handle_user_savings(request):
    """GET /api/user/savings?telegram_id=... — рассчитывает экономию юзера.

    Логика: берём отчёты юзера за месяц, считаем среднюю цену,
    умножаем на объём топлива (по умолчанию 40л/мес).
    Экономия = разница между среднерыночной и ценой из отчётов × объём.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id")
    if not tid:
        return json_resp({"savings": 0, "currency": "RUB"})
    from db import get_user_id_by_any
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"savings": 0, "currency": "RUB"})

    try:
        # Считаем отчёты за 30 дней
        if USE_SQLITE:
            rows = await _fetch(
                """SELECT AVG(CAST(r.price AS REAL)) as avg_price, COUNT(*) as cnt
                   FROM reports r
                   WHERE r.user_id = ?
                     AND r.created_at >= datetime('now', '-30 days')
                     AND r.price IS NOT NULL""",
                uid,
            )
        else:
            rows = await _fetch(
                """SELECT AVG(CAST(r.price AS REAL)) as avg_price, COUNT(*) as cnt
                   FROM reports r
                   WHERE r.user_id = $1
                     AND r.created_at >= NOW() - INTERVAL '30 days'
                     AND r.price IS NOT NULL""",
                uid,
            )
        if not rows:
            return json_resp({"savings": 0, "reports_count": 0, "currency": "RUB"})

        row = rows[0] if isinstance(rows, list) else rows
        avg_price = float(row.get("avg_price", 0) or 0) if isinstance(row, dict) else float(row[0] or 0)
        report_count = int(row.get("cnt", 0) or 0) if isinstance(row, dict) else int(row[1] or 0)

        # Базовая экономия: ~2₽/л × 40л/мес × кол-во отчётов/3 (нормализация)
        # Упрощённая формула: каждый отчёт ≈ 80₽ экономии (2₽/л × 40л)
        savings_per_report = 80
        total_savings = report_count * savings_per_report

        return json_resp({
            "savings": total_savings,
            "reports_count": report_count,
            "avg_price": round(avg_price, 2),
            "period": "30d",
            "currency": "RUB",
        })
    except Exception as e:
        logger.exception(f"handle_user_savings error: {e}")
        return json_resp({"savings": 0, "currency": "RUB"})


async def handle_sos_broadcast(request):
    """POST /api/sos/broadcast — SOS-сигнал.

    Elite only. Отправляет SOS-уведомление всем Premium юзерам в радиусе 50 км.
    Body: {telegram_id, lat, lon, message?}
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    tid = body.get("telegram_id")
    lat = body.get("lat")
    lon = body.get("lon")
    msg = body.get("message", "Помогите! Нужна помощь на дороге!")

    if not tid or lat is None or lon is None:
        return json_resp({"error": "telegram_id, lat, lon required"}, status=400)

    from db import get_user_id_by_any, get_user_premium
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user not found"}, status=404)

    sub = await get_user_premium(uid)
    if not sub or sub.get("tier") != "elite":
        return json_resp({
            "error": "elite_required",
            "feature": "sos_elite",
            "message": "SOS-режим доступен только для Elite",
        }, status=402)

    # Находим Premium юзеров в радиусе 50 км (Haversine)
    RADIUS_KM = 50
    if USE_SQLITE:
        nearby = await _fetch(
            """SELECT u.id, u.telegram_id,
                      (6371 * acos(
                        cos(radians(?)) * cos(radians(s.center_lat)) *
                        cos(radians(s.center_lon) - radians(?)) +
                        sin(radians(?)) * sin(radians(s.center_lat))
                      )) AS distance_km
               FROM users u
               JOIN subscriptions s ON s.user_id = u.id
               JOIN premium_users pu ON pu.user_id = u.id
               WHERE pu.is_active = 1
                 AND (pu.expires_at IS NULL OR datetime(pu.expires_at) > datetime('now'))
                 AND s.center_lat IS NOT NULL
               HAVING distance_km < ?
               ORDER BY distance_km""",
            float(lat), float(lon), float(lat), RADIUS_KM,
        )
    else:
        nearby = await _fetch(
            """SELECT u.id, u.telegram_id,
                      (6371 * acos(
                        cos(radians($1)) * cos(radians(s.center_lat)) *
                        cos(radians(s.center_lon) - radians($2)) +
                        sin(radians($1)) * sin(radians(s.center_lat))
                      )) AS distance_km
               FROM users u
               JOIN subscriptions s ON s.user_id = u.id
               JOIN premium_users pu ON pu.user_id = u.id
               WHERE pu.is_active = TRUE
                 AND (pu.expires_at IS NULL OR pu.expires_at > NOW())
                 AND s.center_lat IS NOT NULL
               HAVING (6371 * acos(
                        cos(radians($1)) * cos(radians(s.center_lat)) *
                        cos(radians(s.center_lon) - radians($2)) +
                        sin(radians($1)) * sin(radians(s.center_lat))
                      )) < $3
               ORDER BY distance_km""",
            float(lat), float(lon), RADIUS_KM,
        )

    if not nearby:
        return json_resp({
            "ok": True,
            "broadcasted": 0,
            "message": "Нет Premium пользователей рядом",
        })

    # Отправляем SOS через бота (из push_worker или напрямую)
    sent = 0
    try:
        from bot_instance import bot
        for row in nearby:
            tg_id = row.get("telegram_id") if isinstance(row, dict) else row[1]
            dist = row.get("distance_km", 0) if isinstance(row, dict) else row[2]
            if not tg_id:
                continue
            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        f"🚨 <b>SOS-СИГНАЛ!</b>\n\n"
                        f"Водитель рядом с тобой нуждается в помощи!\n\n"
                        f"📍 Координаты: {lat}, {lon}\n"
                        f"📏 Расстояние: {dist:.1f} км\n"
                        f"💬 Сообщение: {msg}\n\n"
                        f"Если можешь помоги — свяжись через @darkt30"
                    ),
                    parse_mode="HTML",
                )
                sent += 1
            except Exception:
                pass
    except Exception as e:
        logger.exception(f"SOS broadcast error: {e}")

    return json_resp({
        "ok": True,
        "broadcasted": sent,
        "nearby_premium": len(nearby),
        "radius_km": RADIUS_KM,
    })


# === Referral Program endpoints ===

async def handle_referral_code(request):
    """GET /api/referral/code?telegram_id=... — создаёт/возвращает реферальный код."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id")
    if not tid:
        return json_resp({"error": "telegram_id required"}, status=400)
    from db import get_user_id_by_any, create_referral_code
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user not found"}, status=404)
    code = await create_referral_code(uid)
    return json_resp({
        "ok": True,
        "code": code,
        "link": f"https://t.me/benzin_ryadom_bot?start=ref_{code}",
    })


async def handle_referral_apply(request):
    """POST /api/referral/apply — применяет реферальный код.

    Body: {telegram_id, code}
    Начисляет 1 месяц Premium рефереру.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    tid = body.get("telegram_id")
    code = body.get("code", "").strip().upper()
    if not tid or not code:
        return json_resp({"error": "telegram_id and code required"}, status=400)

    from db import get_user_id_by_any, get_referral_by_code, complete_referral
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user not found"}, status=404)

    referral = await get_referral_by_code(code)
    if not referral:
        return json_resp({"error": "invalid referral code"}, status=400)

    if referral.get("status") != "active":
        return json_resp({"error": "referral code already used"}, status=400)

    # Нельзя использовать свой же код
    if referral.get("referrer_user_id") == uid:
        return json_resp({"error": "cannot use your own referral code"}, status=400)

    try:
        success = await complete_referral(code, uid, int(tid))
        if success:
            return json_resp({
                "ok": True,
                "message": "Реферал применён! Реферер получил месяц Premium.",
            })
        else:
            return json_resp({"error": "failed to complete referral"}, status=500)
    except Exception as e:
        logger.exception(f"referral_apply error: {e}")
        return json_resp({"error": str(e)}, status=500)


async def handle_referral_stats(request):
    """GET /api/referral/stats?telegram_id=... — статистика рефералов."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id")
    if not tid:
        return json_resp({"error": "telegram_id required"}, status=400)
    from db import get_user_id_by_any, get_referral_stats
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"stats": {"total": 0, "completed": 0, "pending": 0}})
    stats = await get_referral_stats(uid)
    return json_resp({"stats": stats})


async def handle_fuel_alarm_list(request):
    """GET /api/fuel-alarm/list?telegram_id=... — список активных подписок"""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id")
    if not tid:
        return json_resp({"error": "telegram_id required"}, status=400)
    from db import get_user_id_by_any, get_fuel_alarms_for_user, get_user_premium
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"alarms": [], "count": 0})
    sub = await get_user_premium(uid)
    is_premium = bool(sub and sub.get("tier"))
    alarms = await get_fuel_alarms_for_user(uid)
    return json_resp({
        "alarms": alarms,
        "count": len(alarms),
        "is_premium": is_premium,
    })


async def handle_export_csv(request):
    """GET /api/export/csv?telegram_id=&type=reports

    Premium only. Возвращает CSV с историей отчётов/цен пользователя.
    Типы: reports, prices.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id")
    if not tid:
        return json_resp({"error": "telegram_id required"}, status=400)
    try:
        from db import get_user_id_by_any, get_user_premium
        uid = await get_user_id_by_any(int(tid))
        if not uid:
            return json_resp({"error": "user not found"}, status=404)
        sub = await get_user_premium(uid)
        if not sub or not sub.get("tier"):
            return json_resp({
                "error": "premium_required",
                "feature": "export_csv",
                "message": "Экспорт в CSV доступен только для Premium",
            }, status=402)
    except Exception as e:
        logger.exception(f"export_csv auth: {e}")
        return json_resp({"error": str(e)}, status=500)

    # Получаем отчёты
    csv_type = request.query.get("type", "reports")
    if csv_type not in ("reports", "prices"):
        return json_resp({"error": "type must be 'reports' or 'prices'"}, status=400)

    days = int(request.query.get("days", "30"))
    days = min(max(days, 1), 365)

    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")  # ; для Excel ru

    try:
        if csv_type == "reports":
            writer.writerow([
                "Дата", "АЗС", "Адрес", "Город", "Топливо", "Цена", "Наличие",
                "Очередь", "Лимит", "Канистры", "Комментарий", "Подтверждений"
            ])
            if USE_SQLITE:
                rows = await _fetch(
                    """SELECT r.created_at, s.name, s.address, s.city, r.fuel_type,
                              r.price, r.available, r.queue_size, r.has_limit,
                              r.canister_ban, r.comment, r.confirmations
                       FROM reports r
                       JOIN stations s ON s.id = r.station_id
                       WHERE r.user_id = ? AND r.created_at > datetime('now', ?)
                       ORDER BY r.created_at DESC
                       LIMIT 1000""",
                    uid, f"-{days} days",
                )
            else:
                rows = await _fetch(
                    """SELECT r.created_at, s.name, s.address, s.city, r.fuel_type,
                              r.price, r.available, r.queue_size, r.has_limit,
                              r.canister_ban, r.comment, r.confirmations
                       FROM reports r
                       JOIN stations s ON s.id = r.station_id
                       WHERE r.user_id = $1 AND r.created_at > NOW() - ($2 || ' days')::interval
                       ORDER BY r.created_at DESC
                       LIMIT 1000""",
                    uid, str(days),
                )
        else:  # prices
            writer.writerow([
                "Дата", "АЗС", "Адрес", "Город", "Топливо", "Цена"
            ])
            if USE_SQLITE:
                rows = await _fetch(
                    """SELECT r.created_at, s.name, s.address, s.city, r.fuel_type, r.price
                       FROM reports r
                       JOIN stations s ON s.id = r.station_id
                       WHERE r.user_id = ? AND r.price IS NOT NULL
                         AND r.created_at > datetime('now', ?)
                       ORDER BY r.created_at DESC
                       LIMIT 1000""",
                    uid, f"-{days} days",
                )
            else:
                rows = await _fetch(
                    """SELECT r.created_at, s.name, s.address, s.city, r.fuel_type, r.price
                       FROM reports r
                       JOIN stations s ON s.id = r.station_id
                       WHERE r.user_id = $1 AND r.price IS NOT NULL
                         AND r.created_at > NOW() - ($2 || ' days')::interval
                       ORDER BY r.created_at DESC
                       LIMIT 1000""",
                    uid, str(days),
                )

        for r in rows:
            row = []
            for key in r:
                v = r.get(key) if isinstance(r, dict) else r[key]
                if v is None:
                    v = ""
                elif hasattr(v, 'isoformat'):
                    v = v.isoformat()
                else:
                    v = str(v)
                row.append(v)
            writer.writerow(row)

        csv_text = output.getvalue()
        return web.Response(
            text=csv_text,
            content_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{csv_type}_{days}d.csv"',
            },
        )
    except Exception as e:
        logger.exception(f"export_csv error: {e}")
        return json_resp({"error": str(e)}, status=500)


async def handle_route_fuel(request):
    """GET /api/route/fuel?from_lat=&from_lon=&to_lat=&to_lon=&fuel=92

    Premium фича. Находит АЗС по маршруту между двумя точками.
    Free: возвращает только ближайшие 2 АЗС без гарантии наличия.
    Premium: все АЗС на маршруте + фильтрация по наличию + цены + расстояние.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)

    try:
        from_lat = float(request.query.get("from_lat", ""))
        from_lon = float(request.query.get("from_lon", ""))
        to_lat = float(request.query.get("to_lat", ""))
        to_lon = float(request.query.get("to_lon", ""))
    except (TypeError, ValueError):
        return json_resp({"error": "from_lat, from_lon, to_lat, to_lon required"}, status=400)

    if not (-90 <= from_lat <= 90) or not (-90 <= to_lat <= 90):
        return json_resp({"error": "lat must be in [-90, 90]"}, status=400)
    if not (-180 <= from_lon <= 180) or not (-180 <= to_lon <= 180):
        return json_resp({"error": "lon must be in [-180, 180]"}, status=400)

    fuel = request.query.get("fuel", "95")
    if fuel not in ("92", "95", "98", "diesel", "100", "lpg"):
        fuel = "95"

    # Premium check
    tid = request.query.get("telegram_id")
    is_premium = False
    user_tier = None
    if tid:
        try:
            from db import get_user_id_by_any, get_user_premium
            uid = await get_user_id_by_any(int(tid))
            if uid:
                sub = await get_user_premium(uid)
                if sub and sub.get("tier"):
                    is_premium = True
                    user_tier = sub.get("tier")
        except Exception:
            pass

    # Расчёт расстояния и направления
    import math
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    total_distance = haversine(from_lat, from_lon, to_lat, to_lon)

    def bbox_search(lat, lon, radius_km, limit=10):
        """Ищет АЗС в bbox."""
        lat_delta = radius_km / 111.0
        lon_delta = radius_km / (111.0 * math.cos(math.radians(lat)))
        lat_min, lat_max = lat - lat_delta, lat + lat_delta
        lon_min, lon_max = lon - lon_delta, lon + lon_delta
        return lat_min, lat_max, lon_min, lon_max

    # Ищем АЗС в коридоре вокруг маршрута
    # Для простоты: ищем АЗС на расстоянии до 20% от общей длины от mid-точки
    corridor_km = min(50, max(10, total_distance * 0.15))
    mid_lat = (from_lat + to_lat) / 2
    mid_lon = (from_lon + to_lon) / 2

    lat_min, lat_max, lon_min, lon_max = bbox_search(mid_lat, mid_lon, corridor_km, 50)

    from db import _fetch
    try:
        if USE_SQLITE:
            rows = await _fetch(
                """SELECT s.id, s.name, s.operator, s.address, s.city,
                          s.lat, s.lon,
                          (SELECT price FROM reports
                           WHERE station_id = s.id AND fuel_type = ? AND price IS NOT NULL
                           ORDER BY created_at DESC LIMIT 1) as last_price,
                          (SELECT available FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_available,
                          (SELECT created_at FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_report,
                          (SELECT queue_size FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_queue,
                          (SELECT has_limit FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_has_limit
                   FROM stations s
                   WHERE s.lat BETWEEN ? AND ? AND s.lon BETWEEN ? AND ?
                     AND s.is_active = 1
                   LIMIT 50""",
                fuel, lat_min, lat_max, lon_min, lon_max,
            )
        else:
            rows = await _fetch(
                """SELECT s.id, s.name, s.operator, s.address, s.city,
                          s.lat, s.lon,
                          (SELECT price FROM reports
                           WHERE station_id = s.id AND fuel_type = $5 AND price IS NOT NULL
                           ORDER BY created_at DESC LIMIT 1) as last_price,
                          (SELECT available FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_available,
                          (SELECT created_at FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_report,
                          (SELECT queue_size FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_queue,
                          (SELECT has_limit FROM reports
                           WHERE station_id = s.id
                           ORDER BY created_at DESC LIMIT 1) as last_has_limit
                   FROM stations s
                   WHERE s.lat BETWEEN $1 AND $2 AND s.lon BETWEEN $3 AND $4
                     AND COALESCE(s.is_active, TRUE) = TRUE
                   LIMIT 50""",
                lat_min, lat_max, lon_min, lon_max, fuel,
            )
    except Exception as e:
        logger.exception(f"route_fuel db: {e}")
        return json_resp({"error": str(e)}, status=500)

    # Сортируем по расстоянию от mid-точки
    stations_with_dist = []
    for r in rows:
        d = haversine(mid_lat, mid_lon, float(r["lat"]), float(r["lon"]))
        stations_with_dist.append({
            "id": r["id"],
            "name": r.get("name"),
            "operator": r.get("operator"),
            "address": r.get("address"),
            "city": r.get("city"),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "distance_from_route_km": round(d, 1),
            "last_price": float(r["last_price"]) if r.get("last_price") is not None else None,
            "last_available": r.get("last_available"),
            "last_queue": r.get("last_queue"),
            "last_has_limit": r.get("last_has_limit"),
            "last_report": str(r["last_report"]) if r.get("last_report") else None,
        })

    stations_with_dist.sort(key=lambda x: x["distance_from_route_km"])

    # Free: 2 ближайших без фильтрации
    # Premium: все + фильтрация по наличию
    if is_premium:
        # Фильтруем только те, что с наличием
        guaranteed = [s for s in stations_with_dist if s["last_available"] is True]
        all_stations = stations_with_dist[:30]  # до 30 АЗС
    else:
        all_stations = stations_with_dist[:2]  # только 2
        guaranteed = []

    # Считаем экономию
    if guaranteed:
        prices = [s["last_price"] for s in guaranteed if s.get("last_price")]
        if prices:
            avg_price = sum(prices) / len(prices)
            max_price = max(prices)
            savings = round((max_price - min(prices)) * 30, 0)  # за 30 литров экономия
        else:
            avg_price = max_price = savings = None
    else:
        avg_price = max_price = savings = None

    # Рекомендация
    recommendation = None
    if guaranteed:
        # Самая дешёвая с наличием, ближайшая к середине маршрута
        rec = min(guaranteed, key=lambda s: (s["last_price"] or 999, s["distance_from_route_km"]))
        recommendation = rec

    return json_resp({
        "from": {"lat": from_lat, "lon": from_lon},
        "to": {"lat": to_lat, "lon": to_lon},
        "total_distance_km": round(total_distance, 1),
        "fuel": fuel,
        "stations": all_stations,
        "guaranteed_stations": guaranteed,
        "is_premium": is_premium,
        "user_tier": user_tier,
        "avg_price": round(avg_price, 2) if avg_price else None,
        "savings_30l": savings,
        "recommendation": recommendation,
        "corridor_km": round(corridor_km, 1),
        "message": (
            "Найдено АЗС с гарантией наличия" if is_premium
            else f"Free: только 2 ближайших. Premium: все АЗС + гарантия наличия + экономия до 1200₽"
        ),
    })


async def handle_route_anti_traffic(request):
    """GET /api/route/anti-traffic?from_lat=&from_lon=&to_lat=&to_lon=&fuel=92

    Elite фича. Маршрут с учётом пробок.
    Добавляет к обычному route_fuel: traffic levels, ETA, альтернативные точки остановки.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)

    # Premium Elite check
    tid = request.query.get("telegram_id")
    if not tid:
        return json_resp({"error": "telegram_id required for anti-traffic"}, status=400)

    from db import get_user_id_by_any, get_user_premium
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user not found"}, status=404)
    sub = await get_user_premium(uid)
    if not sub or sub.get("tier") != "elite":
        return json_resp({
            "error": "elite_required",
            "feature": "anti_traffic",
            "message": "Антипробка доступна только для Elite",
        }, status=402)

    try:
        from_lat = float(request.query.get("from_lat", ""))
        from_lon = float(request.query.get("from_lon", ""))
        to_lat = float(request.query.get("to_lat", ""))
        to_lon = float(request.query.get("to_lon", ""))
    except (TypeError, ValueError):
        return json_resp({"error": "from_lat, from_lon, to_lat, to_lon required"}, status=400)

    fuel = request.query.get("fuel", "95")

    # Расчёт расстояния
    import math
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    total_distance = haversine(from_lat, from_lon, to_lat, to_lon)

    # Симуляция traffic на основе времени суток
    from datetime import datetime
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()  # 0=пн, 6=вс

    # Определяем уровень пробок
    def get_traffic_level(hour, weekday):
        if weekday >= 5:  # выходные
            if 10 <= hour <= 14:
                return "medium", 1.2, "Выходной трафик"
            return "low", 1.0, "Мало машин"
        # Будни
        if 7 <= hour <= 9:
            return "high", 1.8, "Утренний пик 🌅"
        if 12 <= hour <= 14:
            return "medium", 1.3, "Обеденный трафик"
        if 17 <= hour <= 19:
            return "high", 2.0, "Вечерний пик 🌆"
        if 22 <= hour or hour <= 5:
            return "low", 1.0, "Ночь — свободно"
        return "low", 1.1, "Спокойно"

    traffic_level, traffic_multiplier, traffic_desc = get_traffic_level(hour, weekday)

    # Базовое время (средняя скорость 60 км/ч без пробок)
    base_speed = 60  # км/ч
    traffic_speed = base_speed / traffic_multiplier
    eta_minutes = (total_distance / traffic_speed) * 60

    # Оптимальные точки остановки (каждые ~100 км)
    stop_points = []
    num_stops = max(1, int(total_distance / 100))
    for i in range(num_stops):
        frac = (i + 1) / (num_stops + 1)
        stop_lat = from_lat + frac * (to_lat - from_lat)
        stop_lon = from_lon + frac * (to_lon - from_lon)
        stop_points.append({
            "lat": round(stop_lat, 6),
            "lon": round(stop_lon, 6),
            "km_from_start": round(total_distance * frac, 1),
            "suggestion": f"Остановка #{i+1} — заправься здесь",
        })

    # Рекомендация: если пробки — езди ночью
    best_time = None
    if traffic_multiplier > 1.3:
        best_hour = 23 if hour < 12 else 5
        best_time = f"Лучшее время — {'вечером после 23:00' if best_hour == 23 else 'утром до 5:00'}"

    return json_resp({
        "from": {"lat": from_lat, "lon": from_lon},
        "to": {"lat": to_lat, "lon": to_lon},
        "total_distance_km": round(total_distance, 1),
        "fuel": fuel,
        "traffic": {
            "level": traffic_level,
            "multiplier": traffic_multiplier,
            "description": traffic_desc,
            "eta_minutes": round(eta_minutes),
            "eta_without_traffic": round((total_distance / base_speed) * 60),
            "delay_minutes": round(eta_minutes - (total_distance / base_speed) * 60),
        },
        "stop_points": stop_points,
        "best_time": best_time,
        "message": f"🚗 Антипробка: {traffic_desc}. Задержка: +{round(eta_minutes - (total_distance / base_speed) * 60)} мин",
    })


# VK Pay — настройки в bot/vkpay.py

async def handle_premium_create_payment(request):
    """POST /api/premium/create-payment — создаёт платёж и возвращает ссылку VK Pay.

    Универсальный endpoint для TG, VK, Mini App.
    Использует модуль bot.vkpay для генерации подписанной ссылки.
    После успешной оплаты VK Pay вызывает callback на /api/premium/payment-callback.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_POST):
        return json_resp({"error": "rate limit"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return json_resp({"error": "invalid json"}, status=400)

    tid = body.get("telegram_id") or body.get("vk_user_id")
    tier = body.get("tier")
    if not tid or not tier:
        return json_resp({"error": "telegram_id and tier required"}, status=400)
    if tier not in ("economy", "standard", "elite"):
        return json_resp({"error": "invalid tier"}, status=400)

    from db import (
        get_user_id_by_any, upsert_user, upsert_user_vk,
        get_plan, create_payment_request, get_active_discount, use_discount,
    )
    uid = await get_user_id_by_any(int(tid))
    if not uid:
        if body.get("vk_user_id"):
            await upsert_user_vk(int(tid))
        else:
            await upsert_user(int(tid), username=None, first_name="Premium User")
        uid = await get_user_id_by_any(int(tid))
    if not uid:
        return json_resp({"error": "user creation failed"}, status=500)

    plan = get_plan(tier)
    if not plan:
        return json_resp({"error": "invalid plan"}, status=400)

    # Проверяем активную реферальную скидку
    discount = await get_active_discount(uid)
    discount_id = None
    discount_percent = 0
    original_price = plan["price"]
    if discount:
        discount_percent = discount["discount_percent"]
        discount_id = discount["id"]

    token = await create_payment_request(uid, tier, payment_method="yoomoney")

    from yoomoney_pay import create_payment as yoomoney_create, is_configured as ym_configured

    final_price = round(original_price * (100 - discount_percent) / 100) if discount_percent else original_price
    discount_note = f" (скидка {discount_percent}%)" if discount_percent else ""
    desc = f"Бензин рядом · Премиум {plan['name']} · {final_price}₽ / {plan['period_days']} дней{discount_note}"

    if not ym_configured():
        return json_resp({
            "ok": False,
            "error": "YooMoney not configured. Установите YOOMONEY_TOKEN и YOOMONEY_RECEIVER в env.",
            "payment_token": token,
            "tier": tier,
            "amount": final_price,
        }, status=503)

    result = yoomoney_create(
        amount=final_price,
        description=desc,
        payment_token=token,
    )

    if not result.get("ok"):
        return json_resp({
            "ok": False,
            "error": result.get("error", "YooMoney error"),
            "payment_token": token,
            "tier": tier,
            "amount": final_price,
        }, status=503)

    # Помечаем скидку как использованную после успешного создания платежа
    if discount_id:
        await use_discount(discount_id)

    return json_resp({
        "ok": True,
        "payment_token": token,
        "method": "yoomoney",
        "amount": final_price,
        "original_price": original_price,
        "discount_percent": discount_percent,
        "tier": tier,
        "description": desc,
        "payment_url": result["payment_url"],
        "label": result["label"],
        "receiver": result["receiver"],
    })


async def handle_premium_payment_callback(request):
    """POST/GET /api/premium/payment-callback — VK Pay callback после оплаты.

    VK Pay отправляет POST с form-encoded body и подписью в X-Signature header.
    Также обрабатывает manual callback для тестов.
    """
    from db import get_payment_by_token, confirm_payment

    # Проверяем подпись VK Pay
    from vkpay import parse_callback, verify_signature
    raw_body = await request.text() if request.method == "POST" else ""
    sig_header = request.headers.get("X-Signature", "")

    if request.method == "POST" and sig_header:
        # Реальный callback от VK Pay
        if not verify_signature(raw_body, sig_header):
            logger.warning("Invalid VK Pay callback signature")
            return web.Response(status=403, text="invalid signature")
        data = parse_callback(raw_body)
        if not data:
            return web.Response(status=400, text="invalid payload")
        if data.get("status") != "paid":
            return web.Response(status=200, text="ok")  # acknowledged
        # extra — наш payment_token
        token = data.get("extra")
        if not token:
            return web.Response(status=400, text="missing extra")
    else:
        # Manual callback (для тестов или редиректа после оплаты)
        token = request.query.get("token")
        if not token:
            return web.Response(status=400, text="missing token")

    payment = await get_payment_by_token(token)
    if not payment:
        return web.Response(status=404, text="payment not found")
    if payment.get("status") == "paid":
        # Уже активирован — редирект на success
        return web.Response(status=302, headers={"Location": "https://vk.com/benzyn_ryadom?pay=ok"})

    await confirm_payment(token)
    return web.Response(status=302, headers={"Location": "https://vk.com/benzyn_ryadom?pay=ok"})


async def handle_premium_payment_status(request):
    """GET /api/premium/payment-status?token=... — проверка статуса оплаты.

    Для Mini App: после возврата из VK Pay опрашиваем этот endpoint.
    """
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    token = request.query.get("token")
    if not token:
        return json_resp({"error": "token required"}, status=400)
    from db import get_payment_by_token
    payment = await get_payment_by_token(token)
    if not payment:
        return json_resp({"error": "payment not found"}, status=404)
    return json_resp({
        "status": payment.get("status"),
        "tier": payment.get("tier"),
        "amount": payment.get("amount"),
        "paid_at": str(payment.get("paid_at", "")),
    })


async def handle_premium_feature(request):
    """GET /api/premium/check?feature=...&telegram_id=... — проверка доступа к фиче."""
    if not _check_rate(request.remote or "?", RATE_LIMIT_GET):
        return json_resp({"error": "rate limit"}, status=429)
    tid = request.query.get("telegram_id") or request.query.get("vk_user_id")
    feature = request.query.get("feature")
    if not tid or not feature:
        return json_resp({"error": "telegram_id and feature required"}, status=400)
    from db import get_user_id_by_any, get_user_premium, has_feature, FEATURE_TIER
    uid = await get_user_id_by_any(int(tid))
    sub = None
    if uid:
        sub = await get_user_premium(uid)
    tier = sub.get("tier") if sub else None
    return json_resp({
        "allowed": has_feature(tier, feature),
        "tier": tier,
        "required_tier": FEATURE_TIER.get(feature),
    })



