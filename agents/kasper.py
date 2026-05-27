from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import search_web
from .base_agent import BaseAgent


KASPER_SYSTEM = """Ты — Каспер, исследователь ИИ-офиса с доступом к интернету.

Твои компетенции:
- Глубокое изучение любых тем с опорой на актуальные данные из сети
- Синтез информации из множества источников
- Сравнительный анализ технологий, подходов, решений
- Подготовка структурированных исследовательских отчётов
- Мониторинг трендов в IT, бизнесе, науке

Как ты работаешь:
- Перед ответом ты получаешь результаты веб-поиска по запросу пользователя
- Используй эти данные как основу, дополняй своими знаниями
- Всегда указывай источники (URL) из результатов поиска
- Если данные из поиска устарели или противоречивы — отметь это явно
- Разделяй «найдено в сети» и «моё мнение / анализ»

Стиль ответа:
- Структурируй: заголовки, списки, выводы
- Выделяй ключевые инсайты
- Давай конкретные рекомендации
- Указывай степень достоверности информации

Отвечай по-русски, подробно и аналитически."""


class KasperAgent(BaseAgent):
    name = "Каспер"
    role = "Исследователь"
    emoji = "🔍"
    system_prompt = KASPER_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.KASPER_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  Поиск + ответ                                                       #
    # ------------------------------------------------------------------ #

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Переопределяем: сначала ищем в интернете, потом думаем."""
        if not update.message or not update.message.text:
            return

        chat_id   = update.effective_chat.id
        user_text = update.message.text
        user_name = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )

        logger.info(f"[{self.name}] Получено от @{user_name} (chat={chat_id}): {user_text!r}")

        try:
            # 1. Показываем «печатает…» пока идёт поиск
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            # 2. Веб-поиск
            logger.info(f"[{self.name}] Веб-поиск: {user_text!r}")
            search_results = await search_web(user_text)

            # 3. Собираем обогащённый промпт: вопрос + результаты поиска
            enriched_message = (
                f"{user_text}\n\n"
                f"[Результаты веб-поиска]:\n{search_results}\n\n"
                f"Проанализируй найденное и дай развёрнутый ответ."
            )

            # 4. Отправляем в Claude (история сохраняется через BaseAgent)
            answer = await self.think(enriched_message, chat_id)

            await update.message.reply_text(answer)
            logger.info(f"[{self.name}] Ответ отправлен ({len(answer)} символов)")

            await self.post_to_office(answer)

        except Exception as e:
            import traceback
            logger.error(f"[{self.name}] Ошибка: {e}\n{traceback.format_exc()}")
            try:
                await update.message.reply_text("⚠️ Произошла ошибка. Попробуй ещё раз.")
            except Exception:
                pass

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Делегированная задача: поиск + ответ без Telegram update."""
        logger.info(f"[{self.name}] Задача от {from_agent}: {task!r}")

        search_results = await search_web(task)
        enriched = (
            f"Исследовательская задача от {from_agent}: {task}\n\n"
            f"[Результаты веб-поиска]:\n{search_results}\n\n"
            f"Проанализируй и дай структурированный отчёт."
        )
        answer = await self.think(enriched, chat_id=0)
        await self.post_to_office(f"📚 Исследование завершено: {answer[:200]}…")
        return answer

    # ------------------------------------------------------------------ #
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_research(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/research <тема> — глубокое исследование с поиском."""
        topic = " ".join(context.args) if context.args else ""
        if not topic:
            await update.message.reply_text(
                "Использование: /research <тема>\n"
                "Пример: /research последние новости о GPT-5"
            )
            return
        # Перенаправляем через handle_message-логику
        await update.message.reply_text(f"🔍 Ищу информацию по теме: *{topic}*…", parse_mode="Markdown")
        result = await self.handle_task(topic, from_agent="команды /research")
        await update.message.reply_text(result)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("research", self.cmd_research))
