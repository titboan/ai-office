from __future__ import annotations

import json
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

EVA_SYSTEM = """Ты — Ева, личный секретарь AI-офиса.
Читаешь Telegram-каналы через Telethon и почту через IMAP, делаешь структурированные дайджесты.
Отвечай по-русски."""

_EMAIL_DIGEST_PROMPT = """\
Ты — личный секретарь. Тебе дан список писем из почтового ящика.

Задача:
1. Сгруппируй по категориям (используй только нужные):
   🔴 Срочное — требует ответа или действия сегодня
   💼 Работа — рабочая переписка, контрагенты, партнёры
   🛒 Маркетплейсы — WB, Ozon, поставщики, логистика
   📧 Рассылки — newsletters, уведомления, которые можно отложить
   📌 Прочее — всё остальное
2. Для каждого письма одна строка: <b>Тема</b> (От кого) — одна фраза о чём
3. Если категория пуста — пропусти её
4. Язык: русский

Форматируй в HTML для Telegram:
- <b>🔴 Срочное</b> — заголовок категории
- Список писем под ним
- НЕ используй Markdown

Письма:
{messages}"""

_SORT_FOLDERS: dict[str, str] = {
    "Срочное":      "Eva-Urgent",
    "Работа":       "Eva-Work",
    "Маркетплейсы": "Eva-Markets",
    "Рассылки":     "Eva-Newsletter",
    # "Прочее" — не перемещается, остаётся в INBOX
}

_SORT_LABELS: dict[str, str] = {v: k for k, v in _SORT_FOLDERS.items()}

_EMAIL_SORT_PROMPT = """\
Ты — почтовый сортировщик. Тебе дан список писем из INBOX.

Для каждого письма определи одну категорию:
- Срочное — требует ответа или действия сегодня
- Работа — рабочая переписка, контрагенты, партнёры, сервисы
- Маркетплейсы — WB, Ozon, поставщики, логистика, маркетплейс-уведомления
- Рассылки — newsletters, рекламные рассылки, автоматические уведомления
- Прочее — всё остальное (останется в INBOX без перемещения)

Верни ТОЛЬКО валидный JSON-массив, без пояснений, без markdown-обёртки:
[{{"uid": "123", "category": "Рассылки"}}, ...]

Письма:
{messages}"""

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


_BQ_OPEN_RE = re.compile(r"<blockquote(?:\s[^>]*)?>", re.IGNORECASE)
_BQ_CLOSE_RE = re.compile(r"</blockquote>", re.IGNORECASE)


