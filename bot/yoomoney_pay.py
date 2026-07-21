"""
YooMoney P2P оплата для Premium подписки.

Регистрация (15 мин):
1. Создать кошелёк: https://yoomoney.ru
2. Зарегистрировать приложение: https://yoomoney.ru/myservices/new
   - Redirect URI: {BACKEND_URL}/api/yoomoney/callback
   - Scopes: account-info, operation-history, operation-details, payment-p2p
3. Получить ACCESS_TOKEN через OAuth (Authorize URL)
4. Добавить в env: YOOMONEY_TOKEN, YOOMONEY_RECEIVER (номер кошелька)

API:
- Quickpay форма: генерирует URL для оплаты
- Operation history: проверяем входящие переводы по `label` (= payment_token)

Polling:
- Каждые 5 сек проверяем новые входящие переводы
- Если label == payment_token и amount == ожидаемая сумма — активируем подписку
"""
import logging
import os
import certifi
from typing import Optional

logger = logging.getLogger(__name__)

# SSL fix — некоторые окружения (macOS, Render) имеют проблемы с SSL handshake
# к yoomoney.ru. Используем certifi для корректного CA bundle.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())


# === Конфигурация ===
YOOMONEY_TOKEN = os.environ.get("YOOMONEY_TOKEN", "")
YOOMONEY_RECEIVER = os.environ.get("YOOMONEY_RECEIVER", "")  # Номер кошелька (41001...)
YOOMONEY_CLIENT_ID = os.environ.get("YOOMONEY_CLIENT_ID", "")
YOOMONEY_REDIRECT_URI = os.environ.get("YOOMONEY_REDIRECT_URI", "")


def is_configured() -> bool:
    """Проверяет, настроен ли YooMoney."""
    return bool(YOOMONEY_TOKEN) and bool(YOOMONEY_RECEIVER)


def create_payment(
    amount: int,
    description: str,
    payment_token: str,
    success_url: Optional[str] = None,
) -> dict:
    """Создаёт Quickpay-форму и возвращает URL для оплаты.

    YooMoney Quickpay формат:
    https://yoomoney.ru/quickpay/confirm.xml?receiver=41001...
      &quickpay-form=shop&targets=...&paymentType=SB&sum=...
    """
    if not is_configured():
        return {
            "ok": False,
            "error": "YooMoney not configured. Set YOOMONEY_TOKEN and YOOMONEY_RECEIVER env vars.",
        }

    # label = полный payment_token (для однозначной идентификации платежа)
    # В URL label не должен превышать ~250 символов, у нас обычно 32
    label = f"benzin-{payment_token}"

    # URL для Quickpay формы
    import urllib.parse
    params = {
        "receiver": YOOMONEY_RECEIVER,
        "quickpay-form": "shop",
        "targets": description,
        "paymentType": "SB",  # SB = оплата из кошелька, AC = с карты
        "sum": amount,
        "label": label,
        "successURL": success_url or "https://benzin-ryadom.onrender.com",
    }
    quickpay_url = "https://yoomoney.ru/quickpay/confirm.xml?" + urllib.parse.urlencode(params)

    return {
        "ok": True,
        "method": "yoomoney",
        "payment_url": quickpay_url,
        "amount": amount,
        "label": label,
        "payment_token": payment_token,
        "description": description,
        "receiver": YOOMONEY_RECEIVER,
    }


async def check_payment_status(payment_token: str, expected_amount: int) -> dict:
    """Проверяет, был ли платёж с указанным токеном.

    Использует YooMoney API: operation_history.
    Ищет входящий перевод (incoming-transfer) с label == 'benzin-{token}'.
    """
    if not is_configured():
        return {"ok": False, "error": "YooMoney not configured"}

    try:
        from yoomoney import Client
    except ImportError:
        return {"ok": False, "error": "yoomoney library not installed"}

    try:
        client = Client(YOOMONEY_TOKEN)
        label = f"benzin-{payment_token}"
        # Получаем историю операций (последние 30 дней)
        history = client.operation_history(label=label, records=20)

        for op in history.operations:
            # Проверяем входящий перевод с нашим label
            if op.label == label and op.direction == "in" and op.status == "success":
                if op.amount >= expected_amount:
                    logger.info(f"YooMoney payment found: {op.operation_id} amount={op.amount}")
                    return {
                        "ok": True,
                        "paid": True,
                        "operation_id": op.operation_id,
                        "amount": op.amount,
                        "datetime": str(op.datetime),
                    }
                else:
                    return {
                        "ok": True,
                        "paid": False,
                        "error": f"Недостаточная сумма: получено {op.amount}, ожидалось {expected_amount}",
                    }

        return {"ok": True, "paid": False, "error": "Платёж не найден"}
    except Exception as e:
        logger.exception(f"YooMoney check error: {e}")
        return {"ok": False, "error": str(e)}


