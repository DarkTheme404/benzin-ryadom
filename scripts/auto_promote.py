#!/usr/bin/env python3
"""Автоматическая рассылка рекламы «Бензин рядом» по пикам активности.

Расписание по дням недели:
- Пн-Пт: утро (7-9), вечер (17-19)
- Сб: утро (9-11)
- Вс: вечер (16-18)

Запуск:
    python scripts/auto_promote.py              # Однократная проверка
    python scripts/auto_promote.py --daemon     # Фоновый режим (проверка каждые 5 мин)
    python scripts/auto_promote.py --force      # Принудительная отправка сейчас
"""

import asyncio
import os
import sys
import logging
import aiohttp
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "bot", ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# Пики активности по дням недели (Moscow time)
# ============================================================

# Формат: [(start_hour, end_hour), ...]
# Бот отправляет в случайное время внутри окна пика
PEAK_HOURS = {
    0: [(16, 18)],           # Понедельник: вечер (подготовка к утре)
    1: [(7, 9), (17, 19)],   # Вторник: утро + вечер
    2: [(7, 9), (17, 19)],   # Среда: утро + вечер
    3: [(7, 9), (17, 19)],   # Четверг: утро + вечер
    4: [(7, 9), (16, 18)],   # Пятница: утро + вечер (раньше)
    5: [(9, 11)],            # Суббота: утро
    6: [(16, 18)],           # Воскресенье: вечер
}

# Москва = UTC+3
MSK = timezone(timedelta(hours=3))

# Файл для хранения времени последней отправки
STATE_FILE = os.path.join(os.path.dirname(__file__), ".promote_state")

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


def load_state() -> dict:
    """Загружает состояние (когда dernière отправка)."""
    try:
        with open(STATE_FILE, "r") as f:
            return eval(f.read())
    except Exception:
        return {"last_sent": None, "last_day": None}


def save_state(state: dict):
    """Сохраняет состояние."""
    with open(STATE_FILE, "w") as f:
        f.write(repr(state))


def is_peak_now(now: datetime) -> bool:
    """Проверяет, наступило ли время пика."""
    weekday = now.weekday()  # 0=Пн, 6=Вс
    hour = now.hour

    peaks = PEAK_HOURS.get(weekday, [])
    for start, end in peaks:
        if start <= hour < end:
            return True
    return False


def should_send(state: dict, now: datetime) -> bool:
    """Определяет, нужно ли отправлять рассылку."""
    today = now.date()
    last_day = state.get("last_day")

    # Уже отправляли сегодня
    if last_day == str(today):
        return False

    # Проверяем пик
    if not is_peak_now(now):
        return False

    return True


async def get_bot_chats(bot_token: str) -> list:
    """Получает список чатов, где состоит бот."""
    chats = {}

    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {"limit": 100, "allowed_updates": '["message", "channel_post", "my_chat_member"]'}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                for update in data.get("result", []):
                    for key in ("message", "channel_post", "my_chat_member"):
                        if key in update:
                            chat = update[key].get("chat", {})
                            if chat.get("id"):
                                chats[chat["id"]] = {
                                    "id": chat["id"],
                                    "title": chat.get("title", chat.get("first_name", "Unknown")),
                                    "type": chat.get("type", "unknown"),
                                }

    # Канал из переменной окружения
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


async def send_promo(bot_token: str, chat_id: int, chat_title: str) -> bool:
    """Отправляет рекламное сообщение."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": PROMO_TEXT,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("ok"):
                    return True
                else:
                    logger.warning(f"⚠ {chat_title}: {data.get('description', 'error')}")
                    return False
    except Exception as e:
        logger.error(f"❌ {chat_title}: {e}")
        return False


async def do_promote(bot_token: str) -> dict:
    """Выполняет рассылку. Возвращает статистику."""
    chats = await get_bot_chats(bot_token)
    if not chats:
        logger.info("Нет чатов для рассылки")
        return {"sent": 0, "failed": 0, "chats": 0}

    sent = 0
    failed = 0
    for chat in chats:
        ok = await send_promo(bot_token, chat["id"], chat["title"])
        if ok:
            sent += 1
            logger.info(f"✅ {chat['title']}")
        else:
            failed += 1
        await asyncio.sleep(1)

    return {"sent": sent, "failed": failed, "chats": len(chats)}


async def run_daemon():
    """Фоновый режим: проверка каждые 5 минут."""
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        logger.error("BOT_TOKEN не задан!")
        return

    logger.info("=== Auto Promote Daemon ===")
    logger.info("Проверка каждые 5 минут...")

    state = load_state()

    while True:
        now = datetime.now(MSK)

        if should_send(state, now):
            logger.info(f"Пик активности! Отправляю рассылку... ({now.strftime('%H:%M')})")
            result = await do_promote(bot_token)
            logger.info(f"Результат: {result['sent']} отправлено, {result['failed']} ошибок")

            # Сохраняем состояние
            state["last_sent"] = now.isoformat()
            state["last_day"] = str(now.date())
            save_state(state)

        await asyncio.sleep(300)  # 5 минут


async def run_once(force: bool = False):
    """Однократная проверка/отправка."""
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        logger.error("BOT_TOKEN не задан!")
        return

    now = datetime.now(MSK)
    state = load_state()

    logger.info(f"Время: {now.strftime('%Y-%m-%d %H:%M:%S')} MSK")
    logger.info(f"День недели: {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'][now.weekday()]}")
    logger.info(f"Пик сейчас: {is_peak_now(now)}")
    logger.info(f"Отправлено сегодня: {state.get('last_day') == str(now.date())}")

    if force or should_send(state, now):
        logger.info("Отправляю рассылку...")
        result = await do_promote(bot_token)
        logger.info(f"Результат: {result['sent']} отправлено, {result['failed']} ошибок, {result['chats']} чатов")

        state["last_sent"] = now.isoformat()
        state["last_day"] = str(now.date())
        save_state(state)
    else:
        logger.info("Сейчас не пик или уже отправляли сегодня.")


def main():
    parser = argparse.ArgumentParser(description="Авто-рассылка рекламы «Бензин рядом»")
    parser.add_argument("--daemon", action="store_true", help="Фоновый режим (проверка каждые 5 мин)")
    parser.add_argument("--force", action="store_true", help="Принудительная отправка сейчас")
    args = parser.parse_args()

    if args.daemon:
        asyncio.run(run_daemon())
    else:
        asyncio.run(run_once(force=args.force))


if __name__ == "__main__":
    main()
