# Рискованный execute() внутри транзакции — оборачивай в savepoint

**Rule:** Если внутри `async with conn.transaction():` есть `conn.execute(...)`,
который может кинуть исключение (`UniqueViolationError` и т.п.) и это
ожидаемо/штатно (не баг, а нормальный edge case, который нужно поймать и
продолжить работу) — оборачивай именно этот `execute` в СВОЙ вложенный
`async with conn.transaction():` (asyncpg превращает вложенный вызов в
`SAVEPOINT`). Голого `try/except` вокруг `execute()` НЕДОСТАТОЧНО.

**Why:** В Postgres/asyncpg ошибка одного `execute()` внутри транзакции
переводит ВСЮ транзакцию в состояние "aborted" — если поймать исключение в
Python без вложенного savepoint, следующий `execute()`/`fetch()` в ЭТОЙ ЖЕ
транзакции упадёт с "current transaction is aborted, commands ignored until
end of transaction block", а вся транзакция (включая уже успешно выполненные
более ранние операции) откатится при выходе из внешнего `async with
conn.transaction():`.

Эта ошибка была найдена дважды в одном проекте за две соседние сессии:
- `agents/max.py::_auto_populate_side` (`plans/2026-07-14-guided-onboarding-
  analytics.md`, Фаза 2) — написано правильно с первого раза, savepoint на
  каждый товар.
- `db.py::merge_product_rows` (`plans/2026-07-14-cross-marketplace-product-
  merge.md`, Фаза 2) — написано БЕЗ savepoint, несмотря на то, что первый
  случай уже был в том же проекте — субагент не знал про прошлый урок,
  потому что промпт оркестратора не сослался на него явно.

**How to apply:**
1. Любой `execute()`/`fetch()` внутри транзакции, для которого код explicitly
   ожидает и ловит исключение (`try/except SomeError`) — оборачивай в
   `async with conn.transaction():` (вложенный = savepoint).
2. При делегировании такой задачи субагенту — не полагайся на то, что он
   сам вспомнит паттерн из другого файла проекта; сошлись явно на конкретную
   функцию-образец (например "оберни как `_auto_populate_side`, db.py:XXXX")
   или процитируй это правило целиком в промпте.
3. При ревью диффа с транзакциями — отдельно проверь каждый `try/except`
   внутри `async with conn.transaction():`: есть ли внутри него свой
   вложенный `conn.transaction()`, или голый `execute()`.
