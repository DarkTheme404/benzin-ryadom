"""
Парсер цен на топливо из мессенджера MAX (https://max.ru).

Использует открытое Bot API MAX:
  - POST /messages
  - GET /chats — список каналов/чатов бота
  - GET /chats/{link} — инфо о канале по ссылке
  - GET /messages — получить сообщения
  - POST /subscriptions — webhook подписка

⚠️ Для работы нужен бот в MAX + токен.
Регистрация: https://business.max.ru/self (юрлицо / ИП / самозанятый, резидент РФ).

Использование:
  # Сначала зарегистрируй бота и получи токен
  export MAX_BOT_TOKEN='your_token'

  # Добавить бота в каналы MAX (через web-интерфейс)

  python scripts/parse_max.py --list-chats   # показать каналы бота
  python scripts/parse_max.py --all --limit 50
  python scripts/parse_max.py --chat <chat_id> --limit 30
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

BASE_URL = "https://platform-api2.max.ru"
SOURCE_NAME = "max"

# Ключевые слова (переиспользуем из price_parser)
sys.path.insert(0, str(Path(__file__).parent))
from price_parser import (  # noqa: E402
    parse_prices,
    detect_network,
    detect_city,
    detect_queue,
    detect_availability,
)


async def max_request(
    session: aiohttp.ClientSession,
    method: str,
    endpoint: str,
    token: str,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
) -> Optional[dict]:
    """HTTP-запрос к MAX Bot API.

    Токен передаётся через заголовок Authorization.
    """
    url = f"{BASE_URL}{endpoint}"
    headers = {"Authorization": token}
    try:
        async with session.request(
            method, url, headers=headers, params=params, json=json,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            if r.status == 200:
                return await r.json()
            else:
                text = await r.text()
                print(f"  ⚠ MAX API {method} {endpoint} → HTTP {r.status}: {text[:200]}")
    except Exception as e:
        print(f"  ⚠ MAX {method} {endpoint}: {e}")
    return None


async def list_chats(token: str) -> list[dict]:
    """Получает список каналов/чатов, в которые добавлен бот."""
    async with aiohttp.ClientSession() as session:
        result = await max_request(session, "GET", "/chats", token)
        if not result:
            return []
        # API может вернуть {chats: [...]} или просто [...]
        if isinstance(result, list):
            return result
        return result.get("chats", [])


async def get_messages(token: str, chat_id: str, limit: int = 50, days_back: int = 7) -> list[dict]:
    """Получает последние сообщения из канала/чата.

    API: GET /messages?chat_id=<id>&count=<limit>
    """
    async with aiohttp.ClientSession() as session:
        # Параметры могут отличаться — см. доку
        result = await max_request(
            session, "GET", "/messages", token,
            params={"chat_id": chat_id, "count": limit},
        )
        if not result:
            return []
        if isinstance(result, list):
            return result
        return result.get("messages", [])


async def get_chat_by_link(token: str, link: str) -> Optional[dict]:
    """Получает информацию о канале по ссылке (например, max://channel/...)."""
    # API: GET /chats/{link}
    endpoint = f"/chats/{link}"
    async with aiohttp.ClientSession() as session:
        return await max_request(session, "GET", endpoint, token)


async def save_max_messages(messages: list[dict], chat_info: dict, dry_run: bool) -> tuple[int, int]:
    """Сохраняет распарсенные сообщения MAX в БД."""
    if dry_run:
        return (0, 0)

    # Загружаем кеш АЗС
    stations_cache: dict[str, int] = {}
    try:
        rows = await db._fetch("SELECT id, name FROM stations")
        for r in rows:
            stations_cache[r["name"].lower()] = r["id"]
    except Exception as e:
        print(f"⚠ Cache load: {e}")

    chat_title = chat_info.get("title") or chat_info.get("name") or "max_chat"
    chat_id = chat_info.get("chat_id") or chat_info.get("id") or "?"

    total_prices = 0
    total_saved = 0
    for msg in messages:
        text = msg.get("text") or msg.get("body") or ""
        if not text or len(text) < 10:
            continue

        prices = parse_prices(text)
        if not prices:
            continue

        network = detect_network(text)
        city = detect_city(text)
        queue = detect_queue(text)
        available = detect_availability(text)

        # Создаём/находим АЗС
        max_station_name = f"Max: {chat_title} ({network or '?'}/{city or '?'})"
        if max_station_name.lower() in stations_cache:
            station_id = stations_cache[max_station_name.lower()]
        else:
            try:
                new_id = await db._execute(
                    """INSERT INTO stations (name, lat, lon, city, region, operator, is_active, created_at)
                       VALUES (?, 0, 0, ?, '', ?, TRUE, datetime('now'))""",
                    max_station_name, city or "", network or "",
                    returning=True,
                )
                if new_id:
                    stations_cache[max_station_name.lower()] = new_id
                    station_id = new_id
                else:
                    continue
            except Exception:
                continue

        for fuel, price in prices.items():
            try:
                await db.add_report(
                    station_id=station_id,
                    fuel_type=fuel,
                    available=available,
                    price=price,
                    queue_size=queue,
                    source=SOURCE_NAME,
                    comment=f"max://chat/{chat_id}: {text[:100]}",
                )
                total_saved += 1
            except Exception:
                pass
        total_prices += len(prices)
    return (total_prices, total_saved)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-chats", action="store_true", help="Показать каналы/чаты бота")
    parser.add_argument("--all", action="store_true", help="Парсить все чаты бота")
    parser.add_argument("--chat", help="Конкретный chat_id для парсинга")
    parser.add_argument("--limit", type=int, default=50, help="Лимит сообщений на чат")
    parser.add_argument("--days-back", type=int, default=7, help="Глубина в днях")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.getenv("MAX_BOT_TOKEN", "")
    if not token:
        print("❌ MAX_BOT_TOKEN не задан")
        print("Получить: зарегистрируй бота на https://business.max.ru/self")
        print("   Затем: bot must be добавлен в каналы MAX для парсинга")
        print()
        print("Альтернатива: web-скрапинг https://web.max.ru/ (не реализовано)")
        return 1

    print(f"=== Парсер MAX (Bot API) ===")
    if not args.dry_run:
        await db.init_db()

    if args.list_chats:
        chats = await list_chats(token)
        print(f"Каналов/чатов у бота: {len(chats)}")
        for c in chats:
            chat_id = c.get("chat_id") or c.get("id")
            title = c.get("title") or c.get("name") or "?"
            chat_type = c.get("type") or c.get("chat_type") or "?"
            print(f"  {chat_id}: {title} ({chat_type})")
        return 0

    if args.all:
        chats = await list_chats(token)
        if not chats:
            print("❌ Нет каналов/чатов. Добавь бота в каналы MAX.")
            return 1
        total_prices = 0
        total_saved = 0
        for c in chats:
            chat_id = c.get("chat_id") or c.get("id")
            title = c.get("title") or c.get("name") or "?"
            print(f"\n--- {title} ({chat_id}) ---")
            messages = await get_messages(token, str(chat_id), args.limit)
            print(f"Сообщений: {len(messages)}")
            for msg in messages:
                text = msg.get("text") or msg.get("body") or ""
                prices = parse_prices(text)
                if not prices:
                    continue
                network = detect_network(text)
                city = detect_city(text)
                print(f"  → {prices} (net={network}, city={city})")
                print(f"    {text[:100]}")
            p, s = await save_max_messages(messages, c, args.dry_run)
            total_prices += p
            total_saved += s
        print()
        print(f"=== Итого ===")
        print(f"  Цен найдено: {total_prices}")
        if not args.dry_run:
            print(f"  Сохранено отчётов: {total_saved}")
            await db.close_db()
        return 0

    if args.chat:
        chat = {"chat_id": args.chat, "title": f"chat_{args.chat}"}
        messages = await get_messages(token, args.chat, args.limit)
        print(f"Сообщений: {len(messages)}")
        for msg in messages:
            text = msg.get("text") or msg.get("body") or ""
            prices = parse_prices(text)
            if not prices:
                continue
            network = detect_network(text)
            city = detect_city(text)
            print(f"  → {prices} (net={network}, city={city})")
        p, s = await save_max_messages(messages, chat, args.dry_run)
        print(f"=== Итого: цен={p}, сохранено={s} ===")
        if not args.dry_run:
            await db.close_db()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
