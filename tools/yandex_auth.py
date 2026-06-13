#!/usr/bin/env python3
"""
Одноразовый скрипт для получения Yandex OAuth2 refresh_token (для IMAP XOAUTH2).
Запусти один раз локально: python tools/yandex_auth.py

Предварительно:
1. oauth.yandex.ru → Создать приложение → Веб-сервисы
2. Права доступа: Яндекс Почта → "Чтение писем" (mail:imap_ro)
   Если нужна сортировка: + "Чтение и удаление писем" (mail:imap_full)
3. Callback URI: https://oauth.yandex.ru/verification_code
4. Скопируй ClientID и Client secret
"""
import json
import sys
import urllib.parse
import urllib.request

print("=" * 60)
print("Yandex OAuth2 — получение refresh_token для IMAP")
print("=" * 60)
print()

CLIENT_ID     = input("YANDEX_CLIENT_ID:     ").strip()
CLIENT_SECRET = input("YANDEX_CLIENT_SECRET: ").strip()

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ CLIENT_ID и CLIENT_SECRET обязательны")
    sys.exit(1)

auth_url = (
    "https://oauth.yandex.ru/authorize"
    f"?response_type=code"
    f"&client_id={urllib.parse.quote(CLIENT_ID)}"
    "&force_confirm=yes"
)

print()
print("Шаг 1: Открой эту ссылку в браузере:")
print()
print(auth_url)
print()
print("Шаг 2: Войди в Яндекс-аккаунт и разреши доступ.")
print("Шаг 3: Яндекс покажет код подтверждения — скопируй его.")
print()

code = input("Вставь код от Яндекса: ").strip()

if not code:
    print("❌ Код не введён")
    sys.exit(1)

print("\nОбмениваю код на токены...")

req = urllib.request.Request(
    "https://oauth.yandex.ru/token",
    data=urllib.parse.urlencode({
        "grant_type":    "authorization_code",
        "code":          code,
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
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
    print(f"❌ refresh_token не получен. Ответ Яндекса: {data}")
    sys.exit(1)

print()
print("✅ Успешно! Добавь в Railway Variables:")
print()
print(f"YANDEX_CLIENT_ID={CLIENT_ID}")
print(f"YANDEX_CLIENT_SECRET={CLIENT_SECRET}")
print(f"YANDEX_REFRESH_TOKEN={data['refresh_token']}")
print()
print("После добавления переменных — railway up → /email_digest 1d")
