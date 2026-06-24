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
            ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parallel_group INTEGER DEFAULT NULL;
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
            ALTER TABLE marketplace_orders
            ADD COLUMN IF NOT EXISTS region TEXT NOT NULL DEFAULT ''
        """)
        await conn.execute("""
            ALTER TABLE marketplace_orders
            ADD COLUMN IF NOT EXISTS shop_id BIGINT NOT NULL DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS product_cards (
                id              SERIAL PRIMARY KEY,
                chat_id         BIGINT        NOT NULL,
                marketplace     TEXT          NOT NULL,
                product_id      TEXT          NOT NULL,
                title           TEXT,
                description     TEXT,
                characteristics JSONB,
                category        TEXT,
                fetched_at      TIMESTAMPTZ   DEFAULT NOW(),
                UNIQUE(chat_id, marketplace, product_id)
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
        await conn.execute("""
            ALTER TABLE product_mapping
            ADD COLUMN IF NOT EXISTS wb_nm_id TEXT
        """)
        await conn.execute("""
            ALTER TABLE product_mapping
            ADD COLUMN IF NOT EXISTS category TEXT
        """)
        await conn.execute("""
            ALTER TABLE product_mapping
            ADD COLUMN IF NOT EXISTS infographic_updated_at TIMESTAMPTZ
        """)
        await conn.execute("""
            ALTER TABLE product_mapping
            ADD COLUMN IF NOT EXISTS recommended_price_wb   NUMERIC(10,2),
            ADD COLUMN IF NOT EXISTS recommended_price_ozon NUMERIC(10,2)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS competitor_snapshots (
                id            SERIAL PRIMARY KEY,
                keyword       TEXT          NOT NULL,
                position      INT           NOT NULL,
                product_name  TEXT,
                brand         TEXT,
                price         NUMERIC(10,2),
                rating        NUMERIC(3,1),
                review_count  INT,
                marketplace   VARCHAR(10)   NOT NULL DEFAULT 'wb',
                snapshot_date DATE          NOT NULL DEFAULT CURRENT_DATE,
                UNIQUE(keyword, position, snapshot_date, marketplace)
            )
        """)

        # ── Ozon Performance credentials per-shop ──────────────────────────────
        await conn.execute("""
            ALTER TABLE marketplace_shops
            ADD COLUMN IF NOT EXISTS performance_client_id     TEXT,
            ADD COLUMN IF NOT EXISTS performance_client_secret TEXT
        """)

        # ── Multi-shop migration (поддержка нескольких Ozon-магазинов) ─────────

        # 1. Убрать UNIQUE (chat_id, marketplace) из marketplace_shops
        await conn.execute("""
            DO $$ BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_class t ON c.conrelid = t.oid
                    WHERE t.relname = 'marketplace_shops'
                    AND c.contype = 'u'
                    AND c.conname = 'marketplace_shops_chat_id_marketplace_key'
                ) THEN
                    ALTER TABLE marketplace_shops DROP CONSTRAINT marketplace_shops_chat_id_marketplace_key;
                END IF;
            END $$
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_marketplace_shops_multi
            ON marketplace_shops (chat_id, marketplace, COALESCE(client_id, ''))
        """)

        # 2. Добавить shop_id в критичные таблицы
        await conn.execute("""
            ALTER TABLE marketplace_stocks
            ADD COLUMN IF NOT EXISTS shop_id BIGINT NOT NULL DEFAULT 0
        """)
        await conn.execute("""
            ALTER TABLE marketplace_financial_report
            ADD COLUMN IF NOT EXISTS shop_id BIGINT NOT NULL DEFAULT 0
        """)
        await conn.execute("""
            ALTER TABLE marketplace_fin_adv
            ADD COLUMN IF NOT EXISTS shop_id BIGINT NOT NULL DEFAULT 0
        """)

        # 3. Заполнить shop_id существующих записей
        await conn.execute("""
            UPDATE marketplace_stocks ms
            SET shop_id = COALESCE(
                (SELECT id FROM marketplace_shops s
                 WHERE s.chat_id = ms.chat_id AND s.marketplace = ms.marketplace
                 LIMIT 1), 0)
            WHERE ms.shop_id = 0
        """)
        await conn.execute("""
            UPDATE marketplace_financial_report mfr
            SET shop_id = COALESCE(
                (SELECT id FROM marketplace_shops s
                 WHERE s.chat_id = mfr.chat_id AND s.marketplace = mfr.marketplace
                 LIMIT 1), 0)
            WHERE mfr.shop_id = 0
        """)
        await conn.execute("""
            UPDATE marketplace_fin_adv mfa
            SET shop_id = COALESCE(
                (SELECT id FROM marketplace_shops s
                 WHERE s.chat_id = mfa.chat_id AND s.marketplace = mfa.marketplace
                 LIMIT 1), 0)
            WHERE mfa.shop_id = 0
        """)

        # 4. Обновить UNIQUE-ограничения: включить shop_id
        await conn.execute("""
            DO $$
            DECLARE v_conname text;
            BEGIN
                SELECT c.conname INTO v_conname
                FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                WHERE t.relname = 'marketplace_stocks' AND c.contype = 'u'
                AND NOT EXISTS (
                    SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                    AND a.attname = 'shop_id'
                );
                IF v_conname IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE marketplace_stocks DROP CONSTRAINT ' || quote_ident(v_conname);
                    ALTER TABLE marketplace_stocks ADD CONSTRAINT marketplace_stocks_shop_product_wh_key
                        UNIQUE (shop_id, product_id, warehouse_name);
                END IF;
            END $$
        """)
        await conn.execute("""
            DO $$
            DECLARE v_conname text;
            BEGIN
                SELECT c.conname INTO v_conname
                FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                WHERE t.relname = 'marketplace_financial_report' AND c.contype = 'u'
                AND NOT EXISTS (
                    SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                    AND a.attname = 'shop_id'
                );
                IF v_conname IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE marketplace_financial_report DROP CONSTRAINT ' || quote_ident(v_conname);
                    ALTER TABLE marketplace_financial_report ADD CONSTRAINT marketplace_financial_report_shop_product_date_key
                        UNIQUE (shop_id, product_id, report_date);
                END IF;
            END $$
        """)
        await conn.execute("""
            DO $$
            DECLARE v_conname text;
            BEGIN
                SELECT c.conname INTO v_conname
                FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                WHERE t.relname = 'marketplace_fin_adv' AND c.contype = 'u'
                AND NOT EXISTS (
                    SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                    AND a.attname = 'shop_id'
                );
                IF v_conname IS NOT NULL THEN
                    EXECUTE 'ALTER TABLE marketplace_fin_adv DROP CONSTRAINT ' || quote_ident(v_conname);
                    ALTER TABLE marketplace_fin_adv ADD CONSTRAINT marketplace_fin_adv_shop_date_key
                        UNIQUE (shop_id, stat_date);
                END IF;
            END $$
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_plans (
                id          BIGSERIAL    PRIMARY KEY,
                chat_id     BIGINT       NOT NULL,
                title       TEXT         NOT NULL,
                notes       TEXT,
                priority    TEXT         NOT NULL DEFAULT 'medium',
                category    TEXT,
                deadline    DATE,
                status      TEXT         NOT NULL DEFAULT 'active',
                created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS user_plans_chat_status
            ON user_plans (chat_id, status)
        """)

        logger.info("[db] Схема готова ✓ (tasks + marketplace + funnel + snapshots + promotions + kpi + questions + keywords + returns + fin_adv + product_prices + wb_nm_id + category + product_cards + competitor_snapshots + multi-shop + user_plans)")

async def save_project(
    chat_id: int,
    name: str,
    chain_id: str | None = None,
) -> None:
    """Сохраняет или обновляет проект. Upsert по chat_id + name_lower."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO projects (chat_id, name, chain_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (chat_id, name_lower) DO UPDATE
                SET chain_id   = EXCLUDED.chain_id,
                    updated_at = NOW()
            """,
            chat_id, name, chain_id,
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
) -> int:
    """Добавить или обновить магазин. Возвращает id записи в marketplace_shops."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id FROM marketplace_shops
            WHERE chat_id = $1 AND marketplace = $2
              AND COALESCE(client_id, '') = COALESCE($3, '')
            """,
            chat_id, marketplace, client_id,
        )
        if existing:
            await conn.execute(
                """
                UPDATE marketplace_shops
                SET api_token = $1, shop_name = COALESCE($2, shop_name), is_active = true
                WHERE id = $3
                """,
                api_token, shop_name, existing["id"],
            )
            return existing["id"]
        row = await conn.fetchrow(
            """
            INSERT INTO marketplace_shops (chat_id, marketplace, api_token, client_id, shop_name)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            chat_id, marketplace, api_token, client_id, shop_name,
        )
        return row["id"]


async def set_performance_credentials(
    chat_id: int,
    ozon_client_id: str,
    performance_client_id: str,
    performance_client_secret: str,
) -> bool:
    """Привязать Ozon Performance credentials к конкретному магазину. Возвращает True если нашёл."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE marketplace_shops
            SET performance_client_id     = $1,
                performance_client_secret = $2
            WHERE chat_id = $3 AND marketplace = 'ozon' AND client_id = $4
            """,
            performance_client_id, performance_client_secret, chat_id, ozon_client_id,
        )
        return result.split()[-1] != "0"


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
    shop_id: int = 0,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO marketplace_stocks
                (chat_id, marketplace, product_id, product_name, warehouse_name, stock, reserved, shop_id, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            ON CONFLICT (shop_id, product_id, warehouse_name) DO UPDATE
                SET product_name   = EXCLUDED.product_name,
                    stock          = EXCLUDED.stock,
                    reserved       = EXCLUDED.reserved,
                    updated_at     = NOW()
            """,
            chat_id, marketplace, product_id, product_name, warehouse_name or "", stock, reserved, shop_id,
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


async def upsert_fin_adv(chat_id: int, marketplace: str, stat_date, adv_spend: float, shop_id: int = 0) -> None:
    """Сохранить/обновить суммарные рекламные расходы из финотчёта за день."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO marketplace_fin_adv (chat_id, marketplace, stat_date, adv_spend, shop_id, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (shop_id, stat_date) DO UPDATE
                SET adv_spend  = EXCLUDED.adv_spend,
                    updated_at = NOW()
            """,
            chat_id, marketplace, stat_date, adv_spend, shop_id,
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
            SELECT s.marketplace, s.product_id, s.warehouse_name, s.stock, s.reserved,
                   COALESCE(m.display_name, s.product_name, s.product_id) AS display_name
            FROM marketplace_stocks s
            LEFT JOIN product_mapping m
                   ON (s.marketplace = 'wb'   AND LOWER(REPLACE(m.wb_article, ',', '.'))
                                               = LOWER(REPLACE(s.product_id,  ',', '.')))
                   OR (s.marketplace = 'ozon' AND m.ozon_sku = s.product_id)
            WHERE s.chat_id = $1 AND s.stock <= $2
            ORDER BY s.marketplace, display_name, s.stock ASC
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
    region: str = '',
    shop_id: int = 0,
) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO marketplace_orders
                (chat_id, marketplace, order_id, product_id, product_name,
                 quantity, order_date, seller_price, region, shop_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (marketplace, order_id) DO UPDATE
                SET seller_price = EXCLUDED.seller_price,
                    shop_id      = EXCLUDED.shop_id,
                    region       = CASE WHEN marketplace_orders.region = '' AND EXCLUDED.region != ''
                                        THEN EXCLUDED.region ELSE marketplace_orders.region END
                WHERE marketplace_orders.seller_price IS NULL
                   OR (marketplace_orders.region = '' AND EXCLUDED.region != '')
            """,
            chat_id, marketplace, order_id, product_id, product_name,
            quantity, order_date, seller_price, region or '', shop_id,
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
    shop_id: int = 0,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO marketplace_financial_report
                (chat_id, marketplace, product_id, report_date,
                 quantity, revenue, payout, commission, logistics, storage, penalty, shop_id, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,NOW())
            ON CONFLICT (shop_id, product_id, report_date) DO UPDATE SET
                quantity   = EXCLUDED.quantity,
                revenue    = EXCLUDED.revenue,
                payout     = EXCLUDED.payout,
                commission = EXCLUDED.commission,
                logistics  = EXCLUDED.logistics,
                storage    = EXCLUDED.storage,
                penalty    = EXCLUDED.penalty,
                updated_at = NOW()
        """, chat_id, marketplace, product_id, report_date,
             quantity, revenue, payout, commission, logistics, storage, penalty, shop_id)


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


