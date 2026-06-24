# Алекс как планировщик с памятью

Статус: в работе

Задача: пользователь хочет использовать AI Office как планировщик — планы хранятся в Postgres и присылаются по запросу. Работает через свободный текст и команды через Марту.

---

## Архитектура

```
"покажи планы" → Мarta → Alex → handle_task → _run_with_tools()
                                               → Claude (Haiku) + manage_plans tool
                                               → _run_plans_tool() → DB
                                               → Claude форматирует ответ
                                               → Марта отправляет пользователю

"/plans"       → Марта → Alex → __plans__ dispatch → get_user_plans() → текст
```

## Таблица user_plans

```sql
id, chat_id, title, notes, priority (low/medium/high/urgent),
category, deadline (DATE), status (active/in_progress/done/archived),
created_at, updated_at
```

---

## Фазы

### Фаза 1 — DB + Alex tool_use [ ]
- [ ] Добавить `user_plans` в `_create_schema()` в `db.py`
- [ ] Добавить функции: `create_user_plan`, `get_user_plans`, `update_user_plan`, `delete_user_plan`
- [ ] Добавить `ALEX_TOOLS` + `_run_with_tools()` + `_run_plans_tool()` в `alex.py`
- [ ] Обновить `handle_task` Алекса: `__plans__` dispatch + tool_use loop для остального
- [ ] Добавить `cmd_plans` команду

### Фаза 2 — Marta proxy [ ]
- [ ] Добавить `cmd_proxy_plans` → Alex `__plans__`
- [ ] Обновить Marta меню (раздел "Офис" или новый "Задачи")
- [ ] Зарегистрировать `/plans` handler на App Марты
