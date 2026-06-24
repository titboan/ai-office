"""
task_queue.py — очередь задач на Postgres
get_next_task использует UPDATE...RETURNING — атомарно, без race conditions
"""
from __future__ import annotations
import uuid
from datetime import datetime
from loguru import logger
from db import get_pool, log_event


class Task:
    def __init__(self, row: dict) -> None:
        self.id              = row["id"]
        self.assigned_agent  = row["assigned_agent"]
        self.task_type       = row["task_type"]
        self.status          = row["status"]
        self.payload         = row["payload"]
        self.result          = row.get("result")
        self.correlation_id  = row["correlation_id"]
        self.parent_task_id  = row.get("parent_task_id")
        self.from_agent      = row["from_agent"]
        self.chat_id         = row.get("chat_id")
        self.retry_count     = row["retry_count"]
        self.max_retries     = row["max_retries"]
        self.timeout_seconds = row["timeout_seconds"]
        self.created_at      = row["created_at"]
        self.remind_at       = row.get("remind_at")
        self.chain_id        = row.get("chain_id")
        self.chain_index     = row.get("chain_index", 0)
        self.chain_total     = row.get("chain_total", 1)
        self.chain_plan      = row.get("chain_plan")
        self.notion_page_id  = row.get("notion_page_id")
        self.parallel_group  = row.get("parallel_group")

    def __repr__(self):
        return f"Task(id={self.id}, agent={self.assigned_agent!r}, status={self.status!r}, corr={self.correlation_id[:8]}…)"


async def create_task(
    assigned_agent: str,
    payload: str,
    from_agent: str = "user",
    chat_id: int | None = None,
    task_type: str = "general",
    correlation_id: str | None = None,
    parent_task_id: int | None = None,
    max_retries: int = 3,
    timeout_seconds: int = 300,
    priority: int = 0,  # 0=обычный, 10=высокий, 20=срочный
) -> tuple[int, str] | tuple[None, None]:
    try:
        pool = await get_pool()
    except RuntimeError:
        return None, None

    corr_id = correlation_id or str(uuid.uuid4())
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO tasks (
                    assigned_agent, task_type, payload,
                    from_agent, chat_id,
                    correlation_id, parent_task_id,
                    max_retries, timeout_seconds, priority
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                RETURNING id, correlation_id
            """, assigned_agent, task_type, payload,
                from_agent, chat_id,
                corr_id, parent_task_id,
                max_retries, timeout_seconds, priority)
            task_id = row["id"]
            corr_id = row["correlation_id"]
            logger.info(
                f"[task_queue] ✅ Задача id={task_id} "
                f"corr={corr_id[:8]}… agent={assigned_agent!r}"
            )
            await log_event(
                "TASK_CREATED",
                task_id=task_id,
                agent_key=assigned_agent,
                payload={"from_agent": from_agent, "task_type": task_type, "priority": priority},
            )
            return task_id, corr_id
    except Exception as e:
        logger.error(f"[task_queue] create_task error: {e}")
        return None, None


async def get_next_task(agent_name: str) -> Task | None:
    # UPDATE...RETURNING — атомарно берём задачу без отдельного SELECT FOR UPDATE
    # Два воркера одновременно получат разные задачи — race condition исключён
    try:
        pool = await get_pool()
    except RuntimeError:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE tasks
                SET status     = 'acknowledged',
                    started_at = NOW()
                WHERE id = (
                    SELECT id FROM tasks
                    WHERE assigned_agent = $1
                      AND status = 'queued'
                      AND task_type != 'reminder'
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
            """, agent_name)
            if row is None:
                return None
            task = Task(dict(row))
            logger.info(f"[task_queue] 📥 {agent_name} взял: {task}")
            return task
    except Exception as e:
        logger.error(f"[task_queue] get_next_task error: {e}")
        return None


async def mark_running(task_id: int) -> None:
    await _set_status(task_id, "running")


