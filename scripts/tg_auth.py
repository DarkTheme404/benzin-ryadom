"""
Одноразовая авторизация в Telegram.
Создаёт файл tg_session.session, который используется всеми парсерами.

Использование:
  python scripts/tg_auth.py

Введи:
  1. Номер телефона (с + и кодом страны)
  2. Код из Telegram (придёт в Saved Messages)
  3. 2FA пароль (если включён)

После этого файл tg_session.session создаётся рядом.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bot"))

from telethon import TelegramClient


async def main():
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        print("❌ TG_API_ID и TG_API_HASH не заданы в env")
        print("export TG_API_ID='...'")
        print("export TG_API_HASH='...'")
        return 1

    session_path = "tg_session"
    print("=== Telegram авторизация ===")
    print(f"API ID: {api_id[:2]}***{api_id[-2:]}")
    print(f"API Hash: {api_hash[:4]}...{api_hash[-4:]}")
    print(f"Session: {session_path}.session")
    print()
    print("📱 Введи номер телефона в международном формате (+7...):")
    print()

    client = TelegramClient(session_path, int(api_id), api_hash)

    await client.connect()
    if not await client.is_user_authorized():
        phone = input("Phone (+7...): ").strip()
        sent = await client.send_code_request(phone)
        print()
        print(f"📩 Код отправлен в Telegram. Проверь Saved Messages.")
        code = input("Code (5 цифр): ").strip()
        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except Exception as e:
            if "Two-step verification" in str(e):
                password = input("2FA password: ").strip()
                await client.sign_in(password=password)
            else:
                raise

    me = await client.get_me()
    print()
    print(f"✅ Авторизован как: {me.first_name} (@{me.username}, id={me.id})")
    print(f"📁 Session сохранён в {session_path}.session")
    print()
    print("Теперь можно запускать парсер:")
    print("  python scripts/parse_tg_prices.py --all --limit 50")
    print()

    await client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
