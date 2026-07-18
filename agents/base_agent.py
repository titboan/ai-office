from __future__ import annotations

import asyncio
import contextlib
import io
import json
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any, Optional

import anthropic
import redis.asyncio as aioredis
from loguru import logger
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
    ContextTypes,
)

from config import config
from db import log_event
from utils.tg_format import clean_agent_output as _clean_output
from utils.tg_rich import send_rich_or_fallback as _send_rich
from utils.tg_rich import looks_like_html, send_html_message
from task_queue import (
    get_next_task,
    mark_running,
    mark_completed,
    mark_failed,
    cleanup_timed_out_tasks,
    update_task_cost,
)

# Groq SDK — опциональная зависимость (транскрипция голосовых через Whisper)
try:
    from groq import AsyncGroq as _AsyncGroq
    _GROQ_AVAILABLE = True
except ImportError:
    _AsyncGroq = None          # type: ignore[assignment,misc]
    _GROQ_AVAILABLE = False

# TTL истории в Redis: 7 дней. После этого Redis сам удалит ключ.
_HISTORY_TTL_SECONDS: int = 60 * 60 * 24 * 7
# Максимум сообщений в истории (пар user/assistant → 10 диалогов)
_MAX_HISTORY: int = 20

# ── Оптимизация токенов ───────────────────────────────────────────────────────
HISTORY_DEPTH_TASK   = 0     # задачи из воркер-очереди — история не нужна
HISTORY_DEPTH_DIALOG = 5     # живой диалог с пользователем
HISTORY_DEPTH_MAX    = 10    # абсолютный максимум

MAX_PAYLOAD_CHARS        = 4_000   # обрезка входящего payload
MAX_HISTORY_MSG_CHARS    = 1_000   # обрезка одного сообщения в истории
MAX_SUBTASK_CONTEXT_CHARS = 2_000  # макс символов из result предыдущего агента

HISTORY_MAX_MESSAGES = 15          # порог для автосжатия истории
HISTORY_KEEP_RECENT  = 5           # сколько свежих сообщений оставить
SUMMARY_TTL          = 60 * 60 * 24 * 30  # 30 дней

# ── Стоимость Claude API ($  за 1M токенов: input, output) ───────────────────
_COST_PER_1M: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6":          (3.00,  15.00),
    "claude-opus-4-8":            (15.00, 75.00),
    "claude-haiku-4-5-20251001":  (0.80,   4.00),
}
_DEFAULT_COST = (3.00, 15.00)


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_p, out_p = _COST_PER_1M.get(model, _DEFAULT_COST)
    return (input_tokens * in_p + output_tokens * out_p) / 1_000_000


# Имена агентов для уведомлений в цепочках
_AGENT_NAMES: dict[str, str] = {
    "kasper": "🔍 Каспер",
    "kevin":  "👨‍💻 Кевин",
    "peter":  "📊 Питер",
    "elina":  "✍️ Элина",
    "alex":   "🗓️ Алекс",
    "marta":  "👩‍💼 Марта",
}


def _build_context(prev_results: list[dict]) -> str:
    """Собрать контекст из результатов предыдущих агентов цепочки."""
    parts = []
    for r in prev_results:
        agent = r.get("assigned_agent", "?")
        result = r.get("result") or ""
        if len(result) > MAX_SUBTASK_CONTEXT_CHARS:
            result = result[:MAX_SUBTASK_CONTEXT_CHARS] + "\n[обрезано]"
        label = _AGENT_NAMES.get(agent, agent)
        parts.append(f"=== {label} ===\n{result}")
    return "\n\n".join(parts)


def with_company_context(system_prompt: str) -> str:
    """Добавить config.COMPANY_CONTEXT к произвольному system-промпту.

    Единая точка для всех прямых вызовов claude.messages.create в агентах —
    без неё специализированные промпты (аудит, ДРР, chain planner и т.п.)
    молча не получали контекст компании, в отличие от основного tool_use цикла.
    """
    ctx = getattr(config, "COMPANY_CONTEXT", "")
    return f"{system_prompt}\n\n{ctx}" if ctx else system_prompt


def _normalize_plan_steps(steps: list[dict]) -> list[dict]:
    """Добавить поле 'group' если его нет. Обратная совместимость: индекс = группа.

    Шаги с одинаковым group выполняются параллельно.
    Шаги без group получают уникальные group=index (последовательное выполнение).
    """
    if any("group" in s for s in steps):
        return steps
    return [{**s, "group": i} for i, s in enumerate(steps)]