async def mark_completed(task_id: int, result: str) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                UPDATE tasks SET status='completed', result=$2, finished_at=NOW()
                WHERE id=$1
            """, task_id, result)
        logger.info(f"[task_queue] ✅ task_id={task_id} → completed")
    except Exception as e:
        logger.error(f"[task_queue] mark_completed error: {e}")


async def mark_failed(task_id: int, error: str, retry: bool = False) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if retry:
                row = await conn.fetchrow(
                    "SELECT retry_count, max_retries FROM tasks WHERE id=$1", task_id
                )
                if row and row["retry_count"] < row["max_retries"]:
                    await conn.execute("""
                        UPDATE tasks
                        SET status='queued', retry_count=retry_count+1,
                            error_message=$2, started_at=NULL
                        WHERE id=$1
                    """, task_id, error)
                    logger.warning(f"[task_queue] 🔄 task_id={task_id} → retry ({row['retry_count']+1}/{row['max_retries']})")
                    return
            await conn.execute("""
                UPDATE tasks SET status='failed', error_message=$2, finished_at=NOW()
                WHERE id=$1
            """, task_id, error)
            logger.error(f"[task_queue] ❌ task_id={task_id} → failed")
    except Exception as e:
        logger.error(f"[task_queue] mark_failed error: {e}")


async def cleanup_timed_out_tasks() -> int:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                UPDATE tasks
                SET status='timeout', error_message='Timeout exceeded', finished_at=NOW()
                WHERE status IN ('acknowledged','running')
                  AND started_at < NOW() - INTERVAL '1 second' * timeout_seconds
                RETURNING id
            """)
            if rows:
                logger.warning(f"[task_queue] ⏱️ Таймаут задач: {[r['id'] for r in rows]}")
            return len(rows)
    except Exception as e:
        logger.error(f"[task_queue] cleanup error: {e}")
        return 0


async def get_active_tasks() -> list[dict]:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, assigned_agent, status, payload, correlation_id, created_at, priority
                FROM tasks
                WHERE status IN ('queued', 'acknowledged', 'running')
                ORDER BY priority DESC, created_at ASC
            """)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[task_queue] get_active_tasks error: {e}")
        return []


async def enqueue_chain_task(
    pool,  # устарел, всегда передавать None — оставлен для совместимости вызовов
    agent_key: str,
    payload: str,
    chat_id: int | None,
    chain_id: str,
    chain_index: int,
    chain_total: int,
    chain_plan: dict | None = None,
    notion_page_id: str | None = None,
    parent_task_id: int | None = None,
    from_agent: str = "marta",
    correlation_id: str | None = None,
    priority: int = 0,
    timeout_seconds: int = 300,
    parallel_group: int | None = None,
) -> int | None:
    """Создать задачу с chain_* полями. Возвращает task_id или None."""
    import json as _json
    try:
        db_pool = await get_pool()
    except RuntimeError:
        return None
    corr_id = correlation_id or str(uuid.uuid4())
    chain_plan_json = _json.dumps(chain_plan, ensure_ascii=False) if chain_plan else None
    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO tasks (
                    assigned_agent, task_type, payload,
                    from_agent, chat_id,
                    correlation_id, parent_task_id,
                    priority, timeout_seconds,
                    chain_id, chain_index, chain_total, chain_plan, notion_page_id,
                    parallel_group
                )
                VALUES ($1,'general',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14)
                RETURNING id
            """, agent_key, payload,
                from_agent, chat_id,
                corr_id, parent_task_id,
                priority, timeout_seconds,
                chain_id, chain_index, chain_total, chain_plan_json, notion_page_id,
                parallel_group)
            task_id = row["id"]
            logger.info(
                f"[task_queue] chain_enqueue | chain_id={chain_id[:8]} | "
                f"grp={chain_index}/{chain_total-1} | agent={agent_key!r} | task_id={task_id}"
                + (f" | parallel_group={parallel_group}" if parallel_group is not None else "")
            )
            await log_event(
                "TASK_CREATED",
                task_id=task_id,
                agent_key=agent_key,
                chain_id=chain_id,
                payload={"chain_index": chain_index, "chain_total": chain_total, "from_agent": from_agent, "parallel_group": parallel_group},
            )
            return task_id
    except Exception as e:
        logger.error(f"[task_queue] enqueue_chain_task error: {e}")
        return None


async def count_incomplete_in_group(chain_id: str, group_index: int) -> int:
    """Количество задач в параллельной группе, ещё не завершённых (Redis-fallback)."""
    try:
        db_pool = await get_pool()
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT COUNT(*)::int AS cnt
                FROM tasks
                WHERE chain_id = $1
                  AND chain_index = $2
                  AND status NOT IN ('completed', 'failed', 'timeout')
            """, chain_id, group_index)
            return row["cnt"] if row else 0
    except Exception as e:
        logger.error(f"[task_queue] count_incomplete_in_group error: {e}")
        return 0


async def get_chain_results(pool, chain_id: str) -> list[dict]:  # pool устарел, всегда None
    """Completed задачи цепочки, отсортированные по chain_index."""
    try:
        db_pool = await get_pool()
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT assigned_agent, chain_index, result, finished_at
                FROM tasks
                WHERE chain_id = $1 AND status = 'completed'
                ORDER BY chain_index ASC
            """, chain_id)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[task_queue] get_chain_results error: {e}")
        return []


