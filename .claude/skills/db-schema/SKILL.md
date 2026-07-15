---
name: db-schema
description: >
  Используй этот skill при работе с базой данных проекта:
  создание/изменение таблиц, написание SQL-запросов, работа с VIEW,
  task queue, product_mapping, marketplace_orders, marketplace_adv_stats,
  stocks, reviews, sales, финотчёты, воронка, снимки истории.
  Также при подключении к Railway Postgres.
---

# Схема базы данных AI Office

## Подключение

- Использовать `DATABASE_PUBLIC_URL` (не `DATABASE_URL`)
- Railway Console: вкладка Console для SQL, не Data

## Task Queue (Марта)

```sql
tasks
  id, agent_key, task_type, payload (JSONB)
  status: queued → acknowledged → running → completed/failed/timeout
  priority (0/10/20), correlation_id, retry_count, remind_at
  chain_id, chain_index, chain_total, chain_plan (JSONB)
  notion_page_id  -- прокидывается через всю цепочку
  SELECT FOR UPDATE SKIP LOCKED  -- без race conditions
```

## Агент Макс — таблицы

```sql
marketplace_shops        -- магазины: api_token, statistics_token, client_id
marketplace_reviews      -- отзывы: статус, generated_reply, final_reply
marketplace_orders       -- заказы WB (wb_article) + Ozon (product_id = SKU, числовой!)
marketplace_sales        -- выкупы WB + Ozon (product_id = offer_id, строка!) delivered
                         --   is_return BOOLEAN DEFAULT FALSE
                         --   WB: saleID=S... → продажа, saleID=R... → возврат
marketplace_stocks       -- остатки WB (supplierArticle) + Ozon (product_id = offer_id, строка!), текущий снимок
marketplace_adv_stats    -- рекламная статистика WB и Ozon (кампания/день)

wb_campaigns             -- названия WB кампаний (вручную — API 404)

product_mapping          -- реестр товаров
  wb_article             -- nullable, supplierArticle (строка, напр. "КБ50")
  wb_nm_id               -- nullable, WB nmId (числовая строка). Заполняется
                         --   автоматически через Content API при /sync_adv.
                         --   Нужен для join с product_adv_stats (fullstats отдаёт nmId, не wb_article)
  ozon_offer_id          -- nullable
  ozon_sku               -- nullable
  display_name           -- UNIQUE ГЛОБАЛЬНО (не по chat_id!), ключ для /add и /cost
                         --   каталог общий на все чаты, подключённые к боту (см. ниже)

  Заполняется НЕ ТОЛЬКО вручную (/add, /map) — при онбординге и /sync
  db.auto_populate_product_mapping(chat_id) заводит товары автоматически
  из marketplace_stocks (WB — с fallback на marketplace_orders, у Ozon
  fallback нет: там product_id = SKU, а не offer_id). Мэтчинг между
  площадками — только по точному совпадению display_name (без учёта
  регистра), без фаззи — примеры товаров типа wb_article="БК50гр" /
  ozon_offer_id="КБ50" текстом не сопоставить, склейка чужих товаров хуже,
  чем две отдельные строки. При коллизии display_name с другим товаром —
  суффикс " (WB)"/" (Ozon)", существующая строка не перезаписывается.

  wb_barcodes/ozon_barcodes  -- TEXT[], штрихкоды товара на каждой площадке
    Собираются db.collect_and_save_barcodes() из данных, которые уже
    приходят при каждом /sync (без новых API-вызовов): WB — поле barcode в
    ответе /api/v1/supplier/stocks (Statistics-токен), Ozon — из
    /v3/product/info/list (уже вызывается для offer_id-маппинга; точное имя
    поля barcode vs barcodes не подтверждено на живых данных, код пробует
    оба варианта — см. logger.info("[Ozon.product/info/list] пример строки
    (ключи): ...") в tools/marketplace.py). Аппенд-дедуп между синками
    (массив, не перезапись) — у одного wb_article может быть несколько
    штрихкодов на размерные варианты.
    Используются для сопоставления WB/Ozon версий одного физического товара
    с РАЗНЫМИ артикулами продавца, когда текстовое сравнение display_name
    не работает (штрихкод на упаковке — надёжный физический идентификатор).
    См. plans/2026-07-14-cross-marketplace-product-merge.md.

product_merge_dismissed  -- пары строк product_mapping, отклонённые
  пользователем ("это разные товары", не сливать). wb_mapping_id,
  ozon_mapping_id, UNIQUE(wb_mapping_id, ozon_mapping_id).
  db.find_barcode_merge_candidates() их исключает — не предлагает повторно.

db.merge_product_rows(keep_id, remove_id)  -- атомарно сливает две строки
  product_mapping в одну. Единственная связанная FK-таблица —
  product_costs.mapping_id (перепривязывается); product_adv_stats/
  product_funnel_stats/marketplace_financial_report/marketplace_stocks/
  marketplace_orders джойнятся по сырым wb_article/wb_nm_id/ozon_offer_id/
  ozon_sku, не по mapping_id — трогать их не нужно, join сам заработает,
  как только объединённая строка понесёт оба идентификатора. Скалярные поля
  — COALESCE-приоритет у keep_id (одним атомарным UPDATE...FROM, не
  Python-сборкой). Идемпотентна — повторный вызов на уже удалённой строке
  не падает, просто логирует и выходит.

product_costs            -- себестоимость
  mapping_id → cost (₽), updated_at, marketplace ('wb'/'ozon')
  ключ на product_mapping.id, НЕ на артикул МП
  ПО ДВЕ строки на товар (wb + ozon, разная себестоимость) —
  джойн ОБЯЗАН фильтровать c.marketplace, иначе fan-out задваивает строки

product_adv_stats        -- реклама на уровне товара (product_id/день)
  chat_id, marketplace, product_id, campaign_id, stat_date
  views, clicks, ctr, spend, orders_count
  UNIQUE(chat_id, marketplace, product_id, stat_date)
  WB product_id = nmId (числовая строка) — join через product_mapping.wb_nm_id
  Ozon product_id = SKU (числовой)
```

