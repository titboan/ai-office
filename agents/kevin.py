from __future__ import annotations

import json
import re

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import create_repo, create_file, create_branch, create_pull_request, list_repos
from .base_agent import BaseAgent


KEVIN_SYSTEM = """Ты — Кевин, разработчик ИИ-офиса с доступом к GitHub.

Пишешь код (Python, JS, TS, HTML/CSS), создаёшь репо, коммитишь в ветку feature/..., открываешь PR.
Код пиши полностью, не сокращай.

При создании проекта — СТРОГО верни JSON блок:
##GITHUB_ACTION##
{
  "action": "create_project",
  "repo": "название-репо-латиницей",
  "description": "описание проекта",
  "branch": "feature/название",
  "files": [
    {"path": "index.html", "content": "...полный код..."},
    {"path": "style.css", "content": "..."},
    {"path": "README.md", "content": "..."}
  ],
  "pr_title": "Заголовок PR",
  "pr_body": "Описание что сделано, какой стек, как запустить"
}
##END##

Отвечай по-русски. Код пиши полностью, не сокращай."""


def _parse_github_action(text: str) -> dict | None:
    """Извлечь JSON блок действия из ответа Клода."""
    m = re.search(
        r"##GITHUB_ACTION##\s*(\{.*?\})\s*##END##",
        text,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _strip_github_block(text: str) -> str:
    """Убрать блок ##GITHUB_ACTION## из текста."""
    return re.sub(
        r"##GITHUB_ACTION##.*?##END##", "", text, flags=re.DOTALL
    ).strip()


class KevinAgent(BaseAgent):
    name = "Кевин"
    agent_key = "kevin"
    role = "Старший разработчик"
    emoji = "👨‍💻"
    system_prompt = KEVIN_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.KEVIN_BOT_TOKEN)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Написать код и создать PR на GitHub."""
        logger.info(f"[Кевин] Задача от {from_agent}: {task!r}")

        answer = await self.think(
            f"Задача на разработку от {from_agent}: {task}\n\n"
            f"GitHub username: {config.GITHUB_USERNAME}\n"
            f"Создай полноценный проект и верни ##GITHUB_ACTION## блок.",
            chat_id=0,
            is_task=True,
        )

        action = _parse_github_action(answer)
        clean_answer = _strip_github_block(answer)

        if not action:
            logger.warning("[Кевин] GitHub action блок не найден — отвечаю текстом")
            await self.post_to_group(f"💻 Код готов: {clean_answer[:200]}…")
            return clean_answer

        result_lines = [clean_answer] if clean_answer else []
        repo_name = action.get("repo", "kevin-project")
        branch = action.get("branch", "feature/new")

        # 1. Создаём репо
        repo_data = await create_repo(
            name=repo_name,
            description=action.get("description", ""),
            private=False,
        )
        if repo_data:
            result_lines.append(f"📁 Репозиторий создан: {repo_data['html_url']}")
        else:
            result_lines.append(f"📁 Репозиторий `{repo_name}` уже существует")

        # 2. Создаём ветку
        branch_ok = await create_branch(repo_name, branch)
        if branch_ok:
            result_lines.append(f"🌿 Ветка создана: `{branch}`")

        # 3. Коммитим файлы
        files = action.get("files", [])
        committed = 0
        for f in files:
            file_result = await create_file(
                repo=repo_name,
                path=f["path"],
                content=f["content"],
                message=f"feat: add {f['path']}",
                branch=branch,
            )
            if file_result:
                committed += 1
        result_lines.append(f"📝 Файлов закоммичено: {committed}/{len(files)}")

        # 4. Создаём PR
        pr_data = await create_pull_request(
            repo=repo_name,
            title=action.get("pr_title", f"feat: {task[:60]}"),
            body=action.get("pr_body", "Создано агентом Кевин"),
            head=branch,
        )
        if pr_data:
            result_lines.append(f"🔀 Pull Request открыт: {pr_data['html_url']}")

        result = "\n".join(result_lines)
        await self.post_to_group(f"💻 Проект готов: {repo_name}")
        return result

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
