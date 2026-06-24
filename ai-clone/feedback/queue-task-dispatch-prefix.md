---
name: queue-task-dispatch-prefix
description: Когда агент получает задачи из очереди (handle_task), нельзя передавать описательный текст в think() — Claude без инструментов ответит «нет инструментов»
metadata:
  type: feedback
---

При добавлении proxy-команд на Марту payload нельзя оставлять описательным текстом ("покажи список магазинов") — агенты получают его через очередь в `handle_task`, который передаёт текст в `think()` без инструментов и данных.

**Why:** `think()` — это обычный вызов Claude без tools. Если агент не имеет dispatch-логики в `handle_task`, Claude честно отвечает «нет инструментов». Баг обнаружен при тестировании `/shops` через Марту (2026-06-24).

**How to apply:**
- Для каждого агента-получателя проверь, умеет ли его `handle_task` обрабатывать переданный payload.
- Если нет → используй `__keyword__` префиксы и добавляй dispatch в `handle_task`.
- Паттерн: `if task.startswith("__") → _dispatch_queue_task(task, _current_chat_id)`
- Питер: уже имеет keyword-матчинг + `__order__` dispatch.
- Макс: добавлен `_dispatch_queue_task` (2026-06-24) с helper'ами `_X_text(chat_id)`.
- При добавлении новых proxy-команд — сразу добавлять dispatch в агент-получатель.
