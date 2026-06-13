#!/usr/bin/env python3
"""
Одноразовый скрипт для получения Gmail OAuth2 refresh_token.
Запусти один раз локально: python tools/gmail_auth.py
"""
import json
import sys
import urllib.parse
import urllib.request

print("=" * 60)
print("Gmail OAuth2 — получение refresh_token")
print("=" * 60)
print()
print("Нужны client_id и client_secret из Google Cloud Console:")
print("  console.cloud.google.com → Credentials → OAuth 2.0 Client IDs")
print()

CLIENT_ID     = input("GMAIL_CLIENT_ID:     ").strip()
CLIENT_SECRET = input("GMAIL_CLIENT_SECRET: ").strip()

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ CLIENT_ID и CLIENT_SECRET обязательны")
    sys.exit(1)

SCOPE        = "https://www.googleapis.com/auth/gmail.modify"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

auth_url = (
    "https://accounts.google.com/o/oauth2/auth"
    f"?client_id={urllib.parse.quote(CLIENT_ID)}"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
    "&response_type=code"
    f"&scope={urllib.parse.quote(SCOPE)}"
    "&access_type=offline"
    "&prompt=consent"
)

print()
print("Шаг 1: Открой эту ссылку в браузере:")
print()
print(auth_url)
print()
print("Шаг 2: Авторизуйся под своим Gmail-аккаунтом.")
print("Шаг 3: Google покажет страницу с кодом — скопируй его.")
print()

code = input("Вставь код от Google: ").strip()

if not code:
    print("❌ Код не введён")
    sys.exit(1)

print("\nОбмениваю код на токены...")

req = urllib.request.Request(
    "https://oauth2.googleapis.com/token",
    data=urllib.parse.urlencode({
        "code":          code,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code",
    }).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/x-www-form-urlencoded")

try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"❌ HTTP {e.code}: {body}")
    sys.exit(1)

if "refresh_token" not in data:
    print(f"❌ refresh_token не получен. Ответ Google: {data}")
    print()
    print("Попробуй: myaccount.google.com/permissions → найди приложение → Remove Access")
    print("Затем запусти скрипт снова.")
    sys.exit(1)

print()
print("✅ Успешно! Добавь в Railway Variables:")
print()
print(f"GMAIL_CLIENT_ID={CLIENT_ID}")
print(f"GMAIL_CLIENT_SECRET={CLIENT_SECRET}")
print(f"GMAIL_REFRESH_TOKEN={data['refresh_token']}")
print()
print("После добавления переменных — railway up → /email_digest 1d")
