from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent

_MSK = ZoneInfo("Europe/Moscow")
_UTC = timezone.utc

EVA_SYSTEM = """Ты — Ева, редактор новостного дайджеста AI-офиса.
Читаешь Telegram-каналы через Telethon и делаешь структурированные дайджесты.
Отвечай по-русски."""

_DIGEST_PROMPT = """\
Ты — редактор новостного дайджеста. Тебе дан список сообщений из Telegram каналов.

Задача:
1. Сгруппируй по темам (3-7 тем)
2. Для каждой темы: заголовок + 2-3 предложения выжимки + ссылки на 1-3 самых важных сообщения
3. В конце: раздел "Остальное" для мелких новостей без отдельной темы
4. Язык: русский

Форматируй в HTML для Telegram:
- <b>Название темы</b> — заголовки групп
- <a href="url">текст</a> — ссылки на сообщения
- <blockquote expandable>выжимка по теме</blockquote> — содержание каждой темы
- Эмодзи в начале каждой темы (📰 🔍 💡 ⚡ 🌍)
- НЕ используй Markdown: никаких *звёздочек*, ##заголовков

Сообщения:
{messages}"""


def _parse_since(arg: str | None, last_checked: datetime | None) -> datetime | str:
    """Вернуть datetime (UTC-aware) или строку с ошибкой."""
    now = datetime.now(_UTC)

    if not arg:
        if last_checked:
            return last_checked if last_checked.tzinfo else last_checked.replace(tzinfo=_UTC)
        return now - timedelta(hours=24)

    # Nd — N дней
    m = re.fullmatch(r"(\d+)d", arg, re.IGNORECASE)
    if m:
        return now - timedelta(days=int(m.group(1)))

    # Nh — N часов
    m = re.fullmatch(r"(\d+)h", arg, re.IGNORECASE)
    if m:
        return now - timedelta(hours=int(m.group(1)))

    # YYYY-MM-DD — начало дня по МСК
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", arg)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dt_msk = datetime(y, mo, d, 0, 0, 0, tzinfo=_MSK)
            return dt_msk.astimezone(_UTC)
        except ValueError:
            return f"Неверная дата: {arg}"

    return f"Не могу распарсить параметр: «{arg}». Используй: 3d, 12h, 2026-06-01"


