from __future__ import annotations

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


ELINA_SYSTEM = """Ты — Элина, ведущий копирайтер ИИ-офиса.

Твои компетенции:
- Создание продающих текстов и лендингов
- Написание постов для социальных сетей (Telegram, Instagram, LinkedIn)
- Email-рассылки и новостные письма
- SEO-тексты и статьи для блогов
- Сценарии для видео и подкастов
- Редактура и корректура текстов

Стиль работы:
- Пиши живо, ярко, с характером
- Адаптируй тон под аудиторию и платформу
- Используй сторителлинг и эмоции
- Добавляй призывы к действию
- Всегда предлагай несколько вариантов заголовков

Отвечай по-русски, творчески и убедительно."""


class ElinaAgent(BaseAgent):
    name = "Элина"
    role = "Копирайтер"
    emoji = "✍️"
    system_prompt = ELINA_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.ELINA_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        answer = await self.think(
            f"Задача на создание контента от {from_agent}: {task}",
            chat_id=0,
        )
        await self.post_to_office(f"📝 Текст готов: {answer[:200]}...")
        return answer

    async def cmd_write(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /write — написать текст по запросу."""
        brief = " ".join(context.args) if context.args else ""
        if not brief:
            await update.message.reply_text("Использование: /write <бриф на текст>")
            return
        result = await self.handle_task(brief)
        await update.message.reply_text(result)

    async def cmd_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда /post — написать пост для Telegram."""
        topic = " ".join(context.args) if context.args else ""
        if not topic:
            await update.message.reply_text("Использование: /post <тема поста>")
            return
        result = await self.handle_task(f"Напиши Telegram-пост на тему: {topic}")
        await update.message.reply_text(result)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("write", self.cmd_write))
        self.app.add_handler(CommandHandler("post", self.cmd_post))
