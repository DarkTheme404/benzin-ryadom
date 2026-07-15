"""
Алерты админу в Telegram при критических ошибках.
Использует существующий Bot instance из settings.bot.
"""
import asyncio
import logging
import traceback
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ADMIN_TG_IDS = [772577887]  # darkt30


async def send_admin_alert(text: str, bot=None) -> bool:
    """Отправляет сообщение админу в Telegram.

    Args:
        text: текст сообщения
        bot: опционально — Bot instance, иначе берётся из settings

    Returns:
        True если отправлено, False если ошибка
    """
    try:
        if bot is None:
            from config import settings
            bot = settings.bot
        if bot is None:
            logger.warning("send_admin_alert: no bot instance available")
            return False

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        from config import settings
        backend_host = settings.BACKEND_URL.replace("https://", "").replace("http://", "")
        full_text = f"🚨 <b>ALERT [{timestamp}]</b>\n\n{text}\n\n<code>{backend_host}</code>"

        # Telegram лимит 4096 символов
        if len(full_text) > 4000:
            full_text = full_text[:4000] + "\n\n... (truncated)"

        for admin_id in ADMIN_TG_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=full_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"send_admin_alert: failed to send to {admin_id}: {e}")
        return True
    except Exception as e:
        logger.exception(f"send_admin_alert failed: {e}")
        return False


def alert_sync(text: str, exc: Exception = None) -> None:
    """Синхронная версия для использования в sync контексте.
    Запускает send_admin_alert в event loop.
    """
    if exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        text = f"{text}\n\n<code>{''.join(tb[-5:])}</code>"

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(send_admin_alert(text))
        else:
            loop.run_until_complete(send_admin_alert(text))
    except RuntimeError:
        # No event loop — create new one
        asyncio.run(send_admin_alert(text))
    except Exception as e:
        logger.exception(f"alert_sync failed: {e}")


async def alert_critical(text: str, exc: Exception = None) -> None:
    """Асинхронная версия для использования в async контексте."""
    if exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        text = f"{text}\n\n<code>{''.join(tb[-5:])}</code>"
    await send_admin_alert(text)
