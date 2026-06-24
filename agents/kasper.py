from __future__ import annotations

import traceback

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import search_web
from utils.tg_rich import send_rich_or_fallback as _send_rich
from .base_agent import BaseAgent


KASPER_SYSTEM = """Ты — Каспер, исследователь ИИ-офиса.

Получаешь результаты веб-поиска и синтезируешь структурированный отчёт.
Указывай источники (URL). Разделяй факты из поиска и собственный анализ.
Структура: заголовки → факты → выводы → рекомендации.

Отвечай по-русски, аналитически.

Форматируй ответы в Rich Markdown для Telegram:
- **текст** — заголовки разделов (Факты, Анализ, Выводы, Рекомендации)
- *текст* — пояснения и уточнения
- `текст` — URL-адреса, числовые данные, технические термины
- > текст — ключевые выводы и инсайты
- Таблица (до 20 колонок): первая строка начинается с `|`, перед ней — пустая строка.
  НЕ ставь текст перед `|` в той же строке — используй отдельную строку-заголовок выше.
- Эмодзи в начале разделов (🔍 📊 💡 ✅)
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- Длина ответа до 30 000 символов — используй подробные структуры
- НЕ используй HTML-теги: никаких <b>, <i>, <code>"""


class KasperAgent(BaseAgent):
    name = "Каспер"
    agent_key = "kasper"
    role = "Исследователь"
    emoji = "🔍"
    system_prompt = KASPER_SYSTEM
    claude_model = config.CLAUDE_OPUS_MODEL

    def __init__(self) -> None:
        super().__init__(config.KASPER_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  Поиск + ответ (прямые сообщения пользователя)                       #
    # ------------------------------------------------------------------ #

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, override_text: str | None = None) -> None:
        """Переопределяем: сначала ищем в интернете, потом думаем."""
        if not update.message or (not update.message.text and not override_text):
            return

        chat_id   = update.effective_chat.id
        user_text = override_text or update.message.text
        user_name = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )
        logger.info(f"[Каспер] Сообщение от @{user_name} (chat={chat_id}): {user_text!r}")

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            # ── Шаг 1: веб-поиск ──────────────────────────────────────────────
            logger.info(f"[Каспер] Шаг 1 — веб-поиск: {user_text!r}")
            search_results = await search_web(user_text)
            logger.debug(f"[Каспер] Результаты поиска: {len(search_results)} симв.")

            # ── Шаг 2: Claude ─────────────────────────────────────────────────
            enriched_message = (
                f"{user_text}\n\n"
                f"[Результаты веб-поиска]:\n{search_results}\n\n"
                f"Проанализируй найденное и дай развёрнутый ответ."
            )
            logger.info(f"[Каспер] Шаг 2 — Claude (enriched_len={len(enriched_message)})")
            answer = await self.think(enriched_message, chat_id)
            logger.info(f"[Каспер] Claude вернул {len(answer)} символов")

            # ── Шаг 3: отправка пользователю ─────────────────────────────────
            logger.info(f"[Каспер] Шаг 3 — send_rich ({len(answer)} симв.)")
            await _send_rich(self.bot_token, chat_id, answer)

            logger.info(f"[Каспер] Ответ отправлен")
            await self.post_to_group(answer[:500] + ("…" if len(answer) > 500 else ""))

        except Exception as e:
            logger.error(f"[Каспер] НЕОБРАБОТАННАЯ ОШИБКА: {type(e).__name__}: {e}\n{traceback.format_exc()}")
            try:
                await update.message.reply_text(
                    f"⚠️ Произошла ошибка: {type(e).__name__}: {e}\n\nПопробуй ещё раз."
                )
            except Exception as e2:
                logger.error(f"[Каспер] Не удалось отправить сообщение об ошибке: {e2}")

    # ------------------------------------------------------------------ #
    #  Делегированная задача от Марты                                       #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Поиск + ответ без Telegram update."""
        logger.info(f"[Каспер] handle_task от {from_agent}: {task!r}")

        # ── Шаг 1: поиск ──────────────────────────────────────────────────────
        logger.info(f"[Каспер] handle_task шаг 1 — веб-поиск")
        search_results = await search_web(task)
        logger.debug(f"[Каспер] handle_task поиск: {len(search_results)} симв.")

        # ── Шаг 2: Claude ─────────────────────────────────────────────────────
        enriched = (
            f"Исследовательская задача от {from_agent}: {task}\n\n"
            f"[Результаты веб-поиска]:\n{search_results}\n\n"
            f"Проанализируй и дай структурированный отчёт."
        )
        logger.info(f"[Каспер] handle_task шаг 2 — Claude")
        answer = await self.think(enriched, chat_id=0, is_task=True)
        logger.info(f"[Каспер] handle_task Claude вернул {len(answer)} символов")

        await self.post_to_group(f"📚 Исследование завершено: {answer[:200]}…")
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
        await update.message.reply_text(f"🔍 Ищу информацию по теме: {topic}…")
        result = await self.handle_task(topic, from_agent="команды /research")
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    def _help_text(self) -> str:
        return (
            "🔍 **Каспер** — исследователь\n\n"
            "Ищу информацию в интернете, анализирую конкурентов и рынок.\n\n"
            "📌 **Команды:**\n"
            "/research — глубокое исследование по теме\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: /research «тренды WB в категории одежда 2026»"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("research", "Глубокое исследование по теме"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("research", self.cmd_research))