# === Выплаты на карту через YooMoney API ===
import aiohttp


async def payout_to_card(card_number: str, amount: int, comment: str = "") -> dict:
    """Перевод на банковскую карту через YooMoney API.

    Использует endpoint https://yoomoney.ru/api/request-payment
    с pattern_id = 'p2p-incoming' (перевод на карту по номеру).

    Требования:
    - Токен должен иметь права: payment-p2p, payment-shop, account-info
    - На кошельке должно быть достаточно средств
    - Сумма от 100₽ до 75 000₽ за один перевод
    """
    if not is_configured():
        return {"ok": False, "error": "YooMoney not configured"}

    # Валидация номера карты (простая)
    card_clean = card_number.replace(" ", "").replace("-", "")
    if not card_clean.isdigit() or len(card_clean) not in (13, 14, 15, 16, 18, 19):
        return {"ok": False, "error": "Неверный номер карты"}

    if amount < 100 or amount > 75000:
        return {"ok": False, "error": "Сумма должна быть от 100 до 75000₽"}

    # YooMoney request-payment ожидает HTTP Basic auth с токеном
    import base64
    auth = base64.b64encode(f"{YOOMONEY_TOKEN}:".encode()).decode()

    payload = {
        "pattern_id": "p2p-incoming",
        "to": card_clean,
        "amount": amount,
        "comment": comment or f"Выплата benzin-ryadom",
        "message": comment or "Выплата реферальных",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://yoomoney.ru/api/request-payment",
                json=payload,
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("status") == "success":
                    request_id = data.get("request_id")
                    logger.info(f"YooMoney payout created: request_id={request_id}, amount={amount}, to=...{card_clean[-4:]}")
                    return {
                        "ok": True,
                        "request_id": request_id,
                        "amount": amount,
                        "card_last4": card_clean[-4:],
                    }
                else:
                    err = data.get("error", "Unknown error")
                    logger.error(f"YooMoney payout error: {err}, data={data}")
                    return {"ok": False, "error": err, "raw": data}
    except Exception as e:
        logger.exception(f"YooMoney payout exception: {e}")
        return {"ok": False, "error": str(e)}


async def confirm_payout(request_id: str) -> dict:
    """Подтверждает ранее созданный платёж (process-payment).

    После request-payment нужно вызвать process-payment чтобы деньги ушли.
    """
    if not is_configured():
        return {"ok": False, "error": "YooMoney not configured"}

    import base64
    auth = base64.b64encode(f"{YOOMONEY_TOKEN}:".encode()).decode()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://yoomoney.ru/api/process-payment",
                json={"request_id": request_id},
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("status") == "success":
                    logger.info(f"YooMoney payout confirmed: request_id={request_id}")
                    return {
                        "ok": True,
                        "status": data.get("status"),
                        "payment_id": data.get("payment_id"),
                    }
                else:
                    err = data.get("error", "Unknown error")
                    logger.error(f"YooMoney confirm error: {err}")
                    return {"ok": False, "error": err, "raw": data}
    except Exception as e:
        logger.exception(f"YooMoney confirm exception: {e}")
        return {"ok": False, "error": str(e)}


# === Конфиг для Render ENV ===
# YOOMONEY_TOKEN — OAuth access token (получить через https://yoomoney.ru/myservices/new)
# YOOMONEY_RECEIVER — номер вашего кошелька (41001...)
# YOOMONEY_CLIENT_ID — ID приложения (опционально, для переавторизации)
# YOOMONEY_REDIRECT_URI — callback URL
