"""
db.py — asyncpg connection pool для task queue.

Используется task_queue.py и инициализируется из main.py.
Если DATABASE_URL не задан — pool = None, все операции с БД — silent-fail.
"""

from __future__ import annotations

import os

import asyncpg
from loguru import logger

_pool: asyncpg.Pool | None = None

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id               SERIAL PRIMARY KEY,
    assigned_agent   VARCHAR(50)  NOT NULL,
    payload          TEXT         NOT NULL,
    from_agent       VARCHAR(50)  NOT NULL DEFAULT 'user',
    chat_id          BIGINT,
    correlation_id   UUID         NOT NULL DEFAULT gen_random_uuid(),
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending',
    result           TEXT,
    error            TEXT,
    retry_count      INT          NOT NULL DEFAULT 0,
    max_retries      INT          NOT NULL DEFAULT 3,
    timeout_seconds  INT          NOT NULL DEFAULT 300,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tasks_agent_status
    ON tasks (assigned_agent, status, created_at)
    WHERE status = 'pending';
"""


async def init_db() -> None:
    """Создать пул соединений и таблицу tasks (если не существует)."""
    global _pool

    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        logger.warning(
            "[db] DATABASE_URL не задан — task queue недоступен. "
            "Railway: Add Plugin → PostgreSQL → DATABASE_URL подставится автоматически."
        )
        return

    try:
        _pool = await asyncpg.create_pool(
            url,
            min_size=2,
            max_size=10,
            command_timeout=10,
        )
        async with _pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)
        logger.info("[db] PostgreSQL подключён, таблица tasks готова")
    except Exception as e:
        logger.error(f"[db] Ошибка подключения к PostgreSQL: {type(e).__name__}: {e}")
        _pool = None


async def close_db() -> None:
    """Закрыть пул соединений."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("[db] PostgreSQL соединение закрыто")


def get_pool() -> asyncpg.Pool | None:
    """Вернуть пул (None если БД не инициализирована)."""
    return _pool
