from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import save_research
from .base_agent import BaseAgent


PETER_SYSTEM = """Ты Питер, бизнес-аналитик команды.
Твоя роль — брать сырые данные и исследования,
и превращать их в структурированные выводы.

Формат твоего ответа ВСЕГДА:

📊 Ключевые цифры и факты
— [конкретные числа, статистика, даты]

🏆 Конкурентный анализ
— [кто, что, сильные/слабые стороны]

⚠️ Риски и ограничения
— [что может пойти не так]

💡 Выводы и рекомендации
— [конкретные actionable выводы]

Будь конкретным, избегай воды.
Твой вывод читают другие агенты — им нужны факты, не пересказ."""


class PeterAgent(BaseAgent):
    name = "Питер"
    agent_key = "peter"
    role = "Бизнес-аналитик"
    emoji = "📊"
    system_prompt = PETER_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.PETER_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        logger.info(f"[Питер] Задача от {from_agent}: {task!r}")

        answer = await self.think(
            f"Аналитическая задача от {from_agent}: {task}",
            chat_id=0,
            is_task=True,
        )

        # Сохраняем анализ в Notion Research DB
        notion_url = await save_research(
            title=task[:50],
            content=answer,
            source=f"agent:{from_agent}",
            agent="Питер",
        )
        if notion_url:
            logger.info(f"[Питер] Анализ сохранён в Notion: {notion_url}")
            answer = f"{answer}\n\n📄 *Анализ сохранён в Notion:* {notion_url}"

        await self.post_to_group(f"📊 Анализ готов: {answer[:200]}…")
        return answer

    async def cmd_analyze(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/analyze <данные> — бизнес-анализ с выводами."""
        data = " ".join(context.args) if context.args else ""
        if not data:
            await update.message.reply_text(
                "Использование: /analyze <данные или описание>\n"
                "Пример: /analyze рынок CRM систем в России 2024"
            )
            return
        await update.message.reply_text("📊 Анализирую…")
        result = await self.handle_task(data, from_agent="команды /analyze")
        if len(result) <= 4096:
            await update.message.reply_text(result)
        else:
            for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
                await update.message.reply_text(chunk)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("analyze", self.cmd_analyze))
