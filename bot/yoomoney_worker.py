"""
Polling worker для YooMoney — проверяет входящие переводы и активирует подписки.

Запускается как фоновая задача. Каждые 60 сек опрашивает operation_history
и проверяет pending payments (Premium + Founder Pack).

Используем asyncio.to_thread() для синхронных HTTP-вызовов yoomoney,
чтобы не блокировать event loop.

Автоматически отключается после 10 подряд ошибок подключения (Render Free
не может достучаться до YooMoney API).
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60
MAX_CONSECUTIVE_ERRORS = 10  # после этого — спим 1 час вместо опроса
LONG_SLEEP = 3600  # 1 час
_consecutive_errors = 0
_disabled = False


async def yoomoney_polling_loop() -> None:
    """Главный цикл: каждые POLL_INTERVAL сек проверяем pending payments."""
    global _consecutive_errors, _disabled

    if not __import__("os").environ.get("YOOMONEY_TOKEN"):
        logger.info("YooMoney polling: YOOMONEY_TOKEN not set, polling disabled")
        return

    logger.info("YooMoney polling: started (interval=%ds)", POLL_INTERVAL)

    while True:
        if _disabled:
            logger.info("YooMoney polling: DISABLED after %d consecutive errors. Sleeping 1h.", MAX_CONSECUTIVE_ERRORS)
            await asyncio.sleep(LONG_SLEEP)
            continue

        try:
            await _poll_once()
            _consecutive_errors = 0
        except Exception as e:
            _consecutive_errors += 1
            logger.warning("YooMoney poll error (#%d): %s", _consecutive_errors, e)
            if _consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    "YooMoney polling: DISABLED after %d consecutive errors. "
                    "API unreachable? Sleeping 1h.",
                    _consecutive_errors,
                )
                _disabled = True
                continue

        await asyncio.sleep(POLL_INTERVAL)


async def _poll_once() -> None:
    """Один проход: проверяет все pending payments (Premium + Founder).

    Оптимизация: вместо отдельного check_payment_status() для каждого платежа
    делаем ОДИН запрос operation_history и матчим все pending по label.
    """
    from yoomoney_pay import is_configured, YOOMONEY_RECEIVER
    import db

    if not YOOMONEY_RECEIVER or not is_configured():
        return

    # Собираем все pending payment'ы
    pending = await db.get_pending_payments(limit=50)
    pending_founder = await db.get_pending_founder_purchases(limit=50)

    # Фильтруем старые (>30 дней)
    pending = [p for p in pending if not _is_too_old(p.get("created_at"))]
    pending_founder = [p for p in pending_founder if not _is_too_old(p.get("created_at"))]

    all_pending = pending + pending_founder
    if not all_pending:
        return

    # Строим map: label -> (token, amount, type)
    label_map = {}
    for p in pending:
        token = p.get("external_id")
        if token:
            label_map[f"benzin-{token}"] = (token, p.get("amount", 0), "premium")
    for p in pending_founder:
        token = p.get("external_id")
        if token:
            label_map[f"benzin-{token}"] = (token, p.get("amount", 0), "founder")

    if not label_map:
        return

    # ОДИН запрос operation_history (records=100 чтобы покрыть все pending)
    try:
        from yoomoney import Client
        import os, certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())

        client = Client(os.environ.get("YOOMONEY_TOKEN", ""))
        try:
            import httpx
            client._transport._client = httpx.Client(
                timeout=httpx.Timeout(20.0),
                headers=client._transport._auth_headers(),
            )
        except Exception:
            pass

        history = client.operation_history(records=min(100, len(label_map) * 3 + 20))
    except Exception as e:
        logger.warning("YooMoney history fetch failed: %s", e)
        return

    # Матчим операции с pending payment'ами
    matched_tokens = set()
    for op in getattr(history, "operations", []):
        label = getattr(op, "label", "")
        if label not in label_map:
            continue
        if getattr(op, "direction", "") != "in" or getattr(op, "status", "") != "success":
            continue

        token, expected_amount, pay_type = label_map[label]
        if token in matched_tokens:
            continue
        if getattr(op, "amount", 0) < expected_amount:
            logger.warning(
                "YooMoney: label=%s amount=%s < expected=%s",
                label[:30], op.amount, expected_amount,
            )
            continue

        matched_tokens.add(token)
        logger.info(
            "YooMoney: payment matched (type=%s, token=%s, op=%s)",
            pay_type, token[:12], getattr(op, "operation_id", "?"),
        )
        try:
            if pay_type == "premium":
                await db.confirm_payment(token)
            else:
                await db.confirm_founder_purchase(token)
        except Exception as e:
            logger.exception("YooMoney activation error (type=%s): %s", pay_type, e)


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
