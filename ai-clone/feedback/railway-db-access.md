---
name: railway-db-access
description: "railway run psql к production БД разрешён для задач отладки без подтверждения"
metadata:
  type: feedback
---

Борис разрешает выполнять `railway run psql $DATABASE_PUBLIC_URL` для диагностики и отладки задач проекта. SELECT-запросы к данным (отзывы, заказы, статусы, chat_id) можно читать без дополнительного подтверждения.

**Why:** Прямой доступ к БД нужен для диагностики багов — спрашивать каждый раз неудобно.

**How to apply:** При задачах отладки можно сразу выполнять SELECT через `railway run psql`. На изменяющие запросы (INSERT/UPDATE/DELETE) всё равно спрашивать.
