---
name: max-api
description: >
  Используй этот skill при любой задаче связанной с агентом Макс:
  WB API, Ozon API, Ozon Performance API, парсинг CSV/ZIP отчётов,
  синхронизация рекламной статистики, работа с отзывами, заказами,
  остатками, себестоимостью, product_mapping. Также при ошибках
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

## Команды

- `/sync` — синхронизация заказов/остатков/сводка
- `/sync_adv` — ручной запуск синхронизации рекламной статистики
- `/products` — каталог товаров
- `/add` — мастер добавления товара (пошаговый диалог)
- `/cost` — задать себестоимость; быстрый путь: `/cost <ident> <сумма>`
- `/map name=X wb=Y ozon=Z` — добавить/обновить товар
- `/cancel` — отменить активный мастер

## WB Statistics API (отдельный токен, категория "Статистика")

- Заказы: `/api/v1/supplier/orders` flag=0/1
- Продажи: `/api/v1/supplier/sales` — только saleID начинается с "S" (R = возврат)
- Остатки: `/api/v1/supplier/stocks`
- Rate limit: 429 → sleep 60 сек + retry
- Реклама: `/adv/v3/fullstats` — работает ✅
- ⛔ `/adv/v1/promotion/adverts` — 404 с октября 2025 (баг WB). Названия кампаний — вручную в `wb_campaigns`

## WB Feedbacks API (основной токен)

- Список: `/api/v1/feedbacks` isAnswered=false
- Ответ: POST `/api/v1/feedbacks/answer` → 204

## Ozon API (Client-Id + Api-Key, Premium Lite)

- Остатки: POST `/v2/analytics/stock_on_warehouses` + маппинг SKU→offer_id через `/v3/product/info/list`
  - ⛔ `/v2/product/info/list` устарел, возвращает 404. Только `/v3/`
  - v3 возвращает `{"items": [...]}` напрямую (без обёртки `result`)
- Заказы активные: POST `/v3/posting/fbo/list`
- Заказы история: POST `/v1/analytics/data` (агрегат, не точные данные)
- Выкупы: POST `/v3/posting/fbo/list` статус delivered
- Отзывы: ❌ Premium Plus/Pro

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
- **Реализация** = WB `finishedPrice` / Ozon `revenue/qty`
- **ДРР** считать из финотчётов, не наивно:
  - WB: знаменатель = `ppvz_for_pay` из `/api/v5/supplier/reportDetailByPeriod`
  - Ozon: `/v3/finance/transaction/list` + `/v2/finance/realization`
- Дашборд заказов и финансовый ДРР показывают разный оборот — это нормально, две витрины

## Голосовые в группе (фикс)

`base_agent.handle_voice` не вызывает `handle_message` в группах.
`Max._handle_group_message` сам проверяет триггер: `has_mention` / `starts_with_max` / `is_reply_to_bot`.

## DataLens

- URL: https://datalens.yandex/zhnao5ut1xvmj
- Подключение: maglev.proxy.rlwy.net:12614, база railway
