from __future__ import annotations

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import save_content
from utils.tg_format import strip_mdv2 as _strip_mdv2
from .base_agent import BaseAgent


ELINA_SYSTEM = """Ты — Элина, копирайтер ИИ-офиса.

Создаёшь тексты, посты (Telegram/Instagram/LinkedIn), email-рассылки, статьи, сценарии.
Пиши живо, адаптируй тон под платформу, предлагай несколько вариантов заголовков.

Форматируй ответы в MarkdownV2 для Telegram:
- *текст* — заголовки и акценты
- _текст_ — подзаголовки и пояснения
- > текст — готовые тексты для публикации
- Эмодзи по тематике контента
- Спецсимволы . ! ( ) - = внутри текста экранируй через \
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
        # Разбиваем если длинный ответ
        for chunk in [result[i : i + 4000] for i in range(0, len(result), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode="MarkdownV2")
            except Exception:
                await update.message.reply_text(_strip_mdv2(chunk))

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
        try:
            await update.message.reply_text(result, parse_mode="MarkdownV2")
        except Exception:
            await update.message.reply_text(_strip_mdv2(result))

    def _help_text(self) -> str:
        return (
            "✍️ <b>Элина</b> — копирайтер\n\n"
            "Пишу тексты для карточек товаров, посты и рекламные тексты.\n\n"
            "📌 <b>Команды:</b>\n"
            "/write &lt;бриф&gt; — написать текст по заданию\n"
            "/post &lt;тема&gt; — написать пост для Telegram\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: /write «карточка товара: термокружка 500мл»"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("write", "Написать текст по брифу"),
            BotCommand("post", "Написать пост для Telegram"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("write", self.cmd_write))
        self.app.add_handler(CommandHandler("post", self.cmd_post))
