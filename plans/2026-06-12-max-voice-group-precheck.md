# Устранение лишних транскрипций голосовых у Макса

Статус: в работе

## Контекст

В групповых чатах Макс транскрибирует через Groq Whisper API **все** голосовые сообщения — даже те, что явно не адресованы боту. Проверка триггеров (`_handle_group_message`: @mention / "макс" / reply-to-bot) происходит **после** транскрипции. Это означает:

1. Groq API вызывается зря (расходы + задержка)
2. `send_chat_action("typing")` показывается всем, включая нерелевантные голосовые

Корень проблемы: `max.handle_voice()` сначала вызывает `super().handle_voice()` (который скачивает файл и транскрибирует), и только потом передаёт текст в `_handle_group_message()` для проверки триггеров.

---

## Что можно проверить ДО транскрипции

| Триггер | До транскрипции? |
|---------|-----------------|
| `is_reply_to_bot` | ✅ Да — `msg.reply_to_message.from_user.id` |
| `starts_with_max` | ❌ Нет — нужен расшифрованный текст |
| `has_mention` | Никогда не срабатывает для voice (нет entities) |

---

## Решение

**Файл:** `agents/max.py`, метод `handle_voice()` (строки 221–230)

Добавить pre-check в группах: если сообщение **не** является reply-to-bot — не транскрибировать совсем.

### Текущий код

```python
async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    from telegram import Chat
    transcribed = await super().handle_voice(update, context)
    if (
        transcribed
        and update.effective_chat
        and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP)
    ):
        await self._handle_group_message(update, context, override_text=transcribed)
    return transcribed
```

### Новый код

```python
async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    from telegram import Chat
    msg = update.message
    if not msg:
        return None

    is_group = (
        update.effective_chat
        and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP)
    )
    if is_group:
        # В группе транскрибируем только reply на сообщение бота
        reply = msg.reply_to_message
        is_reply_to_bot = bool(
            reply and reply.from_user and reply.from_user.id == context.bot.id
        )
        if not is_reply_to_bot:
            logger.debug("[max:voice] group voice — not reply-to-bot, skip transcription")
            return None

    transcribed = await super().handle_voice(update, context)
    if transcribed and is_group:
        await self._handle_group_message(update, context, override_text=transcribed)
    return transcribed
```

### Побочный эффект (осознанный trade-off)

Голосовой с фразой "макс, ..." в группе без reply-to-bot перестанет работать.  
Это **приемлемо**: в группе можно написать "макс ..." текстом или ответить на сообщение бота голосом.

---

## Фазы

- [x] Применить изменение в `agents/max.py`
- [ ] Проверить что в приватном чате Макс транскрибирует и отвечает (фикс override_text)
- [x] Проверить что в группе: reply-to-bot голосовое транскрибируется
- [x] Проверить что в группе: произвольное голосовое НЕ транскрибируется (нет "typing...")

---

## Проверка

1. Отправить голосовое Максу в личку → должно транскрибироваться и обработаться
2. В группе отправить голосовое без reply/без "макс" → нет реакции, нет "typing..."
3. В группе ответить (reply) на сообщение бота голосовым → транскрибируется, бот отвечает
