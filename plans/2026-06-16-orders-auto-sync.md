# План: автоматическая синхронизация заказов/продаж/остатков

**Статус: в работе**

---

## Контекст

Дашборд не показывает актуальные данные за сегодня. Диагностика (Railway Postgres):

| Таблица | WB | Ozon |
|---|---|---|
| orders | последний заказ 15.06 21:37 UTC | последний 15.06 00:00, за сутки — 0 |
| sales | последняя продажа 13.06 | последняя 14.06 |
| adv_stats | свежая (16.06) | простой с 09.06 |

Причина: `Max.sync_marketplace_data()` (заказы, продажи, остатки) вызывается только вручную — по кнопке "🔄 Синхронизировать" или "Сводка" в Telegram (`agents/max.py:518,531,1902,2343`). В отличие от рекламы (03:00 UTC) и финотчёта (вс 01:30 UTC), для заказов/продаж/остатков нет фонового расписания в `main.py`.

## Решение

Добавить в `main.py` новый фоновый цикл `_scheduled_orders_sync_loop()` по аналогии с `_scheduled_adv_sync_loop()` / `_scheduled_questions_loop()`: каждый час вызывать `max_agent.sync_marketplace_data(chat_id)` для всех активных магазинов (`get_all_active_shops()`).

Изначально выбрали 4 часа, по запросу пользователя сократили до 1 часа — магазинов сейчас немного, риск rate limit (429, обрабатывается retry внутри `sync_marketplace_data`/клиентов) приемлем. При росте числа магазинов вернуться к более редкому интервалу.

## Фазы

- [x] Изучить существующие паттерны фоновых циклов в `main.py`
- [x] Добавить `_scheduled_orders_sync_loop()` в `main.py`, запускающий `sync_marketplace_data` каждые 4 часа для всех активных магазинов
- [x] Зарегистрировать `asyncio.create_task(...)` рядом с остальными scheduled-задачами
- [x] Проверить локально (синтаксис) — `python -m py_compile main.py` прошёл
- [ ] Дождаться подтверждения пользователя для `railway up`

## Итог

Добавлен фоновый цикл `_scheduled_orders_sync_loop()` (`main.py`, после `_scheduled_questions_loop`), вызывающий `Max.sync_marketplace_data(chat_id)` для всех активных магазинов каждый час. Ручная синхронизация по кнопке осталась без изменений. Деплой на Railway — по отдельному подтверждению.

## Доп. находка (16.06): рассинхрон Ozon offer_id / sku в дашборде

При проверке остатков (артикул КБ50 — есть на Ozon, но дашборд показывал только WB) обнаружился отдельный баг в `agents/peter.py` (`_collect_advanced_data`):

- `marketplace_stocks` и `marketplace_sales` хранят для Ozon **offer_id** (`КБ50`)
- `marketplace_orders` и `product_adv_stats` хранят для Ozon **sku** (`1033466212`)
- Запросы `stock_velocity` и `product_metrics` (ROAS) джойнили эти таблицы по `product_id = product_id` напрямую → для Ozon сравнение `offer_id = sku` никогда не совпадало
- Следствие: `daily_orders` для Ozon-товаров всегда 0 → `days_left = 999` → сортировка по `days_left ASC` + `LIMIT 15` выкидывала Ozon-строки из выдачи; `buyouts`/`roas` для Ozon-товаров всегда были 0

Исправлено: в обоих запросах добавлена marketplace-aware трансляция идентификатора через `product_mapping` (`CASE WHEN marketplace='ozon' THEN COALESCE(mm.ozon_offer_id/ozon_sku, product_id) ELSE product_id END`), с сохранением раздельного агрегирования по WB и Ozon (важно: смешивать их скорость продаж в одно число — отдельная ошибка, которую поймали и откатили во время проверки). Проверено на реальных данных (`chat_id=397443854`, артикул КБ50): обе строки (WB и Ozon) теперь видны с собственными `daily_orders`/`days_left`, ROAS по Ozon товарам — реальные ненулевые значения вместо 0.

## Доп. находка (16.06, продолжение): тот же баг ещё в одном месте

Аудит всего репо на этот паттерн нашёл такой же баг в `agents/max.py::_check_stock_alerts` (фоновая проверка низких остатков, шлёт алерт в Telegram) — джойн `marketplace_orders.product_id = marketplace_stocks.product_id` напрямую, для Ozon `daily_velocity` всегда была 0, алерты по низким остаткам для Ozon-товаров не срабатывали (кроме случая stock=0). Исправлено той же marketplace-aware трансляцией через `product_mapping.ozon_sku`. Проверено на реальных данных — `daily_velocity` для Ozon стала ненулевой.

Остальные места (margin/funnel/returns/kw_top/mom_trends в `agents/peter.py`, `agents/max.py::_check_drr_alerts`, `tools/marketplace.py`, `db.py`) проверены аудитом — джойнов с этим несовпадением не найдено. Соответствие offer_id/sku по таблицам задокументировано в `.claude/skills/db-schema/SKILL.md`.

## Доп. находка (16.06, ещё позже): NET-маржа в дашборде — три бага сразу

Пользователь сообщил, что таблица NET-маржи показывает "непонятные значения". Разобрал `agents/peter.py::_collect_advanced_data`:

1. **`product_costs` — fan-out**: таблица хранит ПО ДВЕ строки на товар (`marketplace='wb'` и `marketplace='ozon'`, разная себестоимость). Джойн `JOIN product_costs c ON c.mapping_id = m.id` без фильтра по marketplace в `margin_wb`/`margin_ozon` задваивал каждую строку заказа (по строке на каждую запись себестоимости) → revenue/qty/op_profit были **в 2 раза больше реальных**. Проверено: честный `SUM(quantity)` по `marketplace_orders` = 696, баг показывал 1392.
2. **WB финотчёт ключуется по `nm_id`, а не `wb_article`**: `WBClient.get_financial_report` (`tools/marketplace.py`) брал `nm_id` (внутренний числовой ID карточки WB) как `product_id`, а `product_mapping.wb_article` — это `supplierArticle`. Джойн никогда не совпадал → для всех WB-товаров в NET-марже `cost_per_unit=0`, `net_margin_pct=100%` (выглядело как "идеальная" маржа). Исправлено: используем `sa_name` (= supplierArticle, но в нижнем регистре) из того же отчёта; джойн в SQL — `LOWER(m.wb_article) = LOWER(f.product_id)`.
3. Тот же безусловный `OR`-джойн `product_mapping` по `wb_article`/`ozon_offer_id` без привязки к marketplace (паттерн уже чинили в `stock_velocity`).

Дополнительно вычищены 200 старых строк `marketplace_financial_report` (WB, ключ `nm_id`) и финотчёт пересинхронизирован с новым ключом. Найдена и исправлена опечатка в `product_mapping` (id=10): `wb_article='ЛГ1450'` не существовал ни в одной реальной таблице WB (заказы/остатки/финотчёт), реальный артикул — `ЛГ450`. Из-за этого товар не матчился с mapping вообще ни в одном запросе, не только в NET-марже.

Минорная находка не в скоупе фикса: в WB-данных существует артикул `ГБ2.5` (точка) отдельно от `ГБ2,5` (запятая) — похоже на дубль карточки на стороне продавца, не трогали.
