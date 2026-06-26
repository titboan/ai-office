# Supply: Ozon-кластеры + категорийные склады WB + время доставки

Статус: завершён

## Проблемы
1. `/supply` не показывал распределение по складам Ozon (данные есть, промпт не инструктирован)
2. Склады WB в промпте — все подряд, без учёта категории «лакомства и корм»
3. Время доставки от поставщика (SUPPLY_LEAD_TIME_DAYS=21) игнорировалось в `/supply` — нет правила срочности "кончится до прихода партии"

## Решение

### Фаза 1 — config.py [x]
- Убрали WB_FOOD_CATEGORY_WAREHOUSES (статичный список) — заменили на live WB API
- WB_FOOD_CATEGORIES оставили для контекста категории

### Фаза 2 — _collect_supply_data [x]
- SQL raw_stocks: добавлен MAX(m.category)
- cluster_stocks: сохраняет category
- result: добавлены category, lead_days, safety_days, total_days_left, wb_open_warehouses

### Фаза 3 — cmd_supply промпт [x]
- Добавлены lead_days/safety_days и правила срочности (КРИТИЧНО = total_days_left < lead_days)
- Блок распределения по кластерам Ozon
- WB-склады из live API (коэффициенты приёмки), с кешем Redis 6ч
- Для зоокормов (WB_FOOD_CATEGORIES) — рекомендовать только открытые склады из API

### Фаза 4 — handle_task supply [x]
- lead_days в контексте промпта (из _get_lead_days)

### Фаза 5 — пользовательский срок поставки [x]
- Таблица user_settings (chat_id, key, value) в db.py
- Хелперы get_user_setting / set_user_setting в db.py
- _get_lead_days() и _get_safety_days() — DB → config fallback
- Команда /set lead_time=N / /set safety=N
- Распознавание из естественного языка в handle_task
- Использование во всех расчётах: _collect_supply_data, _collect_order_advice_data, _order_analysis
