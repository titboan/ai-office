from __future__ import annotations

import traceback

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import search_web, save_research
from .base_agent import BaseAgent

# Порог: если ответ длиннее — сохраняем полный текст в Notion,
# пользователю отправляем preview + ссылку
_NOTION_THRESHOLD = 3000
# Размер preview-фрагмента, отправляемого в Telegram
_PREVIEW_CHARS = 900


KASPER_SYSTEM = """Ты — Каспер, исследователь ИИ-офиса.

Получаешь результаты веб-поиска и синтезируешь структурированный отчёт.
Указывай источники (URL). Разделяй факты из поиска и собственный анализ.
Структура: заголовки → факты → выводы → рекомендации.

Отвечай по-русски, аналитически.

Форматируй ответы в HTML для Telegram:
- <b>текст</b> — заголовки разделов (Факты, Анализ, Выводы, Рекомендации)
- <i>текст</i> — пояснения и уточнения
- <code>текст</code> — URL-адреса, числовые данные, технические термины
- <blockquote>текст</blockquote> — ключевые выводы и инсайты
- <blockquote expandable>длинный блок</blockquote> — для больших разделов
- Эмодзи в начале разделов (🔍 📊 💡 ✅)
- НЕ используй Markdown: никаких *звёздочек*, ##заголовков, |таблиц|"""


class KasperAgent(BaseAgent):
    name = "Каспер"
    agent_key = "kasper"
    role = "Исследователь"
    emoji = "🔍"
    system_prompt = KASPER_SYSTEM
    claude_model = config.CLAUDE_OPUS_MODEL

    def __init__(self) -> None:
        super().__init__(config.KASPER_BOT_TOKEN)
        logger.debug(
            f"[Каспер] Init | NOTION_TOKEN={'SET' if config.NOTION_TOKEN else 'НЕ ЗАДАН'} | "
            f"NOTION_RESEARCH_DB={'SET (' + config.NOTION_RESEARCH_DB[:8] + '…)' if config.NOTION_RESEARCH_DB else 'НЕ ЗАДАН'}"
        )

    # ------------------------------------------------------------------ #
    #  Notion helper                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _maybe_save_to_notion(
        title: str,
        answer: str,
        source_label: str = "Tavily веб-поиск",
    ) -> str:
        """Если ответ длиннее порога — сохраняет в Notion и возвращает preview+link.

        Если Notion недоступен или вернул ошибку — всё равно возвращает preview
        (900 символов) + сообщение «Notion недоступен». Полный текст в Telegram
        не отправляется никогда при длинном ответе.

        Никогда не поднимает исключений.
        """
        # ── Всегда логируем на INFO — не DEBUG ────────────────────────────────
        logger.info(
            f"[Каспер] Длина ответа: {len(answer)} символов | "
            f"порог: {_NOTION_THRESHOLD} | "
            f"сохранять в Notion: {len(answer) > _NOTION_THRESHOLD}"
        )

        if len(answer) <= _NOTION_THRESHOLD:
            logger.info("[Каспер] Ответ короткий — отправляем целиком, Notion не нужен")
            return answer

        logger.info(
            f"[Каспер] Ответ длинный ({len(answer)} симв.) — "
            f"вызываем save_research()…"
        )

        # ── Вызов Notion ──────────────────────────────────────────────────────
        notion_url: str | None = None
        try:
            notion_url = await save_research(
                title=title[:100],
                content=answer,
                source=source_label,
                agent="Каспер",
            )
            if notion_url:
                logger.info(f"[Каспер] save_research() вернул URL: {notion_url}")
            else:
                logger.warning(
                    "[Каспер] save_research() вернул None — "
                    "Notion недоступен или ошибка API (см. логи [notion] выше)"
                )
        except Exception as e:
            logger.error(
                f"[Каспер] save_research() выбросил исключение: "
                f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            )

        # ── Всегда формируем preview — независимо от статуса Notion ──────────
        preview = answer[:_PREVIEW_CHARS].rstrip()

        if notion_url:
            # Notion сохранил — даём ссылку
            result = (
                f"{preview}\n\n"
                f"... (текст обрезан — {len(answer)} символов)\n\n"
                f"📄 Полное исследование в Notion:\n{notion_url}"
            )
            logger.info(
                f"[Каспер] Возвращаем preview ({_PREVIEW_CHARS} симв.) + Notion ссылку"
            )
        else:
            # Notion недоступен — preview + объяснение
            result = (
                f"{preview}\n\n"
                f"... (текст обрезан — {len(answer)} символов)\n\n"
                f"⚠️ Notion недоступен — полный текст не сохранён.\n"
                f"Для настройки добавь NOTION_TOKEN и NOTION_RESEARCH_DB в .env"
            )
            logger.warning(
                f"[Каспер] Возвращаем preview ({_PREVIEW_CHARS} симв.) + сообщение об ошибке Notion"
            )

        return result

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

            # ── Шаг 3: Notion (если длинный) ─────────────────────────────────
            logger.info(f"[Каспер] Шаг 3 — проверка Notion (len={len(answer)})")
            answer = await self._maybe_save_to_notion(
                title=user_text,
                answer=answer,
                source_label="Tavily веб-поиск",
            )

            # ── Шаг 4: отправка пользователю ─────────────────────────────────
            logger.info(f"[Каспер] Шаг 4 — reply_text ({len(answer)} симв.)")
            chunks = [answer[i : i + 4000] for i in range(0, len(answer), 4000)]
            for chunk in chunks:
                try:
                    await update.message.reply_text(chunk, parse_mode="HTML")
                except Exception:
                    await update.message.reply_text(chunk)

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

        # ── Шаг 3: Notion ─────────────────────────────────────────────────────
        logger.info(f"[Каспер] handle_task шаг 3 — Notion")
        answer = await self._maybe_save_to_notion(
            title=task,
            answer=answer,
            source_label="Tavily веб-поиск",
        )

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
        for chunk in [result[i : i + 4000] for i in range(0, len(result), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

    def _help_text(self) -> str:
        return (
            "🔍 <b>Каспер</b> — исследователь\n\n"
            "Ищу информацию в интернете, анализирую конкурентов и рынок.\n\n"
            "📌 <b>Команды:</b>\n"
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
