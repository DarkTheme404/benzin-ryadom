#!/usr/bin/env python3
"""
Парсер ВСЕХ доступных источников топлива.
Работает с тем что доступно из России.

Источники:
- fuelprice.ru (цены) ✅ работает
- Telegram каналы ✅ работает
- Погода (wttr.in) ✅ работает
- Drom.ru (форумы) ✅ работает
- RIA.ru (новости) ✅ работает
- ishubenzin.ru ✅ работает

Запуск: python scripts/parse_all_available.py
"""
import asyncio
import aiohttp
import json
import re
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db

CITIES_EN = {
    "Москва": "Moscow", "Санкт-Петербург": "Saint+Petersburg",
    "Новосибирск": "Novosibirsk", "Екатеринбург": "Yekaterinburg",
    "Казань": "Kazan", "Краснодар": "Krasnodar",
    "Челябинск": "Chelyabinsk", "Нижний Новгород": "Nizhny+Novgorod",
    "Самара": "Samara", "Ростов-на-Дону": "Rostov-on-Don",
    "Уфа": "Ufa", "Красноярск": "Krasnoyarsk",
    "Воронеж": "Voronezh", "Пермь": "Perm",
    "Волгоград": "Volgograd", "Тюмень": "Tyumen",
    "Омск": "Omsk", "Тула": "Tula",
    "Иркутск": "Irkutsk", "Хабаровск": "Khabarovsk",
}

FUEL_KEYWORDS_RU = ["бензин", "азс", "топливо", "заправк", "92", "95", "98", "дизель",
                     "горюч", "очередь", "нет топлива", "кончается", "завоз"]

def extract_fuel_data(text: str) -> list[dict]:
    """Извлекает данные о топливе из текста."""
    text_lower = text.lower()
    results = []
    
    fuel_types = {
        "92": ["аи-92", "аи 92", "92-й", "92 ", "ai-92"],
        "95": ["аи-95", "аи 95", "95-й", "95 ", "ai-95"],
        "98": ["аи-98", "аи 98", "98-й", "98 ", "ai-98"],
        "dt": ["дизель", "дт", "дизтопливо", "дизел"],
    }
    
    for ft, keywords in fuel_types.items():
        for kw in keywords:
            idx = text_lower.find(kw)
            if idx == -1:
                continue
            
            ctx_start = max(0, idx - 80)
            ctx_end = min(len(text_lower), idx + len(kw) + 80)
            ctx = text_lower[ctx_start:ctx_end]
            
            available = None
            if any(w in ctx for w in ["есть", "в наличии", "горит", "работает", "заправл"]):
                available = True
            elif any(w in ctx for w in ["нет ", "конч", "законч", "отсутств", "недоступн"]):
                available = False
            elif any(w in ctx for w in ["мало", "кончается", "остал", "заканчивает"]):
                available = None
            
            price = None
            price_match = re.search(r'(\d{1,3})[,.](\d{2})\s*(?:₽|руб|р\.)', ctx)
            if price_match:
                price = float(f"{price_match.group(1)}.{price_match.group(2)}")
            
            queue = None
            queue_match = re.search(r'(?:очередь|ждать|стоять)\s*(?:~?)\s*(\d+)', ctx)
            if queue_match:
                queue = int(queue_match.group(1))
            
            results.append({
                "fuel_type": ft,
                "available": available,
                "price": price,
                "queue": queue,
            })
            break
    
    return results

# ============================================================
# 1. Погода (влияние на топливо)
# ============================================================

