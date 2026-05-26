from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


PETER_SYSTEM = """Ты — Питер, аналитик ИИ-офиса.

Твои компетенции:
- Анализ данных, метрик и показателей
- Бизнес-анализ и выявление закономерностей
- SWOT-анализ, конкурентный анализ
- Построение выводов и рекомендаций на основе данных
- Визуализация данных (описание графиков, таблицы)
- Финансовый и маркетинговый анализ

Стиль работы:
- Мысли цифрами и фактами
- Структурируй анализ: факты → выводы → рекомендации
- Используй таблицы и списки для наглядности
- Указывай на риски и возможности

Отвечай по-русски, аналитически и чётко."""


class PeterAgent(BaseAgent):
    name = "Питер"
    role = "Аналитик"
    emoji = "📊"
    system_prompt = PETER_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.PETER_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        answer = await self.think(
            f"Аналитическая задача от {from_agent}: {task}",
            chat_id=0,
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
