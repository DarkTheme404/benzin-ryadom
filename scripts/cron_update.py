"""
Cron Job для Render — обновление цен каждые 6 часов.

Использует все доступные парсеры:
- fuelprice.ru (главный источник, 60+ городов)
- 2ГИС (если есть ключ)
- azsprice.ru (Москва + Подмосковье)
- benzin-price.ru (если есть Playwright)

Шлёт в TG отчёт админу.

Render Cron Job schedule: "0 */6 * * *"
"""
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Добавляем bot/ в path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))
import db  # noqa: E402

from parse_fuelprice import main as parse_fuelprice_main


# Топ-12 городов
TOP_CITIES = [
    "moskva", "sankt-peterburg", "novosibirsk", "ekaterinburg",
    "kazan", "krasnodar", "chelyabinsk", "nizhniy-novgorod",
    "samara", "rostov-na-donu", "ufa", "krasnoyarsk",
]


# === Telegram уведомления ===
async def notify_admin(bot_token: str, chat_id: str, message: str) -> None:
    """Шлёт сообщение админу через Telegram Bot API."""
    if not bot_token or not chat_id:
        return
    try:
        import aiohttp
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with aiohttp.ClientSession() as s:
            await s.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
    except Exception as e:
        print(f"  ⚠ notify_admin: {e}")


async def run_fuelprice_for_all_cities() -> dict:
    """Запускает fuelprice.ru по всем городам."""
    print(f"\n[fuelprice.ru] {len(TOP_CITIES)} городов")
    print(f"  Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = {"matched": 0, "created": 0, "saved": 0, "errors": 0}

    for city in TOP_CITIES:
        try:
            # Запускаем парсер в subprocess чтобы изолировать
            import subprocess
            cmd = [
                sys.executable,
                os.path.join(os.path.dirname(__file__), "parse_fuelprice.py"),
                "--city", city,
                "--create-new",
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            # Парсим вывод
            output = result.stdout + result.stderr
            for line in output.split("\n"):
                if "сохранено" in line.lower():
                    try:
                        num = int(line.split(":")[-1].strip())
                        results["saved"] += num
                    except (ValueError, IndexError):
                        pass
                elif "матч" in line.lower():
                    try:
                        num = int(line.split(":")[-1].strip())
                        results["matched"] += num
                    except (ValueError, IndexError):
                        pass
                elif "новых азс" in line.lower():
                    try:
                        num = int(line.split(":")[-1].strip())
                        results["created"] += num
                    except (ValueError, IndexError):
                        pass
                elif "error" in line.lower() or "timeout" in line.lower():
                    results["errors"] += 1
        except subprocess.TimeoutExpired:
            print(f"  ⏱ {city}: timeout")
            results["errors"] += 1
        except Exception as e:
            print(f"  ❌ {city}: {e}")
            results["errors"] += 1

    return results


async def main():
    start_time = time.time()
    print("=" * 60)
    print(f"⛽ CRON UPDATE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # === Инициализация БД ===
    await db.init_db()

    # === Запуск парсеров ===
    results = await run_fuelprice_for_all_cities()

    elapsed = time.time() - start_time

    # === Статистика из БД ===
    stats = await db._fetch("""
        SELECT source, COUNT(*) as cnt
        FROM reports
        WHERE created_at > NOW() - INTERVAL '24 hours'
        GROUP BY source
        ORDER BY cnt DESC
    """, one=False)

    total_recent = sum(s["cnt"] for s in stats)

    # === Отчёт ===
    report = (
        f"⛽ <b>Cron Update отчёт</b>\n\n"
        f"⏱ Время: {elapsed:.0f} сек\n"
        f"📊 Обновлено за 24ч: <b>{total_recent}</b> цен\n\n"
        f"<b>Источники:</b>\n"
    )
    for s in stats:
        report += f"  • {s['source']}: {s['cnt']}\n"

    report += f"\n<b>Этот запуск (fuelprice.ru):</b>\n"
    report += f"  ✓ Матчей: {results['matched']}\n"
    report += f"  ✓ Новых АЗС: {results['created']}\n"
    report += f"  ✓ Цен сохранено: {results['saved']}\n"
    if results["errors"]:
        report += f"  ⚠ Ошибок: {results['errors']}\n"

    print("\n" + report.replace("<b>", "").replace("</b>", ""))

    # === TG уведомление ===
    bot_token = os.getenv("BOT_TOKEN", "")
    chat_id = os.getenv("ADMIN_CHAT_ID", os.getenv("CHANNEL_CHAT_ID", ""))
    if bot_token and chat_id:
        await notify_admin(bot_token, chat_id, report)

    await db.close_db()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