async def get_top_keywords_for_competitors(limit: int = 10) -> list[str]:
    """Топ ключей по search_count из product_search_keywords по всем магазинам.
    Используется для еженедельного снапшота цен конкурентов.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (keyword) keyword
            FROM product_search_keywords
            WHERE marketplace = 'wb' AND search_count IS NOT NULL AND search_count > 0
            ORDER BY keyword, search_count DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
    return [r["keyword"] for r in rows]



async def find_product_id_in_text(text: str) -> str | None:
    """Ищет в тексте wb_article, ozon_offer_id или display_name из product_mapping.

    Возвращает wb_nm_id (приоритет) или ozon_offer_id первого совпадения.
    Нужно чтобы Элина принимала "КБ50" или "Корм для кошек" вместо числового nm_id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT wb_article, wb_nm_id, ozon_offer_id, display_name
            FROM product_mapping
            WHERE wb_article IS NOT NULL OR ozon_offer_id IS NOT NULL
            """
        )

    text_lower = text.lower()
    # Сначала ищем по точным артикулам (wb_article, ozon_offer_id) — они короткие и уникальные
    for row in rows:
        for candidate in (row["wb_article"] or "", row["ozon_offer_id"] or ""):
            candidate = candidate.strip().lower()
            if candidate and len(candidate) >= 2 and candidate in text_lower:
                return row["wb_nm_id"] or row["ozon_offer_id"] or candidate

    # Затем по display_name (минимум 4 символа чтобы не было ложных совпадений)
    for row in rows:
        display = (row["display_name"] or "").strip().lower()
        if display and len(display) >= 4 and display in text_lower:
            return row["wb_nm_id"] or row["ozon_offer_id"] or display

    return None




# ── Карточки товаров ──────────────────────────────────────────────────────────

async def upsert_product_card(
    chat_id: int,
    marketplace: str,
    product_id: str,
    title: str | None,
    description: str | None,
    characteristics: list | None,
    category: str | None,
) -> None:
    import json as _json
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO product_cards
                (chat_id, marketplace, product_id, title, description, characteristics, category, fetched_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (chat_id, marketplace, product_id) DO UPDATE SET
                title           = EXCLUDED.title,
                description     = EXCLUDED.description,
                characteristics = EXCLUDED.characteristics,
                category        = EXCLUDED.category,
                fetched_at      = NOW()
            """,
            chat_id, marketplace, product_id,
            title, description,
            _json.dumps(characteristics or [], ensure_ascii=False),
            category,
        )


