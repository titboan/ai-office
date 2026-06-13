#!/usr/bin/env python3
"""
Одноразовый скрипт для получения Gmail OAuth2 refresh_token.
Запусти ОДИН раз локально: python tools/gmail_auth.py
Затем добавь GMAIL_REFRESH_TOKEN в Railway Variables.

Предварительно:
1. console.cloud.google.com → Create Project
2. APIs & Services → Enable → Gmail API
3. OAuth consent screen → External → Add test user (свой email)
4. Credentials → Create → OAuth 2.0 Client ID → Desktop app
5. Скачай JSON → возьми client_id и client_secret → введи ниже
6. В Authorized redirect URIs добавь: http://localhost:8080
"""
import http.server
import json
import threading
import urllib.parse
import urllib.request
import webbrowser

CLIENT_ID     = input("GMAIL_CLIENT_ID:     ").strip()
CLIENT_SECRET = input("GMAIL_CLIENT_SECRET: ").strip()

PORT         = 8080
REDIRECT_URI = f"http://localhost:{PORT}"
SCOPE        = "https://www.googleapis.com/auth/gmail.modify"

_auth_code: list[str] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code = params.get("code", [""])[0]
        if code:
            _auth_code.append(code)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h1>OK! Wernuysya v terminal.</h1>")

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("localhost", PORT), _Handler)
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()

auth_url = (
    "https://accounts.google.com/o/oauth2/auth"
    f"?client_id={urllib.parse.quote(CLIENT_ID)}"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
    "&response_type=code"
    f"&scope={urllib.parse.quote(SCOPE)}"
    "&access_type=offline"
    "&prompt=consent"
)

print(f"\nОткрываю браузер для авторизации Gmail...")
webbrowser.open(auth_url)
print("Авторизуйся в браузере и дождись страницы 'OK'...\n")

import time
while not _auth_code:
    time.sleep(0.3)

server.shutdown()
code = _auth_code[0]

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

with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read())

if "refresh_token" in data:
    print("✅ Успешно!\n")
    print(f"GMAIL_CLIENT_ID={CLIENT_ID}")
    print(f"GMAIL_CLIENT_SECRET={CLIENT_SECRET}")
    print(f"GMAIL_REFRESH_TOKEN={data['refresh_token']}")
    print("\nДобавь все три значения в Railway → Variables.")
else:
    print(f"❌ Ошибка: {data}")
