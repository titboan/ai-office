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
from datetime import date, datetime, timezone
from typing import Any

import aiohttp
from loguru import logger

from config import config

_BASE_URL    = "https://api.notion.com/v1"
_API_VERSION = "2022-06-28"
_BLOCK_SIZE  = 1990   # Notion limit per rich_text block = 2000, берём с запасом
_MAX_CONTENT = 9900   # 5 блоков × 1990 = максимум для rich_text поля
_CHUNK_SIZE  = 90     # Блоков за один PATCH /blocks/{id}/children (лимит API = 100)


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


def _content_to_paragraph_blocks(text: str) -> list[dict]:
    """Разбить произвольный текст на paragraph-блоки Notion (по 1990 символов каждый).

    Каждый блок имеет вид:
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"type": "text", "text": {"content": "..."}}]}}
    Таким образом контент не ограничен 9900 символами свойства rich_text.
    """
    text = (text or "").strip()
    if not text:
        return [{
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": ""}}]},
        }]
    return [
        {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[i : i + _BLOCK_SIZE]}}]},
        }
        for i in range(0, len(text), _BLOCK_SIZE)
    ]


async def _append_blocks(page_id: str, blocks: list[dict], session: aiohttp.ClientSession) -> bool:
    """PATCH /v1/blocks/{page_id}/children — добавить блоки на страницу.

    Возвращает True при успехе (HTTP 200), False иначе. Не поднимает исключений.
    """
    url = f"{_BASE_URL}/blocks/{page_id}/children"
    payload = {"children": blocks}
    try:
        async with session.patch(
            url,
            headers=_headers(),
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            status = resp.status
            if status == 200:
                return True
            raw = await resp.text()
            try:
                err = json.loads(raw)
                code    = err.get("code", "?")
                message = err.get("message", raw[:300])
            except Exception:
                code, message = "?", raw[:300]
            logger.error(
                f"[notion] PATCH /blocks/{page_id[:8]}…/children → "
                f"HTTP {status} | code={code!r} | message={message!r}"
            )
            return False
    except Exception as e:
        logger.error(f"[notion] _append_blocks исключение: {type(e).__name__}: {e}")
        return False


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
    """Сохранить результат исследования в NOTION_RESEARCH_DB.

    Алгоритм:
      1. Создать страницу с метаданными (Name, Source, Agent, Date).
         Content в свойства НЕ кладём — там лимит 9900 символов.
      2. Разбить content на paragraph-блоки по 1990 символов.
      3. Группировать по 90 блоков (Notion лимит 100 блоков за запрос).
      4. PATCH /v1/blocks/{page_id}/children для каждого чанка.
      5. asyncio.sleep(0.3) между чанками — защита от rate limit.
    """
    logger.info("[notion] ── save_research START ──────────────────────────")

    # ── Шаг 1: читаем переменные окружения ───────────────────────────────────
    try:
        token = os.getenv("NOTION_TOKEN", "").strip()
        db_id = os.getenv("NOTION_RESEARCH_DB", "").strip()

        all_notion_vars = {k: v for k, v in os.environ.items() if "NOTION" in k}
        logger.info(f"[notion] token len={len(token)} | db_id len={len(db_id)}")
        logger.info(f"[notion] token[:12]={token[:12]!r} | db_id[:12]={db_id[:12]!r}")
        logger.info(f"[notion] все NOTION_* переменные в os.environ: {list(all_notion_vars.keys())}")

        tok_config = config.NOTION_TOKEN
        db_config  = config.NOTION_RESEARCH_DB
        logger.info(
            f"[notion] config.NOTION_TOKEN len={len(tok_config)} | "
            f"match os.getenv={token == tok_config}"
        )
        logger.info(
            f"[notion] config.NOTION_RESEARCH_DB len={len(db_config)} | "
            f"match os.getenv={db_id == db_config}"
        )
    except Exception as e:
        logger.error(f"[notion] ШАГ 1 УПАЛ: {type(e).__name__}: {e}")
        token, db_id = "", ""

    # ── Шаг 2: guard-проверки ────────────────────────────────────────────────
    if not token:
        logger.warning(
            "[notion] NOTION_TOKEN пустой — пропускаем.\n"
            "  Railway: Variables → NOTION_TOKEN (точное имя, без пробелов)"
        )
        return None
    if not db_id:
        logger.warning(
            "[notion] NOTION_RESEARCH_DB пустой — пропускаем.\n"
            "  Railway: Variables → NOTION_RESEARCH_DB (точное имя, без пробелов)"
        )
        return None

    # ── Шаг 3: создаём страницу с метаданными (без Content) ─────────────────
    logger.info(f"[notion] Шаг 3: POST /pages (только метаданные, без Content)")
    logger.info(f"[notion] Authorization: Bearer {token[:8]}…{token[-4:]}")
    logger.info(f"[notion] parent.database_id: {db_id}")

    props: dict[str, Any] = {
        "Name":   {"title":     _text_blocks(title[:200], max_total=200)},
        "Source": {"rich_text": _text_blocks(source[:500], max_total=500)},
        "Agent":  {"select":    {"name": agent}},
        "Date":   {"date":      {"start": _today()}},
    }
    payload = {"parent": {"database_id": db_id}, "properties": props}
    req_headers = {
        "Authorization":  f"Bearer {token}",
        "Notion-Version": _API_VERSION,
        "Content-Type":   "application/json",
    }

    page_id: str | None = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/pages",
                headers=req_headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                status = resp.status
                body   = await resp.text()
                logger.info(f"[notion] POST /pages → HTTP {status} | body={body[:300]!r}")

                if status != 200:
                    try:
                        err     = json.loads(body)
                        code    = err.get("code", "?")
                        message = err.get("message", body[:300])
                    except Exception:
                        code, message = "parse_error", body[:300]
                    logger.error(
                        f"[notion] Создание страницы провалилось: "
                        f"HTTP {status} | code={code!r} | message={message!r}"
                    )
                    logger.info("[notion] ── save_research END (page creation error) ──")
                    return None

                data    = json.loads(body)
                page_id = data.get("id", "")
                logger.info(f"[notion] ✅ Страница создана: id={page_id}")

            # ── Шаг 4: разбиваем content на paragraph-блоки ──────────────────
            all_blocks = _content_to_paragraph_blocks(content)
            total_blocks = len(all_blocks)

            # Делим на чанки по _CHUNK_SIZE блоков
            chunks = [
                all_blocks[i : i + _CHUNK_SIZE]
                for i in range(0, total_blocks, _CHUNK_SIZE)
            ]
            total_chunks = len(chunks)
            logger.info(
                f"[notion] Текст разбит на {total_blocks} блоков → "
                f"{total_chunks} чанков по ≤{_CHUNK_SIZE} блоков"
            )

            # ── Шаг 5: PATCH для каждого чанка ───────────────────────────────
            failed_chunks = 0
            for idx, chunk in enumerate(chunks, start=1):
                logger.info(
                    f"[notion] PATCH чанк {idx}/{total_chunks} "
                    f"({len(chunk)} блоков) → /blocks/{page_id[:8]}…/children"
                )
                ok = await _append_blocks(page_id, chunk, session)
                if not ok:
                    failed_chunks += 1
                    logger.warning(
                        f"[notion] Чанк {idx}/{total_chunks} не загружен — продолжаем"
                    )
                if idx < total_chunks:
                    await asyncio.sleep(0.3)

            if failed_chunks:
                logger.warning(
                    f"[notion] {failed_chunks}/{total_chunks} чанков не загружены — "
                    "часть текста может отсутствовать на странице"
                )
            else:
                logger.info(f"[notion] Все {total_chunks} чанков успешно загружены")

    except aiohttp.ClientConnectorError as e:
        logger.error(f"[notion] Нет соединения с api.notion.com: {e}")
        logger.info("[notion] ── save_research END (connection error) ──────")
        return None
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
        logger.error(f"[notion] Таймаут запроса к Notion API: {e}")
        logger.info("[notion] ── save_research END (timeout) ──────────────")
        return None
    except Exception as e:
        logger.error(f"[notion] Неожиданная ошибка: {type(e).__name__}: {e}")
        logger.info("[notion] ── save_research END (unexpected error) ──────")
        return None

    if not page_id:
        return None

    url = page_url(page_id)
    logger.info(f"[notion] ✅ Research полностью сохранён: {url}")
    logger.info("[notion] ── save_research END (success) ──────────────────")
    return url


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


# ── Project pages (chain Notion integration) ──────────────────────────────────

_SECTION_MAP: dict[str, str] = {
    "kasper": "🔍 Исследование",
    "kevin":  "🏗️ Архитектура",
    "peter":  "📊 Аналитика",
    "elina":  "✍️ Описание продукта",
    "alex":   "✅ План задач",
}


def _h2_block(text: str) -> dict:
    return {
        "object": "block", "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": text}}],
            "is_toggleable": True,  # toggle heading — поддерживает children
        },
    }


