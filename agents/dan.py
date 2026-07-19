from __future__ import annotations

import base64
import json

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


DAN_SYSTEM = """Ты Дэн, дизайнер маркетплейс-инфографики для карточек товаров Wildberries (WB) \
и Ozon.

Твоя задача — собрать воронку слайдов для карточки товара: обложка + 6-9 описательных слайдов, \
которые публикуются в галерее карточки на маркетплейсе. Типовые роли слайдов (не жёсткий \
список, адаптируй набор и порядок под категорию товара):
- обложка/хук — первый слайд, привлекает внимание
- ключевые преимущества (УТП)
- состав/материал/технология
- размер/комплектация
- сценарий использования
- сравнение с аналогами
- соц. доказательство (отзывы/рейтинг)
- гарантии/сервис

ВАЖНО — юридическое ограничение WB: на самой картинке НЕЛЬЗЯ размещать цены, слова «скидка», \
«хит продаж», «акция» и подобные маркетинговые плашки — штраф до 25 000 ₽. На слайдах — только \
описательная информация: характеристики, состав, применение, комплектация.

Промпты для generate_slide пиши на английском — так лучше работает модель генерации изображений.

Для каждой задачи реши сам набор ролей слайдов (обложка + 6-9 описательных) под категорию \
товара и вызови generate_slide для каждого слайда по очереди.

Форматируй ответы пользователю в Rich Markdown для Telegram:
- **текст** — названия слайдов и этапов
- `текст` — роли слайдов (cover, benefits, composition...)
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Отвечай по-русски."""


FUNNEL_TOOLS = [
    {
        "name": "generate_slide",
        "description": "Генерирует один слайд воронки (обложка или один из описательных слайдов) через AI и сохраняет его во временный набор текущей задачи",
        "input_schema": {
            "type": "object",
            "properties": {
                "role":   {"type": "string", "description": "Роль слайда, например: cover, benefits, composition, size, usage, comparison, social_proof, guarantee"},
                "prompt": {"type": "string", "description": "Промпт на английском для генерации изображения"},
                "size":   {"type": "string", "description": "Размер изображения", "default": "1024x1024"},
            },
            "required": ["role", "prompt"],
        },
    },
]


class DanAgent(BaseAgent):
    name = "Дэн"
    agent_key = "dan"
    role = "Дизайнер"
    emoji = "🎨"
    system_prompt = DAN_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.DEN_BOT_TOKEN)
        # Временный набор слайдов текущей задачи — заполняется generate_slide,
        # сбрасывается в начале каждого handle_task()
        self._current_slides: list[dict] = []

    async def _generate_slide(self, params: dict) -> str:
        from tools.image_gen import generate_image

        role   = params["role"]
        prompt = params["prompt"]
        size   = params.get("size", "1024x1024")

        image_bytes = await generate_image(prompt, size)
        self._current_slides.append({
            "role": role,
            "prompt": prompt,
            "image_b64": base64.b64encode(image_bytes).decode(),
        })
        return f"Слайд '{role}' сгенерирован"

    async def _call_funnel_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "generate_slide":
            return await self._generate_slide(tool_input)
        raise ValueError(f"Неизвестный инструмент: {tool_name}")

    async def _execute_response(self, response) -> tuple[list[dict], str]:
        tool_results: list[dict] = []
        text = ""
        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                try:
                    result = await self._call_funnel_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                except Exception as e:
                    logger.error(f"dan_tool_error | tool={block.name} | error={e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Ошибка: {e}",
                        "is_error": True,
                    })
        return tool_results, text

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Собрать воронку слайдов инфографики через tool_use loop.

        Два входа:
        1. JSON {"action": "build_funnel", "article", "name", "category", "marketplace", "chat_id"}
           — машинный вызов (Питер/Макс).
        2. Обычная фраза человека в Telegram — распознаётся через Claude tool_use loop.
        """
        logger.info(f"[Дэн] Задача от {from_agent}: {task!r}")

        self._current_slides = []

        article: str | None = None
        name: str | None = None
        category: str = ""
        marketplace: str = "wb"
        json_chat_id: int | None = None
        user_message: str | None = None

        try:
            cmd = json.loads(task)
        except (ValueError, TypeError):
            cmd = None

        if isinstance(cmd, dict) and cmd.get("action") == "build_funnel":
            article     = cmd.get("article")
            name        = cmd.get("name") or article
            category    = cmd.get("category", "") or ""
            marketplace = cmd.get("marketplace", "wb") or "wb"
            json_chat_id = cmd.get("chat_id")
            user_message = (
                f"Собери воронку инфографики для товара маркетплейса {marketplace.upper()}.\n"
                f"Артикул: {article}\n"
                f"Название: {name}\n"
                f"Категория: {category or 'не указана'}\n\n"
                "Реши сам набор ролей слайдов (обложка + 6-9 описательных) под эту категорию "
                "и вызови generate_slide для каждого слайда по очереди."
            )

        if user_message is None:
            name = task[:80]
            user_message = (
                f"Задача на дизайн воронки от {from_agent}: {task}\n\n"
                "Определи товар, категорию и маркетплейс из задачи, реши набор ролей слайдов "
                "(обложка + 6-9 описательных) под эту категорию и вызови generate_slide для "
                "каждого слайда по очереди."
            )

        messages: list[dict] = [{"role": "user", "content": user_message}]
        final_text = ""

        try:
            for iteration in range(1, 17):
                response = await self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=4000,
                    system=self._effective_system,
                    messages=messages,
                    tools=FUNNEL_TOOLS,
                )

                if response.stop_reason == "end_turn":
                    for block in response.content:
                        if block.type == "text":
                            final_text = block.text
                    break

                if response.stop_reason == "tool_use":
                    for block in response.content:
                        if block.type == "tool_use":
                            logger.info(
                                f"tool_loop | iteration={iteration} | tool={block.name} | "
                                f"input={json.dumps(block.input, ensure_ascii=False)[:200]}"
                            )

                    tool_results, text = await self._execute_response(response)
                    if text:
                        final_text = text

                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})
                else:
                    logger.warning(f"[Дэн] stop_reason={response.stop_reason!r} — выход из цикла")
                    break

        except Exception as e:
            logger.error(f"[Дэн] Claude API error: {e}")
            return f"⚠️ Ошибка вызова Claude: {e}"

        if not self._current_slides:
            return "⚠️ Не удалось собрать воронку: ни один слайд не сгенерировался (см. ошибки выше)."

        chat_id = getattr(self, "_current_chat_id", None) or json_chat_id or 0
        await self._redis_set(
            f"funnel_ready:{chat_id}",
            json.dumps({
                "article": article,
                "name": name,
                "marketplace": marketplace,
                "category": category,
                "slides": self._current_slides,
            }, ensure_ascii=False),
            ttl=config.FUNNEL_READY_TTL_SECONDS,
        )

        return f"🎨 Собрал воронку: {len(self._current_slides)} слайдов для «{name}». Подтверждение — в чате Марты."

    def _help_text(self) -> str:
        return (
            "🎨 **Дэн** — дизайнер инфографики WB/Ozon\n\n"
            "Собираю воронку слайдов для карточки товара: обложка + 6-9 описательных слайдов "
            "(УТП, состав, размер/комплектация, сценарий использования, сравнение, отзывы, "
            "гарантии) — без цен и слов «скидка»/«акция» на картинке.\n\n"
            "📌 **Как работать:**\n"
            "Напиши «сделай воронку для <товар>, категория <категория>» — соберу набор слайдов. "
            "Подтверждение и публикация набора — в чате Марты.\n\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: «сделай воронку для термокружки, категория посуда для собак»"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        pass
