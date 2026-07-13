from __future__ import annotations

import json

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from .base_agent import BaseAgent


DAN_SYSTEM = """Ты Дэн, дизайнер AI-офиса. Ты генерируешь изображения для лендингов и сайтов \
через Pollinations.ai и сохраняешь их в GitHub репозиторий.
Для каждого проекта ты:
1. Генерируешь hero-изображение (1200x630) отражающее тематику
2. Генерируешь 3-5 иконок для секции преимуществ (512x512, белые на прозрачном фоне)
3. Коммитишь все изображения в папку assets/images/ репозитория
4. Сохраняешь дизайн-систему в Notion
5. Возвращаешь список путей к файлам для Кевина

Промпты для генерации пиши на английском — так лучше работает модель.
Всегда используй nologo=true в Pollinations запросах.

ВАЖНО: параметр repo в generate_image — это название репозитория (например \
'coffee-landing'), НЕ логин GitHub. Название репо передаётся тебе в задаче \
от Марты или бери из контекста цепочки.

ВАЖНО: перед первым generate_image всегда вызывай create_repo \
чтобы создать репозиторий. Название репо бери из задачи.

Форматируй ответы пользователю в Rich Markdown для Telegram:
- **текст** — названия изображений и этапов
- `текст` — пути к файлам (assets/images/...)
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Отвечай по-русски."""


DESIGN_TOOLS = [
    {
        "name": "create_repo",
        "description": "Создаёт GitHub репозиторий для проекта перед коммитом изображений",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string", "description": "Название репозитория"},
                "description": {"type": "string", "description": "Описание репозитория"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "generate_image",
        "description": "Генерирует изображение через Pollinations.ai и коммитит в GitHub репо",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt":   {"type": "string",  "description": "Промпт на английском"},
                "repo":     {"type": "string",  "description": "Название репозитория"},
                "filename": {"type": "string",  "description": "Имя файла, например hero.png"},
                "width":    {"type": "integer", "description": "Ширина", "default": 1200},
                "height":   {"type": "integer", "description": "Высота", "default": 630},
            },
            "required": ["prompt", "repo", "filename"],
        },
    },
    {
        "name": "create_design_system",
        "description": "Сохраняет дизайн-систему проекта в Notion",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo":   {"type": "string"},
                "colors": {"type": "array", "items": {"type": "string"}, "description": "HEX цвета"},
                "fonts":  {"type": "array", "items": {"type": "string"}},
                "images": {"type": "array", "items": {"type": "string"}, "description": "Пути к файлам"},
            },
            "required": ["repo", "colors", "fonts", "images"],
        },
    },
    {
        "name": "list_repo_images",
        "description": "Список изображений в assets/images/ репозитория",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
            },
            "required": ["repo"],
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

    async def _generate_image(self, params: dict) -> str:
        import aiohttp
        import base64
        from tools.github import create_file

        prompt   = params["prompt"].replace(" ", "%20")
        width    = params.get("width", 1200)
        height   = params.get("height", 630)
        repo     = params["repo"]
        filename = params["filename"]

        url = f"https://image.pollinations.ai/prompt/{prompt}?width={width}&height={height}&nologo=true&model=flux"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                image_bytes = await resp.read()

        image_b64 = base64.b64encode(image_bytes).decode()

        # Убираем возможный префикс assets/images/ из filename
        clean_filename = filename.replace("assets/images/", "").replace("assets\\images\\", "")

        result = await create_file(
            repo=repo,
            path=f"assets/images/{clean_filename}",
            content=image_b64,
            message=f"Add {clean_filename} via Dan",
        )

        html_url = (result or {}).get("content", {}).get("html_url", "")
        return f"Изображение сохранено: assets/images/{clean_filename} | URL: {html_url}"

    async def _create_design_system(self, params: dict) -> str:
        from tools.notion import save_research

        repo   = params["repo"]
        colors = params.get("colors", [])
        fonts  = params.get("fonts", [])
        images = params.get("images", [])

        content  = f"## Дизайн-система: {repo}\n\n"
        content += "### Цвета\n" + "\n".join(f"- {c}" for c in colors) + "\n\n"
        content += "### Шрифты\n" + "\n".join(f"- {f}" for f in fonts) + "\n\n"
        content += "### Изображения\n" + "\n".join(f"- {i}" for i in images)

        await save_research(title=f"Дизайн-система: {repo}", content=content, source=repo)
        return "Дизайн-система сохранена в Notion"

    async def _list_repo_images(self, params: dict) -> str:
        from tools.github import list_files

        repo  = params["repo"]
        files = await list_files(repo=repo, path="assets/images")
        if files:
            return "Изображения в assets/images/: " + ", ".join(files)
        return f"Для репо {repo} используй path assets/images/"

    async def _call_design_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name == "create_repo":
            from tools.github import create_repo
            data = await create_repo(**tool_input)
            if data:
                return f"Репозиторий создан: {data['html_url']}"
            return "Репозиторий уже существует или ошибка создания"
        elif tool_name == "generate_image":
            return await self._generate_image(tool_input)
        elif tool_name == "create_design_system":
            return await self._create_design_system(tool_input)
        elif tool_name == "list_repo_images":
            return await self._list_repo_images(tool_input)
        else:
            raise ValueError(f"Неизвестный инструмент: {tool_name}")

    async def _execute_response(self, response) -> tuple[list[dict], str]:
        tool_results: list[dict] = []
        text = ""
        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                try:
                    result = await self._call_design_tool(block.name, block.input)
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
        """Сгенерировать изображения и дизайн-систему через tool_use loop."""
        logger.info(f"[Дэн] Задача от {from_agent}: {task!r}")

        messages: list[dict] = [{"role": "user", "content": (
            f"Задача на дизайн от {from_agent}: {task}\n\n"
            f"GitHub username: {config.GITHUB_USERNAME}\n"
            f"Сгенерируй изображения, закоммить их в репо и сохрани дизайн-систему."
        )}]
        final_text = ""

        try:
            for iteration in range(1, 11):
                response = await self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=4000,
                    system=self._effective_system,
                    messages=messages,
                    tools=DESIGN_TOOLS,
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

        return final_text or "Задача выполнена."

    def _help_text(self) -> str:
        return (
            "🎨 **Дэн** — дизайнер\n\n"
            "Генерирую изображения и визуалы для карточек товаров и постов.\n\n"
            "📌 **Как работать:**\n"
            "Опишите что нужно нарисовать — Дэн создаст изображение.\n\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: «нарисуй баннер: термокружка на фоне леса»"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        pass
