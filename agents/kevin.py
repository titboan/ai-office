from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


KEVIN_SYSTEM = """Ты — Кевин, старший разработчик ИИ-офиса.

Твои компетенции:
- Написание чистого, эффективного кода на Python, JavaScript, TypeScript
- Архитектура программных решений
- Code review и рефакторинг
- Решение технических проблем и багов
- DevOps: Docker, CI/CD, деплой

Стиль работы:
- Пиши рабочий код с комментариями
- Объясняй технические решения понятно
- Указывай на возможные проблемы и edge cases
- Предлагай лучшие практики

Отвечай по-русски, код оформляй в блоки ```python / ```js и т.д."""


class KevinAgent(BaseAgent):
    name = "Кевин"
    agent_key = "kevin"
    role = "Старший разработчик"
    emoji = "👨‍💻"
    system_prompt = KEVIN_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.KEVIN_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        answer = await self.think(
            f"Задача на разработку от {from_agent}: {task}",
            chat_id=0,
        )
        await self.post_to_office(f"💻 Код готов: {answer[:200]}...")
        return answer

    async def cmd_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /code — запросить написание кода."""
        task = " ".join(context.args) if context.args else ""
        if not task:
            await update.message.reply_text("Использование: /code <что нужно написать>")
            return
        result = await self.handle_task(task)
        await update.message.reply_text(result)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("code", self.cmd_code))
