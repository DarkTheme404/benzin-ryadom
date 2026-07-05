#!/usr/bin/env python3
"""
Быстрый парсер: работает с доступными API.
Запуск: python scripts/parse_quick.py
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

CITIES_RU = ["Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
             "Краснодар", "Челябинск", "Нижний Новгород", "Самара", "Ростов-на-Дону",
             "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград", "Тюмень", "Омск",
             "Тула", "Иркутск", "Хабаровск", "Владивосток", "Ярославль", "Барнаул",
             "Тверь", "Калуга", "КурсК", "Оренбург", "Пенза", "Астрахань"]

FUEL_KEYWORDS = ["бензин", "азс", "топливо", "заправк", "92", "95", "98", "дизель",
                 "горюч", "очередь", "нет топлива", "кончается", "завоз"]

def parse_fuel_mentions(text: str) -> dict:
    """Извлекает упоминания о топливе из текста."""
    text_lower = text.lower()
    results = []
    fuel_types = {"92": ["аи-92", "92-й", "92 "], "95": ["аи-95", "95-й", "95 "],
                  "98": ["аи-98", "98-й", "98 "], "dt": ["дизель", "дт", "дизтопливо"]}
    
    for ft, keywords in fuel_types.items():
        for kw in keywords:
            if kw in text_lower:
                ctx_start = max(0, text_lower.find(kw) - 50)
                ctx_end = min(len(text_lower), text_lower.find(kw) + len(kw) + 50)
                ctx = text_lower[ctx_start:ctx_end]
                
                available = None
                if any(w in ctx for w in ["есть", "в наличии", "горит"]):
                    available = True
                elif any(w in ctx for w in ["нет", "конч", "законч", "отсутств"]):
                    available = False
                elif any(w in ctx for w in ["мало", "кончается", "остал"]):
                    available = None
                
                price = None
                price_match = re.search(r'(\d{1,3})[,.](\d{2})\s*(?:₽|руб|р\.)', ctx)
                if price_match:
                    price = float(f"{price_match.group(1)}.{price_match.group(2)}")
                
                queue = None
                queue_match = re.search(r'(?:очередь|ждать|стоять)\s*(?:~?)\s*(\d+)', ctx)
                if queue_match:
                    queue = int(queue_match.group(1))
                
                results.append({"fuel_type": ft, "available": available, "price": price, "queue": queue})
                break
    return {"fuel": results, "has_fuel": len(results) > 0}

# ============================================================
# 1. 2GIS АЗС
# ============================================================

async def parse_2gis(session: aiohttp.ClientSession) -> int:
    """Парсер 2GIS — АЗС из веб-страниц (без API ключа)."""
    print("=== 2GIS ===")
    total = 0
    
    cities_2gis = {
        "Москва": "moscow", "Санкт-Петербург": "saint-petersburg",
        "Новосибирск": "novosibirsk", "Екатеринбург": "yekaterinburg",
        "Казань": "kazan", "Красноярск": "krasnoyarsk",
        "Челябинск": "chelyabinsk", "Нижний Новгород": "nizhny-novgorod",
        "Самара": "samara", "Ростов-на-Дону": "rostov-on-don",
    }
    
    for city_ru, city_en in cities_2gis.items():
        try:
            url = f"https://2gis.ru/{city_en}/search/АЗС"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                   headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Извлекаем координаты из HTML
                    coords = re.findall(r'"lat":([\d.]+),"lon":([\d.]+)', text)
                    names = re.findall(r'"name":"([^"]+)"', text)
                    
                    for i, (lat, lon) in enumerate(coords[:50]):
                        try:
                            lat_f, lon_f = float(lat), float(lon)
                            # Ищем АЗС в БД
                            rows = await db._fetch(
                                "SELECT id FROM stations WHERE ABS(lat-?)<0.002 AND ABS(lon-?)<0.002 LIMIT 1",
                                lat_f, lon_f
                            )
                            if rows:
                                name = names[i] if i < len(names) else "АЗС"
                                await db.add_report(
                                    station_id=rows[0]["id"],
                                    fuel_type="95",
                                    available=True,
                                    comment=f"2gis:{name}",
                                    source="2gis",
                                )
                                total += 1
                        except:
                            continue
                    
                    print(f"  {city_ru}: {len(coords)} координат")
        except Exception as e:
            print(f"  {city_ru}: {e}")
    
    print(f"=== 2GIS total: {total} ===")
    return total

# ============================================================
# 2. Drom.ru (форумы)
# ============================================================

async def parse_drom(session: aiohttp.ClientSession) -> int:
    """Парсер Drom.ru — упоминания о качестве."""
    print("=== Drom.ru ===")
    total = 0
    
    urls = [
        ("https://www.drom.ru/info/?q=качество+бензина", "качество"),
        ("https://www.drom.ru/info/?q=фальсификат+бензина", "фальсификат"),
        ("https://www.drom.ru/info/?q=АЗС+очередь", "очереди"),
        ("https://www.drom.ru/info/?q=бензин+кончился", "дефицит"),
    ]
    
    for url, topic in urls:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                   headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Извлекаем тексты статей
                    articles = re.findall(r'class="[^"]*text[^"]*"[^>]*>(.*?)</(?:div|p|span)', text, re.DOTALL)
                    for article in articles:
                        clean = re.sub(r'<[^>]+>', '', article).strip()
                        if len(clean) > 30:
                            parsed = parse_fuel_mentions(clean)
                            if parsed["has_fuel"]:
                                for fuel in parsed["fuel"]:
                                    await db.add_report(
                                        station_id=1,  # placeholder
                                        fuel_type=fuel["fuel_type"],
                                        available=fuel["available"],
                                        price=fuel["price"],
                                        queue_size=fuel["queue"],
                                        comment=f"drom:{topic}: {clean[:150]}",
                                        source="drom",
                                    )
                                    total += 1
                    print(f"  {topic}: {len(articles)} статей")
        except Exception as e:
            print(f"  {topic}: {e}")
    
    print(f"=== Drom total: {total} ===")
    return total

# ============================================================
# 3. Погода
# ============================================================

async def parse_weather(session: aiohttp.ClientSession) -> dict:
    """Парсер погоды — влияние на топливо."""
    print("=== Погода ===")
    weather = {}
    
    cities_weather = {
        "Москва": "Moscow", "Санкт-Петербург": "Saint+Petersburg",
        "Новосибирск": "Novosibirsk", "Екатеринбург": "Yekaterinburg",
        "Казань": "Kazan", "Краснодар": "Krasnodar",
        "Челябинск": "Chelyabinsk", "Нижний Новгород": "Nizhny+Novgorod",
        "Самара": "Samara", "Ростов-на-Дону": "Rostov-on-Don",
    }
    
    for city_ru, city_en in cities_weather.items():
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
                    
                    weather[city_ru] = {
                        "temp": temp,
                        "description": desc,
                        "humidity": humidity,
                    }
                    print(f"  {city_ru}: {temp}°C, {desc}")
        except Exception as e:
            print(f"  {city_ru}: {e}")
    
    print(f"=== Погода: {len(weather)} городов ===")
    return weather

# ============================================================
# 4. Новости (RIA, TASS)
# ============================================================

async def parse_news(session: aiohttp.ClientSession) -> list:
    """Парсер новостей — лимиты, дефицит."""
    print("=== Новости ===")
    news = []
    
    urls = [
        ("https://ria.ru/search/?query=лимит+топлива+бензин", "лимиты"),
        ("https://ria.ru/search/?query=дефицит+бензина", "дефицит"),
        ("https://ria.ru/search/?query=цены+на+бензин", "цены"),
        ("https://tass.ru/search?query=бензин+цены", "цены"),
    ]
    
    for url, topic in urls:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                                   headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    headlines = re.findall(r'<a[^>]*class="[^"]*list-item[^"]*"[^>]*>(.*?)</a>', text, re.DOTALL)
                    for h in headlines[:5]:
                        clean = re.sub(r'<[^>]+>', '', h).strip()
                        if len(clean) > 10:
                            news.append({"text": clean[:200], "topic": topic})
                    print(f"  {topic}: {len(headlines)} заголовков")
        except Exception as e:
            print(f"  {topic}: {e}")
    
    print(f"=== Новости: {len(news)} ===")
    return news

# ============================================================
# 5. Минэнерго
# ============================================================

async def parse_minenergo(session: aiohttp.ClientSession) -> dict:
    """Парсер Минэнерго — лимиты."""
    print("=== Минэнерго ===")
    data = {}
    
    try:
        url = "https://minenergo.gov.ru/press-center/news"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15),
                               headers={"User-Agent": "Mozilla/5.0"}) as resp:
            if resp.status == 200:
                text = await resp.text()
                # Ищем упоминания о лимитах
                if "лимит" in text.lower():
                    data["has_limits"] = True
                    # Извлекаем текст
                    matches = re.findall(r'лимит[^<]{0,100}', text.lower())
                    data["mentions"] = matches[:5]
                    print(f"  Упоминания о лимитах: {len(matches)}")
                else:
                    data["has_limits"] = False
                    print("  Упоминаний о лимитах нет")
    except Exception as e:
        print(f"  Error: {e}")
    
    return data

# ============================================================
# Основная функция
# ============================================================

async def main():
    start = datetime.now()
    print(f"{'='*60}")
    print(f"БЫСТРЫЙ ПАРСЕР — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    await db.init_db()
    
    async with aiohttp.ClientSession() as session:
        results = {}
        
        # Запускаем все парсеры
        results["2gis"] = await parse_2gis(session)
        results["drom"] = await parse_drom(session)
        results["weather"] = len(await parse_weather(session))
        results["news"] = len(await parse_news(session))
        results["minenergo"] = len(await parse_minenergo(session))
    
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
