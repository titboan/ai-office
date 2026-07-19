# Разбивка аналитики по магазинам

Статус: завершён

## Проблема

При двух магазинах Ozon данные смешиваются в отчётах Питера: `marketplace_orders` не имеет
`shop_id`, поэтому невозможно отделить заказы магазина 1 от магазина 2.

## Фазы

### Фаза 1: DB — добавить shop_id в marketplace_orders
- [x] ALTER TABLE marketplace_orders ADD COLUMN shop_id BIGINT NOT NULL DEFAULT 0
- [x] save_order() — добавить параметр shop_id (default=0)
- [x] Исправить order_id Ozon-аналитики: включить shop_id (иначе конфликт при одинаковых SKU)

### Фаза 2: max.py — передавать shop_id при сохранении заказов
- [x] WB save_order() → shop["id"]
- [x] Ozon analytics save_order() → shop["id"], order_id = f"ozon_analytics_{shop['id']}_{product_id}_{date}"

### Фаза 3: peter.py — разбивка по магазинам
- [x] _collect_data(): JOIN marketplace_orders → marketplace_shops по shop_id
- [x] Если несколько Ozon-магазинов — GROUP BY (marketplace, shop_id, shop_name)
- [x] revenue / top_products / margin_ozon — включить shop_name в группировку
- [x] marketplace_financial_report — уже имеет shop_id, добавить JOIN для имени
- [x] В тексте отчёта: «Ozon» → «Ozon (Название магазина)» при мульти-Ozon
