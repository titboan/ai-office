from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


PETER_SYSTEM = """Ты — Питер, аналитик ИИ-офиса.

Анализируешь данные, метрики, бизнес-ситуации. SWOT, конкурентный, финансовый анализ.
Структура: факты → выводы → рекомендации. Используй таблицы и списки.

Отвечай по-русски, чётко и конкретно."""


class PeterAgent(BaseAgent):
    name = "Питер"
    agent_key = "peter"
    role = "Аналитик"
    emoji = "📊"
    system_prompt = PETER_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.PETER_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        answer = await self.think(
            f"Аналитическая задача от {from_agent}: {task}",
            chat_id=0,
            is_task=True,
        )
        await self.post_to_office(f"📈 Анализ готов: {answer[:200]}...")
        return answer

    async def cmd_analyze(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /analyze — запустить анализ данных или ситуации."""
        data = " ".join(context.args) if context.args else ""
        if not data:
            await update.message.reply_text("Использование: /analyze <данные или описание ситуации>")
            return
        result = await self.handle_task(data)
        await update.message.reply_text(result)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("analyze", self.cmd_analyze))
