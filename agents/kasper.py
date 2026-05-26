from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


KASPER_SYSTEM = """Ты — Каспер, исследователь ИИ-офиса.

Твои компетенции:
- Глубокое изучение любых тем и вопросов
- Поиск и синтез информации из разных источников
- Сравнительный анализ технологий, подходов, решений
- Подготовка исследовательских отчётов
- Мониторинг трендов в IT, бизнесе, науке

Стиль работы:
- Структурируй информацию: заголовки, списки, выводы
- Указывай источники и степень достоверности
- Выделяй ключевые инсайты
- Давай рекомендации на основе найденного

Отвечай по-русски, подробно и структурированно."""


class KasperAgent(BaseAgent):
    name = "Каспер"
    role = "Исследователь"
    emoji = "🔍"
    system_prompt = KASPER_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.KASPER_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        answer = await self.think(
            f"Исследовательская задача от {from_agent}: {task}",
            chat_id=0,
        )
        await self.post_to_office(f"📚 Исследование завершено: {answer[:200]}...")
        return answer

    async def cmd_research(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /research — запустить исследование темы."""
        topic = " ".join(context.args) if context.args else ""
        if not topic:
            await update.message.reply_text("Использование: /research <тема для исследования>")
            return
        result = await self.handle_task(topic)
        await update.message.reply_text(result)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("research", self.cmd_research))
