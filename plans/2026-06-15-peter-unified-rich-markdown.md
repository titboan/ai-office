# План: унификация формата Питера на Rich Markdown

Статус: завершён

## Контекст

В ходе аудита форматирования выяснилось, что у Питера три команды используют отдельный HTML-путь:
`/audit`, `/drr`, `run_weekly_audit` — системные промпты в HTML, отправка через PTB с `parse_mode="HTML"`.

Остальные 8 агентов (и `/report`, `/funnel` у самого Питера) работают через `sendRichMessage`.
Разнобой создаёт: риск рассинхрона, лишний параметр `parse_mode` в `_send_answer`, два кодовых пути.

**Цель:** один механизм отправки Claude-output для всего Питера — `send_rich_or_fallback`.

---

## Затронутые файлы

Только **`agents/peter.py`**. Ничего больше трогать не нужно.

---

## Фазы

### [x] Фаза 1 — Конвертация промптов

Переписать `PETER_AUDIT_PROMPT` (строки 65–103) и `PETER_DRR_PROMPT` (строки 105–133)
с HTML на Rich Markdown по таблице замен:

| HTML | Rich Markdown |
|---|---|
| `<b>текст</b>` | `**текст**` |
| `<code>текст</code>` | `` `текст` `` |
| `<blockquote>текст</blockquote>` | `> текст` |
| `&lt;` | `<` |
| `&gt;` | `>` |

Добавить в начало каждого промпта стандартные Rich Markdown правила (те же что в `PETER_SYSTEM`):
спецсимволы без экранирования, не использовать HTML-теги.

### [x] Фаза 2 — Упрощение `_send_answer`

Текущий `_send_answer` (строки 388–446) содержит `parse_mode: str = "MarkdownV2"` параметр
и HTML-ветку (~15 строк). После фазы 1 HTML-ветка становится мёртвым кодом.

Убрать:
- параметр `parse_mode`
- блок `if parse_mode == "HTML": ...` целиком

Оставить только Rich Markdown ветку:
```python
async def _send_answer(self, answer, *, notion_title, notion_source,
                       notion_link_text="Сохранено в Notion",
                       show_dashboard=True, update=None, chat_id=None, bot=None) -> None:
    notion_url = await save_research(...)
    if notion_url:
        answer = f'{answer}\n\n📄 [{notion_link_text}]({notion_url})'
    _cid = chat_id or (update.effective_chat.id if update else None)
    if _cid:
        markup_dict = None
        if show_dashboard and config.DASHBOARD_URL and update:
            ...
        await _send_rich(self.bot_token, _cid, answer, reply_markup_dict=markup_dict)
```

### [x] Фаза 3 — Обновить вызовы

Убрать `parse_mode="HTML"` из трёх вызовов `_send_answer`:
- `cmd_audit` (~строка 623)
- `cmd_drr` (~строка 691)
- `run_weekly_audit` (~строка 860)

---

## Что НЕ трогаем

- Макс: его 9 статичных `reply_text(..., parse_mode="HTML")` — не Claude output, баг там не возникает
- Статичные сообщения Питера (`reply_text` на строках 747, 813) — не Claude output, оставить как есть
- Все остальные агенты — уже на Rich Markdown

---

## Верификация

1. `/audit` — убедиться что таблицы и жирный текст рендерятся (не отображаются теги)
2. `/drr` — то же самое
3. `/report` — не трогали, но проверить что не сломали
4. `/start` у Питера — Rich Markdown хелп (исправлен ранее в `ca3cad2`)
