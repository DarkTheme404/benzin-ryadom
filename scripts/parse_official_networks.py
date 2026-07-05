"""
Парсер официальных данных сетей АЗС:
- Газпромнефть (gazpromneftrf.ru)
- Лукойл (lukoil.ru)
- Роснефть (rosneft.ru)
- Татнефть (tatneft.ru)
- Башнефть (bashneft.ru)
- Сургутнефтегаз
- ТАИФ
- Тнефтепродукт
- И другие сети

⚠️ Использует только публичные данные (API или HTML-страницы).

Использование:
    python scripts/parse_official_networks.py              # все сети
    python scripts/parse_official_networks.py --network gazprom  # только Газпромнефть
    python scripts/parse_official_networks.py --network lukoil   # только Лукойл
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"
load_dotenv(ENV_PATH)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("official_networks")


# ======================================================================
# ГАЗПРОМНЕФТЬ — gazpromneftrf.ru
# ======================================================================

GAZPROM_URL = "https://www.gazpromneft.ru/price/"
GAZPROM_API_URL = "https://www.gazpromneft.ru/api/price/"

async def parse_gazprom() -> list[dict]:
    """Парсит цены Газпромнефть."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            # API с ценами по городам
            async with session.get(
                GAZPROM_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    cities = data.get("cities", [])
                    for city_data in cities:
                        city_name = city_data.get("name", "")
                        stations = city_data.get("stations", [])
                        for st in stations:
                            prices = st.get("prices", {})
                            for fuel, price in prices.items():
                                if price and 20 < float(price) < 200:
                                    results.append({
                                        "network": "Газпромнефть",
                                        "city": city_name,
                                        "fuel_type": fuel.lower().replace("аи-", ""),
                                        "price": float(price),
                                        "available": True,
                                        "source": "gazprom",
                                    })
                    logger.info("Газпромнефть: %d цен", len(results))
                else:
                    logger.warning("Газпромнефть API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Газпромнефть ошибка: %s", e)

    return results


# ======================================================================
# ЛУКОЙЛ — lukoil.ru
# ======================================================================

LUKOIL_URL = "https://www.lukoil.ru/filling-stations"
LUKOIL_API_URL = "https://www.lukoil.ru/api/stations"

async def parse_lukoil() -> list[dict]:
    """Парсит данные Лукойл."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                LUKOIL_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Лукойл",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "lukoil",
                                })
                    logger.info("Лукойл: %d цен", len(results))
                else:
                    logger.warning("Лукойл API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Лукойл ошибка: %s", e)

    return results


# ======================================================================
# РОСНЕФТЬ — rosneft.ru
# ======================================================================

ROSNEFT_URL = "https://www.rosneft.ru/fuel/stations/"
ROSNEFT_API_URL = "https://www.rosneft.ru/api/stations/"

async def parse_rosneft() -> list[dict]:
    """Парсит данные Роснефть."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ROSNEFT_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Роснефть",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "rosneft",
                                })
                    logger.info("Роснефть: %d цен", len(results))
                else:
                    logger.warning("Роснефть API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Роснефть ошибка: %s", e)

    return results


# ======================================================================
# ТАТНЕФТЬ — tatneft.ru
# ======================================================================

TATNEFT_URL = "https://www.tatneft.ru/filling-stations/"
TATNEFT_API_URL = "https://www.tatneft.ru/api/stations/"

async def parse_tatneft() -> list[dict]:
    """Парсит данные Татнефть."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TATNEFT_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Татнефть",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "tatneft",
                                })
                    logger.info("Татнефть: %d цен", len(results))
                else:
                    logger.warning("Татнефть API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Татнефть ошибка: %s", e)

    return results


# ======================================================================
# БАШНЕФТЬ — bashneft.ru
# ======================================================================

BASHNEFT_URL = "https://www.bashneft.ru/filling-stations/"
BASHNEFT_API_URL = "https://www.bashneft.ru/api/stations/"

async def parse_bashneft() -> list[dict]:
    """Парсит данные Башнефть."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                BASHNEFT_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Башнефть",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "bashneft",
                                })
                    logger.info("Башнефть: %d цен", len(results))
                else:
                    logger.warning("Башнефть API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Башнефть ошибка: %s", e)

    return results


