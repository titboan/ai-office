# Управление рекламными кампаниями Ozon

**Дата:** 2026-06-26  
**Статус: в работе**

## Цель

Замкнуть петлю аналитики → действия для рекламы:
Питер видит DRR/ROAS → предлагает паузу/корректировку → пользователь одобряет inline-кнопкой → Макс выполняет через Ozon Performance API.

## Фазы

### Фаза 1 — Performance API write-методы в marketplace.py [ ]
- `OzonPerformanceClient.get_campaigns()` — список кампаний с метриками
- `OzonPerformanceClient.pause_campaign(campaign_id)` — пауза
- `OzonPerformanceClient.activate_campaign(campaign_id)` — запуск
- `OzonPerformanceClient.update_campaign_daily_budget(campaign_id, budget)` — бюджет

### Фаза 2 — Команды управления в max.py [ ]
- `__campaigns__` payload → список кампаний с DRR и кнопками
- Callback `camp_pause:<id>` → пауза после подтверждения
- Callback `camp_activate:<id>` → запуск после подтверждения
- Добавить `/campaigns` в прокси-команды Марты

### Фаза 3 — Callback-кнопки кампаний через Марту [ ]
- Зарегистрировать `camp_*` callbacks на App Марты (часть Фазы 2 single-entry-marta)
- Кнопки /apply_prices, /bid_adjust тоже перенести на Марту

## Файлы

| Файл | Изменения |
|------|-----------|
| `tools/marketplace.py` | +4 метода OzonPerformanceClient |
| `agents/max.py` | обработка `__campaigns__`, callback camp_* |
| `agents/marta.py` | регистрация camp_* callbacks |
