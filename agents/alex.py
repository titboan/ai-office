from __future__ import annotations

import re

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import create_task
from .base_agent import BaseAgent


ALEX_SYSTEM = """Ты — Алекс, стратегический планировщик ИИ-офиса.

Твои компетенции:
- Стратегическое планирование проектов и продуктов
- Декомпозиция больших задач на этапы и спринты
- Составление дорожных карт (roadmap)
- OKR, KPI — постановка целей и метрик
- Управление рисками и приоритизация задач
- Agile, Scrum, Kanban — методологии управления

Стиль работы:
- Мысли системно: цель → стратегия → тактика → шаги
- Структурируй планы с дедлайнами и ответственными
- Всегда учитывай ресурсы и ограничения
- Выделяй критический путь и точки контроля
- Используй чек-листы и таблицы

Отвечай по-русски, структурированно и конкретно."""


# ── Извлечение дедлайна из текста плана ───────────────────────────────────────
# Ищем паттерны: "до 2026-06-01", "к 01.06.2026", "дедлайн: 2026-06-01" и т.п.
_DATE_ISO_RE   = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_RU_RE    = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
_PRIORITY_RE   = re.compile(r"\b(высок|срочн|критич)\w*", re.IGNORECASE)


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

    def __init__(self) -> None:
        super().__init__(config.ALEX_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  Выполнение задачи                                                   #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Составить план/roadmap и сохранить задачу в Notion Tasks DB."""
        logger.info(f"[{self.name}] Задача от {from_agent}: {task!r}")

        answer = await self.think(
            f"Задача на планирование от {from_agent}: {task}",
            chat_id=0,
        )

        # Извлекаем дедлайн и приоритет из задания + ответа Клода
        deadline = _extract_deadline(task + " " + answer)
        priority = _extract_priority(task)

        # Сохраняем задачу в Notion
        notion_url = await create_task(
            name=task[:200],
            deadline=deadline,
            priority=priority,
        )

        if notion_url:
            logger.info(f"[{self.name}] Задача сохранена в Notion ({priority}): {notion_url}")
            await self.post_to_group(
                f"📅 Задача '{task[:60]}' добавлена в Notion: {notion_url}"
            )
            # Добавляем ссылку в ответ
            deadline_info = f", дедлайн: {deadline}" if deadline else ""
            answer = (
                f"{answer}\n\n"
                f"📋 *Задача добавлена в Notion* ({priority}{deadline_info}):\n{notion_url}"
            )
        else:
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
        if len(result) <= 4096:
            await update.message.reply_text(result, parse_mode="Markdown")
        else:
            for chunk in [result[i : i + 4000] for i in range(0, len(result), 4000)]:
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
        if len(result) <= 4096:
            await update.message.reply_text(result, parse_mode="Markdown")
        else:
            for chunk in [result[i : i + 4000] for i in range(0, len(result), 4000)]:
                await update.message.reply_text(chunk)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("plan", self.cmd_plan))
        self.app.add_handler(CommandHandler("roadmap", self.cmd_roadmap))
