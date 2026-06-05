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
            CREATE TABLE IF NOT EXISTS marketplace_reviews (
                id                BIGSERIAL PRIMARY KEY,
                marketplace       TEXT        NOT NULL,
                review_id         TEXT        NOT NULL,
                product_id        TEXT,
                product_name      TEXT,
                rating            INTEGER,
                text              TEXT,
                author            TEXT,
                status            TEXT        NOT NULL DEFAULT 'new',
                generated_reply   TEXT,
                final_reply       TEXT,
                chat_id           BIGINT,
                created_at        TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (marketplace, review_id)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_shops (
                id            BIGSERIAL PRIMARY KEY,
                chat_id       BIGINT      NOT NULL,
                marketplace   TEXT        NOT NULL,
                api_token     TEXT        NOT NULL,
                client_id     TEXT,
                shop_name     TEXT,
                is_active     BOOLEAN     NOT NULL DEFAULT true,
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (chat_id, marketplace)
            );
        """)
        await conn.execute("""
            ALTER TABLE marketplace_shops
                ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;
        """)
        await conn.execute("""
            ALTER TABLE marketplace_shops
                ADD COLUMN IF NOT EXISTS last_checked_negative TIMESTAMPTZ;
        """)
        await conn.execute("""
            ALTER TABLE marketplace_shops
                ADD COLUMN IF NOT EXISTS statistics_token TEXT;
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_stocks (
                id             BIGSERIAL PRIMARY KEY,
                chat_id        BIGINT      NOT NULL,
                marketplace    TEXT        NOT NULL,
                product_id     TEXT        NOT NULL,
                product_name   TEXT,
                warehouse_name TEXT,
                stock          INTEGER     NOT NULL DEFAULT 0,
                reserved       INTEGER     NOT NULL DEFAULT 0,
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (chat_id, marketplace, product_id, warehouse_name)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_sales (
                id           BIGSERIAL PRIMARY KEY,
                chat_id      BIGINT        NOT NULL,
                marketplace  TEXT          NOT NULL,
                order_id     TEXT          NOT NULL,
                product_id   TEXT,
                product_name TEXT,
                quantity     INTEGER       NOT NULL DEFAULT 1,
                price        NUMERIC(10,2),
                commission   NUMERIC(10,2),
                sale_date    TIMESTAMPTZ,
                created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
                UNIQUE (marketplace, order_id)
            );
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


# ── marketplace_shops / marketplace_reviews ───────────────────────────────────

async def add_marketplace_shop(
    chat_id: int,
    marketplace: str,
    api_token: str,
    shop_name: str | None = None,
    client_id: str | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO marketplace_shops (chat_id, marketplace, api_token, client_id, shop_name)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (chat_id, marketplace) DO UPDATE
                SET api_token  = EXCLUDED.api_token,
                    client_id  = EXCLUDED.client_id,
                    shop_name  = EXCLUDED.shop_name,
                    is_active  = true
            """,
            chat_id, marketplace, api_token, client_id, shop_name,
        )


async def get_marketplace_shops(chat_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM marketplace_shops WHERE chat_id = $1 AND is_active = true ORDER BY created_at",
            chat_id,
        )
        return [dict(r) for r in rows]


async def get_all_active_shops() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM marketplace_shops WHERE is_active = true ORDER BY chat_id"
        )
        return [dict(r) for r in rows]


async def save_review(
    marketplace: str,
    review_id: str,
    product_id: str | None,
    product_name: str | None,
    rating: int | None,
    text: str | None,
    author: str | None,
    chat_id: int,
) -> bool:
    """INSERT нового отзыва. Возвращает True если запись новая."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO marketplace_reviews
                (marketplace, review_id, product_id, product_name, rating, text, author, chat_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (marketplace, review_id) DO NOTHING
            """,
            marketplace, review_id, product_id, product_name, rating, text, author, chat_id,
        )
        return result.split()[-1] != "0"


async def update_review_status(
    marketplace: str,
    review_id: str,
    status: str,
    generated_reply: str | None = None,
    final_reply: str | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE marketplace_reviews
               SET status          = $3,
                   generated_reply = COALESCE($4, generated_reply),
                   final_reply     = COALESCE($5, final_reply)
             WHERE marketplace = $1 AND review_id = $2
            """,
            marketplace, review_id, status, generated_reply, final_reply,
        )


async def get_pending_reviews(chat_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM marketplace_reviews
             WHERE chat_id = $1 AND status = 'pending_approval'
             ORDER BY created_at
            """,
            chat_id,
        )
        return [dict(r) for r in rows]


async def upsert_stock(
    chat_id: int,
    marketplace: str,
    product_id: str,
    product_name: str | None,
    warehouse_name: str | None,
    stock: int,
    reserved: int,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO marketplace_stocks
                (chat_id, marketplace, product_id, product_name, warehouse_name, stock, reserved, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (chat_id, marketplace, product_id, warehouse_name) DO UPDATE
                SET product_name   = EXCLUDED.product_name,
                    stock          = EXCLUDED.stock,
                    reserved       = EXCLUDED.reserved,
                    updated_at     = NOW()
            """,
            chat_id, marketplace, product_id, product_name, warehouse_name or "", stock, reserved,
        )


async def get_low_stocks(chat_id: int, threshold: int = 20) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT marketplace, product_id, product_name, warehouse_name, stock, reserved
            FROM marketplace_stocks
            WHERE chat_id = $1 AND stock <= $2
            ORDER BY marketplace, product_id, stock ASC
            """,
            chat_id, threshold,
        )
        return [dict(r) for r in rows]


async def save_sale(
    chat_id: int,
    marketplace: str,
    order_id: str,
    product_id: str | None,
    product_name: str | None,
    quantity: int,
    price: float | None,
    commission: float | None,
    sale_date,
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO marketplace_sales
                (chat_id, marketplace, order_id, product_id, product_name,
                 quantity, price, commission, sale_date)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (marketplace, order_id) DO NOTHING
            """,
            chat_id, marketplace, order_id, product_id, product_name,
            quantity, price, commission, sale_date,
        )
        return result.split()[-1] != "0"


async def get_sales_summary(chat_id: int, days: int = 1) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                marketplace,
                product_name,
                COUNT(*)              AS orders,
                SUM(quantity)         AS total_qty,
                SUM(price)            AS revenue,
                SUM(commission)       AS commission
            FROM marketplace_sales
            WHERE chat_id = $1
              AND sale_date >= NOW() - ($2 || ' days')::interval
            GROUP BY marketplace, product_name
            ORDER BY marketplace, revenue DESC NULLS LAST
            """,
            chat_id, str(days),
        )
        return [dict(r) for r in rows]


async def get_sales_total(chat_id: int, days: int = 7) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                marketplace,
                COUNT(*)   AS orders,
                SUM(price) AS revenue
            FROM marketplace_sales
            WHERE chat_id = $1
              AND sale_date >= NOW() - ($2 || ' days')::interval
            GROUP BY marketplace
            ORDER BY marketplace
            """,
            chat_id, str(days),
        )
        return [dict(r) for r in rows]


async def reset_last_checked(chat_id: int) -> None:
    """Сбросить last_checked_at для всех магазинов пользователя (принудительная полная проверка)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE marketplace_shops SET last_checked_at = NULL WHERE chat_id = $1",
            chat_id,
        )


async def get_reviews_stats(owner_chat_id: int, days: int = 7) -> list[dict]:
    """Статистика по отзывам за N дней, сгруппированная по площадке."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                marketplace,
                COUNT(*)                                              AS total,
                ROUND(AVG(rating)::numeric, 2)                        AS avg_rating,
                COUNT(*) FILTER (WHERE status = 'auto_replied')       AS auto_replied,
                COUNT(*) FILTER (WHERE status = 'pending_approval')   AS pending,
                COUNT(*) FILTER (WHERE status = 'replied')            AS replied,
                COUNT(*) FILTER (WHERE status = 'skipped')            AS skipped
            FROM marketplace_reviews
            WHERE chat_id = $1
              AND created_at >= NOW() - ($2 || ' days')::interval
            GROUP BY marketplace
            ORDER BY marketplace
            """,
            owner_chat_id, str(days),
        )
        return [dict(r) for r in rows]


async def get_reviews_by_filter(
    owner_chat_id: int,
    marketplace: str | None = None,
    min_rating: int | None = None,
    max_rating: int | None = None,
    days: int = 7,
    limit: int = 20,
) -> list[dict]:
    """Список отзывов с опциональными фильтрами."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT marketplace, product_name, rating, text, status, created_at
            FROM marketplace_reviews
            WHERE chat_id = $1
              AND created_at >= NOW() - ($2 || ' days')::interval
              AND ($3::text IS NULL OR marketplace = $3)
              AND ($4::int  IS NULL OR rating >= $4)
              AND ($5::int  IS NULL OR rating <= $5)
            ORDER BY created_at DESC
            LIMIT $6
            """,
            owner_chat_id, str(days), marketplace, min_rating, max_rating, limit,
        )
        return [dict(r) for r in rows]


async def get_top_negative_products(
    owner_chat_id: int,
    days: int = 30,
    limit: int = 5,
) -> list[dict]:
    """Товары с наибольшим количеством отзывов 1-2★."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                product_name,
                COUNT(*)                       AS count,
                ROUND(AVG(rating)::numeric, 2) AS avg_rating
            FROM marketplace_reviews
            WHERE chat_id = $1
              AND rating <= 2
              AND created_at >= NOW() - ($2 || ' days')::interval
            GROUP BY product_name
            ORDER BY count DESC
            LIMIT $3
            """,
            owner_chat_id, str(days), limit,
        )
        return [dict(r) for r in rows]


async def get_distinct_digest_users() -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT added_by FROM digest_channels WHERE added_by IS NOT NULL"
        )
        return [r["added_by"] for r in rows]
