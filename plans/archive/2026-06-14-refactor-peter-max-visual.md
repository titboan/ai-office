# Рефакторинг peter.py и max.py: дубли и визуал

Статус: завершён
Дата: 2026-06-14

## Контекст

Убрать дублирующийся код и выровнять форматирование вывода в Telegram.

## Фазы

- [x] **1.** `utils/tg_format.py` — добавить `format_money()`
- [x] **2.** `agents/peter.py` — метод `_send_answer()` (убрать 5x повторение блока Notion + чанки + дашборд)
- [x] **3.** `agents/peter.py` — использовать `HTML_FORMAT_RULES` в промптах вместо ручного HTML-гайда
- [x] **4.** `agents/peter.py` — `cmd_funnel`: добавить `system=PETER_SYSTEM` в вызов Claude
- [x] **5.** `agents/peter.py` — унифицировать loading-сообщения, убрать дублирующее `"🤔 Анализирую…"`
- [x] **6.** `agents/peter.py` — HTML-форматирование в справке `/analyze`
- [x] **7.** `agents/max.py` — объединить `_send_wb_stocks()` + `_send_ozon_stocks()` → `_send_stocks(marketplace)`
