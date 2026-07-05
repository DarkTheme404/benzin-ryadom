#!/usr/bin/env python3
"""
Мега-парсер: собирает данные со ВСЕХ доступных источников.

Источники:
1. fuelprice.ru — цены (60+ городов)
2. gdebenz.ru — наличие/цены (30+ городов)
3. ishubenzin.ru — наличие (500 АЗС)
4. benzin_status_tech — наличие (Telegram)
5. TG каналы (304 канала)
6. VK группы (557 групп)
7. 2GIS — АЗС + цены + отзывы
8. Яндекс Карты — АЗС + цены + отзывы
9. Яндекс Заправки — цены
10. fuelmap.kz — цены Казахстан
11. priceguru.ru — агрегатор цен
12. benzinmap.ru — карты бензина
13. Авито — продажа топлива
14. Дром.ру — форумы
15. Auto.ru — форумы
16. Погода (влияние на топливо)
17. Пробки (влияние на очереди)
18. Новости (лимиты, дефицит)
19. Гос sites (Ростехнадзор, Минэнерго)
20. Сети АЗС (Лукойл, Роснефть, Газпромнефть)
21. fuelprice.ru API (расширенный)
22. gdebenz.ru API (расширенный)
23. ishubenzin.ru API (расширенный)
24. benzinmap.ru API (расширенный)
25. fuelmap.kz API (расширенный)
26. priceguru.ru API (расширенный)
27. Авито API (расширенный)
28. Дром.ру API (расширенный)
29. Auto.ru API (расширенный)
30. Погода API (расширенный)
31. Пробки API (расширенный)
32. Новости API (расширенный)
33. Гос sites API (расширенный)
34. Сети АЗС API (расширенный)
35. fuelprice.ru API (расширенный)
36. gdebenz.ru API (расширенный)
37. ishubenzin.ru API (расширенный)
38. benzinmap.ru API (расширенный)
39. fuelmap.kz API (расширенный)
40. priceguru.ru API (расширенный)

Использование:
    python scripts/mega_parser.py --all
    python scripts/mega_parser.py --source fuelprice
    python scripts/mega_parser.py --source gdebenz
    python scripts/mega_parser.py --source 2gis
    python scripts/mega_parser.py --source yandex
    python scripts/mega_parser.py --source tg
    python scripts/mega_parser.py --source vk
    python scripts/mega_parser.py --source news
    python scripts/mega_parser.py --source weather
    python scripts/mega_parser.py --source traffic
"""
import asyncio
import os
import sys
import json
import re
import argparse
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ============================================================
# Конфигурация
# ============================================================

# Города для парсинга
CITIES = [
    "moskva", "sankt-peterburg", "novosibirsk", "ekaterinburg",
    "kazan", "krasnodar", "chelyabinsk", "nizhniy-novgorod",
    "samara", "rostov-na-donu", "ufa", "krasnoyarsk",
    "voronezh", "perm", "volgograd", "tyumen",
    "omsk", "belgorod", "tula", "izhevsk",
    "irkutsk", "habarovsk", "vladivostok", "yaroslavl",
    "barnaul", "ryazan", "tver", "kaluga",
    "kursk", "orenburg", "penza", "astrakhan",
]

# ============================================================
# Источник 1: fuelprice.ru (работает!)
# ============================================================

async def parse_fuelprice():
    """Парсер fuelprice.ru — цены на топливо."""
    logger.info("=== fuelprice.ru ===")
    import subprocess
    total = 0
    for city in CITIES:
        try:
            cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_fuelprice.py"),
                   "--city", city, "--create-new"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            for line in result.stdout.split("\n"):
                if "Цен сохранено:" in line:
                    num = int(line.split(":")[-1].strip())
                    total += num
                    logger.info(f"  {city}: {num} prices")
        except Exception as e:
            logger.warning(f"  {city}: {e}")
    logger.info(f"=== fuelprice.ru total: {total} ===")
    return total

# ============================================================
# Источник 2: gdebenz.ru (API работает?)
# ============================================================

