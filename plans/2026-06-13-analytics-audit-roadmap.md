# 2026-06-13-analytics-audit-roadmap.md

Статус: в работе  
Дата: 2026-06-13

---

## Контекст

Задача — оценить, на каком этапе находится раздел аналитики (Питер + Макс), что не доделано и какие дополнительные данные нужны для полноценного анализа личного кабинета маркетплейсов. Это аудит текущего состояния + дорожная карта недостающего.

---

## Что уже работает

### Питер (agents/peter.py)
- `/report [цель] [период]` — выручка по МП, топ-10 товаров, GROSS-маржа, расходы на рекламу, остатки
- `/audit` — 30-дневный аудит: оценка X/10, SWOT, KPI, топ-5 действий → сохраняется в Notion
- `/drr [период]` — ДРР и ROAS **по маркетплейсу** (не по товару), топ-5 по расходу
- `/analyze` — произвольный анализ

### Макс (agents/max.py)
- Отзывы: получение, авто-ответ (3-5★), флаг на ручное одобрение (1-2★) — WB + Ozon
- Заказы: rolling window 14 дней (WB stats + Ozon analytics)
- Продажи: rolling window 2 дня (выполненные / доставленные)
- Остатки: по складам с кластеризацией — WB + Ozon
- Реклама: кампании + уровень товара (7 дней) — WB Advert API v3 + Ozon Performance API
- Каталог: product_mapping (название + артикул WB + SKU Ozon), product_costs (себестоимость)

### Дашборд Mini App (plan: 2026-06-12-market-analytics-viz.md, Фаза 2+3 ✅)
- Backend `/api/dashboard` с HMAC-SHA256 валидацией — готов в main.py
- Frontend React + Recharts (dashboard/) — готов
- **НЕ готово:** деплой на Vercel + кнопка в peter.py (Фаза 4 текущего плана)

---

## Что НЕ сделано — по приоритетам

### 🔴 КРИТИЧНО: реальная рентабельность (финансовые отчёты МП)

**Проблема:** Питер считает GROSS-маржу — выручка минус себестоимость. Без комиссий и логистики реальная рентабельность завышена на 20-40%.

**Что нужно получить:**
- **WB:** `/api/v5/supplier/reportDetailByPeriod` — `ppvz_for_pay` (фактическая выплата после всех удержаний), комиссия, логистика, хранение, штрафы
- **Ozon:** `/v3/finance/transaction/list` + `/v2/finance/realization` — структура комиссий, бонусы, логистика

**Новая таблица:** `marketplace_financial_transactions`
```sql
id BIGSERIAL PK
chat_id BIGINT
marketplace TEXT
report_period DATE        -- период отчёта (начало недели/месяца)
product_id TEXT           -- артикул / offer_id
transaction_type TEXT     -- commission / logistics / storage / penalty / payout
amount NUMERIC(12,2)
currency TEXT DEFAULT 'RUB'
created_at TIMESTAMPTZ
```

**Файлы:** `agents/max.py` (новый метод sync_financial_report), `db.py` (DDL + upsert), `.claude/skills/db-schema/SKILL.md`, `.claude/skills/peter-analytics/SKILL.md`

---

### 🔴 КРИТИЧНО: возвраты и отмены занижают точность

**Проблема:** Выручка в marketplace_orders завышена на ~10-30% (WB) — возвраты не вычитаются.

**Что нужно:**
- WB: продажи с `saleID` начинающимся на "R" уже парсятся в `get_sales()` — **не сохраняются**
- Ozon: `/v1/analytics/data` с метрикой `returned_items`

**Расширить таблицу `marketplace_orders`:** добавить поле `is_return BOOLEAN DEFAULT FALSE` или создать `marketplace_returns` с reason_code.

**Файлы:** `agents/max.py`, `db.py`

---

### 🟡 ВАЖНО: ДРР и ROAS на уровне товара

**Проблема:** Питер показывает ДРР только по маркетплейсу. Нельзя понять, какой конкретный товар "сжигает" рекламный бюджет.

**Уже есть:** таблица `product_adv_stats` с данными по товару + кампании.

**Что нужно:** изменить SQL-запрос в `/drr` команде peter.py — джойнить `product_adv_stats` с `marketplace_orders` по product_id/период.

**Файлы:** `agents/peter.py` (lines 534-606 — `cmd_drr`)

---

### 🟡 ВАЖНО: исторические данные (тренды МоМ / YoY)

**Проблема:** Rolling window — 7/14 дней для рекламы, 2/14 для заказов. Нельзя сравнить с прошлым месяцем или годом.

**Что нужно:**
- Таблица `daily_revenue_snapshot` — ежедневный агрегат (не rolling window):
  ```sql
  snapshot_date DATE, marketplace TEXT, chat_id BIGINT,
  revenue NUMERIC, orders_count INT, avg_price NUMERIC
  ```
- Фоновая задача в main.py: ежедневно в 01:00 UTC фиксировать агрегат за предыдущий день

**Файлы:** `main.py` (новая задача), `db.py` (DDL)

---

### 🟡 ВАЖНО: воронка конверсии карточки (данные которые не рассматривали)

**Это самый недооценённый блок.** Сейчас мы видим только заказы — но не знаем, сколько людей увидело карточку, кликнуло, добавило в корзину. Без этого нельзя понять, почему один товар продаётся лучше другого.

**WB:** `/api/v1/analytics/nm-report/grouped` — эндпоинт доступен:
- Показы карточки (openCard)
- Добавления в корзину (addToCart)
- Заказы (orders)
- Конверсия: view→cart, cart→order
- Выкупы (buyouts)
- Средняя позиция в поиске

