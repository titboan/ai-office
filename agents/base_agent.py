from __future__ import annotations

import contextlib
import json
import traceback
from abc import ABC, abstractmethod
from typing import Optional

import anthropic
import redis.asyncio as aioredis
from loguru import logger
from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import config

# TTL истории в Redis: 7 дней. После этого Redis сам удалит ключ.
_HISTORY_TTL_SECONDS: int = 60 * 60 * 24 * 7
# Максимум сообщений в истории (пар user/assistant → 10 диалогов)
_MAX_HISTORY: int = 20


class BaseAgent(ABC):
    """Базовый класс для всех агентов ИИ-офиса.

    Хранение истории:
    - Если REDIS_URL задан → Redis (персистентная память между перезапусками)
    - Если REDIS_URL не задан → dict в памяти процесса (работает локально без Redis)
    """

    name: str = "Agent"
    role: str = "Агент"
    emoji: str = "🤖"
    system_prompt: str = ""

    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token
        self.claude = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self.app: Optional[Application] = None

        # Redis-клиент (создаётся лениво при первом обращении)
        self._redis: Optional[aioredis.Redis] = None

        # Fallback: dict в памяти, если Redis не задан
        self._history_fallback: dict[int, list[dict]] = {}

        backend = "Redis" if config.REDIS_URL else "dict (fallback, Redis не задан)"
        logger.debug(f"[{self.name}] Хранилище истории: {backend}")

    # ------------------------------------------------------------------ #
    #  Redis — подключение                                                #
    # ------------------------------------------------------------------ #

    async def _get_redis(self) -> Optional[aioredis.Redis]:
        """Вернуть Redis-клиент или None, если REDIS_URL не задан."""
        if not config.REDIS_URL:
            return None
        if self._redis is None:
            self._redis = aioredis.from_url(
                config.REDIS_URL,
                decode_responses=True,   # получаем str, не bytes
                socket_connect_timeout=3,
                socket_timeout=3,
            )
        return self._redis

    async def _close_redis(self) -> None:
        """Закрыть Redis-соединение при остановке агента."""
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------------ #
    #  История диалога — чтение / запись                                  #
    # ------------------------------------------------------------------ #

    def _history_key(self, chat_id: int) -> str:
        """Redis-ключ для истории конкретного чата."""
        return f"history:{self.name}:{chat_id}"

    async def _load_history(self, chat_id: int) -> list[dict]:
        """Загрузить историю из Redis (или из fallback dict)."""
        redis = await self._get_redis()

        if redis is None:
            # Fallback: простой dict в памяти
            return list(self._history_fallback.get(chat_id, []))

        try:
            raw = await redis.get(self._history_key(chat_id))
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"[{self.name}] Redis read error (chat={chat_id}): {e}")

        return []

    async def _save_history(self, chat_id: int, history: list[dict]) -> None:
        """Сохранить историю в Redis (или в fallback dict)."""
        # Обрезаем до лимита перед сохранением
        if len(history) > _MAX_HISTORY:
            history = history[-_MAX_HISTORY:]

        redis = await self._get_redis()

        if redis is None:
            # Fallback: dict в памяти
            self._history_fallback[chat_id] = history
            return

        try:
            await redis.set(
                self._history_key(chat_id),
                json.dumps(history, ensure_ascii=False),
                ex=_HISTORY_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning(f"[{self.name}] Redis write error (chat={chat_id}): {e}")
            # При ошибке Redis — сохраняем в dict, чтобы не потерять контекст
            self._history_fallback[chat_id] = history

    async def _delete_history(self, chat_id: int) -> None:
        """Удалить историю из Redis и из fallback dict."""
        self._history_fallback.pop(chat_id, None)

        redis = await self._get_redis()
        if redis is None:
            return
        with contextlib.suppress(Exception):
            await redis.delete(self._history_key(chat_id))

    # ------------------------------------------------------------------ #
    #  Claude                                                              #
    # ------------------------------------------------------------------ #

    async def think(self, user_message: str, chat_id: int) -> str:
        """Отправить сообщение в Claude и получить ответ."""
        # Загружаем историю (Redis или dict)
        history = await self._load_history(chat_id)
        history.append({"role": "user", "content": user_message})

        try:
            response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.MAX_TOKENS,
                system=self.system_prompt,
                messages=history,
            )
            answer = response.content[0].text
            history.append({"role": "assistant", "content": answer})

            # Сохраняем обновлённую историю (Redis или dict)
            await self._save_history(chat_id, history)
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
            logger.error(f"[{self.name}] API status {e.status_code}: {e.message}")
            return f"⚠️ Claude API вернул ошибку {e.status_code}: {e.message}"

    # ------------------------------------------------------------------ #
    #  Telegram — отправка в офисную группу                               #
    # ------------------------------------------------------------------ #

    async def post_to_group(self, text: str) -> None:
        """Написать в общую группу офиса от имени агента.

        Работает в двух режимах:
        - self.app задан (бот запущен) → переиспользует HTTP-сессию app.bot
        - self.app = None (worker-режим при делегировании) → создаёт Bot на лету

        Формат: 👤 [Имя агента]: текст
        Если OFFICE_GROUP_ID не задан — пропускает без ошибки.
        """
        if not config.OFFICE_GROUP_ID or not self.bot_token:
            return

        message = f"👤 [{self.name}]: {text}"

        try:
            if self.app:
                # Основной режим: переиспользуем сессию работающего бота
                await self.app.bot.send_message(
                    chat_id=config.OFFICE_GROUP_ID,
                    text=message,
                )
            else:
                # Worker-режим: временный Bot без запущенного Application
                async with Bot(token=self.bot_token) as bot:
                    await bot.send_message(
                        chat_id=config.OFFICE_GROUP_ID,
                        text=message,
                    )
        except Exception as e:
            logger.warning(f"[{self.name}] Ошибка отправки в группу: {e}")

    # Обратная совместимость — старые вызовы post_to_office() продолжают работать
    async def post_to_office(self, text: str) -> None:
        await self.post_to_group(text)

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
        await self._delete_history(chat_id)
        backend = "Redis" if config.REDIS_URL else "памяти"
        await update.message.reply_text(f"🔄 История диалога очищена (из {backend}).")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            logger.debug(f"[{self.name}] Пропуск: нет текста в update")
            return

        chat_id = update.effective_chat.id
        user_text = update.message.text
        user_name = update.effective_user.username or update.effective_user.first_name or "unknown"

        logger.info(f"[{self.name}] Получено сообщение от @{user_name} (chat={chat_id}): {user_text!r}")

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            answer = await self.think(user_text, chat_id)
            await update.message.reply_text(answer)
            logger.info(f"[{self.name}] Ответ отправлен ({len(answer)} символов)")

            await self.post_to_group(answer)

        except Exception as e:
            logger.error(f"[{self.name}] Ошибка в handle_message: {e}\n{traceback.format_exc()}")
            with contextlib.suppress(Exception):
                await update.message.reply_text("⚠️ Произошла внутренняя ошибка. Попробуй ещё раз.")

    @abstractmethod
    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Выполнить задачу, делегированную от другого агента."""

    async def run_task(self, task: str, from_agent: str = "user") -> str:
        """Публичная обёртка над handle_task() с уведомлениями в группу.

        Вызывается из Марты (и агент-к-агенту) вместо handle_task() напрямую.
        Порядок событий в группе:
          1. "📥 [Агент]: Принял задачу от X: ..."
          2. handle_task() выполняется (может постить своё сообщение)
          3. "✅ [Агент]: Задача выполнена: ..."
        """
        short_task = (task[:80] + "…") if len(task) > 80 else task
        await self.post_to_group(f"📥 Принял задачу от {from_agent}: {short_task}")
        logger.info(f"[{self.name}] run_task от {from_agent}: {short_task!r}")

        try:
            result = await self.handle_task(task, from_agent)
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка в handle_task: {e}")
            result = f"⚠️ Не удалось выполнить задачу: {e}"

        short_result = (result[:200] + "…") if len(result) > 200 else result
        await self.post_to_group(f"✅ Задача выполнена: {short_result}")
        return result

    # ------------------------------------------------------------------ #
    #  Запуск                                                             #
    # ------------------------------------------------------------------ #

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Глобальный обработчик ошибок PTB."""
        logger.error(f"[{self.name}] PTB error: {context.error}")
        if context.error:
            logger.error(traceback.format_exc())

    def build_app(self) -> Application:
        self.app = (
            Application.builder()
            .token(self.bot_token)
            .build()
        )
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

    # ── Async-запуск (многоагентный режим) ──────────────────────────────

    async def start_polling_async(self) -> None:
        """Инициализировать и запустить polling без блокировки event loop."""
        app = self.build_app()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info(f"[{self.name}] Polling активен (async mode)")

    async def stop_async(self) -> None:
        """Graceful shutdown: polling + HTTP-сессия + Redis."""
        if self.app:
            with contextlib.suppress(Exception):
                await self.app.updater.stop()
            with contextlib.suppress(Exception):
                await self.app.stop()
            with contextlib.suppress(Exception):
                await self.app.shutdown()
        await self._close_redis()
        logger.info(f"[{self.name}] Остановлен")

    # ── Синхронный запуск (один агент — один процесс) ───────────────────

    def run_polling(self) -> None:
        """PTB 22.x: синхронный, управляет event loop сам."""
        app = self.build_app()
        logger.info(f"[{self.name}] Запуск polling...")
        app.run_polling(drop_pending_updates=True)

    def run_webhook(self, path_suffix: str) -> None:
        """PTB 22.x: синхронный, управляет event loop сам."""
        app = self.build_app()
        webhook_url = f"{config.WEBHOOK_BASE_URL}/{path_suffix}"
        logger.info(f"[{self.name}] Запуск webhook: {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=config.PORT,
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
