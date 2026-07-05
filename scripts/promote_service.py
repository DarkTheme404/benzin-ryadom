#!/usr/bin/env python3
"""Рассылка рекламного текста сервиса «Бензин рядом» по Telegram чатам.

Бот отправляет сообщение во ВСЕ чаты, где он является участником.

Использование:
    python scripts/promote_service.py              # Рассылка по всем чатам
    python scripts/promote_service.py --dry-run    # Только список чатов, без отправки
    python scripts/promote_service.py --chat 12345 # Одному конкретному чату
"""

import asyncio
import os
import sys
import logging
import aiohttp
import argparse
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# Рекламный текст
# ============================================================

PROMO_TEXT = """⛽ <b>«Бензин рядом» — сервис, которого больше нигде нет.</b>

Единственный бот, который собирает данные сразу из 50+ источников и показывает реальную картину на АЗС.

<b>Чем он лучше остальных:</b>

1️⃣ <b>Самая полная база.</b> 27 000 АЗС по всей стране. Ни один другой бот или канал не покрывает такую территорию.

2️⃣ <b>Пять источников данных одновременно.</b> Fuelprice.ru, 2ГИС, 28 региональных TG-каналов, официальные данные сетей и отчёты реальных водителей. Если данные есть где-то — мы их собрали.

3️⃣ <b>Обновление каждый час.</b> Не раз в день, не когда кто-то вспомнил. Каждый час парсеры проверяют все источники и обновляют статусы.

4️⃣ <b>Наличие, а не только адреса.</b> Яндекс.Карты покажут где АЗС. Мы покажем есть ли там бензин.

5️⃣ <b>Данные от водителей.</b> Любой может сообщить о ситуации на АЗС. Пользовательские отчёты живут 7 дней и приоритетнее парсеров.

6️⃣ <b>Работает в обоих мессенджерах.</b> Telegram заблокирован — есть VK. VK недоступен — есть Telegram. Сервис доступен всегда.

7️⃣ <b>Полностью бесплатно.</b>

<b>Как пользоваться:</b>
1. Открой бота в Telegram или VK
2. Отправь геолокацию
3. Получи список ближайших АЗС с ценами и статусами

📱 <b>Telegram:</b> <a href="https://t.me/benzyn_ryadom_bot">@benzyn_ryadom_bot</a>
📱 <b>VK:</b> <a href="https://vk.com/benzyn_ryadom">vk.com/benzyn_ryadom</a>

⏱ Время экономится на каждом выезде — проверяй перед дорогой и не стой в очередях впустую."""


async def get_bot_info(bot_token: str) -> Optional[dict]:
    """Получает информацию о боте."""
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("result")
    return None


async def get_bot_chats(bot_token: str) -> list:
    """Получает список чатов, где состоит бот.

    Telegram Bot API не имеет прямого метода для получения списка чатов.
    Используем getUpdates для получения чатов из последних сообщений.
    """
    chats = {}

    # Метод 1: getUpdates (последние 100 updates)
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"limit": 100, "allowed_updates": '["message", "channel_post", "my_chat_member"]'}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                for update in data.get("result", []):
                    # Из message
                    if "message" in update:
                        chat = update["message"].get("chat", {})
                        if chat.get("id"):
                            chats[chat["id"]] = {
                                "id": chat["id"],
                                "title": chat.get("title", chat.get("first_name", "Unknown")),
                                "type": chat.get("type", "unknown"),
                            }
                    # Из channel_post
                    if "channel_post" in update:
                        chat = update["channel_post"].get("chat", {})
                        if chat.get("id"):
                            chats[chat["id"]] = {
                                "id": chat["id"],
                                "title": chat.get("title", "Unknown"),
                                "type": chat.get("type", "channel"),
                            }
                    # Из my_chat_member (бот добавлен/удалён из чата)
                    if "my_chat_member" in update:
                        chat = update["my_chat_member"].get("chat", {})
                        status = update["my_chat_member"].get("new_chat_member", {}).get("status", "")
                        if chat.get("id") and status in ("member", "administrator", "creator"):
                            chats[chat["id"]] = {
                                "id": chat["id"],
                                "title": chat.get("title", chat.get("first_name", "Unknown")),
                                "type": chat.get("type", "unknown"),
                            }

    # Метод 2: Проверяем канал из SUBSCRIBE_CHANNEL_TG
    channel_username = os.getenv("SUBSCRIBE_CHANNEL_TG", "")
    if channel_username:
        url = f"https://api.telegram.org/bot{bot_token}/getChat"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={"chat_id": f"@{channel_username}"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    chat = data.get("result", {})
                    if chat.get("id"):
                        chats[chat["id"]] = {
                            "id": chat["id"],
                            "title": chat.get("title", channel_username),
                            "type": chat.get("type", "channel"),
                        }

    return list(chats.values())


async def send_promo_to_chat(bot_token: str, chat_id: int, chat_title: str,
                              dry_run: bool = False) -> bool:
    """Отправляет рекламное сообщение в чат."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": PROMO_TEXT,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if dry_run:
        logger.info(f"  [DRY RUN] → {chat_title} ({chat_id})")
        return True

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("ok"):
                    logger.info(f"  ✅ Отправлено в {chat_title} ({chat_id})")
                    return True
                else:
                    error = data.get("description", "Unknown error")
                    logger.warning(f"  ⚠ Ошибка {chat_title} ({chat_id}): {error}")
                    return False
    except Exception as e:
        logger.error(f"  ❌ Ошибка {chat_title} ({chat_id}): {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="Рассылка рекламы сервиса «Бензин рядом»")
    parser.add_argument("--dry-run", action="store_true", help="Только список чатов, без отправки")
    parser.add_argument("--chat", type=int, help="Отправить в конкретный чат (chat_id)")
    parser.add_argument("--text", type=str, help="Кастомный текст (вместо стандартного)")
    args = parser.parse_args()

    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        logger.error("BOT_TOKEN не задан!")
        return

    # Получаем информацию о боте
    bot_info = await get_bot_info(bot_token)
    if bot_info:
        logger.info(f"Бот: @{bot_info.get('username')} ({bot_info.get('first_name')})")

    if args.chat:
        # Отправка в конкретный чат
        text = args.text or PROMO_TEXT
        await send_promo_to_chat(bot_token, args.chat, "Manual chat", dry_run=args.dry_run)
        return

    # Получаем список чатов
    logger.info("Получаю список чатов...")
    chats = await get_bot_chats(bot_token)
    logger.info(f"Найдено чатов: {len(chats)}")

    if not chats:
        logger.warning("Бот не состоит ни в одном чате!")
        logger.info("Добавьте бота в чаты и отправьте там любое сообщение, затем запустите снова.")
        return

    # Показываем чаты
    logger.info("\nЧаты:")
    for chat in chats:
        logger.info(f"  • {chat['title']} ({chat['type']}) — ID: {chat['id']}")

    if args.dry_run:
        logger.info("\n[DRY RUN] Отправка отменена.")
        return

    # Рассылка
    logger.info(f"\nОтправляю рекламу в {len(chats)} чатов...")
    sent = 0
    failed = 0

    for chat in chats:
        ok = await send_promo_to_chat(bot_token, chat["id"], chat["title"])
        if ok:
            sent += 1
        else:
            failed += 1
        # Задержка между отправками (Telegram лимит: 30 сообщений/сек в группах)
        await asyncio.sleep(1)

    logger.info(f"\n=== ИТОГО ===")
    logger.info(f"  Отправлено: {sent}")
    logger.info(f"  Ошибок: {failed}")


if __name__ == "__main__":
    asyncio.run(main())
