"""
task_queue.py — async Postgres-based task queue для ai-office.

Агенты создают задачи через create_task(), воркер-цикл в BaseAgent
поллит get_next_task() и обновляет статус через mark_*.

Атомарность: get_next_task() использует UPDATE...RETURNING, что исключает
race condition при параллельных воркерах (каждый агент — отдельный loop).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from db import get_pool


@dataclass
class Task:
    id: int
    payload: str
    from_agent: str
    chat_id: Optional[int]
    correlation_id: str
    timeout_seconds: int
    retry_count: int
    max_retries: int

    def __str__(self) -> str:
        short = (self.payload[:60] + "…") if len(self.payload) > 60 else self.payload
        return (
            f"Task(id={self.id}, corr={self.correlation_id[:8]}, "
            f"payload={short!r}, retry={self.retry_count}/{self.max_retries})"
        )


async def create_task(
    assigned_agent: str,
    payload: str,
    from_agent: str = "user",
    chat_id: int | None = None,
    timeout_seconds: int = 300,
    max_retries: int = 3,
) -> int | None:
    """Добавить задачу в очередь. Возвращает task.id или None если БД недоступна."""
    pool = get_pool()
    if pool is None:
        logger.warning("[task_queue] create_task: pool недоступен")
        return None
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO tasks (assigned_agent, payload, from_agent, chat_id,
                               timeout_seconds, max_retries)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            assigned_agent, payload, from_agent, chat_id,
            timeout_seconds, max_retries,
        )
        task_id: int = row["id"]
        logger.info(
            f"[task_queue] Задача #{task_id} создана → {assigned_agent} "
            f"(timeout={timeout_seconds}s, retries={max_retries})"
        )
        return task_id
    except Exception as e:
        logger.error(f"[task_queue] create_task ошибка: {type(e).__name__}: {e}")
        return None


async def get_next_task(agent_name: str) -> Task | None:
    """Атомарно взять следующую pending-задачу агента и пометить как running.

    Использует UPDATE...RETURNING — исключает двойное взятие задачи.
    Возвращает None если нет задач или БД недоступна.
    """
    pool = get_pool()
    if pool is None:
        return None
    try:
        row = await pool.fetchrow(
            """
            UPDATE tasks
            SET status = 'running', started_at = NOW()
            WHERE id = (
                SELECT id FROM tasks
                WHERE assigned_agent = $1
                  AND status = 'pending'
                  AND retry_count < max_retries
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, payload, from_agent, chat_id,
                      correlation_id::text,
                      timeout_seconds, retry_count, max_retries
            """,
            agent_name,
        )
        if row is None:
            return None
        return Task(
            id=row["id"],
            payload=row["payload"],
            from_agent=row["from_agent"],
            chat_id=row["chat_id"],
            correlation_id=row["correlation_id"],
            timeout_seconds=row["timeout_seconds"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
        )
    except Exception as e:
        logger.error(f"[task_queue] get_next_task ошибка: {type(e).__name__}: {e}")
        return None


async def mark_running(task_id: int) -> None:
    """No-op: get_next_task() уже атомарно ставит status='running'."""


async def mark_completed(task_id: int, result: str) -> None:
    """Пометить задачу выполненной, сохранить результат."""
    pool = get_pool()
    if pool is None:
        return
    try:
        await pool.execute(
            """
            UPDATE tasks
            SET status = 'completed', result = $2, completed_at = NOW()
            WHERE id = $1
            """,
            task_id, result,
        )
        logger.debug(f"[task_queue] Задача #{task_id} → completed")
    except Exception as e:
        logger.error(f"[task_queue] mark_completed ошибка (id={task_id}): {e}")


async def mark_failed(task_id: int, error: str, retry: bool = True) -> None:
    """Пометить задачу провалившейся.

    retry=True — если есть попытки: retry_count+1, status='pending' (воркер повторит).
    retry=False — сразу status='failed' (таймаут, намеренный отказ).
    """
    pool = get_pool()
    if pool is None:
        return
    try:
        if retry:
            await pool.execute(
                """
                UPDATE tasks
                SET retry_count = retry_count + 1,
                    error       = $2,
                    status      = CASE
                                    WHEN retry_count + 1 >= max_retries THEN 'failed'
                                    ELSE 'pending'
                                  END,
                    started_at  = NULL
                WHERE id = $1
                """,
                task_id, error,
            )
        else:
            await pool.execute(
                """
                UPDATE tasks
                SET status = 'failed', error = $2, completed_at = NOW()
                WHERE id = $1
                """,
                task_id, error,
            )
        logger.debug(f"[task_queue] Задача #{task_id} → failed (retry={retry}): {error[:100]}")
    except Exception as e:
        logger.error(f"[task_queue] mark_failed ошибка (id={task_id}): {e}")


async def cleanup_timed_out_tasks() -> None:
    """Перевести зависшие running-задачи в failed.

    Задача считается зависшей если она в статусе 'running' дольше timeout_seconds.
    Вызывается воркером раз в ~60 секунд.
    """
    pool = get_pool()
    if pool is None:
        return
    try:
        result = await pool.execute(
            """
            UPDATE tasks
            SET status       = 'failed',
                error        = 'Таймаут: задача не завершена вовремя',
                completed_at = NOW()
            WHERE status = 'running'
              AND started_at < NOW() - (timeout_seconds || ' seconds')::INTERVAL
            """
        )
        count = int(result.split()[-1])
        if count > 0:
            logger.warning(
                f"[task_queue] cleanup: {count} зависших задач → failed"
            )
    except Exception as e:
        logger.error(f"[task_queue] cleanup_timed_out_tasks ошибка: {e}")
