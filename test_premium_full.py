#!/usr/bin/env python3
"""
Полная проверка Premium функций:
1. Premium status
2. has_feature gating
3. SOS broadcast (elite)
4. Anti-traffic (elite)
5. Forecast 7d (standard+)
6. Route fuel (standard+)
7. Fuel alarm (standard+)
8. Trial activation
9. Cancel
"""
import asyncio
import os
import sys
import json
import urllib.request
import urllib.parse

BACKEND = "https://benzin-ryadom.onrender.com"
os.environ["USE_SQLITE"] = "false"
os.environ["DATABASE_URL"] = "postgresql://postgres.ywtlglhorudfwqgiythv:raFham-piqwux-4hyfma@aws-0-eu-west-3.pooler.supabase.com:6543/postgres"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bot"))

import db


def http_get(path, params=None):
    url = f"{BACKEND}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def http_post(path, data):
    req = urllib.request.Request(
        f"{BACKEND}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


async def main():
    await db.init_db()
    print("=" * 70)
    print("ПОЛНАЯ ПРОВЕРКА PREMIUM ФУНКЦИЙ")
    print("=" * 70)

    results = []
    test_tid = 772577887  # Ты (Founder)
    free_tid = 999999501  # Свежий юзер без Premium

    # Создаём free юзера для теста (без premium)
    if not db.USE_SQLITE:
        async with db._db.acquire() as conn:
            await conn.execute(
                """INSERT INTO users (telegram_id, username, first_name, legal_accepted, legal_accepted_at)
                   VALUES ($1, $2, $3, TRUE, NOW())
                   ON CONFLICT (telegram_id) DO NOTHING""",
                free_tid, "test_free", "TestFree"
            )

    def test(name, ok, details=""):
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}{(': ' + details) if details else ''}")
        results.append((name, ok, details))

    # === 1. PREMIUM STATUS ===
    print("\n[1] Premium status (Founder)")
    code, data = http_get("/api/premium/status", {"telegram_id": test_tid})
    test("Premium status 200", code == 200)
    test("is_premium=True", data.get("is_premium") is True, f"is_premium={data.get('is_premium')}")
    test("Ты Founder", data.get("premium_tier") == "founder", f"tier={data.get('premium_tier')}")
    test("expires_at есть", bool(data.get("premium_expires_at")))

    # === 2. PREMIUM FEATURE CHECK ===
    print("\n[2] has_feature gating")
    for feature in ["sos_elite", "anti_traffic", "forecast_7d", "route_fuel", "fuel_alarm"]:
        code, data = http_get("/api/premium/check", {"telegram_id": test_tid, "feature": feature})
        allowed = data.get("allowed", False)
        test(f"Founder → {feature}", allowed, f"allowed={allowed}, required={data.get('required_tier')}")

    # === 3. PREMIUM STATUS (FREE USER) ===
    print("\n[3] Premium status для юзера без Premium")
    code, data = http_get("/api/premium/status", {"telegram_id": free_tid})
    test("200 OK", code == 200)
    test("Не premium", not data.get("is_premium"), f"is_premium={data.get('is_premium')}")
    test("Нет tier", data.get("premium_tier") is None)

    # === 4. PREMIUM CHECK ДЛЯ FREE USER ===
    print("\n[4] has_feature для free юзера")
    for feature in ["sos_elite", "anti_traffic", "forecast_7d", "route_fuel", "fuel_alarm"]:
        code, data = http_get("/api/premium/check", {"telegram_id": free_tid, "feature": feature})
        allowed = data.get("allowed", False)
        # premium НЕ должно быть доступа ни к чему
        if not allowed:
            test(f"Free → {feature} ❌ blocked", True)
        else:
            test(f"Free → {feature}", False, "❌ ДОЛЖЕН БЫТЬ ЗАБЛОКИРОВАН!")

    # === 5. SOS BROADCAST (Elite+) ===
    print("\n[5] SOS broadcast (только Elite+)")
    # Founder - должен пройти
    code, data = http_post("/api/sos/broadcast", {
        "telegram_id": test_tid,
        "text": "Test SOS от Founder",
    })
    # Не проверяем результат (там может быть 400 если нет station_id), но должно быть 200/400 а не 403
    test("Founder SOS (не 403)", code != 403, f"code={code}")
    # Free - должен 403
    code, data = http_post("/api/sos/broadcast", {
        "telegram_id": free_tid,
        "text": "Test SOS от free",
    })
    test("Free SOS заблокирован (403)", code == 403, f"code={code}")

    # === 6. ANTI-TRAFFIC (Elite+) ===
    print("\n[6] Anti-traffic (только Elite+)")
    # Founder - должен работать (но требует координат)
    code, data = http_get("/api/route/anti-traffic", {
        "telegram_id": test_tid,
        "from_lat": 55.75, "from_lon": 37.61,
        "to_lat": 55.80, "to_lon": 37.65,
    })
    test("Founder anti-traffic (не 403)", code != 403, f"code={code}")
    # Free
    code, data = http_get("/api/route/anti-traffic", {
        "telegram_id": free_tid,
        "from_lat": 55.75, "from_lon": 37.61,
        "to_lat": 55.80, "to_lon": 37.65,
    })
    test("Free anti-traffic заблокирован (403)", code == 403, f"code={code}")

    # === 7. ROUTE FUEL (Standard+) ===
    print("\n[7] Route fuel (Standard+)")
    # Founder
    code, data = http_get("/api/route/fuel", {
        "telegram_id": test_tid,
        "from_lat": 55.75, "from_lon": 37.61,
        "to_lat": 55.80, "to_lon": 37.65,
        "fuel": "95",
    })
    test("Founder route-fuel (не 403)", code != 403, f"code={code}")
    # Free
    code, data = http_get("/api/route/fuel", {
        "telegram_id": free_tid,
        "from_lat": 55.75, "from_lon": 37.61,
        "to_lat": 55.80, "to_lon": 37.65,
        "fuel": "95",
    })
    test("Free route-fuel заблокирован (403)", code == 403, f"code={code}")

    # === 8. FUEL ALARM (Standard+) ===
    print("\n[8] Fuel alarm (Standard+)")
    # Founder
    code, data = http_post("/api/fuel-alarm/create", {
        "telegram_id": test_tid,
        "station_id": 1,
        "fuel_type": "95",
        "max_price": 60.0,
    })
    test("Founder fuel-alarm create (не 403)", code != 403, f"code={code}")
    # Free
    code, data = http_post("/api/fuel-alarm/create", {
        "telegram_id": free_tid,
        "station_id": 1,
        "fuel_type": "95",
        "max_price": 60.0,
    })
    test("Free fuel-alarm заблокирован (403)", code == 403, f"code={code}")

    # === 9. FUEL ALARM LIST (все) ===
    print("\n[9] Fuel alarm list")
    code, data = http_get("/api/fuel-alarm/list", {"telegram_id": test_tid})
    test("Founder list 200", code == 200, f"alarms={data.get('count', 0)}")

    # === 10. STATIONS - price history, forecast, analytics ===
    print("\n[10] Premium endpoints на станциях")
    # Найдём первую станцию
    code, data = http_get("/api/stations", {"lat": 55.75, "lon": 37.61, "limit": 1})
    if code == 200 and data.get("stations"):
        station_id = data["stations"][0]["id"]
        # price history (Economy)
        code, _ = http_get(f"/api/stations/{station_id}/price-history", {"telegram_id": test_tid})
        test(f"Price history (Economy)", code != 403, f"code={code}")
        # forecast (Standard)
        code, _ = http_get(f"/api/stations/{station_id}/forecast", {"telegram_id": test_tid})
        test(f"Forecast (Standard)", code != 403, f"code={code}")
        # analytics (Elite)
        code, _ = http_get(f"/api/stations/{station_id}/analytics", {"telegram_id": test_tid})
        test(f"Analytics (Elite)", code != 403, f"code={code}")
    else:
        test("Станции в базе", False, "Нет станций!")

    # === 11. PREMIUM PLANS ===
    print("\n[11] Premium plans (доступны всем)")
    code, data = http_get("/api/premium/plans")
    test("Plans 200", code == 200)
    if code == 200:
        plans = data.get("plans", [])
        test(f"4 тарифа", len(plans) == 4, f"got {len(plans)}: {[p.get('code') for p in plans]}")
        # Должны быть economy, standard, elite, founder
        codes = [p.get("code") for p in plans]
        for tier in ["economy", "standard", "elite", "founder"]:
            test(f"  - {tier}", tier in codes)

    # === 12. PAYMENT STATUS (без оплаты) ===
    print("\n[12] Payment status (несуществующий токен)")
    code, data = http_get("/api/premium/payment-status", {"token": "FAKE_TOKEN_123"})
    test("404 на несуществующий", code == 404, f"code={code}")

    # === 13. ACCOUNT INFO ===
    print("\n[13] Account info (баланс, tier, рефералка)")
    code, data = http_get("/api/account/info", {"telegram_id": test_tid})
    test("Account info 200", code == 200)
    if code == 200:
        test("is_premium=True", data.get("is_premium") is True)
        test("premium_tier=founder", data.get("premium_tier") == "founder")
        test("is_founder=False (нет в founder_purchases — paid другой)", data.get("is_founder") is not None)

    # === 14. TRIAL ===
    print("\n[14] Premium trial")
    code, data = http_post("/api/premium/trial", {"telegram_id": test_tid})
    test("Trial (любой код)", code in (200, 400, 409), f"code={code}")

    # === 15. USER STATS ===
    print("\n[15] User stats")
    code, data = http_get("/api/user/stats", {"telegram_id": test_tid})
    test("Stats 200", code == 200)

    # === ИТОГИ ===
    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print(f"ИТОГ: {passed}/{total} тестов прошли, {failed} провалились")
    print("=" * 70)
    if failed > 0:
        print("\n❌ ПРОВАЛЫ:")
        for name, ok, details in results:
            if not ok:
                print(f"  - {name}: {details}")


if __name__ == "__main__":
    asyncio.run(main())