def _split_digest(text: str, limit: int = 4000) -> list[str]:
    """Разрезать дайджест по границам тем (строки ##), не в середине."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.splitlines(keepends=True):
        # Новая тема — возможное место разреза
        if line.startswith("##") and current_len + len(line) > limit and current:
            parts.append("".join(current).rstrip())
            current = []
            current_len = 0
        # Принудительный разрез если блок слишком большой
        if current_len + len(line) > limit and current:
            parts.append("".join(current).rstrip())
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)

    if current:
        parts.append("".join(current).rstrip())
    return [p for p in parts if p.strip()]


class EvaAgent(BaseAgent):
    name = "Ева"
    agent_key = "eva"
    role = "Редактор дайджеста"
    emoji = "📰"
    system_prompt = EVA_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.EVA_BOT_TOKEN)
        self._telethon_client = None
        self._telethon_ready = False

        session_str = config.TELETHON_SESSION
        api_id = config.TELEGRAM_API_ID
        api_hash = config.TELEGRAM_API_HASH

        if not session_str or not api_id or not api_hash:
            logger.warning(
                "[Ева] TELETHON_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы — "
                "Telethon клиент не создан, чтение каналов недоступно"
            )
            return

        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            self._telethon_client = TelegramClient(
                StringSession(session_str),
                int(api_id),
                api_hash,
            )
            logger.info("[Ева] Telethon клиент создан")
        except Exception as e:
            logger.error(f"[Ева] Ошибка создания Telethon клиента: {e}")

    # ------------------------------------------------------------------ #
    #  Запуск / остановка                                                  #
    # ------------------------------------------------------------------ #

    async def start_polling_async(self) -> None:
        if self._telethon_client is not None:
            try:
                await self._telethon_client.start()
                self._telethon_ready = True
                logger.info("[Ева] Telethon клиент подключён")
            except Exception as e:
                logger.error(f"[Ева] Telethon start() упал: {e}")
        await super().start_polling_async()

    async def stop_async(self) -> None:
        await super().stop_async()
        if self._telethon_client is not None and self._telethon_ready:
            try:
                await self._telethon_client.disconnect()
                logger.info("[Ева] Telethon клиент отключён")
            except Exception as e:
                logger.warning(f"[Ева] Telethon disconnect: {e}")

    # ------------------------------------------------------------------ #
    #  handle_task — заглушка (Ева работает через команды, не очередь)    #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        return await self.think(task, chat_id=0, is_task=True)

    # ------------------------------------------------------------------ #
    #  Вспомогательные методы                                             #
    # ------------------------------------------------------------------ #

    async def _fetch_channel_messages(
        self,
        channel,
        since: datetime,
        limit: int = 100,
    ) -> list[dict]:
        """Собрать сообщения из канала начиная с since."""
        messages = []
        username = getattr(channel, "username", None) or ""

        async for msg in self._telethon_client.iter_messages(
            channel,
            offset_date=since,
            reverse=True,
            limit=limit,
        ):
            text = msg.text or msg.message or ""
            if not text.strip():
                continue
            link = f"https://t.me/{username}/{msg.id}" if username else ""
            messages.append({"text": text, "link": link})

        return messages

    async def _resolve_channel(self, username: str):
        """Резолвить username → Telethon entity."""
        return await self._telethon_client.get_entity(username)

    # ------------------------------------------------------------------ #
    #  Дайджест                                                            #
    # ------------------------------------------------------------------ #

    async def run_digest(self, user_chat_id: int, since: datetime | None = None) -> None:
        """Собрать дайджест для пользователя и отправить в Telegram."""
        from db import list_digest_channels, update_channel_last_checked

        if not self._telethon_ready:
            await self._notify_user(
                user_chat_id,
                "⚠️ Ева: Telethon не подключён. Проверь TELETHON_SESSION в переменных окружения."
            )
            return

        channels = await list_digest_channels(user_chat_id)
        if not channels:
            await self._notify_user(
                user_chat_id,
                "📭 Список каналов пуст. Добавь каналы командой /add_channel @username"
            )
            return

        # Определяем since для каждого канала
        now = datetime.now(_UTC)
        all_messages: list[str] = []

        for ch in channels:
            ch_since = since
            if ch_since is None:
                last = ch.get("last_checked_at")
                ch_since = (
                    last if last and last.tzinfo else
                    (last.replace(tzinfo=_UTC) if last else now - timedelta(hours=24))
                )

            try:
                entity = await self._telethon_client.get_entity(
                    ch["username"] or int(ch["chat_id"])
                )
                msgs = await self._fetch_channel_messages(entity, ch_since)
                title = ch.get("title") or ch.get("username") or ch["chat_id"]
                for m in msgs:
                    line = f"[{title}] {m['text']}"
                    if m["link"]:
                        line += f"\n{m['link']}"
                    all_messages.append(line)
                logger.info(f"[Ева] {title}: {len(msgs)} сообщений с {ch_since.isoformat()}")
            except Exception as e:
                logger.warning(f"[Ева] Не удалось прочитать {ch.get('username') or ch['chat_id']}: {e}")

        if not all_messages:
            await self._notify_user(user_chat_id, "📭 Новых сообщений нет.")
            return

        # Суммаризация через Claude
        messages_text = "\n\n---\n\n".join(all_messages[:500])  # не более 500 сообщений
        prompt = _DIGEST_PROMPT.format(messages=messages_text)

        try:
            response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            digest_text = response.content[0].text
        except Exception as e:
            logger.error(f"[Ева] Claude API ошибка: {e}")
            await self._notify_user(user_chat_id, f"❌ Ошибка при генерации дайджеста: {e}")
            return

        # Отправляем по частям
        parts = _split_digest(digest_text)
        for part in parts:
            await self._notify_user(user_chat_id, part)

        # Обновляем last_checked_at только после успешной отправки
        for ch in channels:
            try:
                await update_channel_last_checked(ch["chat_id"], user_chat_id, now)
            except Exception as e:
                logger.warning(f"[Ева] update_channel_last_checked: {e}")

        # Сохраняем в Notion
        try:
            from tools.notion import save_content
            ts = now.astimezone(_MSK).strftime("%Y-%m-%d %H:%M")
            await save_content(
                title=f"Дайджест {ts}",
                text=digest_text,
                content_type="Дайджест",
            )
            logger.info(f"[Ева] Дайджест сохранён в Notion: {ts}")
        except Exception as e:
            logger.warning(f"[Ева] Notion save_content: {e}")

    # ------------------------------------------------------------------ #
    #  Команды бота                                                        #
    # ------------------------------------------------------------------ #

    async def cmd_digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/digest [param] — сгенерировать дайджест."""
        from db import list_digest_channels

        user_id = update.effective_user.id
        arg = context.args[0] if context.args else None

        # Определяем since
        channels = await list_digest_channels(user_id)
        last_checked = None
        if channels:
            dates = [
                ch["last_checked_at"] for ch in channels
                if ch.get("last_checked_at")
            ]
            if dates:
                last_checked = min(dates)  # самый ранний last_checked_at

        since = _parse_since(arg, last_checked)
        if isinstance(since, str):
            await update.message.reply_text(f"❌ {since}")
            return

        since_str = since.astimezone(_MSK).strftime("%d.%m %H:%M МСК")
        await update.message.reply_text(f"📰 Собираю дайджест с {since_str}…")
        await self.run_digest(user_id, since=since)

    async def cmd_add_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/add_channel @username — добавить канал."""
        from db import add_digest_channel

        if not context.args:
            await update.message.reply_text("Использование: /add_channel @username")
            return

        username = context.args[0].lstrip("@")
        user_id = update.effective_user.id

        if not self._telethon_ready:
            await update.message.reply_text("⚠️ Telethon не подключён, не могу резолвить канал.")
            return

        await update.message.reply_text(f"🔍 Ищу канал @{username}…")
        try:
            entity = await self._resolve_channel(username)
            chat_id = str(entity.id)
            title = getattr(entity, "title", username)
            await add_digest_channel(chat_id, username, title, user_id)
            await update.message.reply_text(f"✅ Канал «{title}» (@{username}) добавлен.")
            logger.info(f"[Ева] Добавлен канал @{username} для user={user_id}")
        except Exception as e:
            logger.warning(f"[Ева] add_channel @{username}: {e}")
            await update.message.reply_text(f"❌ Не удалось найти канал @{username}: {e}")

    async def cmd_remove_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/remove_channel @username — удалить канал."""
        from db import remove_digest_channel

        if not context.args:
            await update.message.reply_text("Использование: /remove_channel @username")
            return

        username = context.args[0].lstrip("@")
        user_id = update.effective_user.id

        removed = await remove_digest_channel(username, user_id)
        if removed:
            await update.message.reply_text(f"✅ Канал @{username} удалён.")
        else:
            await update.message.reply_text(f"❓ Канал @{username} не найден в вашем списке.")

    async def cmd_channels(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/channels — список подключённых каналов."""
        from db import list_digest_channels

        user_id = update.effective_user.id
        channels = await list_digest_channels(user_id)

        if not channels:
            await update.message.reply_text(
                "Список каналов пуст. Добавь: /add_channel @username"
            )
            return

        lines = ["📋 <b>Ваши каналы:</b>\n"]
        for ch in channels:
            name = ch.get("title") or f"@{ch.get('username')}" or ch["chat_id"]
            last = ch.get("last_checked_at")
            if last:
                last_str = last.astimezone(_MSK).strftime("%d.%m %H:%M МСК")
            else:
                last_str = "ещё не проверялся"
            username = ch.get("username")
            mention = f"@{username}" if username else ch["chat_id"]
            lines.append(f"• {name} ({mention}) — последний дайджест: {last_str}")

        try:
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception:
            await update.message.reply_text("\n".join(lines))

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("digest",         self.cmd_digest))
        self.app.add_handler(CommandHandler("add_channel",    self.cmd_add_channel))
        self.app.add_handler(CommandHandler("remove_channel", self.cmd_remove_channel))
        self.app.add_handler(CommandHandler("channels",       self.cmd_channels))