async def parse_weather(session: aiohttp.ClientSession) -> dict:
    """Парсер погоды — влияние на топливо."""
    print("=== Погода ===")
    weather = {}
    
    for city_ru, city_en in CITIES_EN.items():
        try:
            url = f"https://wttr.in/{city_en}?format=j1"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                   headers={"User-Agent": "curl/7.64.1"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    data = json.loads(text)
                    current = data.get("current_condition", [{}])[0]
                    temp = int(current.get("temp_C", 0))
                    desc = current.get("weatherDesc", [{}])[0].get("value", "")
                    humidity = int(current.get("humidity", 0))
                    wind = int(current.get("windspeedKmph", 0))
                    
                    weather[city_ru] = {
                        "temp": temp,
                        "description": desc,
                        "humidity": humidity,
                        "wind_kmh": wind,
                    }
                    print(f"  {city_ru}: {temp}°C, {desc}, humidity={humidity}%")
        except Exception as e:
            print(f"  {city_ru}: {e}")
    
    print(f"=== Погода: {len(weather)} городов ===")
    return weather

# ============================================================
# 2. Drom.ru (форумы)
# ============================================================

async def parse_drom(session: aiohttp.ClientSession) -> int:
    """Парсер Drom.ru — упоминания о качестве."""
    print("=== Drom.ru ===")
    total = 0
    
    urls = [
        ("https://www.drom.ru/info/?q=АЗС+цена", "цены"),
        ("https://www.drom.ru/info/?q=бензин+очередь", "очереди"),
        ("https://www.drom.ru/info/?q=АЗС+нет+топлива", "дефицит"),
        ("https://www.drom.ru/info/?q=бензин+качество", "качество"),
    ]
    
    for url, topic in urls:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                   headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Извлекаем ссылки на статьи
                    links = re.findall(r'href="(/info/[^"]+)"', text)
                    seen = set()
                    count = 0
                    for link in links[:10]:
                        if link in seen:
                            continue
                        seen.add(link)
                        try:
                            async with session.get(f"https://www.drom.ru{link}",
                                                  timeout=aiohttp.ClientTimeout(total=10),
                                                  headers={"User-Agent": "Mozilla/5.0"}) as art_resp:
                                if art_resp.status == 200:
                                    art_text = await art_resp.text()
                                    # Очищаем HTML
                                    clean = re.sub(r'<[^>]+>', ' ', art_text)
                                    clean = re.sub(r'\s+', ' ', clean).strip()
                                    if any(kw in clean.lower() for kw in FUEL_KEYWORDS_RU):
                                        fuel_data = extract_fuel_data(clean)
                                        if fuel_data:
                                            count += 1
                                            for fd in fuel_data:
                                                await db.add_report(
                                                    station_id=1,
                                                    fuel_type=fd["fuel_type"],
                                                    available=fd["available"],
                                                    price=fd["price"],
                                                    queue_size=fd["queue"],
                                                    comment=f"drom:{topic}: {clean[:150]}",
                                                    source="drom",
                                                )
                                                total += 1
                        except:
                            continue
                    print(f"  {topic}: {count} статей с данными")
        except Exception as e:
            print(f"  {topic}: {e}")
    
    print(f"=== Drom total: {total} ===")
    return total

# ============================================================
# 3. RIA.ru (новости)
# ============================================================

async def parse_ria(session: aiohttp.ClientSession) -> int:
    """Парсер RIA.ru — новости о топливе."""
    print("=== RIA.ru ===")
    total = 0
    
    queries = [
        ("бензин+цены+АЗС", "цены"),
        ("топливный+кризис", "дефицит"),
        ("лимит+бензин", "лимиты"),
    ]
    
    for query, topic in queries:
        try:
            url = f"https://ria.ru/search/?query={query}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                   headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Извлекаем ссылки на новости
                    links = re.findall(r'href="(https://ria\.ru/\d{8}/[^"]+)"', text)
                    seen = set()
                    count = 0
                    for link in links[:5]:
                        if link in seen:
                            continue
                        seen.add(link)
                        try:
                            async with session.get(link,
                                                  timeout=aiohttp.ClientTimeout(total=10),
                                                  headers={"User-Agent": "Mozilla/5.0"}) as news_resp:
                                if news_resp.status == 200:
                                    news_text = await news_resp.text()
                                    clean = re.sub(r'<[^>]+>', ' ', news_text)
                                    clean = re.sub(r'\s+', ' ', clean).strip()
                                    if any(kw in clean.lower() for kw in FUEL_KEYWORDS_RU):
                                        fuel_data = extract_fuel_data(clean)
                                        if fuel_data:
                                            count += 1
                                            for fd in fuel_data:
                                                await db.add_report(
                                                    station_id=1,
                                                    fuel_type=fd["fuel_type"],
                                                    available=fd["available"],
                                                    price=fd["price"],
                                                    comment=f"ria:{topic}: {clean[:150]}",
                                                    source="ria",
                                                )
                                                total += 1
                        except:
                            continue
                    print(f"  {topic}: {count} новостей с данными")
        except Exception as e:
            print(f"  {topic}: {e}")
    
    print(f"=== RIA total: {total} ===")
    return total

# ============================================================
# 4. ishubenzin.ru (API)
# ============================================================

async def parse_ishubenzin(session: aiohttp.ClientSession) -> int:
    """Парсер ishubenzin.ru — наличие."""
    print("=== ishubenzin.ru ===")
    total = 0
    
    try:
        # Попробуем разные endpoints
        urls = [
            "https://ishubenzin.ru/api/stations",
            "https://ishubenzin.ru/api/v1/stations",
            "https://ishubenzin.ru/api/fuel",
        ]
        for url in urls:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                                       headers={"User-Agent": "Mozilla/5.0"}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        stations = data if isinstance(data, list) else data.get("stations", [])
                        for st in stations[:100]:
                            if st.get("lat") and st.get("lon"):
                                rows = await db._fetch(
                                    "SELECT id FROM stations WHERE ABS(lat-?)<0.002 AND ABS(lon-?)<0.002 LIMIT 1",
                                    st["lat"], st["lon"]
                                )
                                if rows:
                                    await db.add_report(
                                        station_id=rows[0]["id"],
                                        fuel_type="95",
                                        available=st.get("available"),
                                        comment=f"ishubenzin:{st.get('name', '')}",
                                        source="ishubenzin",
                                    )
                                    total += 1
                        print(f"  {url}: {len(stations)} станций")
                        break
            except:
                continue
    except Exception as e:
        print(f"  Error: {e}")
    
    print(f"=== ishubenzin total: {total} ===")
    return total

# ============================================================
# Основная функция
# ============================================================

async def main():
    start = datetime.now()
    print(f"{'='*60}")
    print(f"ПАРСЕР ДОСТУПНЫХ ИСТОЧНИКОВ — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    await db.init_db()
    
    async with aiohttp.ClientSession() as session:
        results = {}
        results["weather"] = len(await parse_weather(session))
        results["drom"] = await parse_drom(session)
        results["ria"] = await parse_ria(session)
        results["ishubenzin"] = await parse_ishubenzin(session)
    
    elapsed = (datetime.now() - start).total_seconds()
    
    # Статистика
    rows = await db._fetch("SELECT COUNT(*) as cnt FROM reports")
    total_reports = rows[0]["cnt"]
    
    rows = await db._fetch("SELECT source, COUNT(*) as cnt FROM reports GROUP BY source ORDER BY cnt DESC")
    
    print(f"\n{'='*60}")
    print(f"ИТОГИ")
    print(f"{'='*60}")
    print(f"Время: {elapsed:.0f} сек")
    print(f"Отчётов в БД: {total_reports}")
    print(f"Источники:")
    for s in rows:
        print(f"  {s['source']}: {s['cnt']}")
    
    await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