# ======================================================================
# СУРГУТНЕФТЕГАЗ
# ======================================================================

SURGUT_URL = "https://www.sngs.ru/filling-stations/"
SURGUT_API_URL = "https://www.sngs.ru/api/stations/"

async def parse_surgut() -> list[dict]:
    """Парсит данные Сургутнефтегаз."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SURGUT_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Сургутнефтегаз",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "surgut",
                                })
                    logger.info("Сургутнефтегаз: %d цен", len(results))
                else:
                    logger.warning("Сургутнефтегаз API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Сургутнефтегаз ошибка: %s", e)

    return results


# ======================================================================
# ТАИФ — taif-nefteprodukt.ru
# ======================================================================

TAIF_URL = "https://taif-nefteprodukt.ru/filling-stations/"
TAIF_API_URL = "https://taif-nefteprodukt.ru/api/stations/"

async def parse_taif() -> list[dict]:
    """Парсит данные ТАИФ."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TAIF_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "ТАИФ",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "taif",
                                })
                    logger.info("ТАИФ: %d цен", len(results))
                else:
                    logger.warning("ТАИФ API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("ТАИФ ошибка: %s", e)

    return results


# ======================================================================
# ТНЕФТЕПРОДУКТ
# ======================================================================

TNEFT_URL = "https://www.tneft.ru/filling-stations/"
TNEFT_API_URL = "https://www.tneft.ru/api/stations/"

async def parse_tneft() -> list[dict]:
    """Парсит данные Тнефтепродукт."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                TNEFT_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Тнефтепродукт",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "tneft",
                                })
                    logger.info("Тнефтепродукт: %d цен", len(results))
                else:
                    logger.warning("Тнефтепродукт API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Тнефтепродукт ошибка: %s", e)

    return results


# ======================================================================
# ОЛМАЛ — olmal.ru
# ======================================================================

OLMAL_URL = "https://olmal.ru/filling-stations/"
OLMAL_API_URL = "https://olmal.ru/api/stations/"

async def parse_olmal() -> list[dict]:
    """Парсит данные ОЛМАЛ."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                OLMAL_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "ОЛМАЛ",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "olmal",
                                })
                    logger.info("ОЛМАЛ: %d цен", len(results))
                else:
                    logger.warning("ОЛМАЛ API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("ОЛМАЛ ошибка: %s", e)

    return results


# ======================================================================
# КНП — Красноярская нефтепродуктовая компания
# ======================================================================

KNP_URL = "https://knp.ru/filling-stations/"
KNP_API_URL = "https://knp.ru/api/stations/"

async def parse_knp() -> list[dict]:
    """Парсит данные КНП."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                KNP_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "КНП",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "knp",
                                })
                    logger.info("КНП: %d цен", len(results))
                else:
                    logger.warning("КНП API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("КНП ошибка: %s", e)

    return results


# ======================================================================
# ПТК — Петербургская топливная компания
# ======================================================================

PTK_URL = "https://ptk.ru/filling-stations/"
PTK_API_URL = "https://ptk.ru/api/stations/"

async def parse_ptk() -> list[dict]:
    """Парсит данные ПТК."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                PTK_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "ПТК",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "ptk",
                                })
                    logger.info("ПТК: %d цен", len(results))
                else:
                    logger.warning("ПТК API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("ПТК ошибка: %s", e)

    return results


# ======================================================================
# ВТК — Воронежская топливная компания
# ======================================================================

VTK_URL = "https://vtk.ru/filling-stations/"
VTK_API_URL = "https://vtk.ru/api/stations/"

async def parse_vtk() -> list[dict]:
    """Парсит данные ВТК."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                VTK_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "ВТК",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "vtk",
                                })
                    logger.info("ВТК: %d цен", len(results))
                else:
                    logger.warning("ВТК API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("ВТК ошибка: %s", e)

    return results


# ======================================================================
# МАГИСТРАЛЬ — magistral116.ru (Казань)
# ======================================================================

MAGISTRAL_URL = "https://magistral116.ru/filling-stations/"
MAGISTRAL_API_URL = "https://magistral116.ru/api/stations/"

async def parse_magistral() -> list[dict]:
    """Парсит данные Магистраль."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                MAGISTRAL_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Магистраль",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "magistral",
                                })
                    logger.info("Магистраль: %d цен", len(results))
                else:
                    logger.warning("Магистраль API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Магистраль ошибка: %s", e)

    return results


