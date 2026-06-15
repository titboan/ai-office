from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import save_content
from utils.tg_rich import send_rich_or_fallback as _send_rich
from .base_agent import BaseAgent


ELINA_SYSTEM = """Ты — Элина, копирайтер ИИ-офиса.

Создаёшь тексты, посты (Telegram/Instagram/LinkedIn), email-рассылки, статьи, сценарии.
Пиши живо, адаптируй тон под платформу, предлагай несколько вариантов заголовков.

Форматируй ответы в Rich Markdown для Telegram:
- **текст** — заголовки и акценты
- *текст* — подзаголовки и пояснения
- > текст — готовые тексты для публикации
- Эмодзи по тематике контента
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- Длина ответа до 30 000 символов
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Отвечай по-русски, творчески."""


# ── Определение типа контента по тексту задачи ────────────────────────────────

_POST_KEYWORDS   = ("пост", "telegram", "тг", "инстаграм", "instagram", "соцсет", "vk", "вконтакте", "linkedin")
_LETTER_KEYWORDS = ("письмо", "email", "рассылк", "newsletter", "e-mail", "почт")
_ARTICLE_KEYWORDS = ("статья", "блог", "blog", "seo", "лендинг", "landing", "сценари", "скрипт")


def _detect_content_type(task: str) -> str:
    """Определить тип контента из описания задачи.

    Returns:
        'Пост' | 'Письмо' | 'Статья' | 'Идея'
    """
    t = task.lower()
    if any(kw in t for kw in _POST_KEYWORDS):
        return "Пост"
    if any(kw in t for kw in _LETTER_KEYWORDS):
        return "Письмо"
    if any(kw in t for kw in _ARTICLE_KEYWORDS):
        return "Статья"
    return "Идея"


class ElinaAgent(BaseAgent):
    name = "Элина"
    agent_key = "elina"
    role = "Копирайтер"
    emoji = "✍️"
    system_prompt = ELINA_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.ELINA_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  Выполнение задачи                                                   #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Создать контент по заданию и сохранить в Notion Content DB."""
        logger.info(f"[{self.name}] Задача от {from_agent}: {task!r}")

        answer = await self.think(
            f"Задача на создание контента от {from_agent}: {task}",
            chat_id=0,
            is_task=True,
        )

        # Определяем тип и сохраняем в Notion
        content_type = _detect_content_type(task)
        notion_url = await save_content(
            title=task[:100],
            text=answer,
            content_type=content_type,
        )

        if notion_url:
            logger.info(f"[{self.name}] Контент сохранён в Notion ({content_type}): {notion_url}")
            await self.post_to_group(
                f"📝 {content_type} готов и сохранён в Notion: {notion_url}"
            )
            # Добавляем ссылку на Notion в конец ответа
            answer = f"{answer}\n\n📄 [Сохранено в Notion ({content_type})]({notion_url})"
        else:
            await self.post_to_group(f"📝 {content_type} готов: {answer[:200]}…")

        return answer

    # ------------------------------------------------------------------ #
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_write(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/write <бриф> — написать текст по брифу."""
        brief = " ".join(context.args) if context.args else ""
        if not brief:
            await update.message.reply_text(
                "Использование: /write <бриф на текст>\n"
                "Пример: /write статья о пользе утренней зарядки, 500 слов"
            )
            return
        await update.message.reply_text("✍️ Пишу текст…")
        result = await self.handle_task(brief, from_agent="команды /write")
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    async def cmd_seo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/seo <product_id> — SEO-карточка на основе ключевых слов WB."""
        product_id = " ".join(context.args).strip() if context.args else ""
        if not product_id:
            await update.message.reply_text(
                "Использование: /seo <product_id>\n"
                "Пример: /seo 12345678\n\n"
                "product_id — артикул WB (nm_id). Данные берутся из /sync_keywords у Макса."
            )
            return

        chat_id = update.effective_user.id
        await update.message.reply_text("🔍 Ищу ключевые слова…")

        from db import get_keywords_top
        keywords = await get_keywords_top(chat_id, marketplace="wb", product_id=product_id, limit=30)

        if not keywords:
            await update.message.reply_text(
                f"❌ Ключевые слова для товара {product_id} не найдены.\n"
                "Сначала запусти /sync_keywords у Макса."
            )
            return

        kw_lines = "\n".join(
            f"- {k['keyword']} (позиция: {k['position'] or '?'}, охват: {k['search_count'] or '?'})"
            for k in keywords[:20]
        )
        product_name = keywords[0].get("product_name") or product_id

        brief = (
            f"Напиши SEO-оптимизированную карточку товара для Wildberries.\n\n"
            f"Товар: {product_name} (артикул {product_id})\n\n"
            f"Ключевые слова из WB-поиска (по убыванию охвата):\n{kw_lines}\n\n"
            f"Требования:\n"
            f"1. Заголовок (до 60 символов) — включи 2-3 ключа с лучшей позицией\n"
            f"2. Описание (300-500 символов) — естественно вписать топ-5 ключей\n"
            f"3. Характеристики (5-7 пунктов) — использовать ключевые слова в формулировках\n"
            f"4. Не перечислять ключи подряд — текст должен читаться естественно"
        )

        await update.message.reply_text("✍️ Пишу SEO-карточку…")
        result = await self.handle_task(brief, from_agent=f"/seo product={product_id}")
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    async def cmd_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/post <тема> — написать пост для Telegram."""
        topic = " ".join(context.args) if context.args else ""
        if not topic:
            await update.message.reply_text(
                "Использование: /post <тема поста>\n"
                "Пример: /post запуск нового продукта — стартап в сфере AI"
            )
            return
        await update.message.reply_text("✍️ Пишу пост…")
        result = await self.handle_task(
            f"Напиши Telegram-пост на тему: {topic}",
            from_agent="команды /post",
        )
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    def _help_text(self) -> str:
        return (
            "✍️ **Элина** — копирайтер\n\n"
            "Пишу тексты для карточек товаров, посты и рекламные тексты.\n\n"
            "📌 **Команды:**\n"
            "/write <бриф> — написать текст по заданию\n"
            "/post <тема> — написать пост для Telegram\n"
            "/seo <product_id> — SEO-карточка по ключевым словам WB\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: /seo 12345678 — карточка на основе реальных поисковых запросов"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("write", "Написать текст по брифу"),
            BotCommand("post", "Написать пост для Telegram"),
            BotCommand("seo", "SEO-карточка по ключевым словам WB"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("write", self.cmd_write))
        self.app.add_handler(CommandHandler("post",  self.cmd_post))
        self.app.add_handler(CommandHandler("seo",   self.cmd_seo))
