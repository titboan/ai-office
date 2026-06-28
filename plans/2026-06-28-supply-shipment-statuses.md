# Статусы поставок на МП в плане поставок

Статус: в работе

## Цель
Добавить в отчёт `/supply` (Питер) статусы поставок, уже зарегистрированных на WB и Ozon:
- Отгрузка разрешена (WB statusID=3)
- В пути / Транзит (WB statusID 7/8)
- Принято (WB statusID 5/6)
- Состояния Ozon supply orders (state string)

## Фазы

### Фаза 1: БД — новая таблица [ ]
- `marketplace_supply_orders(chat_id, marketplace, supply_id, status_id, status_name, product_id, qty, synced_at)`
- `upsert_supply_orders()` и `get_active_supply_orders()` в db.py

### Фаза 2: WB — метод get_supply_statuses() [ ]
- В WBClient (tools/marketplace.py)
- Вызывает `/api/v3/supplies` (уже используется), извлекает statusID
- Маппинг: 1→Не запланировано, 2→Запланировано, 3→Отгрузка разрешена, 4→Идёт приёмка, 5→Принято, 6→Отгружено на воротах, прочие→Статус N
- Для активных поставок (не done) вызывает `/api/v3/supplies/{id}/orders` → product_id + qty

### Фаза 3: Ozon — метод get_supply_statuses() [ ]
- В OzonClient (tools/marketplace.py)
- POST `/v2/supply-order/list` (фильтр: активные), POST `/v2/supply-order/get` для деталей
- Статусы: state строки из ответа (SUPPLY_ORDER_STATE_*)
- Graceful fallback если API недоступен

### Фаза 4: Sync в max.py [ ]
- В `sync_marketplace_data()` после остатков
- Вызов `client.get_supply_statuses()` для wb и ozon
- `upsert_supply_orders(chat_id, marketplace, rows)`

### Фаза 5: Питер — _collect_supply_data + промпт [ ]
- Query `marketplace_supply_orders` → сгруппировать по product_id + status_name
- Добавить `mkt_supplies: [{status, qty}]` в каждый продукт
- Обновить промпт cmd_supply: показывать активные поставки под каждым товаром

## Источники
- WB: `/api/v3/supplies` (marketplace-api.wildberries.ru) — уже работает в get_in_transit()
- Ozon: POST `/v2/supply-order/list` → POST `/v2/supply-order/get`
- WB statusID 1-6 документированы, 7/8 (в пути/транзит) — из UI, не из YAML spec
