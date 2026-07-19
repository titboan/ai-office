# Себестоимость: история изменений + мобильный вид карточками

**Дата:** 2026-07-16
**Статус:** завершён (код всех фаз; живая проверка в Telegram Mini App на телефоне не выполнена — нет окружения из песочницы)

## Контекст

`plans/2026-07-15-cost-price-dashboard-editor.md` перенёс Excel-юнитку в дашборд —
редактируемая таблица `CostEditor.tsx` с автосохранением по `onBlur`. Пользователь
опробовал на телефоне (Telegram Mini App) и дал два замечания сверх точечных UX-багов
(клавиатура/статус сохранения — это уже отдельно, без плана, мелкая правка):

1. Нет истории изменений себестоимости — если ввёл число и не уверен, применилось ли,
   негде посмотреть "что и когда поменялось". Сейчас `product_costs` (`db.py:248-256`)
   хранит только текущее значение (`cost`, `purchase_logistics`, `packaging_marking`,
   `updated_at`) — история перезаписывается при каждом `set_product_cost_breakdown`
   (`db.py:2607-2626`, `ON CONFLICT ... DO UPDATE`).
2. Таблица `CostEditor.tsx` — 7 узких колонок, на телефоне тесно и нужен горизонтальный
   скролл (см. скриншот пользователя: `Товар | WB закупка+лог | WB упак+марк | WB итого |
   Ozon закупка+лог | Ozon упак+марк | Ozon итого`). Нужен отдельный вид для мобильного —
   карточка на товар с крупными полями вместо таблицы.

## Цель

Добавить лог изменений себестоимости (кто/когда/что было → что стало) и адаптивный
карточный вид `CostEditor` для узких экранов, не трогая расчёт NET-маржи Питера
(`agents/peter.py:392`, читает `product_costs.cost` — не меняется).

## Фазы

### Фаза 1 — db.py: таблица истории [x]
- `CREATE TABLE IF NOT EXISTS product_cost_history` (по образцу `product_costs`,
  `db.py:248-256`): `id BIGSERIAL PK`, `mapping_id BIGINT REFERENCES product_mapping(id)`,
  `marketplace TEXT`, `purchase_logistics NUMERIC`, `packaging_marking NUMERIC`,
  `cost NUMERIC`, `changed_by BIGINT` (chat_id из initData), `created_at TIMESTAMPTZ
  DEFAULT now()`. Без `UNIQUE` — каждая запись это отдельное событие, не upsert.
- В `set_product_cost_breakdown` (`db.py:2607-2626`) добавить параметр `changed_by:
  int | None = None` и после `UPDATE`/`INSERT` в `product_costs` — `INSERT` строки в
  `product_cost_history` с теми же значениями. Один вызов, та же транзакция/conn.
- `get_cost_history(mapping_id: int, marketplace: str, limit: int = 20) -> list[dict]` —
  последние записи по товару+площадке, `ORDER BY created_at DESC`.

### Фаза 2 — main.py: API [x]
- `POST /api/set_cost` (`main.py:1033-1090`) — передать `chat_id` (уже есть в хендлере,
  строка 1054) в `set_product_cost_breakdown(..., changed_by=chat_id)`.
- `GET /api/cost_history?mapping_id=&marketplace=` — по образцу `_handle_get_costs`
  (`main.py:991-1031`), тот же initData-only паттерн без `?token=`, rate-limit.

### Фаза 3 — dashboard: показать историю [x]
- `dashboard/src/api.ts`: тип `CostHistoryRow`, функция `getCostHistory(mappingId,
  marketplace)`.
- В `CostEditor.tsx` — при клике на "Итого" (или отдельная иконка часов рядом) открыть
  маленький попап/раскрывающийся блок под строкой с последними 5 записями: дата, было →
  стало. Не отдельная страница — остаёмся на той же карточке.

### Фаза 4 — dashboard: карточный вид на мобильном [x]
- В `CostEditor.tsx` — при ширине экрана `< 640px` (Tailwind `sm:`) рендерить вместо
  `<table>` список карточек: одна карточка на товар, внутри — крупно название, затем
  для каждой площадки, где товар продаётся, два поля ввода (закупка+логистика,
  упаковка+маркировка) друг под другом с подписями текстом (не в шапке таблицы), и
  "Итого" снизу. Логика `fields`/`save`/`status` та же, меняется только разметка —
  вынести JSX ячейки в общую функцию/подкомпонент, чтобы не дублировать код инпутов
  между табличным и карточным вариантом.
- Проверить в браузере на узком viewport (375px) и на десктопном (что табличный вид не
  сломался).

### Фаза 5 — проверка
- [x] Прогнал миграцию Фазы 1 на локальном Postgres 16 в песочнице (`pg_ctlcluster`),
  на пустой БД (`product_mapping` создан вручную — известный пробел, отдельный план
  `plans/2026-07-18-product-mapping-chat-id-isolation.md`, не в рамках этой фичи).
  Проверено напрямую через `db.py` (без HTTP-слоя, как в Фазе 4 предыдущего плана):
  `set_product_cost_breakdown` пишет и в `product_costs` (cost = сумма статей,
  расчёт NET-маржи Питера не задет), и в `product_cost_history`; `get_cost_history`
  отдаёт записи по убыванию `created_at`; старый вызов без `changed_by` не падает
  (дефолт `None`, запись в истории с `changed_by = NULL`); повторный `init_db()` на
  уже заполненной БД — идемпотентен, без ошибок.
- [x] `npx tsc --noEmit` в `dashboard/` — чисто (проверено дважды: субагентом в
  Фазе 3 и повторно оркестратором).
- [ ] Живой клик в браузере / на телефоне — не выполнено в этой сессии (нет
  Telegram Mini App окружения в песочнице). Визуальную проверку карточного вида
  и попапа истории на реальном устройстве нужно сделать отдельно.

## Файлы

| Файл | Изменения |
|---|---|
| `db.py` | +таблица `product_cost_history`, `set_product_cost_breakdown` пишет историю, +`get_cost_history` |
| `main.py` | `_handle_set_cost` передаёт `changed_by`, +`GET /api/cost_history` |
| `dashboard/src/api.ts` | +`CostHistoryRow`, +`getCostHistory` |
| `dashboard/src/charts/CostEditor.tsx` | попап истории, адаптивный карточный вид на мобильном |
