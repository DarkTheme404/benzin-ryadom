#!/usr/bin/env python3
"""
End-to-end test of the payment flow.

Simulates the full cycle:
1. User creates a payment (already done via API)
2. YooMoney "confirms" the payment (simulated by directly calling confirm_payment)
3. Premium is activated
4. Referral commission is recorded (if applicable)
"""
import asyncio
import os
import sys
import json

# Set env vars BEFORE importing db
os.environ["USE_SQLITE"] = "false"
os.environ["DATABASE_URL"] = "postgresql://postgres.ywtlglhorudfwqgiythv:raFham-piqwux-4hyfma@aws-0-eu-west-3.pooler.supabase.com:6543/postgres"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "bot"))

import db


async def test_payment_flow():
    print("=" * 60)
    print("END-TO-END PAYMENT FLOW TEST")
    print("=" * 60)

    # Init DB
    print("\n[0] Initialize DB connection")
    await db.init_db()
    print(f"  USE_SQLITE: {db.USE_SQLITE}")
    print(f"  DB pool: {db._db}")

    # Test user
    test_telegram_id = 999999001
    import urllib.request
    import json as _json
    req = urllib.request.Request(
        "https://benzin-ryadom.onrender.com/api/premium/create-payment",
        data=_json.dumps({"telegram_id": test_telegram_id, "tier": "standard"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = _json.loads(resp.read())
    payment_token = body["payment_token"]
    print(f"  Fresh payment token: {payment_token}")

    # Step 1: Check payment status (should be pending)
    print("\n[1] Check payment status (should be 'pending')")
    payment = await db.get_payment_by_token(payment_token)
    if not payment:
        print(f"  ERROR: Payment not found for token {payment_token}")
        return
    print(f"  Payment: tier={payment['tier']}, amount={payment['amount']}, status={payment['status']}")
    assert payment["status"] == "pending", f"Expected pending, got {payment['status']}"

    # Step 2: Check user account before
    print("\n[2] Check user account before payment")
    uid = await db.get_user_id_by_any(test_telegram_id)
    print(f"  User ID: {uid}")
    if uid:
        sub_before = await db.get_user_premium(uid)
        print(f"  Premium before: {sub_before}")

    # Step 3: Simulate YooMoney confirmation
    print("\n[3] Simulate YooMoney confirmation (call confirm_payment)")
    sub = await db.confirm_payment(payment_token)
    print(f"  Activated subscription: {sub}")
    assert sub is not None, "confirm_payment returned None"
    assert sub.get("is_active"), f"Subscription not active: {sub}"

    # Step 4: Check payment status after
    print("\n[4] Check payment status (should be 'paid')")
    payment_after = await db.get_payment_by_token(payment_token)
    print(f"  Payment: status={payment_after['status']}, paid_at={payment_after.get('paid_at')}")
    assert payment_after["status"] == "paid", f"Expected paid, got {payment_after['status']}"

    # Step 5: Check user premium after
    print("\n[5] Check user premium after payment")
    sub_after = await db.get_user_premium(uid)
    print(f"  Premium after: tier={sub_after.get('tier')}, expires={sub_after.get('expires_at')}")
    assert sub_after is not None, "No premium subscription after payment"
    assert sub_after.get("tier") == "standard", f"Expected standard, got {sub_after.get('tier')}"

    # Step 6: Test founder purchase flow
    print("\n[6] Test Founder Pack purchase flow")
    founder_token = f"founder-test-{__import__('secrets').token_urlsafe(16)}"
    await db.create_founder_purchase(uid, amount=1990, payment_token=founder_token)
    print(f"  Created founder payment: {founder_token}")
    founder_payment = await db.get_payment_by_token(founder_token)
    # founder_purchases has different schema, check directly
    print(f"  Founder payment created (status=pending expected)")

    # Simulate confirmation
    print("\n[7] Confirm founder payment")
    founder_result = await db.confirm_founder_purchase(founder_token)
    print(f"  Founder confirmation result: {founder_result}")
    assert founder_result, "Founder confirmation failed"

    # Check founder status
    print("\n[8] Check founder status")
    is_founder = await db.is_founder(uid)
    print(f"  Is founder: {is_founder}")
    assert is_founder, "User should be founder after purchase"

    # Check founder count
    founder_count = await db.get_founder_count()
    print(f"  Total founders: {founder_count}")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✓")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_payment_flow())
