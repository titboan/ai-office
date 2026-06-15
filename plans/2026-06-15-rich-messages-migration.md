# План: Переход на Rich Messages (Bot API 10.1)

## Context

Исходная проблема: MarkdownV2 (коммит `23c58f3`) не поддерживает таблицы / заголовки / `---`, требует экранирования 18+ спецсимволов → Claude часто не экранирует → Telegram падает → fallback со звёздочками.

Bot API 10.1 (11 июня 2026) добавил `sendRichMessage` с `InputRichMessage { markdown | html }`.

**Возможности Rich Messages:**
- **32 768 символов** на сообщение (8× стандартного лимита 4096) — chunking практически не нужен
- До **500 вложенных блоков** (элементы списков, строки таблиц, цитаты)
- До **16 уровней вложенности**
- До **50 медиа-вложений** (фото, видео, аудио) внутри одного сообщения
- Таблицы до **20 колонок**
- Rich Markdown = GitHub Flavored Markdown: `**bold**`, `# heading`, `| table |`, `---`, `- [ ] task`, `> quote`, вложенные списки, LaTeX формулы
- Никакого экранирования не нужно

PTB 22.7 не оборачивает метод → вызываем через `aiohttp` (уже в зависимостях).

---

## Что НЕ меняется

- **Eva, Tina** — промпты HTML, отправка `parse_mode="HTML"` — работает корректно.
- **Max** — хардкод-отчёты (`_send_sales_summary`, `_send_stocks`) — f-строки с `<b>`, отправляются `parse_mode="HTML"` — оставляем.
- **`post_to_group()`** — без форматирования, не трогаем.

---

## Фазы

### [x] Фаза 1 — Инфраструктура (`utils/tg_rich.py` — новый файл)

```python
RICH_MARKDOWN_FORMAT_RULES = """
Форматируй ответы в Rich Markdown для Telegram:
- **текст** — жирный (заголовки разделов, ключевые числа, выводы)
- *текст* — курсив (пояснения, уточнения, вторичные данные)
- `текст` — моноширинный (артикулы, ID, команды, коды)
- > текст — цитата (инсайт, важный вывод)
- # Заголовок / ## Подраздел / ### Деталь — заголовки разделов
- --- — горизонтальный разделитель между крупными блоками
- | Колонка | Значение | — таблица (до 20 колонок, со строкой |---|---|)
- - пункт / 1. пункт — маркированные и нумерованные списки (до 500 строк)
- - [ ] задача / - [x] выполнено — чеклисты
- Эмодзи в начале разделов для навигации
- Спецсимволы экранировать НЕ нужно — пиши . ! ( ) - + = как есть
- НЕ используй HTML-теги: никаких <b>, <i>, <code>
""".strip()

RICH_MESSAGE_CHUNK_SIZE = 30_000  # лимит API — 32 768, оставляем запас

async def send_rich_message(
    bot_token: str,
    chat_id: int | str,
    markdown: str,
    reply_markup_dict: dict | None = None,
    reply_to_message_id: int | None = None,
) -> bool:
    """POST sendRichMessage через aiohttp. Returns True on success."""
    url = f"https://api.telegram.org/bot{bot_token}/sendRichMessage"
    payload = {"chat_id": chat_id, "rich_message": {"markdown": markdown}}
    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    if reply_markup_dict:
        payload["reply_markup"] = reply_markup_dict
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            return data.get("ok", False)

def gfm_to_html_fallback(text: str) -> str:
    """**bold** → <b>, *italic* → <i>, # Heading → <b>, таблицы/--- убрать."""
    # regex: **bold**, *italic*, `code`, # H, --- → strip, | table | → strip

async def send_rich_or_fallback(
    bot_token: str,
    chat_id: int | str,
    markdown: str,
    reply_markup_dict: dict | None = None,
    reply_to_message_id: int | None = None,
) -> None:
    """Отправить Rich Message; при ошибке — sendMessage с HTML-fallback."""
    success = await send_rich_message(...)
    if not success:
        html = gfm_to_html_fallback(markdown)
        # aiohttp POST sendMessage с parse_mode="HTML" (до 4096 символов)
```

**Почему aiohttp**: уже используется в `tools/github.py`, `agents/dan.py`. PTB 22.7 не поддерживает `sendRichMessage`, `bot._request` — нестабильное внутреннее API.

---

### [x] Фаза 2 — `base_agent.py`: центральный путь отправки

**`_notify_user()` (line 745):**
- Порог чанкинга: 4 000 → **30 000** (практически все отчёты влезают целиком)
- Заменить `bot.send_message(parse_mode="MarkdownV2")` → `send_rich_or_fallback(token, chat_id, chunk)`
- Убрать `strip_mdv2` fallback-логику (теперь в helper)
- `reply_markup` → передать как `.to_dict()` в `reply_markup_dict`

**`handle_message()` (line 468):**
- Заменить `update.message.reply_text(answer, parse_mode="MarkdownV2")` → `send_rich_or_fallback(..., reply_to_message_id=update.message.message_id)`

---

### [x] Фаза 3 — Системные промпты агентов (GFM вместо MarkdownV2)

