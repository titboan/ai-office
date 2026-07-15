# Себестоимость — редактируемая таблица в дашборде вместо Excel-юнитки

**Дата:** 2026-07-15
**Статус:** завершён

## Контекст

Себестоимость хранится в `product_costs` (`mapping_id + marketplace → cost`, отдельная
строка на WB и на Ozon — `db.py:248-254`, миграция `db.py:562-573`). Меняется только вручную,
изредка, одним человеком (владелец), двумя путями:
- `/cost <товар> <wb|ozon> <сумма>` (`agents/max.py:4269`) — быстрая правка одного числа.
- Проактивный мастер себестоимости (`agents/max.py:4335`, Фаза 3
  `plans/2026-07-14-guided-onboarding-analytics.md`) — тоже одно число за товар.

Оба способа пишут итоговое число через `db.set_product_cost` (`db.py:2578`). Отдельно
владелец ведёт расчёт себестоимости (юнитку) в Excel: закупка + логистика до склада,
упаковка и маркировка — эти статьи нигде в системе не хранятся, только их сумма (и то не
всегда, если пользуется /cost). Из-за этого Excel и `product_costs` расходятся.

Себестоимость из `product_costs.cost` — единственный источник для NET-маржи у Питера
(`agents/peter.py:392`: `JOIN product_costs c ON c.mapping_id = m.id AND c.marketplace = f.marketplace`).

## Цель

Перенести Excel-юнитку в дашборд: редактируемая таблица товаров с двумя статьями расходов
(закупка+логистика, упаковка+маркировка), итог считается автоматически и пишется в то же
поле `product_costs.cost`, которое уже использует Питер — расчёт NET-маржи не меняется.
Telegram (`/cost`, мастер) остаётся как есть для быстрой точечной правки одного числа
"на бегу" — их трогать не нужно.

## Фазы

### Фаза 1 — db.py: статьи расходов в product_costs [x]
- `ALTER TABLE product_costs ADD COLUMN IF NOT EXISTS purchase_logistics NUMERIC` (закупка + логистика до склада)
- `ALTER TABLE product_costs ADD COLUMN IF NOT EXISTS packaging_marking NUMERIC` (упаковка + маркировка)
- `set_product_cost_breakdown(mapping_id, marketplace, purchase_logistics, packaging_marking)` —
  upsert, `cost = purchase_logistics + packaging_marking` (пишется в существующую колонку `cost`,
  формат идентичен `set_product_cost`, чтобы `agents/peter.py:392` не менять).
- `get_product_costs_for_dashboard(chat_id)` — список товаров
  (`display_name`, `wb_article`, `ozon_offer_id`) с текущими `purchase_logistics_wb/ozon`,
  `packaging_marking_wb/ozon`, `cost_wb/ozon` для каждой площадки, где товар есть.
- Существующий `set_product_cost` (единое число из `/cost` и мастера) не менять — при вводе
  через него `purchase_logistics`/`packaging_marking` остаются `NULL` (это ок: значит "итог
  внесён вручную, без разбивки на статьи").

### Фаза 2 — main.py: API для дашборда [x]
По образцу `_handle_apply_price` (`main.py:933`): только настоящий Telegram `initData`
(без `?token=` — доступ на запись, а не read-only ссылка для коллег), rate-limit.
- `GET /api/costs` → `get_product_costs_for_dashboard(chat_id)`.
- `POST /api/set_cost` — body `{marketplace, product_id, purchase_logistics, packaging_marking}`.
  Резолвить `mapping_id` по `wb_article`/`ozon_offer_id` (тот же паттерн, что в
  `MaxAgent._apply_price`, `agents/max.py:5673-5677`), вызвать `set_product_cost_breakdown`.

### Фаза 3 — dashboard: секция "Себестоимость" [x]
Дашборд — одна прокручиваемая страница из секций (`App.tsx`, без вкладок,
`NetMarginTable` вставлен напрямую в разметку строка ~178) — новую секцию встраиваем так же.
- `dashboard/src/api.ts`: тип `CostRow`, функции `getCosts()`, `setCost(...)` (по образцу `applyPrice`, `api.ts:118`).
- `dashboard/src/charts/CostEditor.tsx` (новый): таблица товаров, для каждой площадки, где
  товар продаётся, — две редактируемые ячейки (закупка+логистика, упаковка+маркировка) и
  read-only колонка "Итого" = их сумма. Сохранение по blur/Enter с debounce → `setCost`.
- Вставить `<CostEditor .../>` в `App.tsx` рядом с `NetMarginTable`.

### Фаза 4 — проверка [x]
Поднял локальный Postgres 16 в песочнице, прогнал `db.init_db()` (полную схему из `db.py`,
включая миграцию Фазы 1) на пустой БД и проверил `set_product_cost_breakdown` /
`get_product_costs_for_dashboard` напрямую (без HTTP-слоя — тестовое окружение без
Telegram/секретов, полноценно поднять `main.py` не удалось бы):
- [x] `set_product_cost_breakdown(id, 'wb', 100, 20)` → `cost_wb = 120` = сумма статей;
  повторный вызов (эмуляция второго `onBlur`) корректно пересчитывает сумму (upsert).
- [x] Старый способ (`set_product_cost`, единое число из `/cost`/мастера) — `purchase_logistics`/
  `packaging_marking` остаются `NULL`, `cost` не ломается.
- [x] Товар только на одной площадке (WB без Ozon) — `ozon_offer_id`/`cost_ozon` = `NULL`,
  вторая площадка не фабрикуется.
- [x] Расчёт NET-маржи Питера не меняется: `product_costs.cost` (то самое поле из
  `agents/peter.py:392`) после правки через `set_product_cost_breakdown` равно сумме статей —
  проверено прямым SELECT той же колонки.
- [x] `dashboard`: `npx tsc --noEmit` проходит чисто.
- Не проверено (нет живого Telegram/браузера в песочнице): HTTP-слой `main.py` целиком
  (initData-валидация, коды ответов 400/404/429), реальный клик в браузере по дашборду,
  `/cost` в Telegram поверх тех же данных. Резолв `mapping_id` по `wb_article`/`ozon_offer_id`
  в `_handle_set_cost` — тот же паттерн, что и в проверенном `MaxAgent._apply_price`, но не
  прогнан живым запросом.

## Файлы

| Файл | Изменения |
|---|---|
| `db.py` | +2 колонки `product_costs`, +`set_product_cost_breakdown`, +`get_product_costs_for_dashboard` |
| `main.py` | +`GET /api/costs`, +`POST /api/set_cost` |
| `dashboard/src/api.ts` | +`CostRow`, +`getCosts`, +`setCost` |
| `dashboard/src/charts/CostEditor.tsx` (новый) | редактируемая таблица себестоимости |
| `dashboard/src/App.tsx` | вставить `CostEditor` |
