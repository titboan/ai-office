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
        logger.info("[db] Схема tasks готова ✓")

async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("[db] Pool закрыт")
