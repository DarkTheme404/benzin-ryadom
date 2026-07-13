"""
Polling worker для YooMoney — проверяет входящие переводы и активирует подписки.

Запускается как фоновая задача. Каждые 5 сек опрашивает operation_history
и проверяет pending payments.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # секунд


async def yoomoney_polling_loop() -> None:
    """Главный цикл: каждые POLL_INTERVAL сек проверяем pending payments."""
    if not __import__("os").environ.get("YOOMONEY_TOKEN"):
        logger.info("YooMoney polling: YOOMONEY_TOKEN not set, polling disabled")
        return

    logger.info("YooMoney polling: started (interval=%ds)", POLL_INTERVAL)

    while True:
        try:
            await _poll_once()
        except Exception as e:
            logger.exception("YooMoney poll error: %s", e)
        await asyncio.sleep(POLL_INTERVAL)


async def _poll_once() -> None:
    """Один проход: проверяет все pending payments."""
    from yoomoney_pay import check_payment_status, YOOMONEY_RECEIVER
    import db

    if not YOOMONEY_RECEIVER:
        return

    pending = await db.get_pending_payments(limit=30)
    if not pending:
        return

    for payment in pending:
        token = payment.get("external_id")
        amount = payment.get("amount", 0)
        if not token:
            continue

        # Проверяем сколько прошло времени (не проверяем старые — больше 30 дней)
        from datetime import datetime, timezone
        created_at = payment.get("created_at")
        if created_at:
            try:
                if isinstance(created_at, str):
                    created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                else:
                    created_dt = created_at
                age_days = (datetime.now(timezone.utc) - created_dt).days
                if age_days > 30:
                    continue
            except Exception:
                pass

        result = await check_payment_status(token, amount)
        if result.get("ok") and result.get("paid"):
            logger.info(
                "YooMoney: активирую подписку по токену %s (operation=%s)",
                token[:12], result.get("operation_id"),
            )
            try:
                await db.confirm_payment(token)
            except Exception as e:
                logger.exception("Ошибка активации: %s", e)
