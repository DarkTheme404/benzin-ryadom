#!/usr/bin/env python3
"""
Тест автовывода на карту через YooMoney.
Использует реальный API (если YOOMONEY_TOKEN настроен).
"""
import asyncio
import os
import sys

os.environ["USE_SQLITE"] = "false"
os.environ["DATABASE_URL"] = "postgresql://postgres.ywtlglhorudfwqgiythv:raFham-piqwux-4hyfma@aws-0-eu-west-3.pooler.supabase.com:6543/postgres"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bot"))

import db
import yoomoney_pay


async def test_payout():
    print("=" * 60)
    print("ТЕСТ АВТОВЫВОДА")
    print("=" * 60)

    print(f"\n[0] YooMoney настроен: {yoomoney_pay.is_configured()}")
    print(f"    Token: {yoomoney_pay.YOOMONEY_TOKEN[:20]}...")
    print(f"    Receiver: {yoomoney_pay.YOOMONEY_RECEIVER}")

    # Проверим баланс
    print("\n[1] Баланс до вывода (uid=1, Артём)")
    bal_before = await db.get_referral_balance(1)
    print(f"    earned={bal_before['total_earned']} balance={bal_before['balance']} withdrawn={bal_before['total_withdrawn']}")

    # Чтобы был баланс — начислим себе тестовую комиссию
    if bal_before['balance'] == 0:
        print("\n[1.5] Баланс 0, начисляю себе тестовую комиссию 500₽")
        if not db.USE_SQLITE:
            async with db._db.acquire() as conn:
                await conn.execute(
                    "UPDATE referral_balances SET balance = balance + 500, total_earned = total_earned + 500 WHERE user_id = 1"
                )

    # Тестовый номер карты (ЮMoney test card для приёма платежей, выплаты могут не работать на тесте)
    TEST_CARD = "2200700638873280"  # ЮMoney тестовая карта (НЕ реальная выплата!)

    print(f"\n[2] Запрос автовывода 100₽ на карту {TEST_CARD[-4:]}")
    print("    (это РЕАЛЬНЫЙ вызов YooMoney API)")
    result = await db.request_withdrawal(
        user_id=1,
        amount=100,
        method="card",
        details=TEST_CARD,
    )
    print(f"\n[3] Результат:")
    for k, v in result.items():
        print(f"    {k}: {v}")

    # Баланс после
    print("\n[4] Баланс после")
    bal_after = await db.get_referral_balance(1)
    print(f"    earned={bal_after['total_earned']} balance={bal_after['balance']} withdrawn={bal_after['total_withdrawn']}")


if __name__ == "__main__":
    asyncio.run(test_payout())
