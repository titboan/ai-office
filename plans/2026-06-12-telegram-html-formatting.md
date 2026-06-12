# Переход на Telegram HTML + оптимизация моделей

Статус: в работе

## Контекст
Telegram обновил рендеринг — добавили expandable blockquotes, улучшенный HTML.
Переходим с `parse_mode="Markdown"` на `parse_mode="HTML"` во всех агентах.
Notion остаётся для projects/research/content, статус-страница переезжает в pinned message.

---

## Фаза 1: Инфраструктура

- [x] Создать `utils/__init__.py`
- [x] Создать `utils/tg_format.py` — HTML helpers: bold, italic, code, pre, quote, quote_expandable, link, section, table, strip_html, HTML_FORMAT_RULES
- [x] Обновить `config.py` — добавить `CLAUDE_HAIKU_MODEL` и `CLAUDE_OPUS_MODEL`

## Фаза 2: base_agent.py

- [x] `cmd_start` — `*{name}*` → `<b>{name}</b>`, `parse_mode="HTML"`
- [x] `think()` — использует `getattr(self, 'claude_model', None) or config.CLAUDE_MODEL`
- [x] `_notify_user()` — все три `parse_mode="Markdown"` → `"HTML"`

## Фаза 3: Системные промпты и parse_mode агентов

- [x] `kasper.py` — новый HTML system prompt, `claude_model = CLAUDE_OPUS_MODEL`, включён parse_mode в отправке
- [x] `peter.py` — новый HTML system prompt, убраны `_format_for_telegram()` и `_format_for_notion()`, добавлен `strip_html` для Notion, обновлены reply вызовы
- [x] `alex.py` — новый HTML system prompt, `claude_model = CLAUDE_HAIKU_MODEL`, убран `create_task()` (Notion Tasks DB), все `parse_mode="HTML"`
- [x] `elina.py` — новый HTML system prompt, все `parse_mode="HTML"`
- [x] `eva.py` — обновлён `_DIGEST_PROMPT` с HTML инструкциями, `parse_mode="HTML"`
- [x] `max.py` — новый HTML system prompt, `_HELP_TEXT` переведён в HTML, `_generate_reply()` → haiku, все `parse_mode="HTML"`
- [x] `marta.py` — все `*bold*` → `<b>bold</b>`, все `parse_mode="HTML"`, `_plan_chain` → `CLAUDE_OPUS_MODEL`, `_create_project_page` title generation → `CLAUDE_HAIKU_MODEL`

## Фаза 4: Статус в Telegram (pinned message)

- [x] Создать `tools/tg_status.py` — `update_status_pinned(bot, redis, active_tasks, recent_tasks)`
  - Первый вызов: `send_message` + `pin_chat_message`, message_id в Redis под ключом `status:pinned_msg_id`
  - Последующие: `edit_message_text`
  - Fallback: если edit не удался (сообщение удалено) — создать новое и перезакрепить
- [x] Обновить `main.py`:
  - Убрать `from tools.notion import update_status_page`
  - Добавить `from tools.tg_status import update_status_pinned`
  - В `_status_page_loop()`: передавать `started[0].app.bot`

## Фаза 5: Коммит и деплой

- [ ] `git add` поимённо все изменённые файлы
- [ ] `git commit` с осмысленным сообщением
- [ ] Борис деплоит на Railway, проверяет логи

---

## Что НЕ менялось (Notion остаётся)
- `tools/notion.py` — без изменений
- `get_project_context()` — остаётся для resume projects
- Research DB / Content DB — Касп/Питер/Элина продолжают писать, теперь передают `strip_html(answer)` перед сохранением
- Ideas DB — не трогаем (не используется)

## Проверка после деплоя
1. Написать каждому агенту тестовое сообщение — убедиться что HTML рендерится
2. Проверить /report у Питера — таблицы заменены HTML-форматом
3. Проверить /plan у Алекса — нет ссылки на Notion Tasks
4. Убедиться что статус обновляется в pinned message группы офиса
5. Проверить Railway логи — нет `BadRequest: can't parse entities`