**`utils/tg_format.py`:**
- Добавить `from utils.tg_rich import RICH_MARKDOWN_FORMAT_RULES` (re-export)
- Оставить `MARKDOWN_FORMAT_RULES` как deprecated alias временно

**Обновить промпты в 8 агентах:**

| Файл | Где prompt |
|------|-----------|
| `agents/peter.py` | `PETER_SYSTEM` использует `{MARKDOWN_FORMAT_RULES}` → `{RICH_MARKDOWN_FORMAT_RULES}` |
| `agents/marta.py` | Inline rules в `MARTA_SYSTEM` (lines 113–119) |
| `agents/max.py` | Inline rules в `MAX_SYSTEM` (lines 73–80) |
| `agents/elina.py` | Inline в `ELINA_SYSTEM` (lines 17–24) |
| `agents/alex.py` | Inline в `ALEX_SYSTEM` (lines 30–36) |
| `agents/kasper.py` | Inline в `KASPER_SYSTEM` (lines 28–35) |
| `agents/kevin.py` | Inline в `KEVIN_SYSTEM` (lines 68–72) |
| `agents/dan.py` | Inline в `DAN_SYSTEM` (lines 32–35) |

Изменения в каждом промпте:
- `*текст*` → `**текст**` (в инструкции Claude)
- Убрать строку про экранирование спецсимволов `\.`
- Добавить: `# Заголовок`, `| таблица |` (до 20 колонок), вложенные списки, `- [ ] task`
- Упомянуть лимит 32 000 символов → можно делать подробные отчёты

**Не трогать:** `PETER_AUDIT_PROMPT`, `PETER_DRR_PROMPT` — HTML, передаются явно с `parse_mode="HTML"`.

---

### [x] Фаза 4 — Хардкод-строки в `marta.py` и `base_agent.py`

Ручные MarkdownV2-строки в коде (не вывод Claude) → Rich Markdown:
- `*текст*` → `**текст**` (bold в GFM = двойные звёздочки)
- `_текст_` → `*текст*` (italic в GFM = одинарная)
- `\.` `\(` `\)` `\-` `\!` → убрать `\`, оставить символ

**marta.py** — ~25 строк (lines: 475, 480, 494–502, 515–518, 541, 560, 621, 664–683, 695–698, 706–708, 734–737, 745–757, 1025, 1162, 1176, 1181, 1191–1213)

**base_agent.py** — task notifications (lines: 693, 712, 726, 882, 889–897)

---

### [x] Фаза 5 — Кастомные пути отправки в агентах

**`peter.py` — `_send_answer()` (line 378):**
- MarkdownV2-путь (default) → `send_rich_or_fallback(self.bot_token, chat_id, text)`
- HTML-пути (`audit`, `drr`, `weekly_audit`) — оставить `sendMessage + HTML`
- Chunking внутри `_send_answer`: 4 000 → 30 000

**`elina.py`, `alex.py`, `kasper.py`, `kevin.py`:**
- Кастомные команды: `reply_text(chunk, parse_mode="MarkdownV2")` → `send_rich_or_fallback(self.bot_token, chat_id, chunk)`
- `self.bot_token` уже доступен через наследование от `BaseAgent`

**Notion-ссылки** уже в формате `[текст](url)` → GFM-синтаксис, работает ✓

---

## Будущие возможности (не в этом плане)

- **Дэн** (`agents/dan.py`): Rich Messages поддерживают `![alt](url)` медиа — Дэн мог бы слать изображения прямо в Rich Message (до 50 медиа). Задача отдельная.
- **Питер**: таблицы до 20 колонок — P&L отчёты с горизонтальной разбивкой по месяцам.
- **Макс**: хардкод-отчёты перевести с HTML-f-строк на Rich Markdown f-строки (задача отдельная).

---

## Риски

| Риск | Митигация |
|------|-----------|
| `sendRichMessage` недоступен для бота / типа чата | Fallback → `sendMessage + HTML` |
| Хардкод `*bold*` стал italic после деплоя | Проверить в боте до мержа в main |
| PTB апгрейд с aiohttp | aiohttp независим от PTB, нет конфликта |
| Fallback HTML обрезает на 4096 | Логировать случаи, когда Rich упал и пришлось резать |

---

## Порядок коммитов

1. `utils/tg_rich.py` — новый файл (инфраструктура)
2. `utils/tg_format.py` — re-export + deprecated alias
3. `agents/base_agent.py` — центральный путь + chunk size
4. Системные промпты 8 агентов (одним коммитом)
5. Хардкод-строки `marta.py` + `base_agent.py`
6. Кастомные send-методы (peter, elina, alex, kasper, kevin)

---

## Проверка после деплоя

1. `/report` у Питера → таблица маржинальности рендерится, нет `---` как текста
2. Любой вопрос Марте → `**bold**` = жирный, `# заголовок` = заголовок
3. `/plan` у Алекса → нумерованный список, чеклисты `- [x]`
4. `/research` у Каспера → заголовки разделов, таблица сравнения
5. Fallback: на время подменить токен → убедиться что приходит читаемый HTML, не ошибка
6. Ева/Тина/Max-отчёты — убедиться что НЕ изменились

Статус: **завершён**