async def get_product_card(chat_id: int, marketplace: str, product_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM product_cards WHERE chat_id=$1 AND marketplace=$2 AND product_id=$3",
            chat_id, marketplace, product_id,
        )
        return dict(row) if row else None


async def get_seo_context(chat_id: int, product_id: str) -> dict:
    """SEO-контекст: текущая карточка + отзывы + воронка + исторические ключи.

    product_id — nm_id для WB или offer_id для Ozon.
    Возвращает dict с ключами: card, reviews, funnel, keywords.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        card_row = await conn.fetchrow(
            """
            SELECT * FROM product_cards
            WHERE chat_id=$1 AND product_id=$2
            ORDER BY fetched_at DESC LIMIT 1
            """,
            chat_id, product_id,
        )
        card = dict(card_row) if card_row else None

        review_rows = await conn.fetch(
            """
            SELECT text, rating, created_at
            FROM marketplace_reviews
            WHERE chat_id=$1 AND product_id=$2 AND text IS NOT NULL AND text != ''
            ORDER BY created_at DESC
            LIMIT 50
            """,
            chat_id, product_id,
        )
        reviews = [dict(r) for r in review_rows]

        funnel_row = await conn.fetchrow(
            """
            SELECT
                SUM(views)             AS total_views,
                SUM(add_to_cart)       AS total_cart,
                SUM(orders_count)      AS total_orders,
                AVG(avg_position)      AS avg_position,
                AVG(conv_view_to_cart) AS avg_ctr,
                MAX(stat_date)         AS last_date
            FROM product_funnel_stats
            WHERE chat_id=$1 AND product_id=$2
              AND stat_date >= CURRENT_DATE - INTERVAL '30 days'
            """,
            chat_id, product_id,
        )
        funnel = dict(funnel_row) if funnel_row else {}

        kw_rows = await conn.fetch(
            """
            SELECT keyword, position, search_count, stat_date
            FROM product_search_keywords
            WHERE chat_id=$1 AND product_id=$2
            ORDER BY search_count DESC NULLS LAST
            LIMIT 20
            """,
            chat_id, product_id,
        )
        keywords = [dict(r) for r in kw_rows]

    return {
        "card": card,
        "reviews": reviews,
        "funnel": funnel,
        "keywords": keywords,
    }


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


# ── Снапшоты цен конкурентов ───────────────────────────────────────────────────

async def upsert_competitor_snapshot(rows: list[dict]) -> None:
    """Сохраняет снапшот цен конкурентов. rows: список с ключами из get_competitor_prices."""
    if not rows:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO competitor_snapshots
                (keyword, position, product_name, brand, price, rating, review_count, marketplace, snapshot_date)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (keyword, position, snapshot_date, marketplace) DO UPDATE SET
                product_name = EXCLUDED.product_name,
                brand        = EXCLUDED.brand,
                price        = EXCLUDED.price,
                rating       = EXCLUDED.rating,
                review_count = EXCLUDED.review_count
            """,
            [
                (
                    r["keyword"], r["position"], r.get("product_name"), r.get("brand"),
                    r.get("price"), r.get("rating"), r.get("review_count"),
                    r.get("marketplace", "wb"), r["snapshot_date"],
                )
                for r in rows
            ],
        )


