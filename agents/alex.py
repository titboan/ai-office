from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
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


class AlexAgent(BaseAgent):
    name = "Алекс"
    role = "Планировщик"
    emoji = "🗓️"
    system_prompt = ALEX_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.ALEX_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        answer = await self.think(
            f"Задача на планирование от {from_agent}: {task}",
            chat_id=0,
        )
        await self.post_to_office(f"📅 План готов: {answer[:200]}...")
        return answer

    async def cmd_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /plan — составить план проекта или задачи."""
        goal = " ".join(context.args) if context.args else ""
        if not goal:
            await update.message.reply_text("Использование: /plan <цель или проект>")
            return
        result = await self.handle_task(goal)
        await update.message.reply_text(result)

    async def cmd_roadmap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /roadmap — построить дорожную карту."""
        project = " ".join(context.args) if context.args else ""
        if not project:
            await update.message.reply_text("Использование: /roadmap <название проекта>")
            return
        result = await self.handle_task(f"Составь roadmap для проекта: {project}")
        await update.message.reply_text(result)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("plan", self.cmd_plan))
        self.app.add_handler(CommandHandler("roadmap", self.cmd_roadmap))
