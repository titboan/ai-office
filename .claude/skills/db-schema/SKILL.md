---
name: db-schema
description: >
  Используй этот skill при работе с базой данных проекта:
  создание/изменение таблиц, написание SQL-запросов, работа с VIEW,
  task queue, product_mapping, marketplace_orders, marketplace_adv_stats,
  stocks, reviews, sales. Также при подключении к Railway Postgres.
---

# Схема базы данных AI Office

## Подключение

- Использовать `DATABASE_PUBLIC_URL` (не `DATABASE_URL`)
- Railway Console: вкладка Console для SQL, не Data
- DataLens TCP-прокси: maglev.proxy.rlwy.net:12614

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
marketplace_sales        -- выкупы WB (forPay, saleID с "S") + Ozon delivered
marketplace_stocks       -- остатки WB (supplierArticle) + Ozon (offer_id)
marketplace_adv_stats    -- рекламная статистика WB и Ozon

wb_campaigns             -- названия WB кампаний (вручную — API 404)

product_mapping          -- реестр товаров
  wb_article             -- nullable
  ozon_offer_id          -- nullable
  ozon_sku               -- nullable
  display_name           -- UNIQUE, ключ для /add и /cost

product_costs            -- себестоимость
  mapping_id → cost (₽), updated_at
  ключ на product_mapping.id, НЕ на артикул МП
```

## Агент Ева

```sql
digest_channels  -- каналы: chat_id, username, title, added_by, last_checked_at
```

## VIEW

```sql
stocks_unified      -- marketplace_stocks + product_mapping (для DataLens)
adv_stats_unified   -- marketplace_adv_stats + wb_campaigns WHERE marketplace='wb'
adv_stats_summary   -- агрегат: total_views, total_clicks, avg_ctr, total_spend
```

## Важные нюансы

- `product_mapping.wb_article` и `ozon_offer_id` — nullable (товар может быть только на одной ПЛ)
- `UNIQUE(display_name)` — основной ключ для пользовательских команд
- `product_costs` привязан к `product_mapping.id`, не к артикулу МП
- Цена селлера и реализация — РАЗНЫЕ поля (см. skills/max-api)
