"""
Универсальный OAuth-клиент для YooMoney.
Пробует все варианты получения токена: token/code flow, с/без секрета.
"""
import sys
import re
import subprocess
import json
from pathlib import Path
from urllib.parse import urlencode, parse_qs, urlparse
import urllib.request
import urllib.parse

CLIENT_ID = "CE4A381C880CD833C0F3182F473BBADD2067427EEBF459C292C61DB43E884A50"
REDIRECT_URI = "https://benzin-ryadom.onrender.com/api/yoomoney/callback"
SCOPES = "account-info operation-history operation-details payment-p2p"
ENV_PATH = Path(__file__).parent.parent / "bot" / ".env"


def try_token_exchange(code: str, client_secret: str = "") -> str | None:
    """Обменивает authorization code на access token через POST /oauth/token."""
    data = {
        "code": code,
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }
    if client_secret:
        data["client_secret"] = client_secret

    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        "https://yoomoney.ru/oauth/token",
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
            if "access_token" in result:
                return result["access_token"]
            print(f"  Ошибка обмена: {result}")
            return None
    except Exception as e:
        print(f"  Ошибка запроса: {e}")
        return None


def main():
    print("=" * 70)
    print("YooMoney OAuth Token Getter — Universal")
    print("=" * 70)

    print("\nГенерирую 4 варианта URL-ов авторизации:\n")

    variants = [
        ("1. response_type=token, все скоупы, наш redirect",
         f"https://yoomoney.ru/oauth/authorize?{urlencode({'client_id': CLIENT_ID, 'response_type': 'token', 'scope': SCOPES, 'redirect_uri': REDIRECT_URI})}"),
        ("2. response_type=token, без скоупов",
         f"https://yoomoney.ru/oauth/authorize?{urlencode({'client_id': CLIENT_ID, 'response_type': 'token'})}"),
        ("3. response_type=code, все скоупы, наш redirect (для обмена)",
         f"https://yoomoney.ru/oauth/authorize?{urlencode({'client_id': CLIENT_ID, 'response_type': 'code', 'scope': SCOPES, 'redirect_uri': REDIRECT_URI})}"),
        ("4. response_type=token, все скоупы, redirect=example.com",
         f"https://yoomoney.ru/oauth/authorize?{urlencode({'client_id': CLIENT_ID, 'response_type': 'token', 'scope': SCOPES, 'redirect_uri': 'https://example.com'})}"),
    ]

    for name, url in variants:
        print(f"{name}:")
        print(f"  {url}\n")

    # Пытаемся открыть вариант 1
    first_url = variants[0][1]
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", first_url])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", first_url])
        elif sys.platform == "win32":
            subprocess.Popen(["start", first_url], shell=True)
        print("(Вариант 1 открыт в браузере автоматически)\n")
    except Exception:
        pass

    print("=" * 70)
    print("Что делать:")
    print("1. Залогинься в YooMoney (если ещё не)")
    print("2. Нажми 'Разрешить'")
    print("3. После редиректа — посмотри URL в адресной строке браузера")
    print("4. Если видишь access_token=... — скопируй и вставь ниже")
    print("5. Если видишь ?code=... — скопируй весь URL")
    print("=" * 70)
    print()

    inp = input("Вставь сюда токен или URL: ").strip()
    inp = inp.strip().strip("'").strip('"')

    token = None
    code = None

    if "access_token=" in inp:
        m = re.search(r"access_token=([A-Za-z0-9_.]+)", inp)
        if m:
            token = m.group(1)
    elif "code=" in inp or inp.startswith("http"):
        # Ищем code в URL
        try:
            parsed = urlparse(inp)
            params = parse_qs(parsed.query)
            if "code" in params:
                code = params["code"][0]
        except Exception:
            pass
        if not code:
            m = re.search(r"code=([A-Za-z0-9_]+)", inp)
            if m:
                code = m.group(1)

    if token and len(token) >= 20:
        print(f"\n✅ Токен получен: {token[:30]}...")
    elif code and len(code) >= 20:
        print(f"\nПолучен authorization code: {code[:30]}...")
        print("Обмениваю на access_token через POST /oauth/token...")
        client_secret = input("Если есть client_secret — введи (Enter = без секрета): ").strip()
        token = try_token_exchange(code, client_secret)
        if token:
            print(f"✅ Токен получен: {token[:30]}...")
    else:
        print(f"❌ Не удалось распознать токен или code: '{inp[:80]}'")
        sys.exit(1)

    if not token or len(token) < 20:
        print("❌ Токен не получен")
        sys.exit(1)

    # Спрашиваем кошелёк
    receiver = input("\nВведи номер своего кошелька YooMoney (например, 4100111223355444): ").strip()

    # Сохраняем
    if not ENV_PATH.exists():
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    content = re.sub(r"^YOOMONEY_TOKEN=.*\n?", "", content, flags=re.MULTILINE)
    content = re.sub(r"^YOOMONEY_RECEIVER=.*\n?", "", content, flags=re.MULTILINE)
    content = content.rstrip() + f"\n\nYOOMONEY_TOKEN={token}\n"
    if receiver:
        content += f"YOOMONEY_RECEIVER={receiver}\n"
    ENV_PATH.write_text(content)

    print(f"\n{'=' * 70}")
    print("🎉 ГОТОВО!")
    print("=" * 70)
    print(f"\nYOOMONEY_TOKEN={token}")
    if receiver:
        print(f"YOOMONEY_RECEIVER={receiver}")
    print(f"\nСохранено в {ENV_PATH}")
    print("\nДальше: добавь в Render Environment и сделай Clear build cache & deploy")


if __name__ == "__main__":
    main()
