"""
Парсер цен из сообщений ботов-конкурентов через Bot API.

Идея: боты-конкуренты (@benzin_price_bot, @azsprice_bot, и т.д.) — это
обычные Telegram-боты. Мы можем:
  1. Отправить им запрос через inline-режим (@our_bot 95 Иваново) — но
     это работает только если они поддерживают inline.
  2. Написать им напрямую — бот получит и обработает.
  3. Подписаться на их обновления через "приглашение в чат" — но это
     сложно.

Проще всего: **периодически** писать в эти боты типовой запрос и
перехватывать ответ, парсить цены.

Запуск:
  python scripts/parse_tg_bots.py --poll    # раз в час
  python scripts/parse_tg_bots.py --once    # один раз
"""
import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

# Загружаем .env
ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ENV_PATH)

import os  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

import db  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402

logger = logging.getLogger("tg_bots_parser")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# Боты-конкуренты, у которых будем спрашивать цены
COMPETITOR_BOTS = [
    {"username": "@benzin_price_bot", "name": "Benzin Price Bot"},
    {"username": "@azsprice_bot", "name": "АЗС Price Bot"},
    {"username": "@toplivo_bot", "name": "Toplivo Bot"},
    {"username": "@benzinru_bot", "name": "BenzinRU Bot"},
    {"username": "@fuel_prices_bot", "name": "Fuel Prices Bot"},
    {"username": "@azs_prices_bot", "name": "АЗС Prices Bot"},
]

# Запросы, которые посылаем ботам
QUERIES = [
    "АИ-95 Москва",
    "АИ-92 СПб",
    "ДТ Краснодар",
    "АИ-95 Екатеринбург",
    "АИ-92 Новосибирск",
]

# Паттерны цен (такие же как в parse_vk.py)
PRICE_PATTERNS = {
    "92": r"(?:аи-?92|92)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "95": r"(?:аи-?95|95)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "98": r"(?:аи-?98|98)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "diesel": r"(?:дизель|диз|дт)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
    "lpg": r"(?:газ|пропан)[\s\-:]+(?:от\s+)?(\d{2,3}[.,]\d{2})",
}

NETWORK_KEYWORDS = {
    "Лукойл": ["lukoil", "лукойл"],
    "Газпромнефть": ["газпромнефть", "газпром"],
    "Роснефть": ["роснефть"],
    "Татнефть": ["татнефть"],
    "Shell": ["shell"],
}

CITY_KEYWORDS = [
    "Москва", "Санкт-Петербург", "СПб", "Новосибирск", "Екатеринбург",
    "Казань", "Нижний Новгород", "Челябинск", "Самара", "Омск",
    "Ростов-на-Дону", "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград",
    "Краснодар", "Саратов", "Тюмень", "Иваново", "Ярославль", "Кострома",
    "Владимир", "Тула", "Калуга", "Тверь", "Брянск", "Курск", "Магнитогорск",
]


def parse_prices(text: str) -> dict[str, float]:
    """Извлекает цены из ответа бота."""
    prices = {}
    for fuel, pattern in PRICE_PATTERNS.items():
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                prices[fuel] = float(m.group(1).replace(",", "."))
            except (ValueError, IndexError):
                pass
    return prices


def detect_network(text: str) -> str | None:
    text_lower = text.lower()
    for network, kws in NETWORK_KEYWORDS.items():
        for kw in kws:
            if kw in text_lower:
                return network
    return None


def detect_city(text: str) -> str | None:
    for city in CITY_KEYWORDS:
        if city.lower() in text.lower():
            return city
    return None


async def query_bot(bot: Bot, competitor: dict, query: str, timeout: float = 10) -> str | None:
    """Отправляет запрос боту-конкуренту, ждёт ответ. Возвращает текст или None."""
    try:
        # Отправляем сообщение
        sent = await bot.send_message(competitor["username"], query)
        # Ждём ответ (reply на наше сообщение)
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            try:
                # Проверяем последние обновления через getUpdates нельзя для polling бота,
                # но можно через бот-сессию — но наш бот свой, не бот-конкурент.
                # Альтернатива: ждать пока наш бот не получит сообщение через update.
                pass
            except Exception:
                pass
        return None
    except Exception as e:
        logger.warning(f"  ⚠ {competitor['username']} '{query}': {e}")
        return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Один раз (по умолчанию)")
    parser.add_argument("--poll", action="store_true", help="Polling каждые --interval минут")
    parser.add_argument("--interval", type=int, default=60, help="Интервал в минутах")
    parser.add_argument("--dry-run", action="store_true", help="Не сохранять в БД")
    args = parser.parse_args()

    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        print("❌ BOT_TOKEN не задан в .env")
        return 1

    print(f"=== Парсер ботов-конкурентов через Bot API ===")
    print(f"Ботов: {len(COMPETITOR_BOTS)}")
    print(f"Запросов: {len(QUERIES)}")
    if args.poll:
        print(f"Режим: polling, интервал {args.interval} мин")

    bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=None))

    if not args.dry_run:
        await db.init_db()

    while True:
        print(f"\n--- Цикл {datetime.now().strftime('%H:%M:%S')} ---")
        # ВНИМАНИЕ: реальная отправка сообщений ботам требует:
        # 1) Бот-конкурент должен разрешать приём сообщений от нас
        # 2) Или мы должны быть в его inline-запросах
        # Этот скрипт-заглушка показывает структуру, но реальная отправка
        # может не сработать — нужно проверить что боты принимают /start.
        for comp in COMPETITOR_BOTS:
            for q in QUERIES:
                logger.info(f"→ {comp['username']} '{q}'")
                # Реальная логика будет через обработку update'ов в handlers
                # см. parse_tg_bots_handler() ниже

        if not args.poll:
            break
        await asyncio.sleep(args.interval * 60)

    if not args.dry_run:
        await db.close_db()
    await bot.session.close()
    return 0


# === Альтернативный подход: перехват через handlers ===
# Вместо активной отправки сообщений ботам, можно настроить
# в handlers.py перехват сообщений от других ботов:
#
# @dp.message(F.from_user.func(lambda u: u.is_bot))
# async def handle_bot_message(message: Message):
#     """Перехватываем сообщения от любых ботов."""
#     text = message.text or message.caption or ""
#     if not text or len(text) < 10:
#         return
#     prices = parse_prices(text)
#     if prices:
#         # Сохраняем в БД
#         ...
#
# Такой подход не требует credentials для Telethon
# и работает в рамках существующего бота.
if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
