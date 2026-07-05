#!/usr/bin/env python3
"""
Присоединяется к Telegram-чатам по invite ссылкам.

Использование:
  python scripts/join_tg_chats.py t.me/+ABC123
  python scripts/join_tg_chats.py t.me/joinchat/ABC123
  python scripts/join_tg_chats.py --from-file invite_links.txt

Формат invite_links.txt (одна ссылка на строку):
  https://t.me/+ABC123
  https://t.me/joinchat/XYZ789
  +ABC123
"""
import asyncio
import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from telethon import TelegramClient
from telethon.sessions import StringSession

TG_API_ID = os.environ.get("TG_API_ID", "")
TG_API_HASH = os.environ.get("TG_API_HASH", "")
TG_SESSION_STRING = os.environ.get("TG_SESSION_STRING", "")
SESSION_PATH = os.path.join(os.path.dirname(__file__), "..", "tg_session")


async def join_chat(client, invite: str) -> bool:
    """Присоединяется к чату по invite ссылке."""
    try:
        entity = await client.join_chat(invite)
        if hasattr(entity, 'username') and entity.username:
            print(f"  ✅ Joined: @{entity.username} ({entity.title})")
        else:
            print(f"  ✅ Joined: ID={entity.id} ({entity.title})")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Join TG chats via invite links")
    parser.add_argument("links", nargs="*", help="Invite links (t.me/+hash)")
    parser.add_argument("--from-file", help="File with invite links (one per line)")
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
    print(f"✅ Authorized as: @{me.username} ({me.first_name})")

    # Собираем ссылки
    links = list(args.links or [])
    if args.from_file:
        with open(args.from_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    links.append(line)

    if not links:
        print("❌ No invite links provided")
        await client.disconnect()
        return 1

    print(f"\n=== Joining {len(links)} chats ===\n")
    joined = 0
    for link in links:
        # Нормализуем ссылку
        invite = link
        if "t.me/" in invite:
            invite = invite.split("t.me/")[-1]
            if invite.startswith("+"):
                invite = invite[1:]
            elif invite.startswith("joinchat/"):
                invite = "joinchat/" + invite.split("joinchat/")[-1]

        if await join_chat(client, invite):
            joined += 1
        await asyncio.sleep(2)  # Anti-flood

    print(f"\n=== Done: {joined}/{len(links)} joined ===")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    asyncio.run(main())
