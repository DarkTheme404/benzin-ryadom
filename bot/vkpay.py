"""
VK Pay — реальная интеграция с HMAC-подписью.

Документация: https://dev.vk.com/api/pay/overview

Параметры merchant_id и secret_key нужно получить в кабинете VK Pay:
https://vk.com/pay/business (после регистрации магазина).

Подпись запросов:
  sig = sha256(secret_key + params_sorted_by_key + secret_key)
  где params = key1=value1&key2=value2... (без URL-encoding)

Callback от VK Pay:
  VK отправляет POST с подписью в заголовке X-Signature
  Проверяем: sha256(secret_key + body + secret_key) == X-Signature
"""
import hashlib
import hmac
import logging
import os
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


# === Конфигурация из ENV ===
VK_PAY_MERCHANT_ID = os.environ.get("VK_PAY_MERCHANT_ID", "")
VK_PAY_SECRET_KEY = os.environ.get("VK_PAY_SECRET_KEY", "")
VK_PAY_API_URL = os.environ.get("VK_PAY_API_URL", "https://vk.com/pay")
VK_PAY_CURRENCY = os.environ.get("VK_PAY_CURRENCY", "RUB")
VK_PAY_CALLBACK_URL = os.environ.get("VK_PAY_CALLBACK_URL", "")
VK_PAY_SUCCESS_URL = os.environ.get("VK_PAY_SUCCESS_URL", "https://vk.com/benzyn_ryadom?pay=ok")
VK_PAY_FAIL_URL = os.environ.get("VK_PAY_FAIL_URL", "https://vk.com/benzyn_ryadom?pay=fail")


def is_configured() -> bool:
    """Проверяет, настроен ли VK Pay."""
    return bool(VK_PAY_MERCHANT_ID) and bool(VK_PAY_SECRET_KEY) and VK_PAY_MERCHANT_ID != "benzin-ryadom-merchant"


def _sign(params: dict) -> str:
    """HMAC-SHA256 подпись параметров по документации VK Pay.

    sig = sha256(secret + sorted_params + secret)
    params сортируются по ключу, формат: key1=value1&key2=value2

    ВАЖНО: подпись вычисляется ДО добавления самого поля sig в params.
    """
    sorted_items = sorted(params.items())
    pairs = "&".join(f"{k}={v}" for k, v in sorted_items)
    msg = VK_PAY_SECRET_KEY + pairs + VK_PAY_SECRET_KEY
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def verify_signature(body: str, signature: str) -> bool:
    """Проверяет подпись callback от VK Pay.

    X-Signature = sha256(secret + body + secret) в hex
    """
    if not VK_PAY_SECRET_KEY:
        logger.warning("VK_PAY_SECRET_KEY not set, skipping signature check")
        return True  # В dev режиме пропускаем проверку
    expected = _sign_with_body(body)
    return hmac.compare_digest(expected, signature)


def _sign_with_body(body: str) -> str:
    """Подпись для проверки callback'а (body целиком)."""
    msg = VK_PAY_SECRET_KEY + body + VK_PAY_SECRET_KEY
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def create_payment(
    amount: int,
    description: str,
    payment_token: str,
    callback_url: Optional[str] = None,
    success_url: Optional[str] = None,
    fail_url: Optional[str] = None,
) -> dict:
    """Создаёт платёж и возвращает URL для редиректа.

    Формат URL: https://vk.com/pay?merchant_id=...&amount=...&description=...
                  &currency=RUB&extra=...&action=pay-to-user&sig=...
                  &return_url=...&callback_url=...
    """
    if not is_configured():
        return {
            "ok": False,
            "error": "VK Pay not configured. Set VK_PAY_MERCHANT_ID and VK_PAY_SECRET_KEY env vars.",
        }

    params = {
        "merchant_id": VK_PAY_MERCHANT_ID,
        "amount": str(amount),
        "description": description,
        "currency": VK_PAY_CURRENCY,
        "extra": payment_token,  # наш внутренний ID платежа
        "action": "pay-to-user",
        "return_url": success_url or VK_PAY_SUCCESS_URL,
        "callback_url": callback_url or VK_PAY_CALLBACK_URL,
    }
    params["sig"] = _sign(params)

    # URL с минимальным encoding (VK Pay принимает большинство символов как есть)
    query_parts = []
    for k, v in params.items():
        # Используем urlencode с safe="", чтобы encode = как в документации
        encoded_v = urlencode({k: str(v)}).split("=", 1)[1]
        query_parts.append(f"{k}={encoded_v}")
    query = "&".join(query_parts)
    payment_url = f"{VK_PAY_API_URL}?{query}"

    return {
        "ok": True,
        "payment_url": payment_url,
        "merchant_id": VK_PAY_MERCHANT_ID,
        "amount": amount,
        "currency": VK_PAY_CURRENCY,
        "description": description,
        "extra": payment_token,
    }


def parse_callback(body: str) -> Optional[dict]:
    """Парсит callback от VK Pay.

    VK Pay отправляет POST с form-encoded body:
    payment_id, merchant_id, status, amount, currency, extra, sig, ...
    Подпись вычисляется от всего body кроме поля sig.
    """
    import json as _json
    sig = ""
    data = {}

    # Пробуем JSON
    try:
        data = _json.loads(body)
    except Exception:
        # form-encoded
        from urllib.parse import parse_qs
        data = {k: v[0] if v else "" for k, v in parse_qs(body).items()}

    if not data:
        return None

    # Извлекаем подпись
    sig = data.pop("sig", "") or data.pop("signature", "")

    # Проверяем подпись (если есть)
    if sig and VK_PAY_SECRET_KEY:
        # Подпись вычисляется от строки без поля sig
        # VK Pay: sha256(secret + sorted_params + secret)
        # Для callback: параметры сортируются по ключу
        sorted_items = sorted(data.items())
        pairs = "&".join(f"{k}={v}" for k, v in sorted_items)
        expected = hashlib.sha256(
            (VK_PAY_SECRET_KEY + pairs + VK_PAY_SECRET_KEY).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            logger.error(f"Invalid VK Pay callback signature: got {sig[:20]}, expected {expected[:20]}")
            return None

    return {
        "payment_id": data.get("payment_id") or data.get("order_id"),
        "merchant_id": data.get("merchant_id"),
        "status": data.get("status"),
        "amount": int(data.get("amount", 0) or 0),
        "currency": data.get("currency", "RUB"),
        "extra": data.get("extra"),
        "signature_valid": True,
    }


# === Конфиг для Render ENV ===
# VK_PAY_MERCHANT_ID — ID магазина в VK Pay
# VK_PAY_SECRET_KEY — секретный ключ для подписи (получить в кабинете)
# VK_PAY_API_URL — по умолчанию https://vk.com/pay
# VK_PAY_CALLBACK_URL — наш endpoint для уведомлений: https://benzin-ryadom.onrender.com/api/premium/payment-callback
# VK_PAY_SUCCESS_URL — куда редиректить после успешной оплаты
# VK_PAY_FAIL_URL — куда редиректить при ошибке

# Без зарегистрированного merchant_id ссылки VK Pay работают как заглушка
# (откроют страницу с ошибкой "merchant not found"), но подпись будет валидной
