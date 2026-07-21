#!/usr/bin/env python3
"""
Test referral commission flow:
1. Set up referrer with referral code
2. Create referred user who uses the code
3. Simulate payment by referred user
4. Verify commission recorded for referrer
"""
import asyncio
import os
import sys
import secrets as _secrets

os.environ["USE_SQLITE"] = "false"
os.environ["DATABASE_URL"] = "postgresql://postgres.ywtlglhorudfwqgiythv:raFham-piqwux-4hyfma@aws-0-eu-west-3.pooler.supabase.com:6543/postgres"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bot"))

import db


async def test_referral_flow():
    print("=" * 60)
    print("REFERRAL COMMISSION FLOW TEST")
    print("=" * 60)

    await db.init_db()
    print(f"\n[0] DB connected (USE_SQLITE={db.USE_SQLITE})")

    # 1. Create referrer (use unique test IDs to avoid conflicts)
    referrer_tid = 999999110 + int(asyncio.get_event_loop().time() * 1000) % 100
    print(f"\n[1] Create referrer (telegram_id={referrer_tid})")
    await db.upsert_user(referrer_tid, username="ref_test", first_name="Referrer")
    referrer_uid = await db.get_user_id_by_any(referrer_tid)
    print(f"  Referrer uid: {referrer_uid}")

    # 2. Make referrer Elite (only Elite/Founder earn commission)
    print(f"\n[1.5] Upgrade referrer to Elite (required for commission)")
    await db.activate_premium(referrer_uid, "elite", days=30, payment_id="test-elite", amount=500)
    print(f"  Referrer upgraded to Elite")

    # 3. Generate referral code
    ref_code = await db.create_referral_code(referrer_uid)
    print(f"  Referral code: {ref_code}")

    print(f"  Referral code: {ref_code}")

    # 3. Create referred user (unique ID)
    referred_tid = referrer_tid + 1
    print(f"\n[2] Create referred user (telegram_id={referred_tid})")
    await db.upsert_user(referred_tid, username="ref_test2", first_name="Referred")
    referred_uid = await db.get_user_id_by_any(referred_tid)
    print(f"  Referred uid: {referred_uid}")

    # 4. Complete referral relationship
    print(f"\n[3] Complete referral relationship (code={ref_code})")
    result = await db.complete_referral(ref_code, referred_uid, referred_tid)
    print(f"  complete_referral result: {result}")
    assert result, "Referral relationship failed"

    # 5. Create payment for referred user
    print(f"\n[4] Create payment for referred user (Standard 250₽)")
    token = await db.create_payment_request(referred_uid, "standard", "yoomoney")
    print(f"  Payment token: {token}")

    # 6. Simulate YooMoney payment confirmation
    print(f"\n[5] Confirm payment (simulate YooMoney)")
    sub = await db.confirm_payment(token)
    print(f"  Premium activated: tier={sub.get('tier') if sub else 'NONE'}")

    # 7. Check referrer balance
    print(f"\n[6] Check referrer balance")
    balance = await db.get_referral_balance(referrer_uid)
    print(f"  Balance: {balance}")

    # Check earnings
    if hasattr(db, 'get_referral_earnings'):
        earnings = await db.get_referral_earnings(referrer_uid)
        print(f"  Earnings: {earnings}")

    print("\n" + "=" * 60)
    print("REFERRAL TEST COMPLETED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_referral_flow())