# ======================================================================
# ЮНИГАЗ — unigas.ru
# ======================================================================

UNIGAS_URL = "https://unigas.ru/filling-stations/"
UNIGAS_API_URL = "https://unigas.ru/api/stations/"

async def parse_unigas() -> list[dict]:
    """Парсит данные Юнигаз."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                UNIGAS_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Юнигаз",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "unigas",
                                })
                    logger.info("Юнигаз: %d цен", len(results))
                else:
                    logger.warning("Юнигаз API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Юнигаз ошибка: %s", e)

    return results


# ======================================================================
# ОЛВИ — olvi.ru (Самара)
# ======================================================================

OLVI_URL = "https://olvi.ru/filling-stations/"
OLVI_API_URL = "https://olvi.ru/api/stations/"

async def parse_olvi() -> list[dict]:
    """Парсит данные ОЛВИ."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                OLVI_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "ОЛВИ",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "olvi",
                                })
                    logger.info("ОЛВИ: %d цен", len(results))
                else:
                    logger.warning("ОЛВИ API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("ОЛВИ ошибка: %s", e)

    return results


# ======================================================================
# Нефтьмагистраль — neftmagistral.ru
# ======================================================================

NEFTMAGISTRAL_URL = "https://neftmagistral.ru/filling-stations/"
NEFTMAGISTRAL_API_URL = "https://neftmagistral.ru/api/stations/"

async def parse_neftmagistral() -> list[dict]:
    """Парсит данные Нефтьмагистраль."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                NEFTMAGISTRAL_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "Нефтьмагистраль",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "neftmagistral",
                                })
                    logger.info("Нефтьмагистраль: %d цен", len(results))
                else:
                    logger.warning("Нефтьмагистраль API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("Нефтьмагистраль ошибка: %s", e)

    return results


# ======================================================================
# ИРБИС — irbis.ru
# ======================================================================

IRBIS_URL = "https://irbis.ru/filling-stations/"
IRBIS_API_URL = "https://irbis.ru/api/stations/"

async def parse_irbis() -> list[dict]:
    """Парсит данные ИРБИС."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                IRBIS_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "ИРБИС",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "irbis",
                                })
                    logger.info("ИРБИС: %d цен", len(results))
                else:
                    logger.warning("ИРБИС API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("ИРБИС ошибка: %s", e)

    return results


# ======================================================================
# АТАН — atan.ru (Крым)
# ======================================================================

ATAN_URL = "https://atan.ru/filling-stations/"
ATAN_API_URL = "https://atan.ru/api/stations/"

async def parse_atan() -> list[dict]:
    """Парсит данные АТАН."""
    import aiohttp
    results = []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ATAN_API_URL,
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": "BenzinRyadom/1.0"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    stations = data.get("stations", [])
                    for st in stations:
                        city = st.get("city", "")
                        name = st.get("name", "")
                        prices = st.get("prices", {})
                        for fuel, price in prices.items():
                            if price and 20 < float(price) < 200:
                                results.append({
                                    "network": "АТАН",
                                    "city": city,
                                    "station_name": name,
                                    "fuel_type": fuel.lower().replace("аи-", ""),
                                    "price": float(price),
                                    "available": True,
                                    "source": "atan",
                                })
                    logger.info("АТАН: %d цен", len(results))
                else:
                    logger.warning("АТАН API: HTTP %d", resp.status)
    except Exception as e:
        logger.warning("АТАН ошибка: %s", e)

    return results


# ======================================================================
# ДОПОЛНИТЕЛЬНЫЕ СЕТИ
# ======================================================================

NETWORK_PARSERS = {
    "gazprom": parse_gazprom,
    "lukoil": parse_lukoil,
    "rosneft": parse_rosneft,
    "tatneft": parse_tatneft,
    "bashneft": parse_bashneft,
    "surgut": parse_surgut,
    "taif": parse_taif,
    "tneft": parse_tneft,
    "olmal": parse_olmal,
    "knp": parse_knp,
    "ptk": parse_ptk,
    "vtk": parse_vtk,
    "magistral": parse_magistral,
    "unigas": parse_unigas,
    "olvi": parse_olvi,
    "neftmagistral": parse_neftmagistral,
    "irbis": parse_irbis,
    "atan": parse_atan,
}

# Маппинг сеть → source key
NETWORK_SOURCE_MAP = {
    "Газпромнефть": "gazprom",
    "Лукойл": "lukoil",
    "Роснефть": "rosneft",
    "Татнефть": "tatneft",
    "Башнефть": "bashneft",
    "Сургутнефтегаз": "surgut",
    "ТАИФ": "taif",
    "Тнефтепродукт": "tneft",
    "ОЛМАЛ": "olmal",
    "КНП": "knp",
    "ПТК": "ptk",
    "ВТК": "vtk",
    "Магистраль": "magistral",
    "Юнигаз": "unigas",
    "ОЛВИ": "olvi",
    "Нефтьмагистраль": "neftmagistral",
    "ИРБИС": "irbis",
    "АТАН": "atan",
}


async def save_network_results(results: list[dict]) -> int:
    """Сохраняет результаты парсинга сетей в БД."""
    saved = 0

    for r in results:
        network = r.get("network", "")
        city = r.get("city", "")
        fuel_type = r.get("fuel_type", "")
        price = r.get("price")
        source = NETWORK_SOURCE_MAP.get(network, r.get("source", "unknown"))

        if not fuel_type:
            continue

        # Ищем станцию в БД
        station_id = None

        # 1) По сети + городу
        if city:
            stations = await db.find_stations_by_city(
                city=city,
                network=network,
                limit=1,
            )
            if stations:
                station_id = stations[0].get("id")

        # 2) По городу
        if not station_id and city:
            stations = await db.find_stations_by_city(city=city, limit=1)
            if stations:
                station_id = stations[0].get("id")

        # 3) Создаём станцию
        if not station_id:
            station_id = await db.upsert_station_for_import(
                name=f"{network} {city}",
                region=city or "Россия",
                city=city,
                operator=network,
            )

        if not station_id:
            continue

        # Сохраняем отчёт
        await db.add_report(
            station_id=station_id,
            fuel_type=fuel_type,
            available=r.get("available", True),
            price=price,
            source=source,
            comment=f"{network}: {city}",
        )
        saved += 1

    return saved


async def main():
    parser = argparse.ArgumentParser(description="Парсер официальных сетей АЗС")
    parser.add_argument("--network", choices=list(NETWORK_PARSERS.keys()),
                        help="Одна сеть")
    parser.add_argument("--all", action="store_true", help="Все сети")
    args = parser.parse_args()

    if not db.API_MODE:
        await db.init_db()

    all_results = []

    if args.network:
        # Одна сеть
        parse_fn = NETWORK_PARSERS[args.network]
        results = await parse_fn()
        all_results.extend(results)
        print(f"{args.network}: {len(results)} результатов")
    elif args.all:
        # Все сети
        for name, parse_fn in NETWORK_PARSERS.items():
            try:
                results = await parse_fn()
                all_results.extend(results)
                print(f"{name}: {len(results)} результатов")
            except Exception as e:
                print(f"{name}: ошибка — {e}")
            await asyncio.sleep(0.5)
    else:
        # По умолчанию — топ-5 сетей
        top_networks = ["gazprom", "lukoil", "rosneft", "tatneft", "bashneft"]
        for name in top_networks:
            results = await NETWORK_PARSERS[name]()
            all_results.extend(results)
            print(f"{name}: {len(results)} результатов")
            await asyncio.sleep(0.5)

    if all_results:
        print(f"\nВсего: {len(all_results)} результатов")
        saved = await save_network_results(all_results)
        print(f"Сохранено: {saved}")
    else:
        print("Нет данных")

    if not db.API_MODE:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
