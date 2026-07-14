"""
Получает chat_id Telegram-канала по username.
Использование: python get_channel_id.py @benzyn_ryadom
"""
import asyncio
import os
import sys

from aiogram import Bot


async def get_channel_id(channel_username: str, bot_token: str) -> int:
    """Получает chat_id канала по username."""
    username = channel_username.lstrip("@")
    bot = Bot(token=bot_token)
    try:
        chat = await bot.get_chat(f"@{username}")
        print(f"Channel: @{username}")
        print(f"Title: {chat.title}")
        print(f"chat_id: {chat.id}")
        print(f"type: {chat.type}")
        return chat.id
    finally:
        await bot.session.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python get_channel_id.py @channel_username")
        sys.exit(1)

    bot_token = os.environ.get("BOT_TOKEN", "")
    if not bot_token:
        print("BOT_TOKEN env var not set")
        sys.exit(1)

    channel = sys.argv[1]
    chat_id = asyncio.run(get_channel_id(channel, bot_token))
    print(f"\nAdd to Render env: CHANNEL_CHAT_ID={chat_id}")