class BaseAgent(ABC):
    """Базовый класс для всех агентов ИИ-офиса.

    Хранение истории:
    - Если REDIS_URL задан → Redis (персистентная память между перезапусками)
    - Если REDIS_URL не задан → dict в памяти процесса (работает локально без Redis)
    """

    name: str = "Agent"
    role: str = "Агент"
    emoji: str = "🤖"
    agent_key: str = ""  # латиница для БД: "kasper", "kevin", etc.
    system_prompt: str = ""

    @property
    def _effective_system(self) -> str:
        return with_company_context(self.system_prompt)

    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token
        self.claude = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self.app: Optional[Application] = None

        # Redis-клиент (создаётся лениво при первом обращении)
        self._redis: Optional[aioredis.Redis] = None

        # Fallback: dict в памяти, если Redis не задан
        self._history_fallback: dict[int, list[dict]] = {}

        # Groq AsyncGroq клиент — создаётся лениво при первом голосовом сообщении
        self._groq: Any | None = None

        # Worker loop — управление фоновой задачей (создаются в start_polling_async)
        self._worker_stop_event: asyncio.Event = asyncio.Event()
        self._worker_task: asyncio.Task | None = None

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

    async def _redis_get(self, key: str) -> str | None:
        redis = await self._get_redis()
        if redis is None:
            return self._history_fallback.get(key)
        try:
            return await redis.get(key)
        except Exception as e:
            logger.warning(f"[{self.name}] Redis get error ({key}): {e}")
            return None

    async def _redis_set(self, key: str, value: str, ttl: int | None = None) -> None:
        redis = await self._get_redis()
        if redis is None:
            self._history_fallback[key] = value
            return
        try:
            await redis.set(key, value, ex=ttl)
        except Exception as e:
            logger.warning(f"[{self.name}] Redis set error ({key}): {e}")

    async def _redis_acquire_lock(self, key: str, value: str, ttl: int) -> bool:
        """
        Атомарно занять лок (SET NX EX) — вместо check-then-set (`_redis_get` + `_redis_set`
        по отдельности), где между проверкой и установкой есть await-разрыв и конкурентный
        вызов (кнопка + тик планировщика, или два быстрых тапа) может пройти проверку
        одновременно и выполнить действие дважды.
        Returns True — лок наш (действие можно выполнять), False — уже занят кем-то другим
        (в т.ч. при ошибке Redis — безопаснее пропустить действие, чем рискнуть дублем).
        """
        redis = await self._get_redis()
        if redis is None:
            if key in self._history_fallback:
                return False
            self._history_fallback[key] = value
            return True
        try:
            return bool(await redis.set(key, value, nx=True, ex=ttl))
        except Exception as e:
            logger.warning(f"[{self.name}] Redis lock error ({key}): {e}")
            return False

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
    #  Автосжатие истории                                                  #
    # ------------------------------------------------------------------ #

    async def _compress_history_if_needed(self, chat_id: int) -> None:
        """Сжимает историю если накопилось HISTORY_MAX_MESSAGES сообщений.
        Саммари хранится в Redis по ключу summary:{agent_key}:{chat_id}."""
        history = await self._load_history(chat_id)
        if len(history) < HISTORY_MAX_MESSAGES:
            return

        old_messages    = history[:-HISTORY_KEEP_RECENT]
        recent_messages = history[-HISTORY_KEEP_RECENT:]

        formatted = "\n".join(
            f"{m['role'].upper()}: {m['content'][:500]}"
            for m in old_messages
        )

        try:
            response = await self.claude.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": (
                        "Сделай краткую выжимку диалога (3-5 пунктов).\n"
                        "Сохрани: ключевые решения, важные факты, незакрытые задачи.\n"
                        "Выброси: светские беседы, уточнения, повторы.\n"
                        "Только самое важное, кратко.\n\n"
                        f"Диалог:\n{formatted}"
                    ),
                }],
            )
            new_summary = response.content[0].text
        except Exception as e:
            logger.warning(f"[{self.name}] Сжатие истории — ошибка Claude: {e}")
            return

        summary_key = f"summary:{self.agent_key}:{chat_id}"
        existing = await self._redis_get(summary_key)
        if existing:
            new_summary = existing + "\n\n[Продолжение]\n" + new_summary

        await self._redis_set(summary_key, new_summary, ttl=SUMMARY_TTL)
        await self._save_history(chat_id, recent_messages)

        logger.info(
            f"history_compressed | agent={self.agent_key} | chat_id={chat_id} | "
            f"old={len(old_messages)} | kept={len(recent_messages)}"
        )

    async def _get_summary(self, chat_id: int) -> str | None:
        return await self._redis_get(f"summary:{self.agent_key}:{chat_id}")

    # ------------------------------------------------------------------ #
    #  Уточняющие вопросы — пауза перед неоднозначным действием           #
    # ------------------------------------------------------------------ #

    async def _save_clarification(
        self,
        chat_id: int,
        question: str,
        original_payload: str,
    ) -> None:
        """Сохранить запрос на уточнение в Redis на 24 ч.

        Используется Мартой: она сохраняет state и отправляет вопрос через reply_func сама.
        """
        key = f"clarification:{chat_id}"
        await self._redis_set(key, json.dumps({
            "agent_key": self.agent_key,
            "question": question,
            "original_payload": original_payload,
        }, ensure_ascii=False), ttl=86_400)

    async def request_clarification(
        self,
        chat_id: int,
        question: str,
        original_payload: str,
    ) -> None:
        """Сохранить вопрос в Redis и отправить пользователю через _notify_user.

        Для агентов, которые спрашивают mid-task (без доступа к reply_func Марты).
        После ответа пользователя Марта подхватит ответ и поставит задачу повторно:
        original_payload + ответ пользователя в контексте.
        """
        await self._save_clarification(chat_id, question, original_payload)
        await self._notify_user(chat_id, f"❓ {question}")

    async def _pop_clarification(self, chat_id: int) -> dict | None:
        """Прочитать и удалить ожидающий уточнения запрос из Redis.

        Возвращает dict с полями: agent_key, question, original_payload.
        Возвращает None если запроса нет.
        """
        key = f"clarification:{chat_id}"
        raw = await self._redis_get(key)
        if not raw:
            return None
        await self._redis_set(key, "", ttl=1)
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Claude                                                              #
    # ------------------------------------------------------------------ #

    async def think(self, user_message: str, chat_id: int, is_task: bool = False, max_tokens: int | None = None) -> str:
        """Отправить сообщение в Claude и получить ответ.

        is_task=True: задача из воркер-очереди — история не грузится и не сохраняется.
        is_task=False: диалог с пользователем — история из Redis, глубина HISTORY_DEPTH_DIALOG.
        """
        # Обрезаем payload
        if len(user_message) > MAX_PAYLOAD_CHARS:
            user_message = user_message[:MAX_PAYLOAD_CHARS] + "\n[текст обрезан]"

        if is_task:
            history: list[dict] = []
        else:
            await self._compress_history_if_needed(chat_id)
            history = await self._load_history(chat_id)
            history = history[-HISTORY_DEPTH_DIALOG:]
            # Обрезаем длинные сообщения в истории
            history = [
                {**msg, "content": msg["content"][:MAX_HISTORY_MSG_CHARS]}
                if len(msg.get("content", "")) > MAX_HISTORY_MSG_CHARS
                else msg
                for msg in history
            ]
            summary = await self._get_summary(chat_id)
            if summary:
                history = [
                    {"role": "user",      "content": f"[Контекст прошлых сессий]\n{summary}"},
                    {"role": "assistant", "content": "Понял, учту контекст."},
                ] + history

        history.append({"role": "user", "content": user_message})

        try:
            response = await self.claude.messages.create(
                model=getattr(self, "claude_model", None) or config.CLAUDE_MODEL,
                max_tokens=max_tokens or config.MAX_TOKENS,
                system=self._effective_system,
                messages=history,
            )
            answer = response.content[0].text

            logger.debug(
                f"tokens | agent={self.agent_key} | is_task={is_task} | "
                f"history_msgs={len(history)-1} | payload_chars={len(user_message)} | "
                f"input={response.usage.input_tokens} | output={response.usage.output_tokens}"
            )

            task_tokens = getattr(self, "_task_tokens", None)
            if task_tokens is not None:
                _model = getattr(self, "claude_model", None) or config.CLAUDE_MODEL
                task_tokens["input"]  += response.usage.input_tokens
                task_tokens["output"] += response.usage.output_tokens
                task_tokens["cost"]   += _calc_cost(_model, response.usage.input_tokens, response.usage.output_tokens)

            if not is_task:
                history.append({"role": "assistant", "content": answer})
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
    #  Telegram — обработчики                                             #
    # ------------------------------------------------------------------ #

    def _help_text(self) -> str:
        return (
            f"{self.emoji} **{self.name}** — {self.role}\n\n"
            "/start — главное меню\n"
            "/reset — очистить историю\n\n"
            "Напишите задачу, и я займусь ею."
        )

    def _bot_commands(self) -> list[BotCommand]:
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _send_rich(
            self.bot_token, update.effective_chat.id, self._help_text(),
            reply_to_message_id=update.message.message_id,
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _send_rich(
            self.bot_token, update.effective_chat.id, self._help_text(),
            reply_to_message_id=update.message.message_id,
        )

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        await self._delete_history(chat_id)
        backend = "Redis" if config.REDIS_URL else "памяти"
        await update.message.reply_text(f"🔄 История диалога очищена (из {backend}).")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, override_text: str | None = None) -> None:
        if not update.message or (not update.message.text and not override_text):
            logger.debug(f"[{self.name}] Пропуск: нет текста в update")
            return

        chat_id = update.effective_chat.id
        user_text = override_text or update.message.text
        user_name = update.effective_user.username or update.effective_user.first_name or "unknown"

        logger.info(f"[{self.name}] Получено сообщение от @{user_name} (chat={chat_id}): {user_text!r}")

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            answer = await self.think(user_text, chat_id)
            await self._send_agent_text(
                update.effective_chat.id, answer,
                reply_to_message_id=update.message.message_id,
            )
            logger.info(f"[{self.name}] Ответ отправлен ({len(answer)} символов)")

        except Exception as e:
            logger.error(f"[{self.name}] Ошибка в handle_message: {e}\n{traceback.format_exc()}")
            with contextlib.suppress(Exception):
                await update.message.reply_text("⚠️ Произошла внутренняя ошибка. Попробуй ещё раз.")

    # ------------------------------------------------------------------ #
    #  Голосовые сообщения — Groq Whisper                                  #
    # ------------------------------------------------------------------ #

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
        """Транскрибирует голосовое через Groq Whisper, затем передаёт в handle_message().

        Работает для всех агентов: Касперу достанется транскрипция → он сделает
        веб-поиск; Марте → она делегирует нужному агенту; остальным → think().
        Если GROQ_API_KEY не задан — вежливо сообщает об этом пользователю.
        """
        if not update.message:
            return

        chat_id = update.effective_chat.id
        user_name = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )

        # ── Проверяем доступность Groq ─────────────────────────────────────
        if not _GROQ_AVAILABLE:
            logger.warning(f"[{self.name}] groq не установлен — pip install groq")
            await update.message.reply_text(
                "⚠️ Голосовые сообщения не поддерживаются: пакет groq не установлен.\n"
                "Напиши запрос текстом."
            )
            return

        if not config.GROQ_API_KEY:
            logger.warning(f"[{self.name}] GROQ_API_KEY не задан — голосовые недоступны")
            await update.message.reply_text(
                "⚠️ Голосовые сообщения не поддерживаются: GROQ_API_KEY не задан.\n"
                "Добавь ключ на https://console.groq.com → API Keys, "
                "затем пропиши GROQ_API_KEY в переменных окружения."
            )
            return

        # ── Ленивая инициализация клиента ─────────────────────────────────
        if self._groq is None:
            self._groq = _AsyncGroq(api_key=config.GROQ_API_KEY)

        voice = update.message.voice
        logger.info(
            f"[{self.name}] Голосовое от @{user_name} (chat={chat_id}): "
            f"duration={voice.duration}s size={voice.file_size}b"
        )

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        # ── Скачиваем файл в память ────────────────────────────────────────
        try:
            tg_file = await context.bot.get_file(voice.file_id)
            voice_bytes: bytearray = await tg_file.download_as_bytearray()
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка скачивания голосового: {e}")
            await update.message.reply_text("⚠️ Не удалось загрузить голосовое сообщение.")
            return

        # ── Транскрипция через Groq Whisper ────────────────────────────────
        try:
            transcription = await self._groq.audio.transcriptions.create(
                model="whisper-large-v3",
                file=("voice.ogg", bytes(voice_bytes)),
                language="ru",
            )
            user_text = transcription.text.strip()
            logger.info(f"[{self.name}] Whisper распознал: {user_text!r}")
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка Groq Whisper: {type(e).__name__}: {e}")
            await update.message.reply_text("⚠️ Не удалось распознать голосовое сообщение.")
            return

        if not user_text:
            await update.message.reply_text("🎤 Голосовое получено, но текст пустой. Попробуй ещё раз.")
            return

        # ── Показываем транскрипцию и передаём в handle_message ───────────
        from telegram import Chat
        is_group = update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP)
        if not is_group:
            await update.message.reply_text(f"🎤 Распознано: {user_text}")
            await self.handle_message(update, context, override_text=user_text)
        return user_text

    @abstractmethod
    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Выполнить задачу, делегированную от другого агента."""

    async def run_task(self, task: str, from_agent: str = "user") -> str:
        """Публичная обёртка над handle_task().

        Вызывается из Марты (и агент-к-агенту) вместо handle_task() напрямую.
        """
        short_task = (task[:80] + "…") if len(task) > 80 else task
        logger.info(f"[{self.name}] run_task от {from_agent}: {short_task!r}")

        try:
            result = await self.handle_task(task, from_agent)
        except Exception as e:
            logger.error(f"[{self.name}] Ошибка в handle_task: {e}")
            result = f"⚠️ Не удалось выполнить задачу: {e}"

        return result

    # ------------------------------------------------------------------ #
    #  Worker loop — агент поллит свои задачи из Postgres                 #
    # ------------------------------------------------------------------ #

    async def _worker_loop(self) -> None:
        """Фоновый цикл: берёт задачи из очереди и выполняет.

        Алгоритм одной итерации:
          1. get_next_task() — атомарно берём задачу (UPDATE...RETURNING)
          2. Нет задач → sleep 2s и повторяем
          3. mark_running() — no-op, статус уже проставлен в get_next_task
          4. Уведомляем пользователя: 🔵 выполняется
          5. asyncio.wait_for(handle_task(), timeout) — выполняем с таймаутом
          6. mark_completed() / mark_failed() — пишем результат
          7. Уведомляем пользователя: 🟢 готово / 🔴 ошибка
        Каждые ~60 сек — cleanup_timed_out_tasks() для зависших задач других агентов.
        """
        logger.info(f"[{self.name}] Worker loop запущен")
        if not self.agent_key:
            logger.error(f"[{self.name}] agent_key не задан! Worker loop остановлен.")
            return
        logger.info(f"[{self.name}] agent_key={self.agent_key!r}")
        iteration = 0
        while not self._worker_stop_event.is_set():
            try:
                iteration += 1
                if iteration % 30 == 0:
                    await cleanup_timed_out_tasks()
                    from task_queue import get_due_reminders
                    from tools.ntfy import send_push
                    reminders = await get_due_reminders()
                    for reminder in reminders:
                        sent = False
                        if config.NTFY_TOPIC:
                            sent = await send_push(
                                title="⏰ Напоминание",
                                message=reminder.payload,
                                topic=config.NTFY_TOPIC,
                                priority="high",
                            )
                            if sent:
                                logger.info(f"reminder_sent | ntfy | task_id={reminder.id}")
                            else:
                                logger.warning(f"ntfy_failed | topic={config.NTFY_TOPIC!r} | task_id={reminder.id}")
                        else:
                            logger.warning(f"ntfy_topic_not_set | fallback to telegram | task_id={reminder.id}")

                        if not sent and reminder.chat_id:
                            await self._notify_user(
                                reminder.chat_id,
                                f"⏰ Напоминание: {reminder.payload}",
                                bot_token=config.MARTA_BOT_TOKEN,
                            )
                        await mark_completed(reminder.id, "reminder_sent")

                    # Зависшие цепочки: все известные задачи в терминальном статусе, но
                    # следующая группа так и не была создана (barrier завис/enqueue провалился).
                    # Уведомляем пользователя один раз на цепочку (NX-лок).
                    from task_queue import get_stalled_chains
                    stalled = await get_stalled_chains(config.CHAIN_STALL_TIMEOUT_MINUTES)
                    for chain in stalled:
                        chain_id = chain["chain_id"]
                        acquired = await self._redis_acquire_lock(
                            f"chain_stall_notified:{chain_id}", "1", ttl=86_400
                        )
                        if acquired and chain.get("chat_id"):
                            await self._notify_user(
                                chain["chat_id"],
                                "⚠️ Цепочка задач зависла и не продвигается. Проверь `📋 Статус` или начни заново.",
                                bot_token=config.MARTA_BOT_TOKEN,
                            )
                task = await get_next_task(self.agent_key or self.name.lower())
                if task is None:
                    await asyncio.sleep(2)
                    continue
                await mark_running(task.id)
                await log_event(
                    "TASK_STARTED",
                    task_id=task.id,
                    agent_key=self.agent_key,
                    chain_id=task.chain_id,
                    payload={"from_agent": task.from_agent, "chain_index": task.chain_index},
                )
                logger.info(
                    f"[{self.name}] corr={task.correlation_id[:8]} | "
                    f"task_id={task.id} | payload={task.payload[:60]!r}"
                )
                self._current_chat_id = task.chat_id
                self._task_tokens = {"input": 0, "output": 0, "cost": 0.0}
                _task_start = time.monotonic()
                # Единственная точка входа/выхода для пользователя — бот Марты.
                _reply_token: str | None = config.MARTA_BOT_TOKEN
                _task_completed = False
                _completed_result: str | None = None
                try:
                    result = await asyncio.wait_for(
                        self.handle_task(task.payload, from_agent=task.from_agent),
                        timeout=float(task.timeout_seconds),
                    )
                    await mark_completed(task.id, result)
                    # Задача уже отмечена completed — ниже, до конца worker loop, никакая
                    # ошибка НЕ должна вызывать mark_failed(retry=True), иначе завершённая
                    # задача откатится в queued и выполнится повторно (двойной расход бюджета,
                    # повторное применение цены/ставки, дублирующее сообщение пользователю).
                    _task_completed = True
                    _completed_result = result
                except asyncio.TimeoutError:
                    await mark_failed(task.id, f"Таймаут {task.timeout_seconds}с", retry=False)
                    await log_event(
                        "TASK_FAILED",
                        task_id=task.id,
                        agent_key=self.agent_key,
                        chain_id=task.chain_id,
                        payload={"reason": "timeout", "timeout_seconds": task.timeout_seconds},
                    )
                    if task.chain_id:
                        await self._handle_chain_failure(task)
                    elif task.chat_id:
                        await self._notify_user(
                            task.chat_id,
                            f"⏱️ {self.emoji} **{self.name}**: задача превысила лимит времени.\n\nПопробуй разбить задачу на части.",
                            bot_token=_reply_token,
                        )
                except Exception as e:
                    await mark_failed(task.id, f"{type(e).__name__}: {e}", retry=True)
                    await log_event(
                        "TASK_FAILED",
                        task_id=task.id,
                        agent_key=self.agent_key,
                        chain_id=task.chain_id,
                        payload={"reason": f"{type(e).__name__}: {str(e)[:200]}"},
                    )
                    if task.chain_id and task.retry_count + 1 >= task.max_retries:
                        await self._handle_chain_failure(task)
                    elif task.chat_id and task.retry_count + 1 >= task.max_retries:
                        error_short = str(e)[:150].strip()
                        await self._notify_user(
                            task.chat_id,
                            f"🔴 {self.emoji} **{self.name}** не смог выполнить задачу.\n\n"
                            f"**Причина:** `{error_short}`\n\n"
                            f"Попробуй переформулировать задачу или обратись к Марте.",
                            bot_token=_reply_token,
                        )

                if _task_completed:
                    # Задача уже completed в БД — эти шаги только логируют/уведомляют/двигают
                    # цепочку. Их сбой не должен откатывать уже выполненную задачу обратно
                    # в queued, поэтому здесь нет mark_failed — только logger.error.
                    try:
                        result = _completed_result or ""
                        _latency_ms = int((time.monotonic() - _task_start) * 1000)
                        await update_task_cost(task.id, self._task_tokens["cost"], _latency_ms)
                        await log_event(
                            "TASK_COMPLETED",
                            task_id=task.id,
                            agent_key=self.agent_key,
                            chain_id=task.chain_id,
                            payload={"result_len": len(result)},
                        )
                        task.result = result  # обновляем объект — нужно для _advance_chain → Notion
                        if task.chain_id:
                            await self._advance_chain(task)
                        else:
                            if task.chat_id:
                                result_msg = f"{self._agent_label(result)}\n\n{result}"
                                await self._notify_user(
                                    task.chat_id,
                                    result_msg,
                                    bot_token=_reply_token,
                                )
                    except Exception as e:
                        logger.error(
                            f"[{self.name}] Пост-обработка завершённой задачи {task.id} упала "
                            f"({type(e).__name__}: {e}) — задача уже completed, не откатываем."
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.name}] Worker loop ошибка: {e}")
                await asyncio.sleep(5)
        logger.info(f"[{self.name}] Worker loop остановлен")

    def _agent_label(self, result: str) -> str:
        """Шапка результата — в том же формате (HTML/Markdown), что и тело result.

        Хардкод-отчёты Макса (каталог, маржа, магазины) собраны в HTML (<pre>-таблицы) —
        шапка должна быть <b>...</b>, иначе смешение форматов ломает рендер в _notify_user.
        """
        if looks_like_html(result):
            return f"✅ {self.emoji} <b>{self.name}:</b>"
        return f"✅ {self.emoji} **{self.name}:**"

    async def _send_agent_text(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        reply_markup=None,
        bot_token: str | None = None,
    ) -> bool:
        """Отправить текст ответа агента с автоопределением формата (общий путь для
        _notify_user и handle_message).

        Если text — готовый Telegram HTML (хардкод-отчёты Макса: <pre>-таблицы,
        HTML-промпт Тины и т.п.) — отправляем как есть через send_html_message,
        БЕЗ clean_agent_output (который стирает теги) и БЕЗ Rich Markdown/GFM
        (одиночные \\n в HTML-таблицах схлопываются в soft-break при GFM-рендере).
        Иначе — текущий путь: clean_agent_output → Rich Markdown/GFM.
        Fallback (не-HTML путь): sendRichMessage → sendMessage HTML → plain text.
        Возвращает True, если сообщение реально отправлено.
        """
        token = bot_token or self.bot_token
        if not token:
            return False
        markup_dict = reply_markup.to_dict() if reply_markup else None
        if looks_like_html(text):
            return await send_html_message(
                token, chat_id, text,
                reply_markup_dict=markup_dict, reply_to_message_id=reply_to_message_id,
            )
        text = _clean_output(text)
        return await _send_rich(
            token, chat_id, text,
            reply_markup_dict=markup_dict, reply_to_message_id=reply_to_message_id,
        )

    async def _notify_user(self, chat_id: int, text: str, reply_markup=None, bot_token: str | None = None) -> bool:
        """Отправить сообщение пользователю через Rich Messages (Bot API 10.1).

        bot_token — если передан, используется этот токен (явный override), иначе —
        self.bot_token (собственный бот агента). Формат (HTML vs Rich Markdown/GFM)
        определяется автоматически в _send_agent_text().
        Возвращает True, если сообщение реально отправлено (для ретраев вызывающей стороной).
        """
        try:
            return await self._send_agent_text(chat_id, text, reply_markup=reply_markup, bot_token=bot_token)
        except Exception as e:
            logger.warning(f"[{self.name}] _notify_user ошибка (chat={chat_id}): {e}")
            return False

    # ------------------------------------------------------------------ #
    #  Chain — продвижение цепочки агентов                                #
    # ------------------------------------------------------------------ #

    _CHAIN_AGENT_EMOJI = {
        "kasper": "🔍", "kevin": "👨‍💻", "peter": "📊",
        "elina": "✍️", "alex": "🗓️", "marta": "👩‍💼",
        "dan": "🎨", "tina": "📋", "digest": "📰",
    }
    _CHAIN_AGENT_NAME = {
        "kasper": "Каспер", "kevin": "Кевин", "peter": "Питер",
        "elina": "Элина", "alex": "Алекс", "marta": "Марта",
        "dan": "Дэн", "tina": "Тина", "digest": "Дайджест",
    }

    @classmethod
    def _format_chain_completion_message(
        cls, chain_results: list[dict]
    ) -> tuple[str, "InlineKeyboardMarkup | None"]:
        """Собрать финальное сообщение цепочки: резюме по каждому агенту + ссылка на
        сайт/репозиторий, если Кевин создал GitHub Pages/репо. Вынесено из _advance_chain
        отдельно — это чистое форматирование, не оркестрация, и его можно тестировать
        без БД/Redis/Telegram."""
        import re as _re

        github_pages_url = None
        github_repo_url = None
        for r in chain_results:
            r_result = (r.get("result") or "")
            pages_match = _re.search(r'https://[\w\-]+\.github\.io/[\w\-]+(?:/[\w\-]*)*', r_result)
            repo_match  = _re.search(r'https://github\.com/[\w\-]+/[\w\-]+', r_result)
            if pages_match:
                github_pages_url = pages_match.group(0).rstrip(')/,. ')
            if repo_match:
                github_repo_url = repo_match.group(0).rstrip(')/,. ')

        result_lines = ""
        for r in chain_results:
            agent_k = r.get("assigned_agent", "")
            r_result = (r.get("result") or "").strip()
            emoji = cls._CHAIN_AGENT_EMOJI.get(agent_k, "🤖")
            name  = cls._CHAIN_AGENT_NAME.get(agent_k, agent_k)
            plain = _re.sub(r"<[^>]+>", "", r_result)
            excerpt = plain[:200].strip()
            if len(plain) > 200:
                excerpt += "…"
            if excerpt:
                result_lines += f"{emoji} **{name}:** {excerpt}\n\n"

        final_msg = "🎉 **Команда завершила работу!**\n\n"
        if result_lines:
            final_msg += result_lines
        if github_pages_url:
            final_msg += f'🌐 [Открыть сайт]({github_pages_url})\n'
        elif github_repo_url:
            final_msg += f'📦 [Репозиторий]({github_repo_url})\n'

        buttons = []
        if github_pages_url:
            buttons.append(InlineKeyboardButton("🌐 Открыть сайт", url=github_pages_url))
        keyboard = InlineKeyboardMarkup([buttons]) if buttons else None

        return final_msg, keyboard

    async def _advance_chain(self, completed_task) -> None:
        """После завершения задачи — запустить следующий шаг цепочки.

        Поддерживает параллельные группы: шаги с одинаковым chain_index (group)
        выполняются одновременно; следующая группа стартует когда все завершены.
        Атомарный барьер: Redis DECR на ключ chain_barrier:{chain_id}:{group}.
        """
        from task_queue import enqueue_chain_task, get_chain_results, get_chain_plan

        chain_id    = completed_task.chain_id
        chain_index = completed_task.chain_index   # номер группы
        chain_total = completed_task.chain_total   # количество групп
        chat_id     = completed_task.chat_id
        current_result = getattr(completed_task, "result", None)

        # Единственная точка входа/выхода для пользователя — бот Марты.
        _chain_reply_token: str | None = config.MARTA_BOT_TOKEN

        plan = await get_chain_plan(None, chain_id)
        if not plan:
            logger.error(f"chain_advance | chain_id={chain_id} | plan not found")
            return

        plan_steps = _normalize_plan_steps(plan.get("steps", []))
        next_index = chain_index + 1

        logger.info(
            f"chain_advance | chain_id={chain_id[:8]} | "
            f"group={chain_index} | next_group={next_index} | "
            f"total_groups={chain_total} | is_final={next_index >= chain_total}"
        )

        # ── Параллельный барьер ───────────────────────────────────────────────
        # parallel_group хранится в задаче и совпадает с chain_index для параллельных шагов.
        parallel_group = getattr(completed_task, "parallel_group", None)
        if parallel_group is not None:
            redis = await self._get_redis()
            if redis:
                barrier_key = f"chain_barrier:{chain_id}:{chain_index}"
                remaining = await redis.decr(barrier_key)
            else:
                # Fallback без Redis: считаем из БД (minor race condition возможен)
                from task_queue import count_incomplete_in_group
                remaining = await count_incomplete_in_group(chain_id, chain_index)
            if remaining > 0:
                logger.info(
                    f"parallel_wait | chain_id={chain_id[:8]} | "
                    f"group={chain_index} | remaining={remaining}"
                )
                return  # ждём сестринские задачи

        # ── Цепочка завершена ─────────────────────────────────────────────────
        if next_index >= chain_total:
            logger.info(f"chain_done | chain_id={chain_id[:8]} | total={chain_total}")

            if chat_id:
                chain_results = await get_chain_results(None, chain_id)
                final_msg, keyboard = self._format_chain_completion_message(chain_results)
                await self._notify_user(chat_id, final_msg, reply_markup=keyboard, bot_token=_chain_reply_token)
            return

        # ── Переход к следующей группе ────────────────────────────────────────
        next_steps = [s for s in plan_steps if s.get("group") == next_index]
        if not next_steps:
            logger.error(f"chain_advance | chain_id={chain_id[:8]} | no steps for group={next_index}")
            if chat_id:
                await self._notify_user(chat_id, "⚠️ Ошибка цепочки: следующий шаг не найден в плане.", bot_token=_chain_reply_token)
            return

        is_parallel_next = len(next_steps) > 1

        # Защита от циклов (для однозадачных переходов)
        if not is_parallel_next and next_steps[0]["agent"] == self.agent_key:
            logger.error(f"chain_loop | chain_id={chain_id} | agent={self.agent_key}")
            if chat_id:
                await self._notify_user(chat_id, f"⚠️ Ошибка цепочки: цикл на агенте {self.agent_key}", bot_token=_chain_reply_token)
            return

        # Уведомляем пользователя о прогрессе
        if chat_id:
            me = _AGENT_NAMES.get(self.agent_key, self.name)
            if is_parallel_next:
                them = " + ".join(_AGENT_NAMES.get(s["agent"], s["agent"]) for s in next_steps)
            else:
                them = _AGENT_NAMES.get(next_steps[0]["agent"], next_steps[0]["agent"])
            await self._notify_user(
                chat_id,
                f"✅ {me} завершил [{chain_index+1}/{chain_total}]\n➡️ Передаю {them}…",
                bot_token=_chain_reply_token,
            )

        # Собираем контекст из всех завершённых задач
        prev_results = await get_chain_results(None, chain_id)

        # Ставим задачи для каждого шага следующей группы
        enqueued_ids: list[int] = []
        for step in next_steps:
            next_agent = step["agent"]
            if next_agent == "kevin":
                context_parts = [
                    f"[{r.get('assigned_agent','?')}]: {(r.get('result') or '')[:500]}"
                    for r in prev_results
                ]
                context_str = "\n\n".join(context_parts)
            else:
                context_str = _build_context(prev_results)
            full_payload = (
                f"{step['task']}\n\nКонтекст от предыдущих агентов:\n{context_str}"
                if context_str else step["task"]
            )
            task_id = await enqueue_chain_task(
                pool=None,
                agent_key=next_agent,
                payload=full_payload,
                chat_id=chat_id,
                chain_id=chain_id,
                chain_index=next_index,
                chain_total=chain_total,
                parallel_group=next_index if is_parallel_next else None,
                parent_task_id=completed_task.id,
                from_agent=self.agent_key,
                correlation_id=completed_task.correlation_id,
                priority=getattr(completed_task, "priority", 0),
                timeout_seconds=600 if next_agent == "dan" else 300,
            )
            if task_id:
                enqueued_ids.append(task_id)

        # Устанавливаем Redis-барьер для параллельной следующей группы —
        # по реально поставленным в очередь задачам, не по заявленному числу шагов
        # (enqueue_chain_task может вернуть None при сбое БД — тогда барьер по len(next_steps)
        # никогда не досчитается до нуля декрементами, и цепочка зависнет молча).
        if len(enqueued_ids) == 0 and len(next_steps) > 0:
            logger.error(
                f"chain_advance | chain_id={chain_id[:8]} | group={next_index} | "
                f"ни одна из {len(next_steps)} задач не встала в очередь — цепочка остановлена"
            )
            if chat_id:
                await self._notify_user(
                    chat_id,
                    "⚠️ Не удалось запустить следующий шаг цепочки — очередь недоступна.",
                    bot_token=_chain_reply_token,
                )
            return

        if is_parallel_next:
            if len(enqueued_ids) < len(next_steps):
                logger.warning(
                    f"chain_advance | chain_id={chain_id[:8]} | group={next_index} | "
                    f"в очередь встало {len(enqueued_ids)} из {len(next_steps)} задач — "
                    f"барьер выставлен на фактическое число"
                )
            redis = await self._get_redis()
            if redis:
                await redis.set(
                    f"chain_barrier:{chain_id}:{next_index}",
                    len(enqueued_ids),
                    ex=86_400,
                )

        await log_event(
            "CHAIN_ADVANCED",
            task_id=completed_task.id,
            agent_key=self.agent_key,
            chain_id=chain_id,
            payload={
                "from_group": chain_index,
                "to_group": next_index,
                "next_agents": [s["agent"] for s in next_steps],
                "next_task_ids": enqueued_ids,
                "parallel": is_parallel_next,
            },
        )
        logger.info(
            f"chain_advance | chain_id={chain_id[:8]} | "
            f"from={self.agent_key} | next={[s['agent'] for s in next_steps]} | "
            f"[{next_index}/{chain_total-1}] | parallel={is_parallel_next} | tasks={enqueued_ids}"
        )

    async def _handle_chain_failure(self, failed_task) -> None:
        """Обработать провал задачи в цепочке."""
        from task_queue import get_chain_plan

        chain_id    = failed_task.chain_id
        chain_index = failed_task.chain_index
        chain_total = failed_task.chain_total
        chat_id     = failed_task.chat_id

        plan = await get_chain_plan(None, chain_id)
        if not plan:
            return

        step     = plan["steps"][chain_index]
        required = step.get("required", True)
        me       = _AGENT_NAMES.get(self.agent_key, self.name)
        err_msg  = getattr(failed_task, "error_message", None) or "неизвестно"

        logger.error(
            f"chain_failed | chain_id={chain_id[:8]} | "
            f"agent={self.agent_key} | required={required}"
        )

        if required:
            if chat_id:
                await self._notify_user(
                    chat_id,
                    f"❌ Цепочка остановлена: {me} не смог выполнить задачу.\n"
                    f"Шаг {chain_index+1}/{chain_total}.\nОшибка: {err_msg}",
                )
        else:
            if chat_id:
                await self._notify_user(
                    chat_id,
                    f"⚠️ {me} не смог выполнить шаг {chain_index+1} (необязательный).\n"
                    f"Продолжаю цепочку…",
                )
            failed_task.result = "[шаг пропущен из-за ошибки]"
            await self._advance_chain(failed_task)

    # ------------------------------------------------------------------ #
    #  Запуск                                                             #
    # ------------------------------------------------------------------ #

    async def _auth_guard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Единственный владелец (config.OWNER_CHAT_ID) может писать ботам компании.
        Без этого любой человек, узнавший username бота, тратит Claude API за наш счёт.
        Если OWNER_CHAT_ID не настроен (0) — ограничение не включается (dev-режим).
        """
        if not config.OWNER_CHAT_ID:
            return
        user = update.effective_user
        if user and user.id != config.OWNER_CHAT_ID:
            logger.warning(
                f"[{self.name}] Заблокирован доступ: user_id={user.id} "
                f"username={user.username!r}"
            )
            if update.effective_chat:
                with contextlib.suppress(Exception):
                    await context.bot.send_message(update.effective_chat.id, "⛔ Доступ ограничен.")
            raise ApplicationHandlerStop

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Глобальный обработчик ошибок PTB (update processing уровень)."""
        logger.error(f"[{self.name}] PTB error: {context.error}")
        if context.error:
            logger.error(traceback.format_exc())

    def build_app(self) -> Application:
        async def _post_init(app: Application) -> None:
            from telegram import MenuButtonCommands
            await app.bot.set_my_commands(self._bot_commands())
            await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

        self.app = (
            Application.builder()
            .token(self.bot_token)
            .post_init(_post_init)
            .build()
        )
        self.app.add_error_handler(self._error_handler)
        self.app.add_handler(TypeHandler(Update, self._auth_guard), group=-1)
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("reset", self.cmd_reset))
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        self.app.add_handler(
            MessageHandler(filters.VOICE, self.handle_voice)
        )
        self._register_extra_handlers()

        voice_status = "Groq ✓" if (_GROQ_AVAILABLE and config.GROQ_API_KEY) else "Groq — нет ключа"
        logger.info(
            f"[{self.name}] Handlers зарегистрированы: "
            f"/start, /reset, MessageHandler(TEXT), MessageHandler(VOICE) [{voice_status}]"
        )
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

        # Запускаем worker loop как фоновую задачу asyncio
        self._worker_stop_event = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name=f"worker_{self.name.lower()}",
        )
        logger.info(f"[{self.name}] Worker task запущен")

    async def start_worker_only_async(self) -> None:
        """Запустить только worker loop, без Telegram polling.

        Марта — единственная точка входа: остальные агенты больше не принимают
        сообщения напрямую от пользователя в Telegram, но их worker loop должен
        оставаться живым, чтобы забирать делегированные задачи из очереди Postgres
        и выполнять фоновые/расписанные задания (синки, дайджесты, алерты).
        Telegram Application намеренно не создаётся: build_app()/initialize()/
        start()/start_polling() здесь не вызываются, self.app остаётся None.
        """
        self._worker_stop_event = asyncio.Event()
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name=f"worker_{self.name.lower()}",
        )
        logger.info(f"[{self.name}] Worker task запущен (worker-only, без Telegram polling)")

    async def stop_async(self) -> None:
        """Graceful shutdown: worker + polling + HTTP-сессия + Redis."""
        # Останавливаем worker loop
        self._worker_stop_event.set()
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

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