def _divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _para_block(text: str) -> dict:
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:_BLOCK_SIZE]}}]},
    }


def _callout_block(text: str, icon: str = "💡") -> dict:
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": text[:_BLOCK_SIZE]}}],
            "icon": {"type": "emoji", "emoji": icon},
        },
    }


async def create_project_page(
    parent_page_id: str,
    title: str,
    description: str = "",
) -> str | None:
    """Создать страницу проекта в Notion под parent_page_id.

    Возвращает page_id новой страницы или None при ошибке.
    """
    tok = _tok()
    if not tok or not parent_page_id:
        logger.warning("[notion] create_project_page: токен или parent_page_id не задан")
        return None

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks = [
        {   # H1 — название
            "object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"type": "text", "text": {"content": title[:_BLOCK_SIZE]}}]},
        },
    ]
    if description:
        blocks.append(_callout_block(description))
    blocks += [
        _divider_block(),
        _h2_block("🔍 Исследование"),
        _h2_block("🏗️ Архитектура"),
        _h2_block("📊 Аналитика"),
        _h2_block("✍️ Описание продукта"),
        _h2_block("✅ План задач"),
        _divider_block(),
        _para_block(f"⏳ Создано: {now_str} | Статус: в работе"),
    ]

    payload = {
        "parent":     {"page_id": parent_page_id},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": title[:200]}}]}},
        "children":   blocks,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_BASE_URL}/pages",
                headers=_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pid = data["id"]
                    logger.info(f"[notion] create_project_page ✅ page_id={pid[:8]}… title={title[:40]!r}")
                    return pid
                raw = await resp.text()
                logger.error(f"[notion] create_project_page HTTP {resp.status}: {raw[:300]}")
                return None
    except Exception as e:
        logger.error(f"[notion] create_project_page exception: {e}")
        return None


