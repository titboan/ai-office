# Управление рекламными кампаниями Ozon

**Дата:** 2026-06-26  
**Статус: завершён**

## Цель

Замкнуть петлю аналитики → действия для рекламы:
Питер видит DRR/ROAS → предлагает паузу/корректировку → пользователь одобряет inline-кнопкой → Макс выполняет через Ozon Performance API.

## Фазы

### Фаза 1 — Performance API write-методы в marketplace.py [x]
- `OzonPerformanceClient.get_campaigns()` — список кампаний с метриками
- `OzonPerformanceClient.pause_campaign(campaign_id)` — пауза
- `OzonPerformanceClient.activate_campaign(campaign_id)` — запуск
- `OzonPerformanceClient.update_campaign_daily_budget(campaign_id, budget)` — бюджет

### Фаза 2 — Команды управления в max.py [x]
- `_get_campaign_cards()` + `_execute_camp_action()` — переиспользуемая логика
- `/campaigns` с кнопками ⏸️/▶️; callback `camp:pause/activate:{shop_id}:{id}`
- Логика паузы Ozon при ДРР > threshold в `auto_bid_suggest`
- Пороги в `config.py`: DRR_PAUSE_THRESHOLD_OZON=40%, DRR_ALERT_THRESHOLD=25%

### Фаза 3 — Callback-кнопки через Марту [x]
- `camp:` callback зарегистрирован на App Марты → вызывает `_execute_camp_action`
- `/campaigns` в Марте вызывает `_get_campaign_cards` напрямую (без очереди)
- `main.py`: marta_agent._max_agent = max_agent при старте

### Фаза 4 — Управление акциями Ozon [x]
- OzonClient: `get_available_promotions()`, `get_action_products()`, `join_promotion()`, `exit_promotion()`
- `/promotions` — карточки акций с расчётом изменения маржи (учитывает себестоимость и комиссию Ozon 15%)
- Кнопки ✅ Войти / ❌ Пропустить; `promo:` callbacks на Максе и Марте
- Кнопки «📣 Кампании» и «🎁 Акции» в Меню Марты → Маркетплейсы

## Файлы

| Файл | Изменения |
|------|-----------|
| `tools/marketplace.py` | +4 метода OzonPerformanceClient |
| `agents/max.py` | обработка `__campaigns__`, callback camp_* |
| `agents/marta.py` | регистрация camp_* callbacks |
