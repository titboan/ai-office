# Рефакторинг антипаттернов

Статус: в работе

## Контекст

Выявлено 6 антипаттернов в ходе аудита кодовой базы. Два из них дёшевы в исправлении и дают высокую отдачу — их закрываем в первую очередь. Остальные — по мере касания файлов.

---

## Фаза 1 — Единый реестр агентов (приоритет: высокий)

**Проблема:** словари `{agent_key → name/emoji}` продублированы в 6+ местах:
`base_agent.py`, `marta.py` (×2), `tools/tg_status.py`, `tools/notion.py`, и внутри `_advance_chain`.
Добавление нового агента требует обновления всех мест вручную.

- [ ] Создать `agents/registry.py` с единым реестром:
  ```python
  AGENTS = {
      "marta":  {"name": "Марта",  "emoji": "👩‍💼", "timeout": 300},
      "kevin":  {"name": "Кевин",  "emoji": "👨‍💻", "timeout": 300},
      "kasper": {"name": "Каспер", "emoji": "🔍",  "timeout": 300},
      "peter":  {"name": "Питер",  "emoji": "📊",  "timeout": 300},
      "elina":  {"name": "Элина",  "emoji": "✍️",  "timeout": 300},
      "alex":   {"name": "Алекс",  "emoji": "🗓️",  "timeout": 300},
      "dan":    {"name": "Дэн",    "emoji": "🎨",  "timeout": 600},
      "eva":    {"name": "Ева",    "emoji": "📰",  "timeout": 300},
      "max":    {"name": "Макс",   "emoji": "🛒",  "timeout": 300},
      "tina":   {"name": "Тина",   "emoji": "📋",  "timeout": 300},
  }
  ```
- [ ] Заменить дубли в `base_agent.py` (`_AGENT_NAMES`, `_AGENT_EMOJI`, `_AGENT_NAME`) на импорт из реестра
- [ ] Заменить дубли в `marta.py` (`_CHAIN_AGENT_EMOJI`, `_CHAIN_AGENT_NAMES`, `_AGENT_EMOJI`)
- [ ] Заменить дубли в `tools/tg_status.py`
- [ ] Заменить дубли в `tools/notion.py`

---

## Фаза 2 — Таймаут агента из реестра (приоритет: высокий)

**Проблема:** `timeout_seconds=600 if next_agent == "dan" else 300` хардкодом в 4 местах.
Хрупко: переименование агента или добавление нового медленного сломает тихо.

- [ ] Добавить в `agents/registry.py` поле `timeout` (уже заложено в Фазе 1)
- [ ] Заменить все 4 вхождения `600 if next_agent == "dan" else 300` на `AGENTS.get(next_agent, {}).get("timeout", 300)`
  - `base_agent.py:1034`
  - `marta.py:393`
  - `marta.py:716`
  - `marta.py:761`

---

## Фаза 3 — Убрать `pool=None` из сигнатур (приоритет: средний)

**Проблема:** `get_chain_plan(None, chain_id)` и `enqueue_chain_task(pool=None, ...)` — параметр `pool` врёт: `None` это нормальный рабочий путь, а не «не передан».

- [ ] В `task_queue.py`: убрать параметр `pool` из функций `get_chain_plan`, `enqueue_chain_task`, `get_chain_results`, `count_incomplete_in_group` — всегда вызывать `get_pool()` внутри
- [ ] Обновить все вызывающие места (в `base_agent.py` и `marta.py`)

> Делать при следующем касании `task_queue.py` по другой причине.

---

## Фаза 4 — Напоминания Алекса в BaseAgent (приоритет: низкий)

**Проблема:** `base_agent.py:690–714` — каждый агент (Марта, Питер, Макс…) при каждой 30-й итерации делает запрос к БД за напоминаниями. Это логика Алекса.

- [ ] Перенести блок `get_due_reminders` + `send_push` в `AlexAgent._worker_loop` (override с `await super()._worker_loop_tick()` или отдельным методом)

> Делать при следующем касании `alex.py` или `base_agent.py`.

---

## Не делать

- **Вынос `_advance_chain` в отдельный класс** — архитектурно правильно, но цена высокая, выгода нулевая пока цепочки не станут источником багов.
- **Переписывать dict-fallback для Redis** — в prod Redis всегда есть. Достаточно `logger.warning` при старте если Redis недоступен.

---

## Порядок выполнения

1. Фаза 1 + Фаза 2 — одним PR (реестр + таймауты, логически связаны)
2. Фаза 3 — отдельный PR при следующем касании `task_queue.py`
3. Фаза 4 — отдельный PR при следующем касании `alex.py`