**Ozon:** `/v1/analytics/data` с dimension=["offer_id"] и metrics:
- `views` (показы), `session_view`, `conv_tocart`, `ordered_units`, `revenue`

**Новая таблица:** `product_funnel_stats`
```sql
id BIGSERIAL PK
chat_id BIGINT
marketplace TEXT
product_id TEXT
stat_date DATE
views INT
add_to_cart INT
orders_count INT
buyouts INT
avg_position NUMERIC(6,1)   -- средняя позиция в поиске (WB)
conv_view_to_cart NUMERIC(5,2)
conv_cart_to_order NUMERIC(5,2)
updated_at TIMESTAMPTZ
UNIQUE(chat_id, marketplace, product_id, stat_date)
```

**Новый отчёт у Питера:** "Почему товар не продаётся — мало показов или плохая карточка?"

**Файлы:** `agents/max.py` (новый метод sync_funnel), `db.py`, `agents/peter.py` (новая команда /funnel)

---

### 🟡 ВАЖНО: история остатков (оборачиваемость)

**Проблема:** `marketplace_stocks` хранит только последний снимок. Нельзя посчитать оборачиваемость или спрогнозировать дату "out of stock".

**Что нужно:** Фоновая задача — ежедневно копировать текущие остатки в `stock_history_daily`:
```sql
snapshot_date DATE, chat_id BIGINT, marketplace TEXT,
product_id TEXT, warehouse_name TEXT, stock INT
UNIQUE(snapshot_date, chat_id, marketplace, product_id, warehouse_name)
```

**Файлы:** `main.py` (фоновая задача), `db.py`

---

### 🟠 СРЕДНЕ: акционные кампании маркетплейсов

**Ozon:** `/v2/promotion/list` — список акций, условия скидок, период
**WB:** Акции через Seller API

Участие в акциях напрямую влияет на выручку и маржу — сейчас не учитывается.

**Новая таблица:** `marketplace_promotions`
```sql
promotion_id TEXT, marketplace TEXT, title TEXT,
discount_pct NUMERIC, start_date DATE, end_date DATE,
product_ids JSONB, chat_id BIGINT
```

---

### 🟠 СРЕДНЕ: рейтинг продавца и KPI аккаунта

Маркетплейсы снижают видимость при низком рейтинге, высоком проценте отмен, медленном ответе на отзывы.

**WB:** `/api/v1/supplier/info` — рейтинг, процент выкупа, время доставки
**Ozon:** `/v1/rating/summary` — штрафы, рейтинг аккаунта

**Новая таблица:** `shop_kpi_snapshots` — ежедневный снимок рейтинга продавца

---

### ⚪ ЗАВЕРШИТЬ ТЕКУЩИЙ ПЛАН: Фаза 4 Mini App

Из `2026-06-12-market-analytics-viz.md` (Статус: в работе):
- [ ] Деплой `dashboard/` на Vercel → получить `DASHBOARD_URL` (Борис делает вручную)
- [ ] Добавить `DASHBOARD_URL` в Railway (с разрешения Бориса)
- [ ] `agents/peter.py`: добавить кнопку "📊 Дашборд" (WebAppInfo) в `cmd_report`, `cmd_audit`, `cmd_drr`

---

## Сводная таблица пробелов

| # | Что | Приоритет | Усилие | Новые таблицы |
|---|-----|-----------|--------|---------------|
| 1 | Финансовые отчёты МП (реальная комиссия) | 🔴 | Высокое | marketplace_financial_transactions |
| 2 | Возвраты и отмены | 🔴 | Среднее | расширить marketplace_orders |
| 3 | ДРР на уровне товара | 🟡 | Малое | — (данные уже в БД) |
| 4 | Воронка конверсии карточки | 🟡 | Высокое | product_funnel_stats |
| 5 | Исторические агрегаты | 🟡 | Среднее | daily_revenue_snapshot |
| 6 | История остатков | 🟡 | Малое | stock_history_daily |
| 7 | Mini App кнопка (Фаза 4) | 🟡 | Малое | — |
| 8 | Акционные кампании | 🟠 | Среднее | marketplace_promotions |
| 9 | Рейтинг продавца | 🟠 | Малое | shop_kpi_snapshots |

---

## Ключевые файлы для изменений

- `agents/max.py` — новые методы: sync_financial_report, sync_funnel, sync_returns
- `agents/peter.py` — новые команды: /funnel; исправить /drr → ДРР по товару; WebApp кнопка
- `db.py` — DDL для 4+ новых таблиц
- `main.py` — новые фоновые задачи (ежедневные снимки revenue + stocks)
- `.claude/skills/db-schema/SKILL.md` — обновить схему
- `.claude/skills/peter-analytics/SKILL.md` — обновить возможности
- `.claude/skills/max-api/SKILL.md` — обновить API endpoints

---

## Рекомендуемый порядок реализации

- [ ] 1. Фаза 4 Mini App — кнопка в peter.py (ждёт DASHBOARD_URL от Бориса)
- [ ] 2. ДРР на уровне товара — только SQL в peter.py (данные уже есть)
- [ ] 3. Воронка конверсии — NM Report WB + Ozon analytics (новое измерение)
- [ ] 4. Финансовые отчёты МП — реальная P&L (самое важное для бизнеса)
- [ ] 5. Возвраты, исторические снимки, остальное

---

## Верификация

1. `/drr` показывает ДРР и ROAS отдельно по каждому товару (не только по МП)
2. `/funnel` (новая команда) — конверсии view→cart→order по товарам
3. `/report` Питера показывает NET-маржу (ниже текущей на 20-40%) после финансовых отчётов
4. Mini App: кнопка "📊 Дашборд" в ответах /report, /audit, /drr
5. `/report период=90` работает без ошибок (из daily_revenue_snapshot)
