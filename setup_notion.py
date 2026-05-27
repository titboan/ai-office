#!/usr/bin/env python3
"""
setup_notion.py — создаёт структуру баз данных Notion для ai-office.

Использование:
    python setup_notion.py

Требует в .env:
    NOTION_TOKEN           — Integration Token (api.notion.com → My integrations)
    NOTION_PARENT_PAGE_ID  — ID страницы, куда создаются базы данных

Как получить NOTION_PARENT_PAGE_ID:
    Открой страницу в Notion → Share → Copy link
    Пример ссылки: https://www.notion.so/My-Page-abc123def456...
    ID страницы — 32 символа в конце URL (без дефисов или с ними — оба варианта работают)

После выполнения скрипт выводит ID баз данных для вставки в .env.

Зависимости (однократно):
    pip install requests python-dotenv
"""

from __future__ import annotations

import os
import sys
import json
import time
from typing import Any

# ── Импорт зависимостей ────────────────────────────────────────────────────────
try:
    import requests
except ImportError:
    print("❌ Не установлен пакет 'requests'.")
    print("   Установи: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print("❌ Не установлен пакет 'python-dotenv'.")
    print("   Установи: pip install python-dotenv")
    sys.exit(1)

# ── Загружаем .env ─────────────────────────────────────────────────────────────
load_dotenv()

NOTION_TOKEN          = os.getenv("NOTION_TOKEN", "").strip()
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "").strip()
NOTION_VERSION        = "2022-06-28"
BASE_URL              = "https://api.notion.com/v1"

# Цвета для терминала (отключаем на Windows если нет поддержки ANSI)
_USE_COLOR = sys.platform != "win32" or os.getenv("TERM") is not None

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

OK   = _c("32", "✅")
ERR  = _c("31", "❌")
WARN = _c("33", "⚠️ ")
INFO = _c("36", "•")
SEP  = "─" * 62


# ── Notion API ─────────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type":  "application/json",
        "Notion-Version": NOTION_VERSION,
    }