async def save_price_recommendations(chat_id: int, items: list[dict]) -> None:
    """Сохранить рекомендованные цены от Питера в product_mapping.

    items: [{"display_name": str, "recommended_price_wb": float|None,
              "recommended_price_ozon": float|None}]
    Сопоставление идёт по display_name / wb_article.
    """
    if not items:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        for item in items:
            name = item.get("display_name") or item.get("product_name") or ""
            rec_wb   = item.get("recommended_price_wb")
            rec_ozon = item.get("recommended_price_ozon")
            if rec_wb is None and rec_ozon is None:
                continue
            await conn.execute(
                """
                UPDATE product_mapping
                   SET recommended_price_wb   = COALESCE($3, recommended_price_wb),
                       recommended_price_ozon = COALESCE($4, recommended_price_ozon)
                 WHERE chat_id = $1
                   AND (display_name = $2 OR wb_article = $2)
                """,
                chat_id, name, rec_wb, rec_ozon,
            )


async def get_price_recommendations(chat_id: int) -> list[dict]:
    """Товары с ненулевыми рекомендациями цен от Питера."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                COALESCE(display_name, wb_article, ozon_sku) AS name,
                wb_article,
                ozon_sku,
                wb_nm_id,
                wb_price,
                ozon_price,
                recommended_price_wb,
                recommended_price_ozon
            FROM product_mapping
            WHERE chat_id = $1
              AND (recommended_price_wb IS NOT NULL OR recommended_price_ozon IS NOT NULL)
            ORDER BY name
            """,
            chat_id,
        )
    return [dict(r) for r in rows]


