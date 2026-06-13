# Cost Tracking — estimated_cost + latency_ms

Статус: завершён

## Контекст

Сейчас токены только логируются в DEBUG. Стоимость не считается, латентность не хранится.
Нужны: `estimated_cost` (USD) и `latency_ms` в таблице `tasks`.
Отчёт от Питера по запросу. Из ROADMAP.md Phase 6.

## Фазы

- [x] Фаза 1: Создать план
- [x] Фаза 2: `db.py` — ALTER TABLE tasks ADD COLUMN estimated_cost + latency_ms
- [x] Фаза 3: `task_queue.py` — функция `update_task_cost(task_id, cost_usd, latency_ms)`
- [x] Фаза 4: `base_agent.py` — накопление токенов в `_task_tokens`, замер времени, вызов update_task_cost после завершения
- [x] Фаза 5: Коммит, пуш, обновить ROADMAP.md

## Ценообразование Claude API ($ за 1M токенов)

| Модель | Input | Output |
|--------|-------|--------|
| claude-sonnet-4-6 | $3.00 | $15.00 |
| claude-opus-4-8 | $15.00 | $75.00 |
| claude-haiku-4-5-20251001 | $0.80 | $4.00 |

## Что даёт

```sql
-- Топ дорогих задач за сегодня
SELECT assigned_agent, payload, estimated_cost, latency_ms
FROM tasks WHERE finished_at > NOW() - INTERVAL '1 day'
ORDER BY estimated_cost DESC NULLS LAST LIMIT 10;

-- Дневной расход по агентам
SELECT assigned_agent,
       COUNT(*) AS tasks,
       SUM(estimated_cost) AS total_usd,
       AVG(latency_ms) AS avg_ms
FROM tasks
WHERE status = 'completed' AND finished_at > NOW() - INTERVAL '1 day'
GROUP BY assigned_agent ORDER BY total_usd DESC;
```

## Файлы

| Файл | Изменение |
|------|-----------|
| `db.py` | ALTER TABLE estimated_cost + latency_ms |
| `task_queue.py` | +update_task_cost() |
| `base_agent.py` | +_COST_PER_1M, _calc_cost(), _task_tokens накопление, update_task_cost вызов |
