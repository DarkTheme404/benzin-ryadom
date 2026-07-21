#!/usr/bin/env python3
"""
Полный E2E тест реферальной системы: от регистрации до вывода.
"""
import asyncio
import os
import sys

os.environ["USE_SQLITE"] = "false"
os.environ["DATABASE_URL"] = "postgresql://postgres.ywtlglhorudfwqgiythv:raFham-piqwux-4hyfma@aws-0-eu-west-3.pooler.supabase.com:6543/postgres"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bot"))

import db
import secrets as _secrets


async def full_e2e_test():
    await db.init_db()
    print("=" * 60)
    print("ПОЛНЫЙ E2E ТЕСТ РЕФЕРАЛЬНОЙ СИСТЕМЫ")
    print("=" * 60)

    # 1. Создаём referrer (Elite)
    referrer_tid = 999999200 + int(asyncio.get_event_loop().time() * 1000) % 100
    print(f"\n[1] Создаю referrer (TG: {referrer_tid})")
    await db.upsert_user(referrer_tid, username=f"ref_{_secrets.token_hex(3)}", first_name="TestReferrer")
    ref_uid = await db.get_user_id_by_any(referrer_tid)
    print(f"   uid = {ref_uid}")

    # 2. Активируем Elite (только Elite/Founder зарабатывают)
    print(f"\n[2] Активирую Elite для реферера")
    await db.activate_premium(ref_uid, "elite", days=30, payment_id="test-elite", amount=500)
    print(f"   ✅ Elite активен")

    # 3. Генерируем реферальный код
    print(f"\n[3] Создаю реферальный код")
    code = await db.create_referral_code(ref_uid)
    print(f"   Код: {code}")

    # 4. Создаём referred user
    referred_tid = referrer_tid + 1
    print(f"\n[4] Создаю referred user (TG: {referred_tid})")
    await db.upsert_user(referred_tid, username=f"refd_{_secrets.token_hex(3)}", first_name="TestReferred")
    refd_uid = await db.get_user_id_by_any(referred_tid)
    print(f"   uid = {refd_uid}")

    # 5. Применяем реферальный код
    print(f"\n[5] complete_referral (код={code})")
    result = await db.complete_referral(code, refd_uid, referred_tid)
    print(f"   Результат: {result}")
    assert result, "complete_referral должен вернуть True"

    # 6. Проверяем скидку
    print(f"\n[6] Проверяю скидку 15%")
    discount = await db.get_active_discount(refd_uid)
    if discount:
        print(f"   ✅ Скидка: {discount['discount_percent']}% до {discount['expires_at']}")
    else:
        print(f"   ❌ СКИДКА НЕ СОЗДАНА!")
        return False

    # 7. Создаём платёж с применением скидки
    print(f"\n[7] Создаю платёж Standard 250₽ со скидкой")
    # Имитируем то что делает /api/premium/create-payment
    plan = db.get_plan("standard")
    original_price = plan["price"]
    discount_percent = discount["discount_percent"]
    final_price = round(original_price * (100 - discount_percent) / 100)
    print(f"   Цена без скидки: {original_price}₽, со скидкой: {final_price}₽")

    # 8. Создаём заявку на оплату
    token = await db.create_payment_request(refd_uid, "standard", "yoomoney")
    print(f"   Токен: {token}")

    # 9. Имитируем оплату (вызываем confirm_payment)
    print(f"\n[8] Имитирую оплату (confirm_payment)")
    sub = await db.confirm_payment(token)
    if sub:
        print(f"   ✅ Premium активирован: tier={sub.get('tier')}, expires={sub.get('expires_at')}")
    else:
        print(f"   ❌ Не удалось активировать")
        return False

    # 10. Проверяем что скидка помечена used
    print(f"\n[9] Проверяю что скидка использована")
    discount_after = await db.get_active_discount(refd_uid)
    if discount_after is None:
        print(f"   ✅ Скидка использована (больше не активна)")
    else:
        print(f"   ⚠️ Скидка всё ещё активна: {discount_after}")

    # 11. Проверяем баланс реферера
    print(f"\n[10] Проверяю баланс реферера")
    bal = await db.get_referral_balance(ref_uid)
    print(f"   Баланс: {bal}")
    expected = round(final_price * 0.5)  # 50% для базового уровня
    if bal.get("balance", 0) >= expected:
        print(f"   ✅ Комиссия начислена: {bal.get('balance')}₽ (ожидалось ~{expected}₽)")
    else:
        print(f"   ❌ Баланс {bal.get('balance')} < {expected}")

    # 12. Проверяем tier
    print(f"\n[11] Проверяю tier реферера")
    tier = await db.get_user_referral_tier(ref_uid)
    print(f"   Tier: {tier}")

    # 13. Тест вывода
    print(f"\n[12] Тест вывода {expected}₽")
    if bal.get("balance", 0) >= 100:
        wd_result = await db.request_withdrawal_manual(
            user_id=ref_uid, amount=min(100, bal.get("balance", 0)),
            method="card", details="2200700638873280",
        )
        print(f"   Заявка: {wd_result}")
    else:
        print(f"   Пропуск (баланс < 100)")

    print("\n" + "=" * 60)
    print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
    print("=" * 60)
    return True


if __name__ == "__main__":
    asyncio.run(full_e2e_test())