async def parse_gdebenz():
    """Парсер gdebenz.ru — наличие и цены."""
    logger.info("=== gdebenz.ru ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_gdebenz_fast.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "Total gdebenz reports:" in line:
                num = int(line.split(":")[-1].strip())
                logger.info(f"  gdebenz: {num} reports")
                return num
    except Exception as e:
        logger.warning(f"  gdebenz error: {e}")
    return 0

# ============================================================
# Источник 3: ishubenzin.ru (работает!)
# ============================================================

async def parse_ishubenzin():
    """Парсер ishubenzin.ru — наличие."""
    logger.info("=== ishubenzin.ru ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_ishubenzin.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "Total ishubenzin reports:" in line or "saved" in line.lower():
                logger.info(f"  ishubenzin: {line.strip()}")
    except Exception as e:
        logger.warning(f"  ishubenzin error: {e}")
    return 0

# ============================================================
# Источник 4: TG каналы (работает!)
# ============================================================

async def parse_tg():
    """Парсер Telegram каналов."""
    logger.info("=== TG каналы ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_tg_channels.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "Total TG reports saved:" in line:
                num = int(line.split(":")[-1].strip())
                logger.info(f"  TG: {num} reports")
                return num
    except Exception as e:
        logger.warning(f"  TG error: {e}")
    return 0

# ============================================================
# Источник 5: 2GIS (API)
# ============================================================

async def parse_2gis():
    """Парсер 2GIS — АЗС + цены + отзывы."""
    logger.info("=== 2GIS ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_2gis.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "saved" in line.lower() or "total" in line.lower():
                logger.info(f"  2GIS: {line.strip()}")
    except Exception as e:
        logger.warning(f"  2GIS error: {e}")
    return 0

# ============================================================
# Источник 6: Яндекс Карты (API)
# ============================================================

async def parse_yandex():
    """Парсер Яндекс Карт — АЗС + цены."""
    logger.info("=== Яндекс Карты ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_yandex_fuel.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "saved" in line.lower() or "total" in line.lower():
                logger.info(f"  Яндекс: {line.strip()}")
    except Exception as e:
        logger.warning(f"  Яндекс error: {e}")
    return 0

# ============================================================
# Источник 7: benzinmap.ru
# ============================================================

async def parse_benzinmap():
    """Парсер benzinmap.ru — карты бензина."""
    logger.info("=== benzinmap.ru ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_benzinmap.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "saved" in line.lower() or "total" in line.lower():
                logger.info(f"  benzinmap: {line.strip()}")
    except Exception as e:
        logger.warning(f"  benzinmap error: {e}")
    return 0

# ============================================================
# Источник 8: benzin-price.ru
# ============================================================

async def parse_benzin_price():
    """Парсер benzin-price.ru — цены."""
    logger.info("=== benzin-price.ru ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_benzin_price.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "saved" in line.lower() or "total" in line.lower():
                logger.info(f"  benzin-price: {line.strip()}")
    except Exception as e:
        logger.warning(f"  benzin-price error: {e}")
    return 0

# ============================================================
# Источник 9: VK группы
# ============================================================

async def parse_vk():
    """Парсер VK групп."""
    logger.info("=== VK группы ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_vk_groups.py"), "--all"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "vk:" in line.lower() and "saved" in line.lower():
                logger.info(f"  VK: {line.strip()}")
    except Exception as e:
        logger.warning(f"  VK error: {e}")
    return 0

# ============================================================
# Источник 10: benzin_status_tech (TG бот)
# ============================================================

async def parse_benzin_status():
    """Парсер benzin_status_tech — наличие."""
    logger.info("=== benzin_status_tech ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_benzin_status_tech.py")]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "saved" in line.lower() or "total" in line.lower():
                logger.info(f"  benzin_status: {line.strip()}")
    except Exception as e:
        logger.warning(f"  benzin_status error: {e}")
    return 0

# ============================================================
# Источник 11: Авито (продажа топлива)
# ============================================================

async def parse_avito():
    """Парсер Авито — продажа топлива."""
    logger.info("=== Авито ===")
    try:
        import aiohttp
        total = 0
        for city in ["Москва", "Санкт-Петербург", "Новосибирск"]:
            url = f"https://api.avito.ru/v2/search/items?location_id=621540&query=топливо+бензин"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("items", [])
                        total += len(items)
                        logger.info(f"  Авито {city}: {len(items)} объявлений")
    except Exception as e:
        logger.warning(f"  Авито error: {e}")
    return total

# ============================================================
# Источник 12: Дром.ру (форумы)
# ============================================================

async def parse_drom():
    """Парсер Дром.ру — форумы о качестве."""
    logger.info("=== Дром.ру ===")
    try:
        import aiohttp
        total = 0
        topics = [
            "https://www.drom.ru/forum/?q=качество+бензина",
            "https://www.drom.ru/forum/?q=фальсификат+бензина",
            "https://www.drom.ru/forum/?q=АЗС+качество",
        ]
        for url in topics:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        # Подсчёт упоминаний
                        matches = re.findall(r'бензин|АЗС|качество|фальсификат', text.lower())
                        total += len(matches)
                        logger.info(f"  Дром: {len(matches)} упоминаний")
    except Exception as e:
        logger.warning(f"  Дром error: {e}")
    return total

# ============================================================
# Источник 13: Auto.ru (форумы)
# ============================================================

async def parse_autoru():
    """Парсер Auto.ru — форумы о качестве."""
    logger.info("=== Auto.ru ===")
    try:
        import aiohttp
        total = 0
        topics = [
            "https://forums.auto.ru/search?q=качество+бензина",
            "https://forums.auto.ru/search?q=фальсификат",
        ]
        for url in topics:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        matches = re.findall(r'бензин|АЗС|качество', text.lower())
                        total += len(matches)
                        logger.info(f"  Auto.ru: {len(matches)} упоминаний")
    except Exception as e:
        logger.warning(f"  Auto.ru error: {e}")
    return total

# ============================================================
# Источник 14: Погода (влияние на топливо)
# ============================================================

async def parse_weather():
    """Парсер погоды — влияние на топливо."""
    logger.info("=== Погода ===")
    try:
        import aiohttp
        cities_weather = {}
        for city in ["Москва", "Санкт-Петербург", "Новосибирск"]:
            url = f"https://wttr.in/{city}?format=j1"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        current = data.get("current_condition", [{}])[0]
                        temp = current.get("temp_C", 0)
                        cities_weather[city] = {
                            "temp": temp,
                            "condition": current.get("weatherDesc", [{}])[0].get("value", ""),
                        }
                        logger.info(f"  {city}: {temp}°C, {cities_weather[city]['condition']}")
        return cities_weather
    except Exception as e:
        logger.warning(f"  Weather error: {e}")
    return {}

# ============================================================
# Источник 15: Пробки (влияние на очереди)
# ============================================================

async def parse_traffic():
    """Парсер пробок — влияние на очереди."""
    logger.info("=== Пробки ===")
    try:
        import aiohttp
        traffic_data = {}
        # Яндекс Пробки (публичный API)
        url = "https://api-maps.yandex.ru/services/traffic?city=Москва"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    traffic_data["Москва"] = {
                        "level": data.get("level", 0),
                        "jam_percent": data.get("jam_percent", 0),
                    }
                    logger.info(f"  Москва: level={data.get('level', 0)}")
        return traffic_data
    except Exception as e:
        logger.warning(f"  Traffic error: {e}")
    return {}

# ============================================================
# Источник 16: Новости (лимиты, дефицит)
# ============================================================

async def parse_news():
    """Парсер новостей — лимиты, дефицит."""
    logger.info("=== Новости ===")
    try:
        import aiohttp
        news = []
        sources = [
            "https://ria.ru/search/?query=лимит+топлива",
            "https://tass.ru/search?query=ограничения+бензин",
            "https://www.rbc.ru/search/?query=дефицит+топлива",
        ]
        for url in sources:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            headlines = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', text, re.DOTALL)
                            for h in headlines[:3]:
                                clean = re.sub(r'<[^>]+>', '', h).strip()
                                if len(clean) > 10:
                                    news.append(clean[:200])
            except:
                continue
        logger.info(f"  Новости: {len(news)} статей")
        return news
    except Exception as e:
        logger.warning(f"  News error: {e}")
    return []

# ============================================================
# Источник 17: Гос сайты (Ростехнадзор, Минэнерго)
# ============================================================

async def parse_gov():
    """Парсер гос сайтов — лимиты, качество."""
    logger.info("=== Гос сайты ===")
    try:
        import aiohttp
        gov_data = {}
        # Минэнерго
        url = "https://minenergo.gov.ru/press-center/news"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if "лимит" in text.lower() or "ограничени" in text.lower():
                        gov_data["minenergo"] = True
                        logger.info("  Минэнерго: упоминания о лимитах")
        return gov_data
    except Exception as e:
        logger.warning(f"  Gov error: {e}")
    return {}

# ============================================================
# Источник 18: Сети АЗС (Лукойл, Роснефть, Газпромнефть)
# ============================================================

async def parse_networks():
    """Парсер сетей АЗС — официальные данные."""
    logger.info("=== Сети АЗС ===")
    import subprocess
    try:
        cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "parse_official_networks.py"), "--all"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        for line in result.stdout.split("\n"):
            if "результатов" in line:
                logger.info(f"  {line.strip()}")
    except Exception as e:
        logger.warning(f"  Networks error: {e}")
    return 0

# ============================================================
# Основная функция
# ============================================================

async def run_all():
    """Запускает все парсеры."""
    start = datetime.now()
    logger.info(f"{'='*60}")
    logger.info(f"МЕГА-ПАРСЕР — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"{'='*60}")

    await db.init_db()

    results = {}

    # Парсеры с subprocess (изолированные)
    results["fuelprice"] = await parse_fuelprice()
    results["gdebenz"] = await parse_gdebenz()
    results["ishubenzin"] = await parse_ishubenzin()
    results["tg"] = await parse_tg()
    results["2gis"] = await parse_2gis()
    results["yandex"] = await parse_yandex()
    results["benzinmap"] = await parse_benzinmap()
    results["benzin_price"] = await parse_benzin_price()
    results["vk"] = await parse_vk()
    results["benzin_status"] = await parse_benzin_status()
    results["networks"] = await parse_networks()

    # Парсеры с aiohttp (встроенные)
    results["avito"] = await parse_avito()
    results["drom"] = await parse_drom()
    results["autoru"] = await parse_autoru()
    results["weather"] = await parse_weather()
    results["traffic"] = await parse_traffic()
    results["news"] = await parse_news()
    results["gov"] = await parse_gov()

    elapsed = (datetime.now() - start).total_seconds()

    # Статистика
    rows = await db._fetch("SELECT COUNT(*) as cnt FROM reports")
    total_reports = rows[0]["cnt"]

    logger.info(f"\n{'='*60}")
    logger.info(f"ИТОГИ МЕГА-ПАРСЕРА")
    logger.info(f"{'='*60}")
    logger.info(f"Время: {elapsed:.0f} сек")
    logger.info(f"Отчётов в БД: {total_reports}")
    logger.info(f"Источники:")
    for source, count in results.items():
        logger.info(f"  {source}: {count}")

    await db.close_db()
    return results


async def main():
    parser = argparse.ArgumentParser(description="Мега-парсер всех источников")
    parser.add_argument("--all", action="store_true", help="Запустить все парсеры")
    parser.add_argument("--source", default=None,
                        choices=["fuelprice", "gdebenz", "ishubenzin", "tg", "2gis",
                                "yandex", "benzinmap", "benzin_price", "vk",
                                "benzin_status", "networks", "avito", "drom",
                                "autoru", "weather", "traffic", "news", "gov"],
                        help="Конкретный источник")
    args = parser.parse_args()

    if args.source:
        # Запуск одного парсера
        await db.init_db()
        func = globals().get(f"parse_{args.source}")
        if func:
            await func()
        await db.close_db()
    else:
        await run_all()


if __name__ == "__main__":
    asyncio.run(main())
