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
marketplace_orders       -- заказы WB + Ozon
marketplace_sales        -- выкупы WB + Ozon delivered
                         --   is_return BOOLEAN DEFAULT FALSE
                         --   WB: saleID=S... → продажа, saleID=R... → возврат
marketplace_stocks       -- остатки WB (supplierArticle) + Ozon (offer_id), текущий снимок
marketplace_adv_stats    -- рекламная статистика WB и Ozon (кампания/день)

wb_campaigns             -- названия WB кампаний (вручную — API 404)

product_mapping          -- реестр товаров
  wb_article             -- nullable
  ozon_offer_id          -- nullable
  ozon_sku               -- nullable
  display_name           -- UNIQUE, ключ для /add и /cost

product_costs            -- себестоимость
  mapping_id → cost (₽), updated_at, marketplace ('wb'/'ozon')
  ключ на product_mapping.id, НЕ на артикул МП

product_adv_stats        -- реклама на уровне товара (product_id/день)
  chat_id, marketplace, product_id, campaign_id, stat_date
  views, clicks, ctr, spend, orders_count
  UNIQUE(chat_id, marketplace, product_id, stat_date)
```

## Аналитические таблицы (новые)

```sql
marketplace_financial_report   -- финотчёты МП: реальные выплаты, комиссии
  chat_id, marketplace, product_id, report_date (DATE, понедельник недели)
  quantity, revenue, payout, commission, logistics, storage, penalty
  UNIQUE(chat_id, marketplace, product_id, report_date)
  -- WB: из /api/v5/supplier/reportDetailByPeriod (statistics_token)
  -- Ozon: из /v3/finance/transaction/list (orders + returns tx_type)
  -- upsert = EXCLUDED-семантика (замена, не накопление)

product_funnel_stats           -- воронка конверсии карточки
  chat_id, marketplace, product_id, stat_date
  views, add_to_cart, orders_count, buyouts, avg_position
  conv_view_to_cart, conv_cart_to_order
  UNIQUE(chat_id, marketplace, product_id, stat_date)
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
- `UNIQUE(display_name)` — основной ключ для пользовательских команд
- `product_costs` привязан к `product_mapping.id`, не к артикулу МП
- Цена селлера и реализация — РАЗНЫЕ поля (см. skills/max-api)
- `marketplace_financial_report.report_date` — всегда понедельник недели (агрегат за неделю)
- Ozon возвраты уже агрегированы в `marketplace_financial_report` (tx_type="returns"), в `marketplace_sales.is_return` они не дублируются
