"""
Worker для автопубликации свежих отчётов в Telegram-канал.
Раз в N минут сканирует позитивные отчёты и публикует топ-новости.
"""
import asyncio
import logging
import random
from collections import defaultdict

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import db
from config import settings

logger = logging.getLogger(__name__)

CHANNEL_INTERVAL_SEC = 1800   # 30 мин между постами
CHANNEL_SCAN_MINUTES = 120    # берём отчёты за последние 2 часа
CHANNEL_TOP_PER_POST = 5      # максимум АЗС в одном посте
CHANNEL_MIN_REPORTS = 2       # минимум отчётов в городе для поста
CHANNEL_CITIES_PER_POST = 1   # сколько городов в одном посте


CHANNEL_POST_TEMPLATES = [
    "⛽ <b>Где есть бензин — {city}</b>",
    "🔥 <b>Свежие цены — {city}</b>",
    "💡 <b>Топ АЗС с топливом — {city}</b>",
    "⛽ <b>Есть бензин рядом — {city}</b>",
]


def _pick_template(city: str) -> str:
    """Случайный шаблон для разнообразия постов."""
    tpl = random.choice(CHANNEL_POST_TEMPLATES)
    return tpl.format(city=city or "Россия")


def _format_post(city: str, items: list) -> str:
    """Форматирует пост для канала: топ АЗС в городе с verified-приоритетом."""
    lines = [_pick_template(city) + "\n"]
    for i, it in enumerate(items, 1):
        name = it.get("name") or "АЗС"
        operator = it.get("operator") or ""
        fuel = it.get("fuel_type", "?")
        address = it.get("address") or ""
        available = it.get("available")
        price = it.get("price")
        verified = bool(it.get("is_verified"))

        if available is True or available == 1:
            icon = "✅"
        elif available is None or available == 2:
            icon = "⚠️"
        else:
            icon = "❌"

        display_name = name if not operator or operator == name else f"{name} ({operator})"
        if verified:
            display_name = f"✓ {display_name}"

        line = f"{i}. {icon} <b>{display_name}</b> — АИ-{fuel}"
        if price is not None:
            line += f" · <b>{float(price):.2f}₽</b>"
        lines.append(line)
        if address:
            lines.append(f"   📍 {address}")
    lines.append("\n💡 Сообщи о наличии → @benzyn_ryadom_bot")
    lines.append("📊 Источник: краудсорс водителей в реальном времени")
    return "\n".join(lines)


async def channel_loop(bot: Bot):
    """Главный цикл: раз в CHANNEL_INTERVAL_SEC публикует в канал."""
    chat_id = settings.CHANNEL_CHAT_ID
    if not chat_id:
        logger.info("Channel worker: CHANNEL_CHAT_ID не задан, пропускаем")
        return
    logger.info("Channel worker started, target: %s", chat_id)
    while True:
        try:
            await _channel_iteration(bot, chat_id)
        except Exception as e:
            logger.exception("Channel iteration failed: %s", e)
        await asyncio.sleep(CHANNEL_INTERVAL_SEC)


async def _channel_iteration(bot: Bot, chat_id: str):
    """Одна итерация: агрегировать отчёты по городам, опубликовать топ-N городов."""
    reports = await db.get_recent_fuel_reports(minutes=CHANNEL_SCAN_MINUTES)
    if not reports:
        logger.debug("Channel: no recent reports")
        return

    by_city = defaultdict(list)
    for r in reports:
        city = (r.get("city") or "").strip()
        by_city[city].append(r)

    cities_with_data = [
        (city, items) for city, items in by_city.items() if len(items) >= CHANNEL_MIN_REPORTS
    ]
    if not cities_with_data:
        return

    cities_with_data.sort(key=lambda x: -len(x[1]))

    for city, items in cities_with_data[:CHANNEL_CITIES_PER_POST]:
        items = items[:CHANNEL_TOP_PER_POST]
        items.sort(key=lambda r: (
            0 if r.get("is_verified") else 1,
            0 if r.get("price") else 1,
            -(r.get("created_at_timestamp") or 0),
        ))

        text = _format_post(city, items)
        try:
            me = await bot.get_me()
            bot_username = me.username or "benzyn_ryadom_bot"
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🗺 Открыть в боте",
                        url=f"https://t.me/{bot_username}",
                    )],
                ]),
            )
            logger.info("Channel: posted for city=%s (%d items)", city or "—", len(items))
            break
        except TelegramRetryAfter as e:
            logger.warning("Channel: rate limit, retry after %ds", e.retry_after)
            await asyncio.sleep(e.retry_after)
        except TelegramAPIError as e:
            logger.warning("Channel post failed: %s", e)
            break

