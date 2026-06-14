# 2026-06-14 — Analytics Audit Roadmap (п.2–5)

## Какая задача была

Дореализовать аналитику Питера и Макса по плану `2026-06-13-analytics-audit-roadmap.md`:
- п.2: явный ДРР на уровне товара в SQL
- п.3: воронка конверсии карточки
- п.4: реальная P&L через финотчёты маркетплейсов
- п.5: возвраты, ежедневные снимки выручки и остатков

## Как решали

**п.2 — ДРР на товар**: добавили явный CASE WHEN в SQL `_collect_advanced_data` (peter.py) — джойн `product_adv_stats` с `marketplace_orders` через product_id.

**п.3 — Воронка**:
- Новая таблица `product_funnel_stats` (views, add_to_cart, orders_count, buyouts, конверсии)
- `WBClient.get_funnel_stats` → WB NM Report API (`/api/v1/analytics/nm-report/grouped`), пагинация по `isNextPage`
- `OzonClient.get_funnel_stats` → Ozon `/v1/analytics/data` с metrics=[views, conv_tocart, ordered_units], add_to_cart = round(views * conv_tocart / 100)
- Макс: `/sync_funnel` → `upsert_funnel_stat`
- Питер: `/funnel` → Claude анализирует узкие места воронки

**п.4 — Финотчёты (реальная маржа)**:
- Новая таблица `marketplace_financial_report` (payout, commission, logistics, storage, penalty)
- `WBClient.get_financial_report` → `/api/v5/supplier/reportDetailByPeriod` (statistics_token, пагинация по rrdid), агрегат по nmId/неделя (понедельник)
- `OzonClient.get_financial_report` → `/v3/finance/transaction/list` (orders + returns tx_type), агрегат по offer_id/неделя
- `upsert_financial_report` — EXCLUDED-семантика (замена, не накопление) во избежание двойного счёта
- Питер: `_collect_data` добавляет `net_margin` = payout − qty × себестоимость; промпт /report приоритизирует NET перед GROSS

**п.5 — Возвраты и снимки**:
- `marketplace_sales.is_return BOOLEAN DEFAULT FALSE`
- WB `get_sales` теперь включает saleID=R... с `is_return=True` (раньше скипались)
- `sync_marketplace_data` передаёт `is_return` в `save_sale`
- Таблицы `daily_revenue_snapshot` и `stock_history_daily`
- `_daily_snapshot_loop` в main.py (01:00 UTC): агрегирует вчерашние заказы + копирует текущие остатки

## Решили — да / нет / частично

**Да** — все 4 пункта (п.2–5) реализованы и компилируются чисто. Статус плана → завершён (п.6–9 остаются как отдельные будущие задачи).

## Что можно было лучше

- Ozon возвраты через `is_return` не реализованы: Ozon Premium Lite не даёт транзакционных данных по возвратам (только агрегат в `/v3/finance/transaction/list` который уже идёт в `marketplace_financial_report` как тип "returns"). Стоит явно задокументировать это в скиле, не оставлять как TODO.
- WB Financial Report агрегирует по `week_start` (понедельник): если синкать несколько раз за неделю, получаем частичные данные за текущую неделю — это нормально, но Питеру стоит это объяснять в промпте.

## Что узнал нового о проекте

- WB `/api/v5/supplier/reportDetailByPeriod` требует `statistics_token` (не основной), пагинация по `rrdid` (не offset)
- Ozon `/v3/finance/transaction/list` отдаёт orders + returns как отдельные `tx_type` — возвраты уже агрегируются в финотчёт, дублировать в `marketplace_sales` не нужно
- `upsert` с EXCLUDED-семантикой vs накопление — критична для финотчётов где повторный синк не должен удваивать суммы

## Нарушил ли агент правило молча?

Нет.