async def get_chain_status(pool, chain_id: str) -> dict:  # pool устарел, всегда None
    """Статистика по статусам задач цепочки."""
    try:
        db_pool = await get_pool()
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT status, count(*)::int AS cnt
                FROM tasks WHERE chain_id = $1
                GROUP BY status
            """, chain_id)
            counts: dict = {"total": 0, "completed": 0, "failed": 0, "running": 0, "queued": 0}
            for r in rows:
                s = r["status"]
                counts[s] = r["cnt"]
                counts["total"] += r["cnt"]
            return counts
    except Exception as e:
        logger.error(f"[task_queue] get_chain_status error: {e}")
        return {}


async def get_chain_plan(pool, chain_id: str) -> dict | None:  # pool устарел, всегда None
    """Получить план цепочки из первой задачи (chain_index=0)."""
    import json as _json
    try:
        db_pool = await get_pool()
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT chain_plan FROM tasks
                WHERE chain_id = $1 AND chain_index = 0
                LIMIT 1
            """, chain_id)
            if row and row["chain_plan"]:
                plan = row["chain_plan"]
                # asyncpg возвращает JSONB как str или dict
                if isinstance(plan, str):
                    return _json.loads(plan)
                return dict(plan)
            return None
    except Exception as e:
        logger.error(f"[task_queue] get_chain_plan error: {e}")
        return None


async def get_due_reminders() -> list[Task]:
    """Получить задачи-напоминания у которых наступило время."""
    try:
        pool = await get_pool()
    except RuntimeError:
        return []
    sql = """
        UPDATE tasks
        SET status     = 'acknowledged',
            started_at = NOW()
        WHERE id IN (
            SELECT id FROM tasks
            WHERE task_type = 'reminder'
              AND status    = 'queued'
              AND remind_at <= NOW()
            ORDER BY remind_at ASC
            LIMIT 10
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
            return [Task(dict(r)) for r in rows]
    except Exception as e:
        logger.error(f"[task_queue] get_due_reminders error: {e}")
        return []


async def create_reminder(
    chat_id: int,
    text: str,
    remind_at,  # datetime объект
    from_agent: str = "alex",
) -> tuple[int, str] | tuple[None, None]:
    """Создать напоминание с временем срабатывания."""
    task_id, corr_id = await create_task(
        assigned_agent="marta",
        payload=text,
        from_agent=from_agent,
        chat_id=chat_id,
        task_type="reminder",
        priority=10,
    )
    if task_id and remind_at is not None:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE tasks SET remind_at = $2 WHERE id = $1",
                    task_id, remind_at,
                )
        except Exception as e:
            logger.error(f"[task_queue] create_reminder set remind_at error: {e}")
    return task_id, corr_id


async def get_recent_tasks(limit: int = 10) -> list[dict]:
    """Получить последние завершённые задачи."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, assigned_agent, status, payload,
                       result, correlation_id, created_at,
                       finished_at, priority
                FROM tasks
                WHERE status IN ('completed', 'failed', 'timeout')
                ORDER BY finished_at DESC NULLS LAST
                LIMIT $1
            """, limit)
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[task_queue] get_recent_tasks error: {e}")
        return []


async def cancel_task(task_id: int) -> bool:
    """Отменить задачу если она ещё в статусе queued."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE tasks
                SET status        = 'failed',
                    error_message = 'Cancelled by user',
                    finished_at   = NOW()
                WHERE id = $1
                  AND status = 'queued'
                RETURNING id
            """, task_id)
            return row is not None
    except Exception as e:
        logger.error(f"[task_queue] cancel_task error: {e}")
        return False


async def update_task_cost(task_id: int, cost_usd: float, latency_ms: int) -> None:
    """Записать расчётную стоимость и латентность после завершения задачи."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET estimated_cost=$2, latency_ms=$3 WHERE id=$1",
                task_id, cost_usd, latency_ms,
            )
    except Exception as e:
        logger.debug(f"[task_queue] update_task_cost task_id={task_id}: {e}")


async def _set_status(task_id: int, status: str) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET status=$2 WHERE id=$1", task_id, status)
    except Exception as e:
        logger.error(f"[task_queue] _set_status error: {e}")