## Аналитические таблицы (новые)

```sql
marketplace_financial_report   -- финотчёты МП: реальные выплаты, комиссии
  chat_id, marketplace, product_id, report_date (DATE, понедельник недели)
  quantity, revenue, payout, commission, logistics, storage, penalty
  UNIQUE(chat_id, marketplace, product_id, report_date)
  -- WB: из /api/v5/supplier/reportDetailByPeriod (statistics_token)
  --   product_id = sa_name (supplierArticle, НИЖНИЙ РЕГИСТР!) — НЕ nm_id.
  --   Джойн с product_mapping.wb_article — через LOWER() с обеих сторон.
  -- Ozon: из /v3/finance/transaction/list (orders + returns tx_type)
  -- upsert = EXCLUDED-семантика (замена, не накопление)

product_funnel_stats           -- воронка конверсии карточки
  chat_id, marketplace, product_id, stat_date
  views, add_to_cart, orders_count, buyouts, avg_position
  conv_view_to_cart, conv_cart_to_order
  UNIQUE(chat_id, marketplace, product_id, stat_date)
  Ozon product_id = SKU (числовой)
  -- WB: /api/v1/analytics/nm-report/grouped
  -- Ozon: /v1/analytics/data с metrics=[views, conv_tocart, ordered_units]

daily_revenue_snapshot         -- агрегат заказов за день (для MoM-трендов)
  snapshot_date DATE, chat_id, marketplace
  revenue, orders_count, avg_price
  UNIQUE(snapshot_date, chat_id, marketplace)
  -- заполняется _daily_snapshot_loop в 01:00 UTC

stock_history_daily            -- история остатков (для оборачиваемости)
  snapshot_date DATE, chat_id, marketplace, product_id, warehouse_name
  stock
  UNIQUE(snapshot_date, chat_id, marketplace, product_id, warehouse_name)
  -- заполняется _daily_snapshot_loop в 01:00 UTC (копия marketplace_stocks)
```

## Агент Ева

```sql
digest_channels  -- каналы: chat_id, username, title, added_by, last_checked_at
```

## VIEW

```sql
stocks_unified      -- marketplace_stocks + product_mapping
adv_stats_unified   -- marketplace_adv_stats + wb_campaigns WHERE marketplace='wb'
adv_stats_summary   -- агрегат: total_views, total_clicks, avg_ctr, total_spend
```

## Важные нюансы

- `product_mapping.wb_article` и `ozon_offer_id` — nullable (товар может быть только на одной ПЛ)
- `UNIQUE(display_name)` — основной ключ для пользовательских команд, ГЛОБАЛЬНЫЙ
  (не по `chat_id`, хотя колонка `chat_id` в таблице есть — используется только
  в `save_price_recommendations`/`get_price_recommendations`). Каталог товаров
  сейчас один на все чаты, подключённые к боту — не проблема, пока бот
  обслуживает один бизнес с несколькими chat_id, но станет реальным багом
  (чужие товары/себестоимость видны друг другу), если появится больше одного
  независимого магазина от разных людей.
- `product_costs` привязан к `product_mapping.id`, не к артикулу МП
- Цена селлера и реализация — РАЗНЫЕ поля (см. skills/max-api)
- `marketplace_financial_report.report_date` — всегда понедельник недели (агрегат за неделю)
- Ozon возвраты уже агрегированы в `marketplace_financial_report` (tx_type="returns"), в `marketplace_sales.is_return` они не дублируются
- **КРИТИЧНО для джойнов по Ozon `product_id`**: для WB везде один и тот же `wb_article`, но для Ozon в БД ходят ДВА разных идентификатора под одним именем колонки `product_id`:
  - `offer_id` (строка, как у WB) — в `marketplace_sales`, `marketplace_stocks`
  - `sku` (число) — в `marketplace_orders`, `product_adv_stats`, `product_funnel_stats`, **и в `marketplace_financial_report` для Ozon** (`/v3/finance/transaction/list` отдаёт `items[].sku`, `offer_id` там вообще нет — джойн на `m.ozon_offer_id` всегда давал 0 строк, финотчёт Ozon был тихо пустой; чинить через `m.ozon_sku`, см. `agents/peter.py` net_margin)
  - Прямой джойн `a.product_id = b.product_id` между таблицами из разных списков для Ozon никогда не совпадёт. Мостить через `product_mapping.ozon_offer_id`/`ozon_sku`, причём per-marketplace (не объединять скорость продаж/метрики WB и Ozon в одно число при трансляции). Баг такого типа уже находили и чинили дважды (`agents/peter.py::_collect_advanced_data`, `agents/max.py::_check_stock_alerts`) — см. `retrospectives/2026-06-16_dashboard-sync-roas-ozon-id-mismatch.md`.
