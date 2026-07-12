---
name: max-api
description: >
  Используй этот skill при любой задаче связанной с агентом Макс:
  WB API, Ozon API, Ozon Performance API, парсинг CSV/ZIP отчётов,
  синхронизация рекламы, финотчётов, воронки, отзывов, заказов,
  остатков, себестоимости, product_mapping. Также при ошибках
  rate limit, 404 от WB, PermissionDenied от Ozon.
---

# Макс — Маркетплейс менеджер (WB + Ozon)

## Что умеет

- Отзывы WB: автоответ 3-5★, одобрение 1-2★ через Telegram
- Отзывы Ozon: ❌ недоступны (нужен Premium Plus/Pro ~20к/мес)
- Заказы: синхронизация WB и Ozon в Postgres
- Остатки: WB (11 регионов) и Ozon (кластеры), порог < 20 шт
- Ежедневная сводка: сегодня/вчера/неделю назад, 09:00 МСК
- ИИ-агент в группе партнёров: реагирует на "@бот" или "Макс"
- Реклама WB: marketplace_adv_stats, синк через /adv/v3/fullstats, 03:00 UTC
- Реклама Ozon: Performance API + OAuth, токен кешируется в Redis (TTL 25 мин)
- Финотчёты: WB + Ozon → marketplace_financial_report (реальные выплаты/комиссии)
- Воронка: WB NM Report + Ozon analytics → product_funnel_stats

## Команды

- `/sync` — синхронизация заказов/остатков/сводка
- `/sync_adv` — ручной запуск синхронизации рекламной статистики
- `/sync_fin [дней=90]` — финансовые отчёты (комиссии, выплаты, логистика)
- `/sync_funnel` — воронка конверсии карточек (30 дней)
- `/sync_sku` — подтянуть Ozon SKU в реестр
- `/products` — каталог товаров
- `/add` — мастер добавления товара (пошаговый диалог)
- `/cost` — задать себестоимость; быстрый путь: `/cost <ident> <сумма>`
- `/map name=X wb=Y ozon=Z` — добавить/обновить товар
- `/cancel` — отменить активный мастер

## WB Statistics API (отдельный токен, категория "Статистика")

- Заказы: `/api/v1/supplier/orders` flag=0/1
- Продажи + возвраты: `/api/v1/supplier/sales`
  - saleID начинается с "S" → продажа, `is_return=False`
  - saleID начинается с "R" → возврат, `is_return=True`
  - Оба типа сохраняются в `marketplace_sales`
- Остатки: `/api/v1/supplier/stocks`
- Rate limit: 429 → sleep 60 сек + retry
- Реклама: `/adv/v3/fullstats` — работает ✅
- ⛔ `/adv/v1/promotion/adverts` — 404 с октября 2025 (баг WB). Названия кампаний — вручную в `wb_campaigns`
- Рекомендуемая рыночная ставка: `GET /api/advert/v0/bids/recommendations?advertId=&nmId=` — требует ОБА параметра (кампания + артикул, рекомендация считается на пару). Ответ вложенный: `base.competitiveBid.bidKopecks` (конкурентная ставка, в копейках — делить на 100 для рублей), также `base.leadersBid`/`base.top2` и `normQueries[].reachMax/Medium/Min`. Схема взята из community OpenAPI-спека (`raw.githubusercontent.com/eslazarev/wildberries-sdk/main/specs/08-promotion.yaml`) — официальные доки dev.wildberries.ru отдают 403 на прямой фетч. Живым вызовом на проде ещё не подтверждено (см. `plans/2026-07-12-wb-recommended-bid.md`), при расхождении — поправить `WBClient.get_recommended_bid`. nm_id брать через `get_campaigns_nms([campaign_id])`.

## WB Analytics API (основной токен)

- Воронка NM: `/api/v1/analytics/nm-report/grouped`
  - Параметры: `nmIDs[]`, `period.begin/end`, `aggregationLevel=day`
  - Поля: `openCardCount` (views), `addToCartCount`, `ordersCount`, `buyoutsCount`
  - `addToCartPercent` (view→cart), `cartToOrderPercent` (cart→order)
  - Пагинация: `isNextPage` в ответе → page+1

## WB Financial Report API (statistics_token!)

- `/api/v5/supplier/reportDetailByPeriod`
  - `dateFrom`, `dateTo`, `rrdid` (пагинация — начать с 0)
  - Ключевые поля: `nm_id`, `ppvz_for_pay` (выплата), `ppvz_office_id`, `delivery_rub`, `storage_fee`, `penalty`
  - Агрегируется по (nm_id, week_start_monday) в памяти перед upsert
  - upsert = EXCLUDED-семантика (не накопление!)

## WB Feedbacks API (основной токен)

- Список: `/api/v1/feedbacks` isAnswered=false
- Ответ: POST `/api/v1/feedbacks/answer` → 204

## WB Questions API (основной токен) — feedbacks-api.wildberries.ru

- Список: GET `/api/v1/questions?isAnswered=false&take=100&skip=0`
- Ответить: PATCH `/api/v1/questions`
  ```json
  {"id": "question_id", "answer": {"text": "текст ответа"}, "state": "wbRu"}
  ```
  - `state: "wbRu"` — ответ виден на сайте покупателям
  - `state: "none"` — вопрос отклонён продавцом (скрыт)
  - ⚠️ `text` должен быть внутри объекта `answer`, НЕ на верхнем уровне
  - ⚠️ state = `"wbGoodsQaStatePublished"` — неверное значение (WB вернёт 200 но проигнорирует)
