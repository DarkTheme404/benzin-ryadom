"""
Polling worker для YooMoney — проверяет входящие переводы и активирует подписки.

Запускается как фоновая задача. Каждые 5 сек опрашивает operation_history
и проверяет pending payments (Premium + Founder Pack).
"""
import asyncio
import logging

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

        result = await check_payment_status(token, amount)
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

        result = await check_payment_status(token, amount)
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
