# План: починить доставку HTML-отчётов через очередь задач

Статус: завершён

## Проблема

Скриншоты от Бориса: результат Макса (`📦 Каталог товаров`, `💲 Себестоимость и маржа`,
`🛒 Ваши магазины`), пришедший через Марту (делегирование задачи по очереди),
выглядит одной нечитаемой простынёй текста — таблица `<pre>` без переносов строк,
списки без переносов.

## Причина

`Max._catalog_text()` / `_margin_check_text()` / `_shops_text()` строят хардкод-отчёт
в HTML (`<pre>`, `<b>`, `<i>`) — так и задумано (план `2026-06-15-rich-messages-migration.md`,
раздел «Что НЕ меняется»: «Max — хардкод-отчёты … остаются на HTML»).

Но когда задача Максу приходит через очередь (`task_queue`), результат уходит
единым путём в `agents/base_agent.py::_worker_loop` (строка ~739):

```python
result_msg = f"✅ {self.emoji} **{self.name}:**\n\n{result}"
await self._notify_user(task.chat_id, result_msg, bot_token=_reply_token)
```

`_notify_user()` безусловно вызывает:
1. `clean_agent_output()` (`utils/tg_format.py`) — вырезает ВСЕ HTML-теги regex'ом
   `<[^>]+>`, включая `<pre>` — таблица теряет обёртку.
2. `send_rich_or_fallback()` (`utils/tg_rich.py`) — отправляет остаток как
   Rich Markdown (GFM) через `sendRichMessage`.

Подтверждено локально (`python3 -c` с `clean_agent_output` + `gfm_to_html_fallback`):
одиночные `\n` между строками таблицы остаются в самой Python-строке, но
GFM-рендер Rich Message трактует одиночный `\n` как soft-break (мягкий перенос —
по спецификации CommonMark сворачивается в пробел, если это не двойной перенос
и не код-блок/HTML). Именно поэтому все строки таблицы визуально склеиваются
в один абзац — это и есть баг со скриншотов.

Итог: разметка «под HTML» (пробелы для выравнивания колонок + одиночные `\n`)
физически несовместима с конвейером Rich Markdown/GFM. Нужно ветвление по формату
в единственной точке доставки задач из очереди.

## Фаза 1 — `utils/tg_rich.py`: детектор HTML + HTML-отправка

- [x] `looks_like_html(text: str) -> bool` — `re.search` на `</?(b|i|u|s|code|pre|blockquote|a)\b`
  (те же теги, что `utils/tg_format.py` умеет генерировать: `bold/italic/code/pre/quote/link`).
  Легитимный Rich Markdown от Клода никогда не содержит такие теги — промпт
  `RICH_MARKDOWN_FORMAT_RULES` явно запрещает `<b>`, `<i>`, `<code>`.
- [x] `send_html_message(bot_token, chat_id, html_text, reply_markup_dict=None, reply_to_message_id=None) -> bool`
  — чанки по 4096 символов (границы по `\n\n`, как в `_html_fallback`), `sendMessage`
  с `parse_mode="HTML"`; при ошибке API на чанк — fallback на тот же чанк без тегов
  (`re.sub(r"<[^>]+>", "", chunk)`) без `parse_mode`. Переиспользовать существующий
  цикл чанкования/фоллбека из `_html_fallback` — вынести общую часть в приватный
  хелпер `_send_html_chunks()`, чтобы не дублировать код между `_html_fallback` (уже
  сконвертированный GFM→HTML) и новым `send_html_message` (уже готовый HTML).

## Фаза 2 — `agents/base_agent.py`: ветвление в точке доставки

- [x] `_notify_user()` (строка ~789): если `looks_like_html(text)` — отправлять через
  `send_html_message` напрямую, БЕЗ `clean_agent_output` (который стирает теги).
  Иначе — текущий путь (`clean_agent_output` → `send_rich_or_fallback`).
- [x] Хелпер `_agent_label(result: str) -> str`, возвращающий `f"✅ {self.emoji} <b>{self.name}:</b>"`
  если `looks_like_html(result)`, иначе `f"✅ {self.emoji} **{self.name}:**"` — использовать
  в `_worker_loop` при сборке `result_msg` (строка ~739), чтобы шапка была в том же
  формате, что и тело (не смешивать `**...**` с HTML-телом).
- [x] Прогнать через `_dispatch_queue_task` в `agents/max.py` все ветки
  (`__shops__`, `__products__`, `__margin__`, `__data_status__`, `__shop_kpi__` и др.) —
  убедиться что все они строят HTML (а не смешанный формат) и корректно детектятся.

## Проверка

- Юнит-тест или ручной прогон: взять реальный вывод `_catalog_text()` /
  `_margin_check_text()` / `_shops_text()`, прогнать через новую `_notify_user`-логику,
  убедиться что `<pre>`-таблица не теряет теги и не уходит в `send_rich_or_fallback`.
- В боте: делегировать Максу через Марту (`"покажи каталог товаров"`, `"маржа"`,
  `"мои магазины"`) — сверить что таблица отображается моноширинным шрифтом с
  переносами строк, как при прямом `/products`.
- Прямые команды (`/products`, `/margin`, `/shops`) не трогаем — они и так шлют
  `parse_mode="HTML"` напрямую, минуя `_notify_user`.
