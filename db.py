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
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(10,6);
        """)
        await conn.execute("""
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS latency_ms INT;
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
            CREATE TABLE IF NOT EXISTS marketplace_orders (
                id           BIGSERIAL PRIMARY KEY,
                chat_id      BIGINT        NOT NULL,
                marketplace  TEXT          NOT NULL,
                order_id     TEXT          NOT NULL,
                product_id   TEXT,
                product_name TEXT,
                quantity     INTEGER       NOT NULL DEFAULT 1,
                seller_price NUMERIC(10,2),
                order_date   TIMESTAMPTZ,
                created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
                UNIQUE (marketplace, order_id)
            );
        """)
        await conn.execute("""
            ALTER TABLE marketplace_orders
            ADD COLUMN IF NOT EXISTS seller_price NUMERIC(10,2)
        """)
        await conn.execute("""
            ALTER TABLE marketplace_orders
            DROP COLUMN IF EXISTS price
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS product_costs (
                wb_article  TEXT PRIMARY KEY,
                cost        NUMERIC NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS product_adv_stats (
                id           SERIAL PRIMARY KEY,
                chat_id      BIGINT       NOT NULL,
                marketplace  VARCHAR(10)  NOT NULL,
                product_id   VARCHAR(50)  NOT NULL,
                campaign_id  VARCHAR(50),
                stat_date    DATE         NOT NULL,
                views        BIGINT       DEFAULT 0,
                clicks       BIGINT       DEFAULT 0,
                ctr          NUMERIC(6,4) DEFAULT 0,
                spend        NUMERIC(12,2) DEFAULT 0,
                orders_count INTEGER      DEFAULT 0,
                updated_at   TIMESTAMPTZ  DEFAULT now(),
                UNIQUE(chat_id, marketplace, product_id, stat_date)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tender_opportunities (
                id                      BIGSERIAL PRIMARY KEY,
                tender_id               TEXT        NOT NULL UNIQUE,
                title                   TEXT,
                nmck                    NUMERIC,
                region                  TEXT,
                status                  TEXT,
                submission_deadline     TIMESTAMPTZ,
                lot_description         TEXT,
                supplier_price_estimate NUMERIC,
                expected_winning_price  NUMERIC,
                margin_estimate         NUMERIC,
                recommendation          TEXT,
                analysis_json           JSONB,
                chat_id                 BIGINT,
                created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tender_opp_chat_rec
                ON tender_opportunities (chat_id, recommendation, created_at DESC);
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS task_events (
                id          BIGSERIAL PRIMARY KEY,
                task_id     BIGINT,
                chain_id    TEXT,
                agent_key   TEXT,
                event_type  TEXT        NOT NULL,
                payload     JSONB,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_events_task
                ON task_events (task_id, created_at);
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_events_chain
                ON task_events (chain_id, created_at)
                WHERE chain_id IS NOT NULL;
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_financial_report (
                id           BIGSERIAL PRIMARY KEY,
                chat_id      BIGINT        NOT NULL,
                marketplace  TEXT          NOT NULL,
                product_id   TEXT          NOT NULL,
                report_date  DATE          NOT NULL,
                quantity     INT           DEFAULT 0,
                revenue      NUMERIC(12,2) DEFAULT 0,
                payout       NUMERIC(12,2) DEFAULT 0,
                commission   NUMERIC(12,2) DEFAULT 0,
                logistics    NUMERIC(12,2) DEFAULT 0,
                storage      NUMERIC(12,2) DEFAULT 0,
                penalty      NUMERIC(12,2) DEFAULT 0,
                updated_at   TIMESTAMPTZ   DEFAULT NOW(),
                UNIQUE(chat_id, marketplace, product_id, report_date)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS product_funnel_stats (
                id                 BIGSERIAL PRIMARY KEY,
                chat_id            BIGINT      NOT NULL,
                marketplace        TEXT        NOT NULL,
                product_id         TEXT        NOT NULL,
                stat_date          DATE        NOT NULL,
                views              INT         DEFAULT 0,
                add_to_cart        INT         DEFAULT 0,
                orders_count       INT         DEFAULT 0,
                buyouts            INT         DEFAULT 0,
                avg_position       NUMERIC(6,1),
                conv_view_to_cart  NUMERIC(5,2),
                conv_cart_to_order NUMERIC(5,2),
                updated_at         TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(chat_id, marketplace, product_id, stat_date)
            )
        """)
        await conn.execute("""
            ALTER TABLE marketplace_sales
            ADD COLUMN IF NOT EXISTS is_return BOOLEAN DEFAULT FALSE
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_revenue_snapshot (
                id            BIGSERIAL PRIMARY KEY,
                snapshot_date DATE          NOT NULL,
                chat_id       BIGINT        NOT NULL,
                marketplace   TEXT          NOT NULL,
                revenue       NUMERIC(12,2) DEFAULT 0,
                orders_count  INT           DEFAULT 0,
                avg_price     NUMERIC(10,2) DEFAULT 0,
                UNIQUE(snapshot_date, chat_id, marketplace)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_history_daily (
                id             BIGSERIAL PRIMARY KEY,
                snapshot_date  DATE          NOT NULL,
                chat_id        BIGINT        NOT NULL,
                marketplace    TEXT          NOT NULL,
                product_id     TEXT          NOT NULL,
                warehouse_name TEXT          NOT NULL DEFAULT '',
                stock          INT           DEFAULT 0,
                UNIQUE(snapshot_date, chat_id, marketplace, product_id, warehouse_name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_promotions (
                id             BIGSERIAL PRIMARY KEY,
                chat_id        BIGINT        NOT NULL,
                marketplace    TEXT          NOT NULL,
                promotion_id   TEXT          NOT NULL,
                title          TEXT          NOT NULL DEFAULT '',
                discount_pct   NUMERIC(5,2)  DEFAULT 0,
                start_date     DATE,
                end_date       DATE,
                product_ids    JSONB         DEFAULT '[]',
                synced_at      TIMESTAMPTZ   DEFAULT NOW(),
                UNIQUE(chat_id, marketplace, promotion_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS shop_kpi_snapshots (
                id               BIGSERIAL PRIMARY KEY,
                chat_id          BIGINT        NOT NULL,
                marketplace      TEXT          NOT NULL,
                snapshot_date    DATE          NOT NULL,
                rating           NUMERIC(4,2),
                return_pct       NUMERIC(5,2),
                cancellation_pct NUMERIC(5,2),
                penalty_count    INT           DEFAULT 0,
                extra_data       JSONB         DEFAULT '{}',
                UNIQUE(snapshot_date, chat_id, marketplace)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_questions (
                id               BIGSERIAL PRIMARY KEY,
                chat_id          BIGINT        NOT NULL,
                marketplace      TEXT          NOT NULL DEFAULT 'ozon',
                question_id      TEXT          NOT NULL,
                product_id       TEXT,
                product_name     TEXT,
                question_text    TEXT,
                status           TEXT          NOT NULL DEFAULT 'new',
                generated_answer TEXT,
                final_answer     TEXT,
                created_at       TIMESTAMPTZ,
                answered_at      TIMESTAMPTZ,
                UNIQUE(marketplace, question_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS product_search_keywords (
                chat_id      BIGINT        NOT NULL,
                marketplace  TEXT          NOT NULL,
                product_id   TEXT          NOT NULL,
                keyword      TEXT          NOT NULL,
                position     INT,
                search_count BIGINT,
                ctr          NUMERIC(6,4),
                conv_rate    NUMERIC(6,4),
                stat_date    DATE          NOT NULL,
                synced_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
                UNIQUE(chat_id, marketplace, product_id, keyword, stat_date)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS product_returns_analytics (
                chat_id       BIGINT         NOT NULL,
                marketplace   TEXT           NOT NULL,
                product_id    TEXT           NOT NULL,
                product_name  TEXT,
                stat_date     DATE           NOT NULL,
                returns_count INT            NOT NULL DEFAULT 0,
                return_amount NUMERIC(12,2)  NOT NULL DEFAULT 0,
                return_rate   NUMERIC(6,4),
                synced_at     TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
                UNIQUE(chat_id, marketplace, product_id, stat_date)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_fin_adv (
                id          SERIAL PRIMARY KEY,
                chat_id     BIGINT        NOT NULL,
                marketplace VARCHAR(10)   NOT NULL,
                stat_date   DATE          NOT NULL,
                adv_spend   NUMERIC(12,2) DEFAULT 0,
                updated_at  TIMESTAMPTZ   DEFAULT NOW(),
                UNIQUE(chat_id, marketplace, stat_date)
            )
        """)
        await conn.execute("""
            ALTER TABLE product_mapping
            ADD COLUMN IF NOT EXISTS wb_price   NUMERIC(10,2),
            ADD COLUMN IF NOT EXISTS ozon_price NUMERIC(10,2),
            ADD COLUMN IF NOT EXISTS prices_updated_at TIMESTAMPTZ
        """)
        logger.info("[db] Схема готова ✓ (tasks + marketplace + funnel + snapshots + promotions + kpi + questions + keywords + returns + fin_adv + product_prices)")

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


async def upsert_ad_stat(
    chat_id: int, marketplace: str, campaign_id: str, campaign_name: str,
    stat_date: str, views: int, clicks: int, ctr: float, spend: float,
) -> None:
    """Сохранить/обновить статистику рекламной кампании за день."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO marketplace_adv_stats
                (chat_id, marketplace, campaign_id, campaign_name, stat_date,
                 views, clicks, ctr, spend, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            ON CONFLICT (chat_id, marketplace, campaign_id, stat_date) DO UPDATE
                SET campaign_name = EXCLUDED.campaign_name,
                    views         = EXCLUDED.views,
                    clicks        = EXCLUDED.clicks,
                    ctr           = EXCLUDED.ctr,
                    spend         = EXCLUDED.spend,
                    updated_at    = NOW()
            """,
            chat_id, marketplace, campaign_id, campaign_name,
            stat_date, views, clicks, ctr, spend,
        )


async def upsert_fin_adv(chat_id: int, marketplace: str, stat_date, adv_spend: float) -> None:
    """Сохранить/обновить суммарные рекламные расходы из финотчёта за день."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO marketplace_fin_adv (chat_id, marketplace, stat_date, adv_spend, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (chat_id, marketplace, stat_date) DO UPDATE
                SET adv_spend  = EXCLUDED.adv_spend,
                    updated_at = NOW()
            """,
            chat_id, marketplace, stat_date, adv_spend,
        )


async def upsert_product_ad_stat(
    chat_id: int, marketplace: str, product_id: str, campaign_id: str | None,
    stat_date, views: int, clicks: int, ctr: float, spend: float,
    orders_count: int = 0,
) -> None:
    """Сохранить/обновить рекламную статистику на уровне товара за день."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_adv_stats
                (chat_id, marketplace, product_id, campaign_id, stat_date,
                 views, clicks, ctr, spend, orders_count, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            ON CONFLICT (chat_id, marketplace, product_id, stat_date) DO UPDATE
                SET campaign_id   = COALESCE(EXCLUDED.campaign_id, product_adv_stats.campaign_id),
                    views         = product_adv_stats.views + EXCLUDED.views,
                    clicks        = product_adv_stats.clicks + EXCLUDED.clicks,
                    ctr           = CASE WHEN (product_adv_stats.views + EXCLUDED.views) > 0
                                         THEN ROUND((product_adv_stats.clicks + EXCLUDED.clicks)::numeric
                                              / (product_adv_stats.views + EXCLUDED.views) * 100, 4)
                                         ELSE 0 END,
                    spend         = product_adv_stats.spend + EXCLUDED.spend,
                    orders_count  = product_adv_stats.orders_count + EXCLUDED.orders_count,
                    updated_at    = NOW()
            """,
            chat_id, marketplace, product_id, campaign_id,
            stat_date, views, clicks, ctr, spend, orders_count,
        )


async def cleanup_old_stocks(chat_id: int, marketplace: str) -> int:
    """Удалить записи где product_id состоит только из цифр (старые nmId)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM marketplace_stocks WHERE chat_id=$1 AND marketplace=$2 AND product_id ~ '^\\d+$'",
            chat_id, marketplace,
        )
    deleted = int(result.split()[-1]) if result else 0
    return deleted


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
    is_return: bool = False,
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO marketplace_sales
                (chat_id, marketplace, order_id, product_id, product_name,
                 quantity, price, commission, sale_date, is_return)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (marketplace, order_id) DO NOTHING
            """,
            chat_id, marketplace, order_id, product_id, product_name,
            quantity, price, commission, sale_date, is_return,
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


async def save_order(
    chat_id: int,
    marketplace: str,
    order_id: str,
    product_id: str | None,
    product_name: str | None,
    quantity: int,
    order_date,
    seller_price: float | None = None,
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO marketplace_orders
                (chat_id, marketplace, order_id, product_id, product_name,
                 quantity, order_date, seller_price)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (marketplace, order_id) DO UPDATE
                SET seller_price = EXCLUDED.seller_price
                WHERE marketplace_orders.seller_price IS NULL
                  AND EXCLUDED.seller_price IS NOT NULL
            """,
            chat_id, marketplace, order_id, product_id, product_name,
            quantity, order_date, seller_price,
        )
        return result.split()[-1] != "0"


async def get_orders_summary(chat_id: int, date_from, date_to) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        debug_rows = await conn.fetch(
            """
            SELECT marketplace, COUNT(*) AS row_count, SUM(quantity) AS total_qty,
                   MIN(order_date) AS min_date, MAX(order_date) AS max_date
            FROM marketplace_orders
            WHERE chat_id = $1 AND order_date >= $2 AND order_date < $3
            GROUP BY marketplace
            """,
            chat_id, date_from, date_to,
        )
        from loguru import logger as _log
        for r in debug_rows:
            _log.debug(
                f"[get_orders_summary] mp={r['marketplace']} rows={r['row_count']} "
                f"qty={r['total_qty']} min={r['min_date']} max={r['max_date']}"
            )
        rows = await conn.fetch(
            """
            SELECT marketplace, SUM(quantity) AS orders, SUM(seller_price * quantity) AS revenue
            FROM marketplace_orders
            WHERE chat_id = $1 AND order_date >= $2 AND order_date < $3
            GROUP BY marketplace
            ORDER BY marketplace
            """,
            chat_id, date_from, date_to,
        )
        return [dict(r) for r in rows]


async def get_sales_period(chat_id: int, date_from, date_to) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT marketplace, COUNT(*) AS orders, SUM(price) AS revenue
            FROM marketplace_sales
            WHERE chat_id = $1 AND sale_date >= $2 AND sale_date < $3
            GROUP BY marketplace
            ORDER BY marketplace
            """,
            chat_id, date_from, date_to,
        )
        return [dict(r) for r in rows]


async def get_orders_days_count(chat_id: int, date_from, date_to) -> int:
    """Количество дней с заказами в периоде (по всем площадкам)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(DISTINCT DATE(order_date AT TIME ZONE 'UTC')) AS days
            FROM marketplace_orders
            WHERE chat_id = $1 AND order_date >= $2 AND order_date < $3
            """,
            chat_id, date_from, date_to,
        )
    return int(row["days"]) if row else 0


async def get_orders_total(chat_id: int, days: int = 7) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT marketplace, SUM(quantity) AS orders, SUM(seller_price * quantity) AS revenue
            FROM marketplace_orders
            WHERE chat_id = $1
              AND order_date >= NOW() - ($2 || ' days')::interval
            GROUP BY marketplace
            ORDER BY marketplace
            """,
            chat_id, str(days),
        )
        return [dict(r) for r in rows]


async def clear_ozon_numeric_stocks(chat_id: int) -> int:
    """Удалить записи остатков Ozon где product_id состоит только из цифр (старые SKU)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM marketplace_stocks WHERE chat_id = $1 AND marketplace = 'ozon' AND (product_id ~ '^\\d+$' OR length(product_id) > 20)",
            chat_id,
        )
    return int(result.split()[-1]) if result else 0


async def clear_ozon_analytics(chat_id: int, date_from, date_to) -> int:
    """Удалить аналитические записи Ozon за период перед пересохранением."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM marketplace_orders
            WHERE chat_id = $1 AND marketplace = 'ozon'
              AND order_id LIKE 'ozon_analytics_%'
              AND order_date >= $2 AND order_date < $3
            """,
            chat_id, date_from, date_to,
        )
    return int(result.split()[-1]) if result else 0


async def clear_orders(chat_id: int, marketplace: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM marketplace_orders WHERE chat_id = $1 AND marketplace = $2",
            chat_id, marketplace,
        )
    return int(result.split()[-1]) if result else 0


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


async def log_event(
    event_type: str,
    task_id: int | None = None,
    agent_key: str | None = None,
    chain_id: str | None = None,
    payload: dict | None = None,
) -> None:
    """Записать событие в task_events. Ошибки подавляются — трейсинг не должен ломать основной поток."""
    import json as _json
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO task_events (task_id, chain_id, agent_key, event_type, payload)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                task_id,
                chain_id,
                agent_key,
                event_type,
                _json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
            )
    except Exception as e:
        logger.debug(f"[log_event] {event_type} task_id={task_id}: {e}")


async def upsert_financial_report(
    chat_id: int,
    marketplace: str,
    product_id: str,
    report_date,
    quantity: int = 0,
    revenue: float = 0,
    payout: float = 0,
    commission: float = 0,
    logistics: float = 0,
    storage: float = 0,
    penalty: float = 0,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO marketplace_financial_report
                (chat_id, marketplace, product_id, report_date,
                 quantity, revenue, payout, commission, logistics, storage, penalty, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())
            ON CONFLICT (chat_id, marketplace, product_id, report_date) DO UPDATE SET
                quantity   = EXCLUDED.quantity,
                revenue    = EXCLUDED.revenue,
                payout     = EXCLUDED.payout,
                commission = EXCLUDED.commission,
                logistics  = EXCLUDED.logistics,
                storage    = EXCLUDED.storage,
                penalty    = EXCLUDED.penalty,
                updated_at = NOW()
        """, chat_id, marketplace, product_id, report_date,
             quantity, revenue, payout, commission, logistics, storage, penalty)


async def upsert_funnel_stat(
    chat_id: int,
    marketplace: str,
    product_id: str,
    stat_date,
    views: int = 0,
    add_to_cart: int = 0,
    orders_count: int = 0,
    buyouts: int = 0,
    avg_position: float | None = None,
    conv_view_to_cart: float | None = None,
    conv_cart_to_order: float | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO product_funnel_stats
                (chat_id, marketplace, product_id, stat_date,
                 views, add_to_cart, orders_count, buyouts,
                 avg_position, conv_view_to_cart, conv_cart_to_order, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,NOW())
            ON CONFLICT (chat_id, marketplace, product_id, stat_date) DO UPDATE SET
                views              = EXCLUDED.views,
                add_to_cart        = EXCLUDED.add_to_cart,
                orders_count       = EXCLUDED.orders_count,
                buyouts            = EXCLUDED.buyouts,
                avg_position       = EXCLUDED.avg_position,
                conv_view_to_cart  = EXCLUDED.conv_view_to_cart,
                conv_cart_to_order = EXCLUDED.conv_cart_to_order,
                updated_at         = NOW()
        """, chat_id, marketplace, product_id, stat_date,
             views, add_to_cart, orders_count, buyouts,
             avg_position, conv_view_to_cart, conv_cart_to_order)


async def get_distinct_digest_users() -> list[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT added_by FROM digest_channels WHERE added_by IS NOT NULL"
        )
        return [r["added_by"] for r in rows]


async def upsert_daily_snapshot(
    snapshot_date,
    chat_id: int,
    marketplace: str,
    revenue: float,
    orders_count: int,
    avg_price: float,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO daily_revenue_snapshot
                (snapshot_date, chat_id, marketplace, revenue, orders_count, avg_price)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (snapshot_date, chat_id, marketplace) DO UPDATE SET
                revenue      = EXCLUDED.revenue,
                orders_count = EXCLUDED.orders_count,
                avg_price    = EXCLUDED.avg_price
        """, snapshot_date, chat_id, marketplace, revenue, orders_count, avg_price)


async def upsert_stock_history(
    snapshot_date,
    chat_id: int,
    marketplace: str,
    product_id: str,
    warehouse_name: str,
    stock: int,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO stock_history_daily
                (snapshot_date, chat_id, marketplace, product_id, warehouse_name, stock)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (snapshot_date, chat_id, marketplace, product_id, warehouse_name) DO UPDATE SET
                stock = EXCLUDED.stock
        """, snapshot_date, chat_id, marketplace, product_id, warehouse_name or "", stock)


async def upsert_promotion(
    chat_id: int,
    marketplace: str,
    promotion_id: str,
    title: str,
    discount_pct: float,
    start_date,
    end_date,
    product_ids: list,
) -> None:
    import json as _json
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO marketplace_promotions
                (chat_id, marketplace, promotion_id, title, discount_pct, start_date, end_date, product_ids, synced_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            ON CONFLICT (chat_id, marketplace, promotion_id) DO UPDATE SET
                title        = EXCLUDED.title,
                discount_pct = EXCLUDED.discount_pct,
                start_date   = EXCLUDED.start_date,
                end_date     = EXCLUDED.end_date,
                product_ids  = EXCLUDED.product_ids,
                synced_at    = NOW()
        """, chat_id, marketplace, promotion_id, title or "",
             float(discount_pct or 0), start_date, end_date,
             _json.dumps(product_ids or []))


async def upsert_shop_kpi(
    chat_id: int,
    marketplace: str,
    snapshot_date,
    rating: float | None,
    return_pct: float | None,
    cancellation_pct: float | None,
    penalty_count: int,
    extra_data: dict,
) -> None:
    import json as _json
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO shop_kpi_snapshots
                (chat_id, marketplace, snapshot_date, rating, return_pct, cancellation_pct, penalty_count, extra_data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (snapshot_date, chat_id, marketplace) DO UPDATE SET
                rating           = EXCLUDED.rating,
                return_pct       = EXCLUDED.return_pct,
                cancellation_pct = EXCLUDED.cancellation_pct,
                penalty_count    = EXCLUDED.penalty_count,
                extra_data       = EXCLUDED.extra_data
        """, chat_id, marketplace, snapshot_date,
             rating, return_pct, cancellation_pct, penalty_count or 0,
             _json.dumps(extra_data or {}))


# ── Вопросы покупателей ────────────────────────────────────────────────────────

async def save_question(
    chat_id: int,
    marketplace: str,
    question_id: str,
    product_id: str | None,
    product_name: str | None,
    question_text: str | None,
    created_at=None,
) -> bool:
    """INSERT нового вопроса. Возвращает True если запись новая."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO marketplace_questions
                (chat_id, marketplace, question_id, product_id, product_name, question_text, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (marketplace, question_id) DO NOTHING
            """,
            chat_id, marketplace, question_id, product_id, product_name, question_text, created_at,
        )
        return result.split()[-1] != "0"


async def update_question_status(
    marketplace: str,
    question_id: str,
    status: str,
    generated_answer: str | None = None,
    final_answer: str | None = None,
    answered_at=None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE marketplace_questions
               SET status           = $3,
                   generated_answer = COALESCE($4, generated_answer),
                   final_answer     = COALESCE($5, final_answer),
                   answered_at      = COALESCE($6, answered_at)
             WHERE marketplace = $1 AND question_id = $2
            """,
            marketplace, question_id, status, generated_answer, final_answer, answered_at,
        )


async def get_pending_questions(chat_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM marketplace_questions
             WHERE chat_id = $1 AND status = 'pending_approval'
             ORDER BY created_at
            """,
            chat_id,
        )
        return [dict(r) for r in rows]


# ── Ключевые слова WB ──────────────────────────────────────────────────────────

async def upsert_search_keyword(
    chat_id: int,
    marketplace: str,
    product_id: str,
    keyword: str,
    position: int | None,
    search_count: int | None,
    ctr: float | None,
    conv_rate: float | None,
    stat_date,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_search_keywords
                (chat_id, marketplace, product_id, keyword, position, search_count, ctr, conv_rate, stat_date, synced_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
            ON CONFLICT (chat_id, marketplace, product_id, keyword, stat_date) DO UPDATE SET
                position     = EXCLUDED.position,
                search_count = EXCLUDED.search_count,
                ctr          = EXCLUDED.ctr,
                conv_rate    = EXCLUDED.conv_rate,
                synced_at    = NOW()
            """,
            chat_id, marketplace, product_id, keyword,
            position, search_count, ctr, conv_rate, stat_date,
        )


async def get_keywords_top(
    chat_id: int,
    marketplace: str = "wb",
    product_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Топ ключевых слов по search_count. Если product_id задан — только по нему."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if product_id:
            rows = await conn.fetch(
                """
                SELECT product_id, product_name, keyword, position, search_count, ctr, conv_rate, stat_date
                FROM product_search_keywords
                WHERE chat_id=$1 AND marketplace=$2 AND product_id=$3
                ORDER BY search_count DESC NULLS LAST
                LIMIT $4
                """,
                chat_id, marketplace, product_id, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (keyword) product_id, product_name, keyword, position, search_count, ctr, conv_rate, stat_date
                FROM product_search_keywords
                WHERE chat_id=$1 AND marketplace=$2
                ORDER BY keyword, search_count DESC NULLS LAST
                LIMIT $3
                """,
                chat_id, marketplace, limit,
            )
        return [dict(r) for r in rows]


# ── Аналитика возвратов ────────────────────────────────────────────────────────

async def upsert_returns_analytics(
    chat_id: int,
    marketplace: str,
    product_id: str,
    product_name: str | None,
    stat_date,
    returns_count: int,
    return_amount: float,
    return_rate: float | None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_returns_analytics
                (chat_id, marketplace, product_id, product_name, stat_date,
                 returns_count, return_amount, return_rate, synced_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            ON CONFLICT (chat_id, marketplace, product_id, stat_date) DO UPDATE SET
                product_name  = EXCLUDED.product_name,
                returns_count = EXCLUDED.returns_count,
                return_amount = EXCLUDED.return_amount,
                return_rate   = EXCLUDED.return_rate,
                synced_at     = NOW()
            """,
            chat_id, marketplace, product_id, product_name or "", stat_date,
            returns_count or 0, float(return_amount or 0), return_rate,
        )