def _split_digest(text: str, limit: int = 4000) -> list[str]:
    """Разрезать дайджест на части ≤limit по границам пустой строки (\\n\\n).

    Дайджест форматируется в HTML (`<blockquote expandable>`, `<b>` и т.п.),
    поэтому резать нужно только там, где на данный момент нет незакрытого
    <blockquote...> — иначе получившийся кусок будет с непарным тегом и
    Telegram не сможет его распарсить. Если разрез на границе \\n\\n попадает
    внутрь открытого <blockquote>, откатываемся к ближайшей предыдущей
    сбалансированной \\n\\n-границе (перед этим тегом).
    """
    if len(text) <= limit:
        return [text]

    # Разбиваем на блоки по границам "\n\n", разделитель приклеен к концу
    # предыдущего блока — конкатенация блоков даёт исходный текст без потерь.
    tokens = re.split(r"(\n\n+)", text)
    blocks: list[str] = []
    for i in range(0, len(tokens), 2):
        sep = tokens[i + 1] if i + 1 < len(tokens) else ""
        blocks.append(tokens[i] + sep)

    parts: list[str] = []
    current: list[str] = []
    cum_open = [0]   # cum_open[k] — открытых <blockquote> в первых k блоках current
    cum_close = [0]
    current_len = 0

    for block in blocks:
        if current_len + len(block) > limit and current:
            # Ищем самую правую точку среди накопленных блоков, где теги сбалансированы
            split_idx = None
            for k in range(len(current), 0, -1):
                if cum_open[k] == cum_close[k]:
                    split_idx = k
                    break
            if split_idx:
                parts.append("".join(current[:split_idx]).rstrip())
                current = current[split_idx:]
                cum_open = [0]
                cum_close = [0]
                for b in current:
                    cum_open.append(cum_open[-1] + len(_BQ_OPEN_RE.findall(b)))
                    cum_close.append(cum_close[-1] + len(_BQ_CLOSE_RE.findall(b)))
                current_len = sum(len(b) for b in current)
            # если split_idx не найден — даже первый накопленный блок открывает
            # незакрытый тег, разрез откладывается до появления баланса

        current.append(block)
        cum_open.append(cum_open[-1] + len(_BQ_OPEN_RE.findall(block)))
        cum_close.append(cum_close[-1] + len(_BQ_CLOSE_RE.findall(block)))
        current_len += len(block)

    if current:
        parts.append("".join(current).rstrip())

    # Fallback: часть всё ещё длиннее лимита (например один <blockquote>
    # длиннее лимита целиком, либо во всём тексте нет ни одной "\n\n"-границы) —
    # жёсткий разрез по длине, как раньше, но с явным предупреждением в лог,
    # т.к. это может разорвать HTML-тег.
    result: list[str] = []
    for part in parts:
        if len(part) <= limit:
            result.append(part)
        else:
            logger.warning(
                f"[Ева] _split_digest: не нашлось безопасной границы \\n\\n для среза "
                f"≤{limit} символов (похоже, один HTML-блок длиннее лимита) — "
                f"жёсткий разрез по длине, возможен разрыв тега"
            )
            for start in range(0, len(part), limit):
                result.append(part[start:start + limit])

    return [p for p in result if p.strip()]


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

    def _email_accounts(self) -> list[dict]:
        """Вернуть список настроенных почтовых ящиков."""
        accounts = []
        if config.EMAIL_USER and config.EMAIL_APP_PASS:
            accounts.append({
                "host": config.EMAIL_IMAP_HOST,
                "user": config.EMAIL_USER,
                "password": config.EMAIL_APP_PASS,
            })
        if config.EMAIL_USER_2 and config.EMAIL_APP_PASS_2:
            accounts.append({
                "host": config.EMAIL_IMAP_HOST_2,
                "user": config.EMAIL_USER_2,
                "password": config.EMAIL_APP_PASS_2,
            })
        return accounts

    async def run_email_digest(self, user_chat_id: int, since_days: int = 1) -> None:
        """Прочитать почту всех ящиков и отправить дайджест в Telegram."""
        from tools.email_reader import fetch_inbox_messages

        accounts = self._email_accounts()
        if not accounts:
            await self._notify_user(
                user_chat_id,
                "⚠️ EMAIL_USER / EMAIL_APP_PASS не заданы — почтовый дайджест недоступен."
            )
            return

        for account in accounts:
            await self._notify_user(user_chat_id, f"📧 Читаю {account['user']} за {since_days} д…")
            try:
                messages = await fetch_inbox_messages(
                    host=account["host"],
                    user=account["user"],
                    password=account["password"],
                    since_days=since_days,
                    max_messages=60,
                )
            except Exception as e:
                logger.error(f"[Ева] IMAP ошибка {account['user']}: {e}")
                await self._notify_user(user_chat_id, f"❌ {account['user']}: не удалось подключиться: {e}")
                continue

            if not messages:
                await self._notify_user(user_chat_id, f"📭 {account['user']}: новых писем нет.")
                continue

            lines = []
            for m in messages:
                lines.append(f"От: {m['from_']}\nТема: {m['subject']}\nДата: {m['date']}\n{m['body_preview']}")
            messages_text = "\n\n---\n\n".join(lines)

            prompt = _EMAIL_DIGEST_PROMPT.format(messages=messages_text)
            try:
                response = await self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}],
                )
                digest_text = response.content[0].text
            except Exception as e:
                logger.error(f"[Ева] Claude API (email) {account['user']}: {e}")
                await self._notify_user(user_chat_id, f"❌ {account['user']}: ошибка генерации дайджеста: {e}")
                continue

            header = f"📧 <b>Дайджест почты</b> {account['user']} — за {since_days} д. ({len(messages)} писем)\n\n"
            parts = _split_digest(header + digest_text)
            for part in parts:
                await self._notify_user(user_chat_id, part)
            logger.info(f"[Ева] Email-дайджест {account['user']} отправлен user={user_chat_id}, писем={len(messages)}")

    async def run_sort_emails(self, user_chat_id: int, since_days: int = 1) -> None:
        """Категоризировать письма всех ящиков через Claude и разложить по IMAP-папкам."""
        from tools.email_reader import fetch_inbox_messages, sort_emails_to_folders

        accounts = self._email_accounts()
        if not accounts:
            await self._notify_user(
                user_chat_id,
                "⚠️ EMAIL_USER / EMAIL_APP_PASS не заданы — сортировка недоступна."
            )
            return

        for account in accounts:
            await self._notify_user(user_chat_id, f"📂 {account['user']}: читаю за {since_days} д…")
            try:
                messages = await fetch_inbox_messages(
                    host=account["host"],
                    user=account["user"],
                    password=account["password"],
                    since_days=since_days,
                    max_messages=60,
                )
            except Exception as e:
                logger.error(f"[Ева] IMAP ошибка {account['user']}: {e}")
                await self._notify_user(user_chat_id, f"❌ {account['user']}: не удалось подключиться: {e}")
                continue

            if not messages:
                await self._notify_user(user_chat_id, f"📭 {account['user']}: новых писем нет.")
                continue

            await self._notify_user(user_chat_id, f"🧠 {account['user']}: категоризирую {len(messages)} писем…")
            lines = [
                f"uid={msg['uid']} | От: {msg['from_']} | Тема: {msg['subject']}"
                for msg in messages
            ]
            prompt = _EMAIL_SORT_PROMPT.format(messages="\n".join(lines))

            try:
                response = await self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}],
                )
                match = re.search(r'\[[\s\S]*\]', response.content[0].text)
                if not match:
                    raise ValueError("JSON не найден в ответе Claude")
                categorized: list[dict] = json.loads(match.group(0))
            except Exception as e:
                logger.error(f"[Ева] Claude sort error {account['user']}: {e}")
                await self._notify_user(user_chat_id, f"❌ {account['user']}: ошибка категоризации: {e}")
                continue

            moves = [
                {"uid": item["uid"], "folder": _SORT_FOLDERS[item["category"]]}
                for item in categorized
                if isinstance(item, dict) and item.get("category") in _SORT_FOLDERS
            ]

            if not moves:
                await self._notify_user(
                    user_chat_id,
                    f"📌 {account['user']}: все письма «Прочее» — перемещение не требуется."
                )
                continue

            await self._notify_user(user_chat_id, f"📁 {account['user']}: перемещаю {len(moves)} писем…")
            try:
                result = await sort_emails_to_folders(
                    host=account["host"],
                    user=account["user"],
                    password=account["password"],
                    moves=moves,
                )
            except Exception as e:
                logger.error(f"[Ева] sort_emails_to_folders {account['user']}: {e}")
                await self._notify_user(user_chat_id, f"❌ {account['user']}: ошибка при перемещении: {e}")
                continue

            folder_counts: dict[str, int] = {}
            for move in moves:
                folder_counts[move["folder"]] = folder_counts.get(move["folder"], 0) + 1

            report = [f"📂 <b>Сортировка {account['user']}</b> — {result['moved']} писем перемещено\n"]
            for folder, count in sorted(folder_counts.items()):
                report.append(f"• {_SORT_LABELS.get(folder, folder)}: {count}")
            stayed = len(messages) - result["moved"]
            if stayed > 0:
                report.append(f"• Прочее (INBOX): {stayed}")
            if result["errors"]:
                report.append(f"\n⚠️ Ошибок: {len(result['errors'])}")

            await self._notify_user(user_chat_id, "\n".join(report))
            logger.info(f"[Ева] Сортировка {account['user']}: moved={result['moved']}, errors={len(result['errors'])}")

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

    async def cmd_email_digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/email_digest [Nd] — дайджест почты в Telegram."""
        arg = context.args[0] if context.args else None
        since_days = 1

        if arg:
            m = re.fullmatch(r"(\d+)d", arg, re.IGNORECASE)
            if m:
                since_days = int(m.group(1))
            else:
                await update.message.reply_text(
                    "❌ Неверный параметр. Используй: /email_digest 3d"
                )
                return

        await self.run_email_digest(update.effective_user.id, since_days=since_days)

    async def cmd_sort_emails(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sort_emails [Nd] — разложить письма по папкам Eva-Urgent/Work/Markets/Newsletter."""
        arg = context.args[0] if context.args else None
        since_days = 1

        if arg:
            m = re.fullmatch(r"(\d+)d", arg, re.IGNORECASE)
            if m:
                since_days = int(m.group(1))
            else:
                await update.message.reply_text(
                    "❌ Неверный параметр. Используй: /sort_emails 3d"
                )
                return

        await self.run_sort_emails(update.effective_user.id, since_days=since_days)

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

    def _help_text(self) -> str:
        return (
            "📰 **Ева** — личный секретарь\n\n"
            "Каждый день в 09:30 МСК автоматически:\n"
            "• дайджест Telegram-каналов → в этот чат\n"
            "• дайджест почты → в этот чат\n\n"
            "📌 **Команды:**\n"
            "/digest [3d|12h|YYYY-MM-DD] — дайджест каналов прямо сейчас\n"
            "/email_digest [Nd] — дайджест почты прямо сейчас\n"
            "/sort_emails [Nd] — разложить письма по папкам (Eva-Urgent/Work/Markets/Newsletter)\n"
            "/add_channel @username — добавить канал\n"
            "/remove_channel @username — удалить канал\n"
            "/channels — список подключённых каналов\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: /sort_emails 3d"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start",          "Запуск и помощь"),
            BotCommand("digest",         "Дайджест Telegram-каналов"),
            BotCommand("email_digest",   "Дайджест почты"),
            BotCommand("sort_emails",    "Разложить письма по папкам"),
            BotCommand("add_channel",    "Добавить канал"),
            BotCommand("remove_channel", "Удалить канал"),
            BotCommand("channels",       "Список каналов"),
            BotCommand("reset",          "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("digest",         self.cmd_digest))
        self.app.add_handler(CommandHandler("email_digest",   self.cmd_email_digest))
        self.app.add_handler(CommandHandler("sort_emails",    self.cmd_sort_emails))
        self.app.add_handler(CommandHandler("add_channel",    self.cmd_add_channel))
        self.app.add_handler(CommandHandler("remove_channel", self.cmd_remove_channel))
        self.app.add_handler(CommandHandler("channels",       self.cmd_channels))
