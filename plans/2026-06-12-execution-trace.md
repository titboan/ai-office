# Execution Trace — таблица task_events

Статус: в работе

## Контекст

Нужен таймлайн каждой задачи и цепочки в Postgres.
Даёт: дебаг где сломалось, сколько длилось, база для Cost Tracking.
Из ROADMAP.md Phase 6.

## Фазы

- [x] Фаза 1: Создать план
- [x] Фаза 2: `db.py` — таблица `task_events` + функция `log_event()`
- [x] Фаза 3: `task_queue.py` — TASK_CREATED при enqueue
- [x] Фаза 4: `base_agent.py` — TASK_STARTED / TASK_COMPLETED / TASK_FAILED / CHAIN_ADVANCED
- [ ] Фаза 5: Коммит, пуш, обновить ROADMAP.md

## События

| event_type      | Когда логируем                        |
|-----------------|---------------------------------------|
| TASK_CREATED    | task_queue.create_task / enqueue_chain_task |
| TASK_STARTED    | _worker_loop после get_next_task      |
| TASK_COMPLETED  | _worker_loop после mark_completed     |
| TASK_FAILED     | _worker_loop timeout + exception      |
| CHAIN_ADVANCED  | _advance_chain при передаче следующему |

TOOL_CALLED / TOOL_FAILED — отдельная задача (требует правки 5+ агентов).

## Файлы

| Файл | Изменение |
|------|-----------|
| `db.py` | CREATE TABLE task_events + log_event() |
| `task_queue.py` | +log_event в create_task, enqueue_chain_task |
| `base_agent.py` | +log_event в _worker_loop, _advance_chain |