def create_database(title: str, properties: dict[str, Any]) -> dict:
    """Создать базу данных в Notion под родительской страницей.

    Возвращает JSON-ответ или завершает процесс при ошибке.
    """
    payload = {
        "parent": {
            "type":    "page_id",
            "page_id": NOTION_PARENT_PAGE_ID,
        },
        "title": [
            {"type": "text", "text": {"content": title}}
        ],
        "properties": properties,
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/databases",
            headers=_headers(),
            json=payload,
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        print(f"\n{ERR} Нет соединения с api.notion.com. Проверь интернет.")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print(f"\n{ERR} Таймаут запроса к Notion API (>30 с).")
        sys.exit(1)

    if resp.status_code == 200:
        return resp.json()

    # ── Понятные сообщения об ошибках ──────────────────────────────────────────
    err = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    code    = err.get("code", "")
    message = err.get("message", resp.text)

    if resp.status_code == 401:
        print(f"\n{ERR} Ошибка авторизации: неверный NOTION_TOKEN.")
        print(f"   Получи токен: https://www.notion.so/my-integrations")
    elif resp.status_code == 404:
        print(f"\n{ERR} Страница не найдена: NOTION_PARENT_PAGE_ID неверный.")
        print(f"   Убедись что Integration добавлена к этой странице: Share → Connections")
    elif code == "validation_error":
        print(f"\n{ERR} Ошибка валидации при создании '{title}':")
        print(f"   {message}")
    else:
        print(f"\n{ERR} Ошибка {resp.status_code} при создании '{title}':")
        print(f"   {message}")

    sys.exit(1)


# ── Определения баз данных ─────────────────────────────────────────────────────

def _select(*options: tuple[str, str]) -> dict:
    """Вспомогательная функция: поле типа Select."""
    return {
        "select": {
            "options": [{"name": name, "color": color} for name, color in options]
        }
    }


def _multi_select(*options: tuple[str, str]) -> dict:
    """Вспомогательная функция: поле типа Multi-select."""
    return {
        "multi_select": {
            "options": [{"name": name, "color": color} for name, color in options]
        }
    }


PRIORITY_OPTIONS = [
    ("Высокий", "red"),
    ("Средний", "yellow"),
    ("Низкий",  "gray"),
]


def schema_projects() -> dict:
    return {
        "Name":        {"title": {}},
        "Status":      _select(
            ("В работе",  "blue"),
            ("Идея",      "yellow"),
            ("Завершён",  "green"),
        ),
        "Description": {"rich_text": {}},
        "Deadline":    {"date": {}},
    }


def schema_tasks(projects_id: str) -> dict:
    return {
        "Name":    {"title": {}},
        "Status":  _select(
            ("Сделать",    "red"),
            ("В процессе", "yellow"),
            ("Готово",     "green"),
        ),
        "Project": {
            "relation": {
                "database_id":   projects_id,
                "type":          "single_property",
                "single_property": {},
            }
        },
        "Deadline": {"date": {}},
        "Priority": _select(*PRIORITY_OPTIONS),
    }


def schema_ideas() -> dict:
    return {
        "Name":        {"title": {}},
        "Description": {"rich_text": {}},
        "Tags":        _multi_select(
            ("AI",          "purple"),
            ("Бизнес",      "blue"),
            ("Технологии",  "green"),
            ("Маркетинг",   "orange"),
            ("Продукт",     "pink"),
        ),
        "Priority":    _select(*PRIORITY_OPTIONS),
    }


def schema_research() -> dict:
    return {
        "Name":    {"title": {}},
        "Content": {"rich_text": {}},
        "Source":  {"rich_text": {}},
        "Agent":   _select(
            ("Каспер", "purple"),
            ("Кевин",  "blue"),
            ("Питер",  "green"),
            ("Элина",  "pink"),
            ("Алекс",  "orange"),
            ("Марта",  "gray"),
        ),
        "Date":    {"date": {}},
    }


def schema_content() -> dict:
    return {
        "Name": {"title": {}},
        "Text": {"rich_text": {}},
        "Type": _select(
            ("Статья", "blue"),
            ("Пост",   "green"),
            ("Письмо", "yellow"),
            ("Идея",   "purple"),
        ),
        "Date": {"date": {}},
    }


# ── Главная функция ────────────────────────────────────────────────────────────

def validate_env() -> None:
    errors: list[str] = []

    if not NOTION_TOKEN:
        errors.append(f"  {ERR} NOTION_TOKEN не задан в .env")
    elif not NOTION_TOKEN.startswith("secret_") and not NOTION_TOKEN.startswith("ntn_"):
        # Проверка формата (internal tokens начинаются с "secret_" или "ntn_")
        print(f"{WARN} NOTION_TOKEN выглядит нестандартно (обычно начинается с 'secret_' или 'ntn_')")

    if not NOTION_PARENT_PAGE_ID:
        errors.append(f"  {ERR} NOTION_PARENT_PAGE_ID не задан в .env")

    if errors:
        print("\n".join(errors))
        print(f"\n{INFO} Инструкция: https://www.notion.so/my-integrations")
        sys.exit(1)


def main() -> None:
    validate_env()

    print()
    print(_c("1", "  🗂️  Настройка баз данных Notion для ai-office"))
    print(SEP)
    print(f"  Token: ...{NOTION_TOKEN[-8:]}")
    print(f"  Parent page: {NOTION_PARENT_PAGE_ID}")
    print(SEP)
    print()
    print(f"{WARN} Если запустишь скрипт повторно — базы создадутся ещё раз (дубликаты).")
    print(f"   Для пересоздания удали старые базы в Notion вручную.\n")

    # Словарь для хранения результатов (сохраняем даже если упадём в середине)
    created: dict[str, str] = {}

    steps = [
        ("📁 Projects",  lambda: create_database("Projects",  schema_projects()),          "NOTION_PROJECTS_DB"),
        ("✅ Tasks",      lambda: None,  # создаётся после Projects
                                                                                           "NOTION_TASKS_DB"),
        ("💡 Ideas",     lambda: create_database("Ideas",     schema_ideas()),             "NOTION_IDEAS_DB"),
        ("🔍 Research",  lambda: create_database("Research",  schema_research()),          "NOTION_RESEARCH_DB"),
        ("✍️  Content",  lambda: create_database("Content",   schema_content()),           "NOTION_CONTENT_DB"),
    ]

    # ── 1. Projects ────────────────────────────────────────────────────────────
    label = "📁 Projects"
    print(f"  {label}...", end=" ", flush=True)
    projects_db = create_database("Projects", schema_projects())
    projects_id = projects_db["id"]
    created["NOTION_PROJECTS_DB"] = projects_id
    print(f"{OK}")

    # ── 2. Tasks (relation → Projects) ────────────────────────────────────────
    print(f"  ✅  Tasks...", end=" ", flush=True)
    tasks_db = create_database("Tasks", schema_tasks(projects_id))
    tasks_id = tasks_db["id"]
    created["NOTION_TASKS_DB"] = tasks_id
    print(f"{OK}")

    # ── 3. Ideas ──────────────────────────────────────────────────────────────
    print(f"  💡  Ideas...", end=" ", flush=True)
    ideas_db = create_database("Ideas", schema_ideas())
    ideas_id = ideas_db["id"]
    created["NOTION_IDEAS_DB"] = ideas_id
    print(f"{OK}")

    # ── 4. Research ───────────────────────────────────────────────────────────
    print(f"  🔍  Research...", end=" ", flush=True)
    research_db = create_database("Research", schema_research())
    research_id = research_db["id"]
    created["NOTION_RESEARCH_DB"] = research_id
    print(f"{OK}")

    # ── 5. Content ────────────────────────────────────────────────────────────
    print(f"  ✍️   Content...", end=" ", flush=True)
    content_db = create_database("Content", schema_content())
    content_id = content_db["id"]
    created["NOTION_CONTENT_DB"] = content_id
    print(f"{OK}")

    # ── Результат ─────────────────────────────────────────────────────────────
    print()
    print(SEP)
    print(_c("32;1", "  ✅ Все 5 баз данных созданы!"))
    print(SEP)
    print()
    print("  Добавь в .env:\n")

    env_lines = [
        f"NOTION_PROJECTS_DB={created['NOTION_PROJECTS_DB']}",
        f"NOTION_TASKS_DB={created['NOTION_TASKS_DB']}",
        f"NOTION_IDEAS_DB={created['NOTION_IDEAS_DB']}",
        f"NOTION_RESEARCH_DB={created['NOTION_RESEARCH_DB']}",
        f"NOTION_CONTENT_DB={created['NOTION_CONTENT_DB']}",
    ]

    for line in env_lines:
        print(f"  {_c('33', line)}")

    print()
    print(SEP)
    print()

    # Дополнительно сохраняем в файл notion_ids.txt рядом со скриптом
    ids_file = os.path.join(os.path.dirname(__file__), "notion_ids.txt")
    with open(ids_file, "w", encoding="utf-8") as f:
        f.write("# Notion Database IDs — сгенерировано setup_notion.py\n")
        f.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        for line in env_lines:
            f.write(line + "\n")
    print(f"  {INFO} ID также сохранены в файл: {_c('36', 'notion_ids.txt')}")
    print()


if __name__ == "__main__":
    main()
