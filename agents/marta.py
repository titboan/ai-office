from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


MARTA_SYSTEM = """Ты — Марта, главный координатор ИИ-офиса.

Твоя роль:
- Принимать задачи от пользователей и руководства
- Анализировать задачу и определять, кому из команды её делегировать
- Следить за ходом выполнения и сводить результаты

Команда офиса:
• Кевин (Kevin) — разработчик, пишет код, решает технические задачи
• Каспер (Kasper) — исследователь, ищет информацию, изучает темы
• Питер (Peter) — аналитик, анализирует данные и строит выводы
• Элина (Elina) — копирайтер, создаёт тексты и контент
• Алекс (Alex) — планировщик, составляет планы и стратегии

Отвечай чётко и структурированно. При делегировании используй формат:
→ [Имя агента]: [что нужно сделать]

Общайся по-русски, профессионально, но дружелюбно."""


class MartaAgent(BaseAgent):
    name = "Марта"
    role = "Координатор офиса"
    emoji = "👩‍💼"
    system_prompt = MARTA_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.MARTA_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        answer = await self.think(
            f"Задача от {from_agent}: {task}\n\nПроанализируй и делегируй команде.",
            chat_id=0,
        )
        await self.post_to_office(f"📋 Новая задача получена. {answer}")
        return answer

    async def cmd_delegate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /delegate — явное делегирование задачи команде."""
        task = " ".join(context.args) if context.args else ""
        if not task:
            await update.message.reply_text("Использование: /delegate <описание задачи>")
            return

        result = await self.handle_task(task, from_agent="менеджера")
        await update.message.reply_text(f"✅ Задача делегирована:\n\n{result}")

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("delegate", self.cmd_delegate))