- Источник: [eslazarev/wildberries-sdk specs/09-communications.yaml](https://github.com/eslazarev/wildberries-sdk)

## Ozon API (Client-Id + Api-Key, Premium Lite)

- Остатки: POST `/v2/analytics/stock_on_warehouses` + маппинг SKU→offer_id через `/v3/product/info/list`
  - ⛔ `/v2/product/info/list` устарел, возвращает 404. Только `/v3/`
  - v3 возвращает `{"items": [...]}` напрямую (без обёртки `result`)
- Заказы активные: POST `/v3/posting/fbo/list`
- Заказы история: POST `/v1/analytics/data` (агрегат, не точные данные)
- Выкупы: POST `/v3/posting/fbo/list` статус delivered
- Воронка: POST `/v1/analytics/data`
  - dimension: `["sku"]`, metrics: `["views", "conv_tocart", "ordered_units"]`
  - `add_to_cart = round(views * conv_tocart / 100)`
- Финансы: POST `/v3/finance/transaction/list`
  - tx_type: "orders" и "returns" (отдельные запросы)
  - Агрегируется по (offer_id, week_start_monday)
  - revenue ≈ payout + commission + logistics (приближение, точной выручки нет)
- Отзывы: ❌ Premium Plus/Pro
- Вопросы и ответы (Q&A): ❌ тоже требует Premium Plus — это ОДНА и та же подписка (24 990 ₽/мес),
  «работа с отзывами и вопросами через Seller API» идёт в ней единым пунктом, отдельной доп. услуги
  для вопросов нет. Premium Lite (4 990 ₽) и Premium (9 990 ₽) доступа к API отзывов/вопросов не дают —
  только к личному кабинету (там вопросы видны и без API, как на скриншоте пользователя).
  Если подписки Premium Plus нет, `/v1/question/list` вернёт PermissionDenied — это ожидаемо, не баг.
  - Список: POST `/v1/question/list`, body `{"filter": {"status": "UNPROCESSED"}, "last_id": "..."}` — курсорная пагинация через `last_id`, НЕ `page`/`page_size`
  - `status` в фильтре: `NEW` / `VIEWED` / `PROCESSED` / `UNPROCESSED` / `ALL`
  - Текст вопроса — поле `text` (НЕ `question_text`), дата — `published_at`; `product_name` в ответе не приходит, только `sku` + `product_url`
  - Ответ: POST `/v1/question/answer/create`, body `{"question_id", "sku": <int>, "text"}` — `sku` обязателен, без него Ozon отклоняет запрос
  - Источник: github.com/salacoste/ozon-daytona-seller-api `src/types/{requests,responses}/questions-answers.ts`

## Ozon Performance API (OAuth, обязателен с апр 2026)

- Токен: POST `https://api-performance.ozon.ru/api/client/token`
  - Кешируется в Redis, ключ `ozon_perf_token`, TTL 25 мин
  - client_id формат: `цифры@advertising.performance.ozon.ru`
  - Переменные: `OZON_PERFORMANCE_CLIENT_ID`, `OZON_PERFORMANCE_CLIENT_SECRET`
- Кампании: GET `/api/client/campaign` (только CAMPAIGN_STATE_RUNNING)
  - Возвращает ~88 кампаний, реальных ~19 — остальные REF_VK → фильтровать
- Статистика (async, 3 шага):
  1. POST → получить UUID
  2. GET poll до state=OK
  3. GET report → ZIP-архив

### Парсинг отчёта Ozon Performance

- Ответ: ZIP-архив (content-type: application/zip)
- Внутри: CSV в UTF-8 с BOM → `zipfile` + `.decode("utf-8-sig")`
- Формат: отчёт по SKU, campaign_id парсится из первой строки («№ XXXXX»)
- Метрики агрегируются по строкам SKU; строка «Всего» пропускается
- Батчи по 10 кампаний (лимит API), retry при 429

## Цены и финансы (не путать!)

- **Цена селлера** = WB `priceWithDisc` / Ozon `products[].price` из `/v3/posting/*`
  - НЕ из `/v1/analytics/data` — там её нет
- **Выплата (payout)** = WB `ppvz_for_pay` / Ozon `accruals_for_sale` из `/v3/finance/transaction/list`
- **Выручка Ozon (revenue)** = `sum(items[].price * items[].quantity)` — берётся напрямую из транзакций (точная цена товара). Раньше было приближение `payout + commission + logistics`.
- **NET-маржа** = payout − qty × себестоимость (в `marketplace_financial_report`)
- **ДРР** считать из финотчётов, не наивно:
  - WB: знаменатель = `ppvz_for_pay` из `/api/v5/supplier/reportDetailByPeriod`
  - Ozon: `/v3/finance/transaction/list`
- Дашборд заказов и финансовый ДРР показывают разный оборот — это нормально, две витрины

## Фоновые задачи в main.py

| Время UTC | Задача |
|---|---|
| 01:00 | `_daily_snapshot_loop` — снимок выручки + остатков в историю |
| 03:00 | `_scheduled_adv_sync_loop` — реклама WB + Ozon |
| 06:00, 11:00, 17:00 | `_scheduled_reviews_loop` — отзывы |
| каждые 15 мин | `_negative_reviews_loop` — быстрый polling 1-2★ |
| пн 07:00 | `_weekly_audit_loop` — еженедельный аудит у Питера |

## Голосовые в группе (фикс)

`base_agent.handle_voice` не вызывает `handle_message` в группах.
`Max._handle_group_message` сам проверяет триггер: `has_mention` / `starts_with_max` / `is_reply_to_bot`.