async def clear_price_recommendations(chat_id: int, marketplace: str) -> None:
    """Обнулить рекомендации после успешного применения цен."""
    pool = await get_pool()
    col = "recommended_price_wb" if marketplace == "wb" else "recommended_price_ozon"
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE product_mapping SET {col} = NULL WHERE chat_id = $1",
            chat_id,
        )


async def create_user_plan(
    chat_id: int,
    title: str,
    notes: str | None = None,
    priority: str = "medium",
    category: str | None = None,
    deadline: str | None = None,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO user_plans (chat_id, title, notes, priority, category, deadline)
            VALUES ($1, $2, $3, $4, $5, $6::date)
            RETURNING id
            """,
            chat_id, title, notes, priority, category, deadline,
        )
    return row["id"]


async def get_user_plans(chat_id: int, status_filter: str = "active") -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status_filter == "all":
            rows = await conn.fetch(
                "SELECT * FROM user_plans WHERE chat_id=$1 ORDER BY priority DESC, created_at DESC",
                chat_id,
            )
        elif status_filter in ("done", "archived", "in_progress"):
            rows = await conn.fetch(
                "SELECT * FROM user_plans WHERE chat_id=$1 AND status=$2 ORDER BY updated_at DESC",
                chat_id, status_filter,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM user_plans WHERE chat_id=$1 AND status IN ('active','in_progress') ORDER BY priority DESC, deadline NULLS LAST, created_at DESC",
                chat_id,
            )
    return [dict(r) for r in rows]


async def update_user_plan(plan_id: int, chat_id: int, **kwargs) -> bool:
    allowed = {"title", "notes", "priority", "category", "deadline", "status"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False
    pool = await get_pool()
    set_parts = [f"{k} = ${i+3}" for i, k in enumerate(fields)]
    set_parts.append("updated_at = NOW()")
    sql = f"UPDATE user_plans SET {', '.join(set_parts)} WHERE id=$1 AND chat_id=$2"
    async with pool.acquire() as conn:
        result = await conn.execute(sql, plan_id, chat_id, *fields.values())
    return result != "UPDATE 0"


async def delete_user_plan(plan_id: int, chat_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM user_plans WHERE id=$1 AND chat_id=$2", plan_id, chat_id
        )
    return result != "DELETE 0"


async def get_competitor_snapshots(weeks: int = 4) -> list[dict]:
    """Медиана и диапазон цен по ключевым словам за последние N недель."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                keyword,
                snapshot_date,
                marketplace,
                COUNT(*)                                                          AS product_count,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)::numeric, 0) AS median_price,
                ROUND(AVG(price)::numeric, 0)                                    AS avg_price,
                MIN(price)                                                        AS min_price,
                MAX(price)                                                        AS max_price
            FROM competitor_snapshots
            WHERE snapshot_date >= CURRENT_DATE - ($1 * 7)
            GROUP BY keyword, snapshot_date, marketplace
            ORDER BY keyword, snapshot_date DESC
            """,
            weeks,
        )
    return [dict(r) for r in rows]
