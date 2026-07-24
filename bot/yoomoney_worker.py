"""
Polling worker для YooMoney — проверяет входящие переводы и активирует подписки.

Запускается как фоновая задача. Каждые 60 сек опрашивает operation_history
и проверяет pending payments (Premium + Founder Pack).

Используем asyncio.to_thread() для синхронных HTTP-вызовов yoomoney,
чтобы не блокировать event loop.
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60  # секунд (было 5 — убивало память на Render Free)
CONSECUTIVE_ERRORS = 0
MAX_BACKOFF = 300  # макс 5 минут между проверками при ошибках


async def yoomoney_polling_loop() -> None:
    """Главный цикл: каждые POLL_INTERVAL сек проверяем pending payments."""
    if not __import__("os").environ.get("YOOMONEY_TOKEN"):
        logger.info("YooMoney polling: YOOMONEY_TOKEN not set, polling disabled")
        return

    logger.info("YooMoney polling: started (interval=%ds)", POLL_INTERVAL)

    global CONSECUTIVE_ERRORS
    while True:
        try:
            await _poll_once()
            CONSECUTIVE_ERRORS = 0
        except Exception as e:
            CONSECUTIVE_ERRORS += 1
            logger.warning("YooMoney poll error (#%d): %s", CONSECUTIVE_ERRORS, e)
        wait = POLL_INTERVAL * min(2 ** min(CONSECUTIVE_ERRORS, 5), MAX_BACKOFF // POLL_INTERVAL)
        await asyncio.sleep(wait)


async def _poll_once() -> None:
    """Один проход: проверяет все pending payments (Premium + Founder)."""
    from yoomoney_pay import check_payment_status, YOOMONEY_RECEIVER
    import db

    if not YOOMONEY_RECEIVER:
        return

    # 1) Premium подписки (economy / standard / elite)
    pending = await db.get_pending_payments(limit=30)
    for payment in pending:
        token = payment.get("external_id")
        amount = payment.get("amount", 0)
        if not token:
            continue

        if _is_too_old(payment.get("created_at")):
            continue

        # check_payment_status — синхронный (httpx) → запускаем в thread pool
        result = await asyncio.to_thread(check_payment_status, token, amount)
        if result.get("ok") and result.get("paid"):
            logger.info(
                "YooMoney: активирую Premium по токену %s (operation=%s)",
                token[:12], result.get("operation_id"),
            )
            try:
                await db.confirm_payment(token)
            except Exception as e:
                logger.exception("Ошибка активации Premium: %s", e)

    # 2) Founder Pack (пожизненный Elite)
    pending_founder = await db.get_pending_founder_purchases(limit=30)
    for purchase in pending_founder:
        token = purchase.get("external_id")
        amount = purchase.get("amount", 0)
        if not token:
            continue

        if _is_too_old(purchase.get("created_at")):
            continue

        result = await asyncio.to_thread(check_payment_status, token, amount)
        if result.get("ok") and result.get("paid"):
            logger.info(
                "YooMoney: активирую Founder Pack по токену %s (operation=%s)",
                token[:12], result.get("operation_id"),
            )
            try:
                await db.confirm_founder_purchase(token)
            except Exception as e:
                logger.exception("Ошибка активации Founder: %s", e)


def _is_too_old(created_at) -> bool:
    """Проверяет, старше ли платёж 30 дней (не проверяем — слишком старые)."""
    if not created_at:
        return False
    from datetime import datetime, timezone
    try:
        if isinstance(created_at, str):
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_dt = created_at
        age_days = (datetime.now(timezone.utc) - created_dt).days
        return age_days > 30
    except Exception:
        return False
