from __future__ import annotations

import traceback
from abc import ABC, abstractmethod
from typing import Optional

import anthropic
from loguru import logger
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import config


class BaseAgent(ABC):
    """Базовый класс для всех агентов ИИ-офиса.

    Использует:
    - anthropic.AsyncAnthropic (нативный async-клиент, без run_in_executor)
    - python-telegram-bot 22.x (Application / polling / webhook)
    """

    name: str = "Agent"
    role: str = "Агент"
    emoji: str = "🤖"
    system_prompt: str = ""

    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token
        # AsyncAnthropic — нативный async-клиент (появился в anthropic >= 0.3)
        self.claude = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self.app: Optional[Application] = None
        self._conversation_history: dict[int, list[dict]] = {}

    # ------------------------------------------------------------------ #
    #  Claude                                                              #
    # ------------------------------------------------------------------ #

    async def think(self, user_message: str, chat_id: int) -> str:
        """Отправить сообщение в Claude и получить ответ."""
        history = self._conversation_history.setdefault(chat_id, [])
        history.append({"role": "user", "content": user_message})

        # Храним не более 20 последних сообщений (10 пар user/assistant)
        if len(history) > 20:
            history = history[-20:]
            self._conversation_history[chat_id] = history

        try:
            # anthropic 0.104.1: AsyncAnthropic.messages.create() — нативный корутин
            response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.MAX_TOKENS,
                system=self.system_prompt,
                messages=history,
            )
            answer = response.content[0].text
            history.append({"role": "assistant", "content": answer})
            return answer

        except anthropic.RateLimitError as e:
            logger.warning(f"[{self.name}] Rate limit: {e}")
            return "⏳ Превышен лимит запросов. Попробуй через минуту."

        except anthropic.AuthenticationError as e:
            logger.error(f"[{self.name}] Auth error: {e}")
            return "🔑 Ошибка API-ключа. Проверь ANTHROPIC_API_KEY."

        except anthropic.APIConnectionError as e:
            logger.error(f"[{self.name}] Connection error: {e}")
            return "🌐 Нет связи с Claude API. Проверь сеть."

        except anthropic.APIStatusError as e:
            # HTTP 4xx / 5xx от сервера
            logger.error(f"[{self.name}] API status {e.status_code}: {e.message}")
            return f"⚠️ Claude API вернул ошибку {e.status_code}: {e.message}"

    # ------------------------------------------------------------------ #
    #  Telegram — отправка в офисный чат                                  #
    # ------------------------------------------------------------------ #

    async def post_to_office(self, text: str) -> None:
        """Написать сообщение в общую группу офиса."""
        if not self.app or not config.OFFICE_GROUP_ID:
            return
        try:
            await self.app.bot.send_message(
                chat_id=config.OFFICE_GROUP_ID,
                text=f"{self.emoji} *{self.name}*: {text}",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка отправки в группу: {e}")

    # ------------------------------------------------------------------ #
    #  Telegram — обработчики                                             #
    # ------------------------------------------------------------------ #

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"{self.emoji} Привет! Я *{self.name}* — {self.role}.\n"
            f"Напиши мне задачу, и я займусь ею.",
            parse_mode="Markdown",
        )

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        self._conversation_history.pop(chat_id, None)
        await update.message.reply_text("🔄 История диалога очищена.")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Защита: update или message могут быть None (редактирование, канал и т.д.)
        if not update.message or not update.message.text:
            logger.debug(f"[{self.name}] Пропуск: нет текста в update")
            return

        chat_id = update.effective_chat.id
        user_text = update.message.text
        user_name = update.effective_user.username or update.effective_user.first_name or "unknown"

        # ← Логирование каждого входящего сообщения
        logger.info(f"[{self.name}] Получено сообщение от @{user_name} (chat={chat_id}): {user_text!r}")

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            answer = await self.think(user_text, chat_id)
            await update.message.reply_text(answer)
            logger.info(f"[{self.name}] Ответ отправлен ({len(answer)} символов)")

            # Агент дублирует ответ в общий офисный чат
            await self.post_to_office(answer)

        except Exception as e:
            logger.error(f"[{self.name}] Ошибка в handle_message: {e}\n{traceback.format_exc()}")
            try:
                await update.message.reply_text("⚠️ Произошла внутренняя ошибка. Попробуй ещё раз.")
            except Exception:
                pass

    @abstractmethod
    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Выполнить задачу, делегированную от другого агента."""

    # ------------------------------------------------------------------ #
    #  Запуск                                                             #
    # ------------------------------------------------------------------ #

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Глобальный обработчик ошибок PTB — логирует всё что падает внутри handlers."""
        logger.error(f"[{self.name}] PTB error: {context.error}")
        if context.error:
            logger.error(traceback.format_exc())

    def build_app(self) -> Application:
        self.app = (
            Application.builder()
            .token(self.bot_token)
            .build()
        )
        # Глобальный error handler — исключения больше не теряются молча
        self.app.add_error_handler(self._error_handler)

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("reset", self.cmd_reset))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        self._register_extra_handlers()

        logger.info(f"[{self.name}] Handlers зарегистрированы: /start, /reset, MessageHandler(TEXT)")
        return self.app

    def _register_extra_handlers(self) -> None:
        """Переопределить в потомке для регистрации дополнительных команд."""

    def run_polling(self) -> None:
        """Запустить бота в режиме long-polling (локальная разработка).

        PTB 22.x: run_polling() — синхронный метод, управляет event loop сам.
        """
        app = self.build_app()
        logger.info(f"[{self.name}] Запуск polling...")
        app.run_polling(drop_pending_updates=True)

    def run_webhook(self, path_suffix: str) -> None:
        """Запустить бота на вебхуке (Railway / production).

        PTB 22.x: run_webhook() — синхронный метод, управляет event loop сам.
        """
        app = self.build_app()
        webhook_url = f"{config.WEBHOOK_BASE_URL}/{path_suffix}"
        logger.info(f"[{self.name}] Запуск webhook: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
