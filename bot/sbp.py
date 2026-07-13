"""
СБП (Система Быстрых Платежей) — оплата через прямой перевод.

Используется как основной способ оплаты Premium подписки.
Без API — генерирует инструкцию для перевода по номеру телефона или QR-коду.
Админ проверяет перевод и активирует подписку вручную через /api/premium/activate.
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# === Настройки магазина ===
SBP_PHONE = os.environ.get("SBP_PHONE", "+79001234567")  # Номер телефона для переводов
SBP_BANK_NAME = os.environ.get("SBP_BANK_NAME", "Сбербанк")
SBP_RECIPIENT_NAME = os.environ.get("SBP_RECIPIENT_NAME", "ИП Иванов И.И.")  # ФИО получателя
SBP_INN = os.environ.get("SBP_INN", "")  # ИНН (опционально)
SBP_QR_URL = os.environ.get("SBP_QR_URL", "")  # URL QR-кода (опционально)


def is_configured() -> bool:
    """Проверяет, настроен ли СБП."""
    return bool(SBP_PHONE) and SBP_PHONE != "+79001234567"


def create_payment(
    amount: int,
    description: str,
    payment_token: str,
) -> dict:
    """Создаёт инструкцию для СБП-перевода.

    Возвращает:
    - phone: номер для перевода
    - amount: сумма
    - recipient: получатель
    - bank: банк
    - message: сообщение для комментария (для идентификации платежа)
    - instructions: текст инструкции для пользователя
    """
    # Сообщение в комментарии = payment_token (для идентификации)
    comment = f"benzin-{payment_token[:8]}"

    instructions = (
        f"💳 <b>Оплата через СБП</b>\n\n"
        f"💰 Сумма: <b>{amount}₽</b>\n"
        f"📱 Переведите на номер: <code>{SBP_PHONE}</code>\n"
        f"🏦 Банк: {SBP_BANK_NAME}\n"
        f"👤 Получатель: {SBP_RECIPIENT_NAME}\n"
        f"💬 В комментарии: <code>{comment}</code>\n\n"
        f"<b>Инструкция:</b>\n"
        f"1. Откройте приложение банка\n"
        f"2. Выберите «Перевод по номеру телефона» или «СБП»\n"
        f"3. Введите номер {SBP_PHONE}\n"
        f"4. Укажите сумму <b>{amount}₽</b>\n"
        f"5. В комментарии укажите <code>{comment}</code>\n"
        f"6. Подтвердите перевод\n\n"
        f"⏳ После перевода нажмите «Я оплатил» — мы проверим в течение 5 минут\n\n"
        f"⚠️ Без комментария платёж не будет засчитан"
    )

    return {
        "ok": True,
        "method": "sbp",
        "amount": amount,
        "currency": "RUB",
        "phone": SBP_PHONE,
        "bank": SBP_BANK_NAME,
        "recipient": SBP_RECIPIENT_NAME,
        "comment": comment,
        "payment_token": payment_token,
        "description": description,
        "instructions": instructions,
    }


def format_receipt(payment: dict) -> str:
    """Форматирует чек для админа (когда он проверяет перевод)."""
    return (
        f"💳 <b>СБП-платёж #{payment.get('id')}</b>\n"
        f"👤 User: {payment.get('user_id')}\n"
        f"💰 Сумма: {payment.get('amount')}₽\n"
        f"💬 Комментарий: <code>{payment.get('external_id', '')[:8]}</code>\n"
        f"📅 Создан: {payment.get('created_at')}\n"
    )
