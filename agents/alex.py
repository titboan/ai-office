from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from task_queue import create_reminder
from tools.ntfy import send_push
from .base_agent import BaseAgent


ALEX_SYSTEM = """Ты Алекс, планировщик и личный ассистент ИИ-офиса.

Твои функции:
1. НАПОМИНАНИЯ — принимаешь запросы вида 'напомни через час',
   'напомни в 18:00', 'напомни завтра утром'.
   Парсишь время и создаёшь напоминание через remind_at.
   Подтверждаешь: '⏰ Напомню в [время]'

2. ПЛАНИРОВАНИЕ — roadmap, OKR, декомпозиция задач, дедлайны.

Для напоминаний используй формат remind_at в ответе.
Никогда не отказывай в напоминании — это твоя ключевая функция.

Форматируй ответы в MarkdownV2 для Telegram:
- *текст* — заголовки этапов и дедлайны
- `текст` — даты, метрики, команды
- Нумерованные списки для шагов
- Эмодзи: ⏰ 📅 🎯 ✅
- Спецсимволы . ! ( ) - = внутри текста экранируй через \
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Отвечай по-русски, структурированно."""


# ── Извлечение дедлайна из текста плана ───────────────────────────────────────
# Ищем паттерны: "до 2026-06-01", "к 01.06.2026", "дедлайн: 2026-06-01" и т.п.
_DATE_ISO_RE   = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_RU_RE    = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
_PRIORITY_RE   = re.compile(r"\b(высок|срочн|критич)\w*", re.IGNORECASE)


def _parse_remind_at(text: str) -> datetime | None:
    """Попытаться найти время напоминания в тексте."""
    MSK = ZoneInfo("Europe/Moscow")
    now = datetime.now(MSK)
    t = text.lower()

    if "через час" in t:
        return now + timedelta(hours=1)
    if "через 30 минут" in t or "через полчаса" in t:
        return now + timedelta(minutes=30)
    if "через 15 минут" in t:
        return now + timedelta(minutes=15)
    if "через 5 минут" in t:
        return now + timedelta(minutes=5)
    if "сегодня вечером" in t:
        return now.replace(hour=18, minute=0, second=0, microsecond=0)
    if "завтра утром" in t:
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)

    m = re.search(r'\bв\s+(\d{1,2}):(\d{2})\b', t)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        result = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if result <= now:
            result += timedelta(days=1)
        return result

    return None


def _extract_deadline(text: str) -> str | None:
    """Попытаться найти дедлайн в тексте плана.

    Returns:
        Дата в формате 'YYYY-MM-DD' или None если не найдена.
    """
    # ISO-формат: 2026-06-01
    m = _DATE_ISO_RE.search(text)
    if m:
        return m.group(1)
    # Русский формат: 01.06.2026
    m = _DATE_RU_RE.search(text)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        return f"{year}-{month}-{day.zfill(2)}"
    return None


def _extract_priority(task: str) -> str:
    """Определить приоритет задачи из её описания."""
    if _PRIORITY_RE.search(task):
        return "Высокий"
    if any(w in task.lower() for w in ("низк", "потом", "когда-нибудь", "не срочн")):
        return "Низкий"
    return "Средний"


class AlexAgent(BaseAgent):
    name = "Алекс"
    agent_key = "alex"
    role = "Планировщик"
    emoji = "🗓️"
    system_prompt = ALEX_SYSTEM
    claude_model = config.CLAUDE_HAIKU_MODEL

    def __init__(self) -> None:
        super().__init__(config.ALEX_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  Выполнение задачи                                                   #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Составить план/roadmap и сохранить задачу в Notion Tasks DB."""
        logger.info(f"[{self.name}] Задача от {from_agent}: {task!r}")

        # Проверяем, это напоминание с временем?
        remind_at = _parse_remind_at(task)
        chat_id = getattr(self, "_current_chat_id", None)

        if remind_at and chat_id:
            task_id, corr_id = await create_reminder(
                chat_id=chat_id,
                text=task,
                remind_at=remind_at,
                from_agent="alex",
            )
            time_str = remind_at.strftime("%H:%M")
            logger.info(f"[{self.name}] Напоминание #{task_id} запланировано на {time_str} UTC")
            return f"⏰ Напоминание запланировано на {time_str} UTC.\nЗапишу в задачи — пришлю в нужное время."

        answer = await self.think(
            f"Задача на планирование от {from_agent}: {task}",
            chat_id=0,
            is_task=True,
        )

        await self.post_to_group(f"📅 План готов: {answer[:200]}…")
        return answer

    # ------------------------------------------------------------------ #
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/plan <цель> — составить план и добавить задачу в Notion."""
        goal = " ".join(context.args) if context.args else ""
        if not goal:
            await update.message.reply_text(
                "Использование: /plan <цель или проект>\n"
                "Пример: /plan запустить MVP за 2 недели"
            )
            return
        await update.message.reply_text("🗓️ Составляю план…")
        result = await self.handle_task(goal, from_agent="команды /plan")
        for chunk in [result[i : i + 4000] for i in range(0, len(result), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

    async def cmd_roadmap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/roadmap <проект> — построить дорожную карту."""
        project = " ".join(context.args) if context.args else ""
        if not project:
            await update.message.reply_text(
                "Использование: /roadmap <название проекта>\n"
                "Пример: /roadmap мобильное приложение для фитнеса"
            )
            return
        await update.message.reply_text("🗺️ Строю roadmap…")
        result = await self.handle_task(
            f"Составь roadmap для проекта: {project}",
            from_agent="команды /roadmap",
        )
        for chunk in [result[i : i + 4000] for i in range(0, len(result), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

    async def cmd_testpush(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/testpush — проверить отправку push через ntfy.sh."""
        if not config.NTFY_TOPIC:
            await update.message.reply_text("❌ NTFY_TOPIC не задан в Railway Variables.")
            return

        success = await send_push(
            title="Test push",
            message="Тестовое уведомление от Алекса",
            topic=config.NTFY_TOPIC,
            priority="high",
        )

        if success:
            await update.message.reply_text(
                f"✅ Пуш отправлен на топик: <code>{config.NTFY_TOPIC}</code>",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка отправки. Проверь Railway logs → <code>ntfy_response</code>.",
                parse_mode="HTML",
            )

    def _help_text(self) -> str:
        return (
            "🗓️ <b>Алекс</b> — планировщик\n\n"
            "Составляю планы, дорожные карты и отправляю push-уведомления.\n\n"
            "📌 <b>Команды:</b>\n"
            "/plan &lt;задача&gt; — составить план и добавить в Notion\n"
            "/roadmap &lt;проект&gt; — построить дорожную карту\n"
            "/testpush — проверить push-уведомления\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: /plan «запустить новую карточку товара к пятнице»"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("plan", "Составить план и добавить в Notion"),
            BotCommand("roadmap", "Построить дорожную карту"),
            BotCommand("testpush", "Проверить push-уведомления"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("plan", self.cmd_plan))
        self.app.add_handler(CommandHandler("roadmap", self.cmd_roadmap))
        self.app.add_handler(CommandHandler("testpush", self.cmd_testpush))
