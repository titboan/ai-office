# Supply: Ozon-кластеры + категорийные склады WB + время доставки

Статус: в работе

## Проблемы
1. `/supply` не показывает распределение по складам Ozon (данные есть, промпт не инструктирован)
2. Склады WB в промпте — все подряд, без учёта категории «лакомства и корм»
3. Время доставки от поставщика (SUPPLY_LEAD_TIME_DAYS=21) игнорируется в `/supply` — нет правила срочности "кончится до прихода партии"

## Решение

### Фаза 1 — config.py [ ]
- Добавить WB_FOOD_CATEGORY_WAREHOUSES — список складов WB для зоокормов/лакомств
- Добавить WB_FOOD_CATEGORIES — список категорий для матчинга

### Фаза 2 — _collect_supply_data [ ]
- SQL raw_stocks: добавить MAX(m.category)
- cluster_stocks: сохранить category
- result: добавить category, lead_days, safety_days, total_days_left (суммарный по всем кластерам)

### Фаза 3 — cmd_supply промпт [ ]
- Добавить lead_days/safety_days в пояснение полей
- Правила срочности через lead_days (КРИТИЧНО = кончится до прихода партии)
- Блок распределения по кластерам Ozon (аналогично WB)
- Правило: для category в WB_FOOD_CATEGORIES — только склады из WB_FOOD_CATEGORY_WAREHOUSES

### Фаза 4 — handle_task supply [ ]
- Добавить lead_days в контекст промпта (строка ~877)
