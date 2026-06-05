"""
db.py — asyncpg pool + DDL для таблицы tasks
"""
from __future__ import annotations
import os
from loguru import logger
import asyncpg

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        raise RuntimeError("Вызови await init_db() при старте")
    return _pool

async def init_db() -> None:
    global _pool
    url = os.getenv("DATABASE_URL", "")
    if not url:
        logger.warning("[db] DATABASE_URL не задан — task queue отключён")
        return
    try:
        _pool = await asyncpg.create_pool(dsn=url, min_size=2, max_size=10, command_timeout=30)
        logger.info("[db] Pool создан")
    except Exception as e:
        logger.error(f"[db] Не удалось подключиться: {e}")
        return
    await _create_schema()

async def _create_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'task_status') THEN
                    CREATE TYPE task_status AS ENUM (
                        'queued', 'acknowledged', 'running',
                        'completed', 'failed', 'timeout'
                    );
                END IF;
            END$$;
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id               BIGSERIAL PRIMARY KEY,
                assigned_agent   TEXT        NOT NULL,
                task_type        TEXT        NOT NULL DEFAULT 'general',
                status           task_status NOT NULL DEFAULT 'queued',
                payload          TEXT        NOT NULL,
                result           TEXT,
                error_message    TEXT,
                correlation_id   TEXT        NOT NULL,
                parent_task_id   BIGINT,
                from_agent       TEXT        NOT NULL DEFAULT 'user',
                chat_id          BIGINT,
                retry_count      INT         NOT NULL DEFAULT 0,
                max_retries      INT         NOT NULL DEFAULT 3,
                timeout_seconds  INT         NOT NULL DEFAULT 300,
                priority         INT         NOT NULL DEFAULT 0,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                started_at       TIMESTAMPTZ,
                finished_at      TIMESTAMPTZ
            );
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority INT NOT NULL DEFAULT 0;
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS remind_at TIMESTAMPTZ;
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS chain_id TEXT;
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS chain_index INT DEFAULT 0;
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS chain_total INT DEFAULT 1;
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS chain_plan JSONB;
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS notion_page_id TEXT;
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_agent_status
                ON tasks (assigned_agent, status, created_at);
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id              BIGSERIAL PRIMARY KEY,
                chat_id         BIGINT NOT NULL,
                name            TEXT NOT NULL,
                name_lower      TEXT GENERATED ALWAYS AS (lower(name)) STORED,
                notion_page_id  TEXT,
                chain_id        TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_chat_name
                ON projects(chat_id, name_lower);
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS digest_channels (
                id               BIGSERIAL PRIMARY KEY,
                chat_id          TEXT        NOT NULL,
                username         TEXT,
                title            TEXT,
                added_by         BIGINT,
                last_checked_at  TIMESTAMPTZ,
                created_at       TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (chat_id, added_by)
            );
        """)
        logger.info("[db] Схема tasks + projects + digest_channels готова ✓")

async def save_project(
    chat_id: int,
    name: str,
    notion_page_id: str,
    chain_id: str | None = None,
) -> None:
    """Сохраняет или обновляет проект. Upsert по chat_id + name_lower."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO projects (chat_id, name, notion_page_id, chain_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (chat_id, name_lower) DO UPDATE
                SET notion_page_id = EXCLUDED.notion_page_id,
                    chain_id       = EXCLUDED.chain_id,
                    updated_at     = NOW()
            """,
            chat_id, name, notion_page_id, chain_id,
        )


async def find_project(chat_id: int, name_query: str) -> dict | None:
    """Ищет проект по chat_id и части названия (ILIKE).
    Возвращает dict с полями id, name, notion_page_id, chain_id или None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, name, notion_page_id, chain_id
            FROM projects
            WHERE chat_id = $1 AND name_lower ILIKE $2
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            chat_id, f"%{name_query.lower()}%",
        )
        return dict(row) if row else None


async def list_projects(chat_id: int) -> list[dict]:
    """Возвращает все проекты пользователя, отсортированные по updated_at DESC."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, notion_page_id, chain_id
            FROM projects
            WHERE chat_id = $1
            ORDER BY updated_at DESC
            """,
            chat_id,
        )
        return [dict(r) for r in rows]


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("[db] Pool закрыт")


# ── digest_channels ───────────────────────────────────────────────────────────

async def add_digest_channel(
    chat_id: str,
    username: str | None,
    title: str | None,
    added_by: int,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO digest_channels (chat_id, username, title, added_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (chat_id, added_by) DO UPDATE
                SET username = EXCLUDED.username,
                    title    = EXCLUDED.title
            """,
            chat_id, username, title, added_by,
        )


async def remove_digest_channel(username: str, added_by: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM digest_channels WHERE username = $1 AND added_by = $2",
            username.lstrip("@"), added_by,
        )
        return result.split()[-1] != "0"


async def list_digest_channels(added_by: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chat_id, username, title, last_checked_at
            FROM digest_channels
            WHERE added_by = $1
            ORDER BY created_at
            """,
            added_by,
        )
        return [dict(r) for r in rows]


async def update_channel_last_checked(
    chat_id: str,
    added_by: int,
    checked_at,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE digest_channels
               SET last_checked_at = $3
             WHERE chat_id = $1 AND added_by = $2
            """,
            chat_id, added_by, checked_at,
        )


async def get_distinct_digest_users() -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT added_by FROM digest_channels WHERE added_by IS NOT NULL"
        )
        return [r["added_by"] for r in rows]