async def append_agent_result(
    token: str,
    page_id: str,
    agent_key: str,
    result: str,
) -> None:
    """Добавить результат агента внутрь toggle-заголовка соответствующего раздела.

    Ищет toggle H2 блок по тексту, PATCH-ит его children напрямую.
    Если toggle не найден — добавляет в конец страницы с новым toggle.
    """
    if not token or not page_id:
        return

    section_title = _SECTION_MAP.get(agent_key)
    if not section_title:
        logger.warning(f"[notion] append_agent_result: нет секции для agent={agent_key!r}")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Notion-Version": _API_VERSION,
    }
    result_blocks = _content_to_paragraph_blocks(result)

    try:
        async with aiohttp.ClientSession() as session:
            # Шаг 1: GET children страницы — ищем toggle heading_2
            async with session.get(
                f"{_BASE_URL}/blocks/{page_id}/children",
                headers=headers,
                params={"page_size": 100},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raw = await resp.text()
                    logger.error(f"[notion] GET blocks HTTP {resp.status}: {raw[:200]}")
                    return
                data = await resp.json()

            # Шаг 2: ищем toggle H2 с нужным текстом
            target_block_id: str | None = None
            for block in data.get("results", []):
                btype = block.get("type", "")
                if btype == "heading_2":
                    h2 = block.get("heading_2", {})
                    if not h2.get("is_toggleable"):
                        continue
                    texts = h2.get("rich_text", [])
                    plain = "".join(
                        t.get("plain_text", "") or t.get("text", {}).get("content", "")
                        for t in texts
                    )
                    if section_title in plain:
                        target_block_id = block["id"]
                        break

            if target_block_id:
                # Шаг 3a: PATCH children toggle-блока напрямую (поддерживается API)
                patch_url = f"{_BASE_URL}/blocks/{target_block_id}/children"
            else:
                # Шаг 3b: toggle не найден — добавляем toggle + контент в конец страницы
                logger.warning(f"[notion] toggle '{section_title}' не найден, добавляем в конец")
                toggle_block = _h2_block(section_title)
                # Сначала создаём toggle
                async with session.patch(
                    f"{_BASE_URL}/blocks/{page_id}/children",
                    headers=headers,
                    json={"children": [toggle_block]},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        resp_data = await resp.json()
                        new_blocks = resp_data.get("results", [])
                        if new_blocks:
                            target_block_id = new_blocks[0]["id"]
                            patch_url = f"{_BASE_URL}/blocks/{target_block_id}/children"
                        else:
                            patch_url = f"{_BASE_URL}/blocks/{page_id}/children"
                    else:
                        patch_url = f"{_BASE_URL}/blocks/{page_id}/children"

            # Шаг 4: добавляем контент чанками
            for i in range(0, len(result_blocks), _CHUNK_SIZE):
                chunk = result_blocks[i:i + _CHUNK_SIZE]
                async with session.patch(
                    patch_url,
                    headers=headers,
                    json={"children": chunk},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        raw = await resp.text()
                        logger.error(f"[notion] PATCH blocks HTTP {resp.status}: {raw[:200]}")
                        return
                if i + _CHUNK_SIZE < len(result_blocks):
                    await asyncio.sleep(0.3)

            logger.debug(
                f"[notion] append_agent_result ✅ agent={agent_key} "
                f"target={target_block_id or page_id}… blocks={len(result_blocks)}"
            )

    except Exception as e:
        logger.error(f"[notion] append_agent_result exception: {e}")


async def update_project_status(
    token: str,
    page_id: str,
    status: str,
) -> None:
    """Добавить строку статуса в конец страницы проекта."""
    if not token or not page_id:
        return

    icon = {"завершён": "✅", "ошибка": "❌"}.get(status, "⏳")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = f"{icon} Обновлено: {now_str} | Статус: {status}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "Notion-Version": _API_VERSION,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{_BASE_URL}/blocks/{page_id}/children",
                headers=headers,
                json={"children": [_para_block(text)]},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    raw = await resp.text()
                    logger.error(f"[notion] update_project_status HTTP {resp.status}: {raw[:200]}")
                    return
        logger.debug(f"[notion] update_project_status ✅ status={status!r} page={page_id[:8]}…")
    except Exception as e:
        logger.error(f"[notion] update_project_status exception: {e}")
