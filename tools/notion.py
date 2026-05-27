"""
tools/notion.py — Notion API integration for ai-office agents.

Предоставляет async-функции для записи структурированных данных в базы Notion.
Все функции — silent-fail: если токен или ID базы не заданы, возвращают None
без исключений. Агенты продолжают работать без Notion.

Зависимость: aiohttp (уже в requirements.txt)
"""

from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp
from loguru import logger

from config import config

_BASE_URL    = "https://api.notion.com/v1"
_API_VERSION = "2022-06-28"
_BLOCK_SIZE  = 1990   # Notion limit per rich_text block = 2000, берём с запасом
_MAX_CONTENT = 9900   # 5 блоков × 1990 = максимум для rich_text поля


# ── Helpers ────────────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Content-Type":  "application/json",
        "Notion-Version": _API_VERSION,
    }


def _text_blocks(text: str, max_total: int = _MAX_CONTENT) -> list[dict]:
    """Разбить текст на блоки для rich_text / title (лимит 2000 символов на блок).

    Возвращает список блоков, пригодных как для поля title, так и для rich_text.
    """
    text = (text or "").strip()[:max_total]
    if not text:
        return [{"type": "text", "text": {"content": ""}}]
    return [
        {"type": "text", "text": {"content": text[i : i + _BLOCK_SIZE]}}
        for i in range(0, len(text), _BLOCK_SIZE)
    ]


def _today() -> str:
    """Сегодняшняя дата в формате YYYY-MM-DD."""
    return date.today().isoformat()


def page_url(page_id: str) -> str:
    """Notion URL страницы по её ID (с дефисами или без — оба формата работают)."""
    return f"https://www.notion.so/{page_id.replace('-', '')}"


async def _create_page(database_id: str, properties: dict[str, Any]) -> dict | None:
    """POST /v1/pages — создать запись в базе данных Notion.

    Возвращает JSON-ответ или None при ошибке.
    """
    if not config.NOTION_TOKEN:
        return None

    payload = {
        "parent":     {"database_id": database_id},
        "properties": properties,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/pages",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status == 200:
                    return data
                logger.warning(
                    f"[notion] Ошибка {resp.status} для базы {database_id[:8]}…: "
                    f"{data.get('message', data)}"
                )
                return None
    except aiohttp.ClientConnectorError:
        logger.warning("[notion] Нет соединения с api.notion.com")
        return None
    except TimeoutError:
        logger.warning("[notion] Таймаут запроса к Notion API")
        return None
    except Exception as e:
        logger.warning(f"[notion] Неожиданная ошибка: {e}")
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

async def save_research(
    title: str,
    content: str,
    source: str = "",
    agent: str = "Каспер",
) -> str | None:
    """Сохранить результат исследования в NOTION_RESEARCH_DB.

    Args:
        title:   Заголовок записи (до 200 символов).
        content: Полный текст исследования (до 9900 символов сохраняется в Notion).
        source:  Источник (URL или описание, до 500 символов).
        agent:   Имя агента-исполнителя (Каспер/Кевин/Питер/Элина/Алекс/Марта).

    Returns:
        URL созданной страницы Notion или None при ошибке/отсутствии настроек.
    """
    db = config.NOTION_RESEARCH_DB
    if not db:
        logger.debug("[notion] save_research: NOTION_RESEARCH_DB не задан")
        return None

    props: dict[str, Any] = {
        "Name":    {"title":     _text_blocks(title[:200], max_total=200)},
        "Content": {"rich_text": _text_blocks(content)},
        "Source":  {"rich_text": _text_blocks(source[:500], max_total=500)},
        "Agent":   {"select":    {"name": agent}},
        "Date":    {"date":      {"start": _today()}},
    }

    result = await _create_page(db, props)
    if result:
        url = page_url(result["id"])
        logger.info(f"[notion] Research сохранён ({len(content)} симв.): {url}")
        return url
    return None


async def save_content(
    title: str,
    text: str,
    content_type: str = "Статья",
) -> str | None:
    """Сохранить текстовый контент в NOTION_CONTENT_DB.

    Args:
        title:        Заголовок записи.
        text:         Текст контента.
        content_type: 'Статья' | 'Пост' | 'Письмо' | 'Идея'

    Returns:
        URL созданной страницы Notion или None при ошибке/отсутствии настроек.
    """
    db = config.NOTION_CONTENT_DB
    if not db:
        logger.debug("[notion] save_content: NOTION_CONTENT_DB не задан")
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
        logger.info(f"[notion] Content сохранён ({content_type}): {url}")
        return url
    return None


async def create_task(
    name: str,
    deadline: str | None = None,
    priority: str = "Средний",
) -> str | None:
    """Создать задачу в NOTION_TASKS_DB со статусом 'Сделать'.

    Args:
        name:     Название задачи.
        deadline: Дата в формате 'YYYY-MM-DD' или None.
        priority: 'Высокий' | 'Средний' | 'Низкий'

    Returns:
        URL созданной страницы Notion или None при ошибке/отсутствии настроек.
    """
    db = config.NOTION_TASKS_DB
    if not db:
        logger.debug("[notion] create_task: NOTION_TASKS_DB не задан")
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
        logger.info(f"[notion] Task создан ({priority}): {url}")
        return url
    return None


async def save_idea(
    name: str,
    description: str = "",
    tags: list[str] | None = None,
    priority: str = "Средний",
) -> str | None:
    """Сохранить идею в NOTION_IDEAS_DB.

    Args:
        name:        Название идеи.
        description: Описание идеи.
        tags:        Список тегов (multi_select).
        priority:    'Высокий' | 'Средний' | 'Низкий'

    Returns:
        URL созданной страницы Notion или None при ошибке/отсутствии настроек.
    """
    db = config.NOTION_IDEAS_DB
    if not db:
        logger.debug("[notion] save_idea: NOTION_IDEAS_DB не задан")
        return None

    props: dict[str, Any] = {
        "Name":        {"title":     _text_blocks(name[:200], max_total=200)},
        "Description": {"rich_text": _text_blocks(description)},
        "Priority":    {"select":    {"name": priority}},
    }
    if tags:
        # Notion multi_select: каждый тег — отдельный объект, лимит имени 100 символов
        props["Tags"] = {
            "multi_select": [{"name": t[:100]} for t in tags[:10]]
        }

    result = await _create_page(db, props)
    if result:
        url = page_url(result["id"])
        logger.info(f"[notion] Idea сохранена: {url}")
        return url
    return None


async def create_project(
    name: str,
    description: str = "",
    deadline: str | None = None,
) -> str | None:
    """Создать проект в NOTION_PROJECTS_DB со статусом 'В работе'.

    Args:
        name:        Название проекта.
        description: Краткое описание (до 9900 символов).
        deadline:    Дата дедлайна 'YYYY-MM-DD' или None.

    Returns:
        URL созданной страницы Notion или None при ошибке/отсутствии настроек.
    """
    db = config.NOTION_PROJECTS_DB
    if not db:
        logger.debug("[notion] create_project: NOTION_PROJECTS_DB не задан")
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
        logger.info(f"[notion] Project создан '{name[:50]}': {url}")
        return url
    return None
