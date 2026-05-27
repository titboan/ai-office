"""
tools/notion.py — Notion API integration for ai-office agents.

Предоставляет async-функции для записи структурированных данных в базы Notion.
Все функции — silent-fail: если токен или ID базы не заданы, возвращают None
без исключений. Агенты продолжают работать без Notion.

Зависимость: aiohttp (уже в requirements.txt)
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from typing import Any

import aiohttp
from loguru import logger

from config import config

_BASE_URL    = "https://api.notion.com/v1"
_API_VERSION = "2022-06-28"
_BLOCK_SIZE  = 1990   # Notion limit per rich_text block = 2000, берём с запасом
_MAX_CONTENT = 9900   # 5 блоков × 1990 = максимум для rich_text поля


# ── Env-var accessors (читаем КАЖДЫЙ раз из os.environ, не из замороженного config) ──
#
# ПОЧЕМУ os.getenv(), а не config.NOTION_TOKEN:
#   config.py читает переменные ОДИН РАЗ при импорте модуля (class-level os.getenv).
#   Если Railway выставляет переменные уже после старта процесса или при переменной
#   с пробелом/опечаткой — config.NOTION_TOKEN будет пустой строкой навсегда.
#   Прямой os.getenv() читает из os.environ каждый вызов и видит актуальное значение.

def _tok() -> str:
    return os.getenv("NOTION_TOKEN", "").strip()

def _db(env_var: str) -> str:
    return os.getenv(env_var, "").strip()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_tok()}",
        "Content-Type":  "application/json",
        "Notion-Version": _API_VERSION,
    }


def _text_blocks(text: str, max_total: int = _MAX_CONTENT) -> list[dict]:
    """Разбить текст на блоки для rich_text / title (лимит 2000 символов на блок)."""
    text = (text or "").strip()[:max_total]
    if not text:
        return [{"type": "text", "text": {"content": ""}}]
    return [
        {"type": "text", "text": {"content": text[i : i + _BLOCK_SIZE]}}
        for i in range(0, len(text), _BLOCK_SIZE)
    ]


def _today() -> str:
    return date.today().isoformat()


def page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


# ── Core HTTP ──────────────────────────────────────────────────────────────────

async def _create_page(database_id: str, properties: dict[str, Any]) -> dict | None:
    """POST /v1/pages — создать запись в базе данных Notion.

    Возвращает JSON-ответ или None при ошибке. Никогда не поднимает исключений.
    """
    # ── Guard: конфигурация ────────────────────────────────────────────────────
    tok = _tok()
    if not tok:
        logger.warning("[notion] _create_page: NOTION_TOKEN пустой — пропускаем")
        return None
    if not database_id:
        logger.warning("[notion] _create_page: database_id пустой — пропускаем")
        return None

    logger.debug(
        f"[notion] POST /pages | db={database_id[:8]}… | "
        f"token={tok[:8]}… (len={len(tok)}) | "
        f"props={list(properties.keys())}"
    )

    payload = {
        "parent":     {"database_id": database_id},
        "properties": properties,
    }

    # ── HTTP запрос ────────────────────────────────────────────────────────────
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/pages",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                status = resp.status
                raw    = await resp.text()                # читаем сырой текст всегда

                logger.debug(f"[notion] Ответ: HTTP {status} | body_len={len(raw)}")

                if status == 200:
                    data = json.loads(raw)
                    logger.debug(f"[notion] Страница создана: id={data.get('id', '?')}")
                    return data

                # ── Ошибка от Notion — логируем полный body ────────────────────
                try:
                    err = json.loads(raw)
                    code    = err.get("code", "?")
                    message = err.get("message", raw[:300])
                except Exception:
                    code, message = "?", raw[:300]

                logger.error(
                    f"[notion] HTTP {status} для db={database_id[:8]}… | "
                    f"code={code!r} | message={message!r}"
                )
                return None

    # ── Сетевые ошибки ────────────────────────────────────────────────────────
    except aiohttp.ClientConnectorError as e:
        logger.error(f"[notion] Нет соединения с api.notion.com: {e}")
        return None
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
        logger.error(f"[notion] Таймаут запроса к Notion API (>20 с): {e}")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"[notion] aiohttp.ClientError: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        logger.error(f"[notion] Неожиданная ошибка: {type(e).__name__}: {e}")
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

async def save_research(
    title: str,
    content: str,
    source: str = "",
    agent: str = "Каспер",
) -> str | None:
    """Сохранить результат исследования в NOTION_RESEARCH_DB."""

    # ── Диагностический лог — видим ровно то, что читается в момент вызова ──────
    tok_env    = os.getenv("NOTION_TOKEN", "")            # прямо из os.environ
    db_env     = os.getenv("NOTION_RESEARCH_DB", "")      # прямо из os.environ
    tok_config = config.NOTION_TOKEN                       # через config (import-time)
    db_config  = config.NOTION_RESEARCH_DB                 # через config (import-time)

    logger.info(
        f"[notion] save_research | title={title[:60]!r} | "
        f"content_len={len(content)} | agent={agent!r}"
    )
    logger.info(
        f"[notion] NOTION_TOKEN     present: os.getenv={bool(tok_env)} | "
        f"config={bool(tok_config)} | "
        f"match={tok_env == tok_config}"
    )
    logger.info(
        f"[notion] NOTION_RESEARCH_DB: os.getenv={db_env[:8] + '…' if db_env else 'ПУСТО'!r} | "
        f"config={db_config[:8] + '…' if db_config else 'ПУСТО'!r} | "
        f"match={db_env == db_config}"
    )

    # Используем os.getenv() — актуальное значение, не замороженное
    db = db_env
    if not db:
        logger.warning(
            "[notion] save_research: NOTION_RESEARCH_DB не задан.\n"
            "  Проверь: Railway → Variables → NOTION_RESEARCH_DB\n"
            "  Имя переменной должно быть ровно: NOTION_RESEARCH_DB (без пробелов)"
        )
        return None
    if not _tok():
        logger.warning(
            "[notion] save_research: NOTION_TOKEN не задан.\n"
            "  Проверь: Railway → Variables → NOTION_TOKEN"
        )
        return None

    props: dict[str, Any] = {
        "Name":    {"title":     _text_blocks(title[:200], max_total=200)},
        "Content": {"rich_text": _text_blocks(content)},
        "Source":  {"rich_text": _text_blocks(source[:500], max_total=500)},
        "Agent":   {"select":    {"name": agent}},
        "Date":    {"date":      {"start": _today()}},
    }
    logger.debug(f"[notion] Content blocks count: {len(props['Content']['rich_text'])}")

    result = await _create_page(db, props)
    if result:
        url = page_url(result["id"])
        logger.info(f"[notion] ✅ Research сохранён ({len(content)} симв.): {url}")
        return url

    logger.warning("[notion] save_research: _create_page вернул None")
    return None


async def save_content(
    title: str,
    text: str,
    content_type: str = "Статья",
) -> str | None:
    """Сохранить текстовый контент в NOTION_CONTENT_DB."""
    db = _db("NOTION_CONTENT_DB")
    logger.info(
        f"[notion] save_content | title={title[:60]!r} | "
        f"type={content_type!r} | text_len={len(text)} | "
        f"db={db[:8] + '…' if db else 'НЕ ЗАДАН'}"
    )
    if not db:
        logger.warning("[notion] save_content: NOTION_CONTENT_DB не задан — пропускаем")
        return None

    props: dict[str, Any] = {
        "Name": {"title":     _text_blocks(title[:200], max_total=200)},
        "Text": {"rich_text": _text_blocks(text)},
        "Type": {"select":    {"name": content_type}},
        "Date": {"date":      {"start": _today()}},
    }

    result = await _create_page(db, props)
    if result:
        url = page_url(result["id"])
        logger.info(f"[notion] ✅ Content сохранён ({content_type}): {url}")
        return url

    logger.warning("[notion] save_content: _create_page вернул None")
    return None


async def create_task(
    name: str,
    deadline: str | None = None,
    priority: str = "Средний",
) -> str | None:
    """Создать задачу в NOTION_TASKS_DB со статусом 'Сделать'."""
    db = _db("NOTION_TASKS_DB")
    logger.info(
        f"[notion] create_task | name={name[:60]!r} | "
        f"priority={priority!r} | deadline={deadline!r} | "
        f"db={db[:8] + '…' if db else 'НЕ ЗАДАН'}"
    )
    if not db:
        logger.warning("[notion] create_task: NOTION_TASKS_DB не задан — пропускаем")
        return None

    props: dict[str, Any] = {
        "Name":     {"title":  _text_blocks(name[:200], max_total=200)},
        "Status":   {"select": {"name": "Сделать"}},
        "Priority": {"select": {"name": priority}},
    }
    if deadline:
        props["Deadline"] = {"date": {"start": deadline}}

    result = await _create_page(db, props)
    if result:
        url = page_url(result["id"])
        logger.info(f"[notion] ✅ Task создан ({priority}): {url}")
        return url

    logger.warning("[notion] create_task: _create_page вернул None")
    return None


async def save_idea(
    name: str,
    description: str = "",
    tags: list[str] | None = None,
    priority: str = "Средний",
) -> str | None:
    """Сохранить идею в NOTION_IDEAS_DB."""
    db = _db("NOTION_IDEAS_DB")
    logger.info(
        f"[notion] save_idea | name={name[:60]!r} | "
        f"priority={priority!r} | tags={tags} | "
        f"db={db[:8] + '…' if db else 'НЕ ЗАДАН'}"
    )
    if not db:
        logger.warning("[notion] save_idea: NOTION_IDEAS_DB не задан — пропускаем")
        return None

    props: dict[str, Any] = {
        "Name":        {"title":     _text_blocks(name[:200], max_total=200)},
        "Description": {"rich_text": _text_blocks(description)},
        "Priority":    {"select":    {"name": priority}},
    }
    if tags:
        props["Tags"] = {
            "multi_select": [{"name": t[:100]} for t in tags[:10]]
        }

    result = await _create_page(db, props)
    if result:
        url = page_url(result["id"])
        logger.info(f"[notion] ✅ Idea сохранена: {url}")
        return url

    logger.warning("[notion] save_idea: _create_page вернул None")
    return None


async def create_project(
    name: str,
    description: str = "",
    deadline: str | None = None,
) -> str | None:
    """Создать проект в NOTION_PROJECTS_DB со статусом 'В работе'."""
    db = _db("NOTION_PROJECTS_DB")
    logger.info(
        f"[notion] create_project | name={name[:60]!r} | "
        f"deadline={deadline!r} | "
        f"db={db[:8] + '…' if db else 'НЕ ЗАДАН'}"
    )
    if not db:
        logger.warning("[notion] create_project: NOTION_PROJECTS_DB не задан — пропускаем")
        return None

    props: dict[str, Any] = {
        "Name":        {"title":     _text_blocks(name[:200], max_total=200)},
        "Status":      {"select":    {"name": "В работе"}},
        "Description": {"rich_text": _text_blocks(description)},
    }
    if deadline:
        props["Deadline"] = {"date": {"start": deadline}}

    result = await _create_page(db, props)
    if result:
        url = page_url(result["id"])
        logger.info(f"[notion] ✅ Project создан '{name[:50]}': {url}")
        return url

    logger.warning("[notion] create_project: _create_page вернул None")
    return None
