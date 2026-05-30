from __future__ import annotations

import json

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import create_repo, create_file, create_branch, create_pull_request, enable_pages
from .base_agent import BaseAgent


KEVIN_SYSTEM = """Ты — Кевин, разработчик ИИ-офиса с доступом к GitHub.

Пишешь код (Python, JS, TS, HTML/CSS), создаёшь репо, коммитишь в ветку feature/..., открываешь PR.
Для веб-проектов (лендинг, сайт) — деплоишь на GitHub Pages.
Код пиши полностью, не сокращай. HTML файл всегда называй index.html.

ОБЯЗАТЕЛЬНЫЙ ПОРЯДОК ДЕЙСТВИЙ для каждой задачи:
1. create_repo — создать репо (или использовать существующее если 422)
2. create_branch — создать ветку feature/название-задачи
3. create_file — закоммитить все файлы в эту ветку
4. create_pull_request — открыть PR из feature-ветки в main
5. enable_pages — включить GitHub Pages (только для сайтов и лендингов)

ВАЖНО: ошибка 422 от create_repo означает что репо уже существует.
Это НЕ ошибка — продолжай работу используя это репо.
Никогда не останавливайся после 422. Выполни шаги 2-5.

Для работы с GitHub используй доступные инструменты.
Можешь вызывать несколько инструментов последовательно в одном ответе.

Отвечай по-русски."""


GITHUB_TOOLS = [
    {
        "name": "create_repo",
        "description": "Создать новый GitHub репозиторий",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":        {"type": "string",  "description": "Название репозитория (латиница, дефисы)"},
                "description": {"type": "string",  "description": "Описание"},
                "private":     {"type": "boolean", "description": "Приватный?"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_file",
        "description": "Создать или обновить файл в репозитории",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo":    {"type": "string", "description": "Название репозитория"},
                "path":    {"type": "string", "description": "Путь к файлу (напр. index.html)"},
                "content": {"type": "string", "description": "Полное содержимое файла"},
                "message": {"type": "string", "description": "Commit message"},
                "branch":  {"type": "string", "description": "Ветка (по умолчанию main)", "default": "main"},
            },
            "required": ["repo", "path", "content", "message"],
        },
    },
    {
        "name": "create_branch",
        "description": "Создать новую ветку в репозитории",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo":        {"type": "string", "description": "Название репозитория"},
                "branch":      {"type": "string", "description": "Имя новой ветки"},
                "from_branch": {"type": "string", "description": "Базовая ветка", "default": "main"},
            },
            "required": ["repo", "branch"],
        },
    },
    {
        "name": "create_pr",
        "description": "Открыть Pull Request",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo":  {"type": "string", "description": "Название репозитория"},
                "title": {"type": "string", "description": "Заголовок PR"},
                "body":  {"type": "string", "description": "Описание что сделано"},
                "head":  {"type": "string", "description": "Ветка-источник"},
                "base":  {"type": "string", "description": "Целевая ветка", "default": "main"},
            },
            "required": ["repo", "title", "head"],
        },
    },
    {
        "name": "enable_pages",
        "description": "Включить GitHub Pages для репозитория (деплой из ветки gh-pages)",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Название репозитория"},
            },
            "required": ["repo"],
        },
    },
]


class KevinAgent(BaseAgent):
    name = "Кевин"
    agent_key = "kevin"
    role = "Старший разработчик"
    emoji = "👨‍💻"
    system_prompt = KEVIN_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.KEVIN_BOT_TOKEN)

    async def _call_github_tool(self, tool_name: str, params: dict) -> str:
        """Вызвать нужную функцию GitHub API по имени инструмента."""
        if tool_name == "create_repo":
            data = await create_repo(**params)
            if data:
                return f"Репозиторий создан: {data['html_url']}"
            return "Репозиторий уже существует или ошибка создания"

        elif tool_name == "create_file":
            data = await create_file(**params)
            if data:
                return f"Файл {params.get('path')!r} сохранён"
            return f"Ошибка создания файла {params.get('path')!r}"

        elif tool_name == "create_branch":
            ok = await create_branch(**params)
            return f"Ветка {params.get('branch')!r} создана" if ok else f"Ошибка создания ветки"

        elif tool_name == "create_pr":
            data = await create_pull_request(**params)
            if data:
                return f"PR открыт: {data['html_url']}"
            return "Ошибка создания PR"

        elif tool_name == "enable_pages":
            url = await enable_pages(**params)
            if url:
                return f"GitHub Pages включён: {url}"
            return "Pages включён (ссылка появится через 1-2 минуты)"

        else:
            raise ValueError(f"Неизвестный инструмент: {tool_name}")

    async def _execute_response(self, response) -> str:
        """Обработать ответ Claude — выполнить tool_use вызовы и собрать результат."""
        text_response = ""
        results: list[str] = []

        for block in response.content:
            if block.type == "text":
                text_response = block.text

            elif block.type == "tool_use":
                tool_name  = block.name
                tool_input = block.input
                logger.info(
                    f"kevin_tool | tool={tool_name} | "
                    f"input={json.dumps(tool_input, ensure_ascii=False)[:200]}"
                )
                try:
                    result = await self._call_github_tool(tool_name, tool_input)
                    results.append(f"✅ {tool_name}: {result}")
                except Exception as e:
                    results.append(f"❌ {tool_name}: {e}")
                    logger.error(f"kevin_tool_error | tool={tool_name} | error={e}")

        if results:
            sep = "\n\n" if text_response else ""
            return text_response + sep + "\n".join(results)
        return text_response

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Написать код и выполнить GitHub операции через tool_use."""
        logger.info(f"[Кевин] Задача от {from_agent}: {task!r}")

        prompt = (
            f"Задача на разработку от {from_agent}: {task}\n\n"
            f"GitHub username: {config.GITHUB_USERNAME}\n"
            f"Создай полноценный проект: репо, ветку feature/..., закоммить файлы, открой PR."
        )

        try:
            response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=4096,
                system=KEVIN_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=GITHUB_TOOLS,
            )
        except Exception as e:
            logger.error(f"[Кевин] Claude API error: {e}")
            return f"⚠️ Ошибка вызова Claude: {e}"

        result = await self._execute_response(response)
        await self.post_to_group(f"💻 Кевин выполнил задачу: {task[:80]}")
        return result or "Задача выполнена."

    async def cmd_code(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/code <задача> — написать код и создать PR."""
        task = " ".join(context.args) if context.args else ""
        if not task:
            await update.message.reply_text(
                "Использование: /code <задача>\n"
                "Пример: /code сделай лендинг для кофейни"
            )
            return
        await update.message.reply_text("👨‍💻 Пишу код и создаю PR…")
        result = await self.handle_task(task, from_agent="команды /code")
        if len(result) <= 4096:
            await update.message.reply_text(result)
        else:
            for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
                await update.message.reply_text(chunk)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("code", self.cmd_code))
