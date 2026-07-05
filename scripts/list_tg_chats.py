#!/usr/bin/env python3
"""
Показывает все Telegram-чаты, на которые подписан аккаунт.

Использование:
  python scripts/list_tg_chats.py              # все чаты
  python scripts/list_tg_chats.py --fuel       # только топливные чаты
  python scripts/list_tg_chats.py --private    # только приватные чаты
  python scripts/list_tg_chats.py --export     # экспорт в JSON
"""
import asyncio
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from telethon import TelegramClient
from telethon.sessions import StringSession

TG_API_ID = os.environ.get("TG_API_ID", "")
TG_API_HASH = os.environ.get("TG_API_HASH", "")
TG_SESSION_STRING = os.environ.get("TG_SESSION_STRING", "")
SESSION_PATH = os.path.join(os.path.dirname(__file__), "..", "tg_session")

FUEL_KEYWORDS = [
    "бензин", "азс", "топливо", "заправк", "горюч",
    "где заправ", "где залить", "нет топлива", "очередь",
    "92", "95", "98", "дизель", "бенз",
]


async def main():
    parser = argparse.ArgumentParser(description="List all TG chats")
    parser.add_argument("--fuel", action="store_true", help="Only fuel-related chats")
    parser.add_argument("--private", action="store_true", help="Only private chats")
    parser.add_argument("--export", action="store_true", help="Export to JSON")
    parser.add_argument("--export-file", default="joined_chats.json", help="Export filename")
    args = parser.parse_args()

    if not TG_API_ID or not TG_API_HASH:
        print("❌ TG_API_ID / TG_API_HASH не заданы")
        return 1

    if TG_SESSION_STRING:
        client = TelegramClient(StringSession(TG_SESSION_STRING), int(TG_API_ID), TG_API_HASH)
    else:
        client = TelegramClient(str(SESSION_PATH), int(TG_API_ID), TG_API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"✅ Authorized as: @{me.username} ({me.first_name})\n")

    chats = []
    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue

        name = dialog.name or ""
        name_lower = name.lower()
        is_private = not (hasattr(dialog.entity, 'username') and dialog.entity.username)
        is_fuel = any(kw in name_lower for kw in FUEL_KEYWORDS)

        # Проверяем описание
        if not is_fuel:
            try:
                if hasattr(dialog.entity, 'about') and dialog.entity.about:
                    bio = dialog.entity.about.lower()
                    is_fuel = any(kw in bio for kw in FUEL_KEYWORDS)
            except:
                pass

        # Фильтры
        if args.fuel and not is_fuel:
            continue
        if args.private and not is_private:
            continue

        chat_info = {
            "id": dialog.id,
            "name": name,
            "type": "channel" if dialog.is_channel else "group",
            "is_private": is_private,
            "is_fuel": is_fuel,
        }
        if not is_private:
            chat_info["username"] = dialog.entity.username
            chat_info["link"] = f"https://t.me/{dialog.entity.username}"

        chats.append(chat_info)

    # Сортируем: топливные первые, потом по имени
    chats.sort(key=lambda x: (-x["is_fuel"], x["name"].lower()))

    # Вывод
    print(f"Found {len(chats)} chats:\n")
    print(f"{'Name':<40} {'Type':<10} {'Private':<10} {'Fuel':<10} {'Link'}")
    print("-" * 100)

    for chat in chats:
        link = chat.get("link", f"ID:{chat['id']}")
        print(f"{chat['name'][:40]:<40} {chat['type']:<10} {'Yes' if chat['is_private'] else 'No':<10} {'Yes' if chat['is_fuel'] else 'No':<10} {link}")

    # Экспорт
    if args.export:
        with open(args.export_file, "w") as f:
            json.dump(chats, f, indent=2, ensure_ascii=False)
        print(f"\n✅ Exported to {args.export_file}")

    # Статистика
    fuel_chats = [c for c in chats if c["is_fuel"]]
    private_chats = [c for c in chats if c["is_private"]]
    print(f"\nStats: {len(fuel_chats)} fuel chats, {len(private_chats)} private chats")

    await client.disconnect()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
