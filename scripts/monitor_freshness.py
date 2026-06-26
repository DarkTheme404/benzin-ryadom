"""
Мониторинг свежести данных — шлёт alert админу если данные устарели.

Запускается вместе с ботом каждые 6 часов.
Проверяет:
- fuelprice_ru: должен обновляться каждые 6ч (через Cron Job)
- miniapp, owner, user: могут быть любыми

Шлёт в TG админу если:
- fuelprice_ru: последний отчёт > 6 часов
- Другие: > 24 часов (только info)
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Добавляем bot/ в path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

import aiohttp
import db  # noqa: E402

logger = logging.getLogger(__name__)


# === Telegram уведомления ===
async def notify_admin(bot_token: str, chat_id: str, message: str) -> None:
    """Шлёт сообщение админу через Telegram Bot API."""
    if not bot_token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with aiohttp.ClientSession() as s:
            await s.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
    except Exception as e:
        logger.warning(f"notify_admin failed: {e}")


async def check_source_freshness() -> list[dict]:
    """Проверяет свежесть каждого источника."""
    rows = await db._fetch("""
        SELECT source,
               COUNT(*) as total,
               MAX(created_at) as last_update,
               COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as h1,
               COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') as h24
        FROM reports
        GROUP BY source
    """)
    return [dict(r) for r in rows]


def get_status(hours_ago: float, critical_hours: float = 6, warn_hours: float = 24) -> str:
    if hours_ago < critical_hours:
        return "OK"
    elif hours_ago < warn_hours:
        return "WARN"
    else:
        return "DEAD"


async def send_alert_if_needed(sources: list[dict]) -> bool:
    """Шлёт alert если нужно. Возвращает True если alert был отправлен."""
    bot_token = os.getenv("BOT_TOKEN", "")
    chat_id = os.getenv("ADMIN_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False

    issues = []
    for s in sources:
        last = s.get("last_update")
        if not last:
            continue
        hours_ago = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        # Критические источники: fuelprice_ru (главный)
        if s["source"] == "fuelprice_ru":
            status = get_status(hours_ago, critical_hours=6, warn_hours=12)
            if status in ("WARN", "DEAD"):
                issues.append(f"🔴 <b>fuelprice_ru</b> не обновлялся {hours_ago:.1f}ч")
        # Другие источники (Telegram, User, Owner) — менее критичны
        elif s["source"] in ("telegram", "tg_prices"):
            status = get_status(hours_ago, critical_hours=2, warn_hours=12)
            if status in ("WARN", "DEAD"):
                issues.append(f"🟡 <b>{s['source']}</b> не обновлялся {hours_ago:.1f}ч")

    if not issues:
        return False

    message = (
        f"⚠️ <b>Бот «Бензин рядом» — алерт</b>\n\n"
        + "\n".join(issues) + "\n\n"
        f"🔧 Проверьте: https://dashboard.render.com\n"
        f"📊 Подробнее: /stats или /api/admin/stats"
    )

    await notify_admin(bot_token, chat_id, message)
    return True


async def main():
    """Главная функция — проверка свежести и отправка alert."""
    print("=== Мониторинг свежести данных ===")
    await db.init_db()

    sources = await check_source_freshness()
    print(f"\n📊 Проверено источников: {len(sources)}")
    print()

    critical = []
    for s in sources:
        last = s.get("last_update")
        if not last:
            continue
        hours_ago = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        status = get_status(hours_ago, critical_hours=6, warn_hours=24)
        status_icon = {"OK": "✅", "WARN": "🟡", "DEAD": "🔴"}.get(status, "❓")
        print(f"  {status_icon} {s['source']:25s} {s['total']:>5} цен, last {hours_ago:.1f}ч назад")
        if status in ("WARN", "DEAD"):
            critical.append(s)

    if critical:
        print(f"\n⚠️ {len(critical)} источников требуют внимания")
        sent = await send_alert_if_needed(sources)
        if sent:
            print("  ✉️ Alert отправлен админу")
        else:
            print("  ⚠️ Alert не отправлен (нет ADMIN_CHAT_ID)")
    else:
        print("\n✅ Все источники в норме")

    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
