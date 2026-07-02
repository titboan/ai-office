# Единый расчёт остатков с учётом поставок (in_transit + supply_committed)

Статус: завершён

## Проблема

Найдено 3 места, где считаются остатки/дозаказ, и каждое учитывает поставки по-разному:

1. `Peter._collect_supply_data` (`agents/peter.py`, команда `/supply`) — эталон:
   `to_order = max(0, total_need - in_transit - supply_committed)`.
   `in_transit` из `marketplace_in_transit` (аналитика остатков WB/Ozon),
   `supply_committed` из `marketplace_supply_orders` (активные статусы, множества
   `_WB_COMMITTED`/`_OZON_COMMITTED` захардкожены прямо в функции).

2. `Peter._collect_order_advice_data` (`agents/peter.py`, команда `/order`) —
   считает `qty_30d/60d/90d` только от `total_stock`, **вообще не учитывает**
   ни `in_transit`, ни `supply_committed`. Может рекомендовать заказ, который уже
   и так едет или оформлен в поставке.

3. `Max._check_stock_alerts` (`agents/max.py`, фоновые алерты + `/sync` сводка) —
   учитывает `in_transit` из `marketplace_in_transit`, но **не учитывает**
   `supply_committed` из `marketplace_supply_orders` вообще.

Результат: `/supply`, `/order` и алерты Макса могут дать разные рекомендации по
одному и тому же товару.

## Решение

### Фаза 1 — config.py [x]
- Вынести `_WB_COMMITTED`/`_OZON_COMMITTED` из `agents/peter.py` в CONSTANTS:
  `config.SUPPLY_COMMITTED_STATUSES_WB`, `config.SUPPLY_COMMITTED_STATUSES_OZON`

### Фаза 2 — db.py: общий хелпер [x]
- `get_supply_pipeline(chat_id) -> dict[(marketplace, product_id), {"in_transit", "supply_committed"}]`
- Собирает `marketplace_in_transit` + `marketplace_supply_orders` (фильтр по
  статусам из `config.SUPPLY_COMMITTED_STATUSES_*`) в одну карту

### Фаза 3 — agents/peter.py [x]
- `_collect_supply_data`: использовать константы из config вместо хардкода
- `_collect_order_advice_data`: подтянуть `get_supply_pipeline`, вычитать
  `in_transit + supply_committed` при расчёте `qty_30d/60d/90d`; добавить поля
  `in_transit`/`supply_committed` в результат; обновить промпт `_order_analysis`

### Фаза 4 — agents/max.py [x]
- `_check_stock_alerts`: подтянуть `get_supply_pipeline`, вычитать
  `supply_committed` (в дополнение к уже вычитаемому `in_transit`) при расчёте
  `qty_to_order` и `covered`

### Фаза 5 — проверка [x]
- Прогнать синтаксис (`python -m py_compile`)
- Убедиться что нет циклических импортов
