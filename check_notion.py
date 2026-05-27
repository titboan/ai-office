#!/usr/bin/env python3
"""
check_notion.py — диагностика Notion интеграции для ai-office.

Использование:
    python check_notion.py

Что проверяет:
    1. NOTION_TOKEN  — GET /v1/users/me (валидность токена, имя бота)
    2. Каждая из 5 баз данных — GET /v1/databases/{id}
       (доступ, название базы в Notion, совпадение имени)

Итог:
    Выводит сводку: сколько проверок прошло, что нужно исправить.

Зависимости: requests, python-dotenv (уже в requirements.txt)
"""

from __future__ import annotations

import os
import sys
from typing import Any

try:
    import requests
except ImportError:
    print("❌ Установи requests:  pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print("❌ Установи python-dotenv:  pip install python-dotenv")
    sys.exit(1)

load_dotenv()

# ── Принудительно UTF-8 на Windows (иначе cp1251 не переваривает эмодзи) ──────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Конфигурация ───────────────────────────────────────────────────────────────
NOTION_VERSION = "2022-06-28"
BASE_URL       = "https://api.notion.com/v1"
TIMEOUT        = 10   # секунд

TOKEN = os.getenv("NOTION_TOKEN", "").strip()

# Базы данных: (env-переменная, ожидаемое название в Notion)
DATABASES: list[tuple[str, str]] = [
    ("NOTION_PROJECTS_DB", "Projects"),
    ("NOTION_TASKS_DB",    "Tasks"),
    ("NOTION_IDEAS_DB",    "Ideas"),
    ("NOTION_RESEARCH_DB", "Research"),
    ("NOTION_CONTENT_DB",  "Content"),
]

# ── Цвета (ANSI) ───────────────────────────────────────────────────────────────
_COLOR = sys.platform != "win32" or os.getenv("TERM") is not None

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text

OK   = _c("32", "✅")
FAIL = _c("31", "❌")
WARN = _c("33", "⚠️ ")
DIM  = lambda t: _c("2", t)
BOLD = lambda t: _c("1", t)
SEP  = "-" * 62


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _get(path: str) -> tuple[int, dict]:
    """GET {BASE_URL}/{path}. Возвращает (status_code, json_body)."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Notion-Version": NOTION_VERSION,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        try:
            data = resp.json()
        except Exception:
            data = {"message": resp.text[:200]}
        return resp.status_code, data
    except requests.exceptions.ConnectionError:
        return -1, {"message": "Нет соединения с api.notion.com"}
    except requests.exceptions.Timeout:
        return -2, {"message": f"Таймаут запроса (>{TIMEOUT} с)"}
    except Exception as e:
        return -3, {"message": str(e)}


def _db_title(data: dict) -> str:
    """Извлечь название базы из ответа GET /databases/{id}."""
    try:
        title_parts = data.get("title", [])
        return "".join(p.get("text", {}).get("content", "") for p in title_parts) or "?"
    except Exception:
        return "?"


def _notion_error(status: int, data: dict) -> str:
    """Человекочитаемое объяснение ошибки Notion."""
    code    = data.get("code", "")
    message = data.get("message", "")

    if status == -1:
        return f"Нет соединения с api.notion.com"
    if status == -2:
        return f"Таймаут запроса (>{TIMEOUT} с) — проверь интернет"
    if status < 0:
        return f"Ошибка запроса: {message}"
    if status == 400:
        return f"400 Bad Request — неверный формат ID базы данных"
    if status == 401:
        return (
            f"401 Unauthorized — токен недействителен или истёк.\n"
            f"       Обнови токен: notion.so/my-integrations"
        )
    if status == 403:
        return (
            f"403 Forbidden — Integration не добавлена к этой базе.\n"
            f"       Открой базу в Notion → ••• → Connections → добавь интеграцию"
        )
    if status == 404:
        return (
            f"404 Not Found — база не существует или ID неверный.\n"
            f"       Запусти setup_notion.py чтобы пересоздать базы"
        )
    if status == 429:
        return f"429 Rate Limit — слишком много запросов, подожди немного"
    return f"HTTP {status}: {message or code}"


# ── Проверки ───────────────────────────────────────────────────────────────────

def check_token() -> bool:
    """Проверить NOTION_TOKEN через GET /v1/users/me."""
    print(f"\n{BOLD('[1/6]')} NOTION_TOKEN")

    if not TOKEN:
        print(f"  Значение : {FAIL} ПУСТО — переменная не задана в .env")
        print(f"  Подсказка: получи токен на notion.so/my-integrations")
        return False

    # Маскируем токен
    masked = TOKEN[:10] + "…" + TOKEN[-4:] if len(TOKEN) > 14 else TOKEN[:6] + "…"
    print(f"  Значение : {masked} (len={len(TOKEN)})")

    # Проверяем формат токена
    if not TOKEN.startswith(("secret_", "ntn_")):
        print(f"  {WARN} Токен не начинается с 'secret_' или 'ntn_' — возможно неверный формат")

    print(f"  Запрос   : GET /v1/users/me …", end=" ", flush=True)
    status, data = _get("/users/me")

    if status == 200:
        name     = data.get("name", "?")
        bot_type = data.get("type", "?")
        owner    = data.get("bot", {}).get("owner", {}).get("type", "?")
        print(f"{OK}")
        print(f"  {OK} Токен валидный")
        print(f"  Бот      : «{name}» | тип: {bot_type} | workspace: {owner}")
        return True
    else:
        print(f"{FAIL}")
        print(f"  {FAIL} Токен недействителен")
        print(f"  Причина  : {_notion_error(status, data)}")
        return False


def check_database(env_var: str, expected_name: str, index: int, total: int) -> bool:
    """Проверить одну базу данных через GET /v1/databases/{id}."""
    db_id = os.getenv(env_var, "").strip()

    print(f"\n{BOLD(f'[{index}/{total}]')} {env_var}")

    # ── Переменная не задана ───────────────────────────────────────────────────
    if not db_id:
        print(f"  Значение : {FAIL} ПУСТО — переменная не задана в .env")
        print(f"  Подсказка: запусти setup_notion.py и скопируй выведенные ID")
        return False

    # ── Проверяем длину (UUID должен быть 32–36 символов) ─────────────────────
    clean_id = db_id.replace("-", "")
    print(f"  Значение : {db_id[:8]}…{db_id[-4:]} (len={len(db_id)}, clean={len(clean_id)})")

    if len(clean_id) != 32:
        print(f"  {FAIL} Неверный формат ID — должно быть 32 hex-символа, получено {len(clean_id)}")
        return False

    # ── GET запрос ─────────────────────────────────────────────────────────────
    print(f"  Запрос   : GET /v1/databases/{db_id[:8]}… …", end=" ", flush=True)
    status, data = _get(f"/databases/{db_id}")

    if status != 200:
        print(f"{FAIL}")
        print(f"  {FAIL} Нет доступа к базе «{expected_name}»")
        print(f"  Причина  : {_notion_error(status, data)}")
        return False

    # ── Успех — проверяем название ─────────────────────────────────────────────
    print(f"{OK}")
    actual_name = _db_title(data)
    obj_type    = data.get("object", "?")
    archived    = data.get("archived", False)
    in_trash    = data.get("in_trash", False)

    if archived or in_trash:
        print(f"  {WARN} База в корзине (archived={archived}, in_trash={in_trash})")
        print(f"  Восстанови базу в Notion или запусти setup_notion.py заново")
        return False

    name_ok = actual_name.strip().lower() == expected_name.strip().lower()
    name_icon = OK if name_ok else WARN

    print(f"  {OK} Доступ есть")
    print(f"  Название : {name_icon} «{actual_name}»"
          + ("" if name_ok else f" {DIM(f'(ожидалось «{expected_name}»)')}"))
    print(f"  Тип      : {obj_type}")
    return True


# ── Главная функция ────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print(SEP)
    print(f"  {BOLD('check_notion.py')} — диагностика Notion для ai-office")
    print(SEP)

    results: dict[str, bool] = {}

    # 1. Токен
    token_ok = check_token()
    results["NOTION_TOKEN"] = token_ok

    # 2–6. Базы данных (можно проверять даже если токен невалидный —
    #       увидим точные ошибки по каждой базе)
    total = 1 + len(DATABASES)   # токен + 5 баз
    for i, (env_var, expected_name) in enumerate(DATABASES, start=2):
        ok = check_database(env_var, expected_name, i, total)
        results[env_var] = ok

    # ── Сводка ────────────────────────────────────────────────────────────────
    passed = sum(results.values())
    total_checks = len(results)
    all_ok = passed == total_checks

    print()
    print(SEP)
    if all_ok:
        print(f"  {OK} {BOLD('Всё настроено верно!')} {passed}/{total_checks} проверок прошли")
    else:
        failed = total_checks - passed
        print(f"  {BOLD('Итог:')} {passed}/{total_checks} прошли | "
              f"{_c('31', str(failed) + ' с ошибкой')}")
        print()
        print(f"  Что нужно исправить:")
        for name, ok in results.items():
            icon = OK if ok else FAIL
            print(f"    {icon} {name}")
    print(SEP)
    print()

    # Код возврата для использования в CI/скриптах
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
