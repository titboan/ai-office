# Питер: план поставок по кластерам/регионам

Статус: завершён

## Цель

Когда пользователь спрашивает «в какие регионы везти и сколько», Питер должен выдавать
конкретный план: товар × кластер × текущий остаток × дней осталось × рекомендованное
количество к поставке.

## Ограничения данных

- `marketplace_stocks` → warehouse_name → кластер (есть)
- `marketplace_orders` — склад (регион заказа) НЕ хранится → нельзя разбить продажи по кластерам
- Прокси: days_left = stock_кластер / daily_rate_всего. Достаточно для приоритетизации поставок.

## Фазы

### Фаза 1: новый метод `_collect_supply_data` в peter.py
- [x] Запрос marketplace_stocks: product_id, warehouse_name, stock, marketplace
- [x] Join с product_mapping для display_name
- [x] Запрос sales velocity (orders/day за 14 дней) из marketplace_orders
- [x] Маппинг warehouses→clusters через `_get_cluster`/`_get_ozon_cluster` из max.py
- [x] Возвращает: cluster_stocks (product × cluster × stock), velocity (product × daily_rate)

### Фаза 2: новый метод `cmd_supply` в peter.py
- [x] Вычисляет days_left = cluster_stock / daily_rate per product per cluster
- [x] Вычисляет qty_to_send = max(0, TARGET_DAYS * daily_rate - cluster_stock), TARGET_DAYS=45
- [x] Формирует prompt Claude: таблица остатков + план поставки
- [x] Сохраняет в Notion

### Фаза 3: регистрация команды
- [x] Добавить `/supply` в `_bot_commands`
- [x] Зарегистрировать handler в `_register_extra_handlers`
- [x] Добавить в `_help_text` и inline-меню Питера

### Фаза 4: Марта-роутинг
- [x] Ключевые слова «поставки», «регионы», «склады» → supply data → peter handle_task

## Целевой вывод (пример)

```
📦 ПЛАН ПОСТАВКИ — следующие 30 дней

КБ50 (Ozon, 86 шт/день):
  Москва и МО     — 12 шт, 0 дн  🔴 СРОЧНО → везти 3 870 шт
  Центральный     — 45 шт, 0.5 дн 🔴 СРОЧНО → везти 1 800 шт
  Приволжский     — 230 шт, 2.7 дн 🟡 Скоро  → везти 950 шт
  Сибирский       — 180 шт, 2.1 дн 🟡 Скоро  → везти 1 125 шт

ТГ100 (WB, 1.6 шт/день):
  Центральный     — 44 шт, 27 дн  🟢 Норма
  Уральский       — 3 шт,  2 дн   🔴 СРОЧНО → везти 69 шт
```
