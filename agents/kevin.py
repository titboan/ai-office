from __future__ import annotations

import json
import uuid

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from config import config
from tools import create_repo, create_file, create_branch, create_pull_request, enable_pages
from utils.tg_rich import send_rich_or_fallback as _send_rich
from .base_agent import BaseAgent

# Критичные инструменты (ROADMAP Phase 9 — Approval Gates): "критичные (деплой, PR) —
# только вручную". Кевин пишет и коммитит код автономно, но открытие реального PR
# ждёт подтверждения кнопкой, а не выполняется молча внутри tool-use цикла.
_CRITICAL_TOOLS = {"create_pr"}
_PENDING_TTL = 1800  # 30 минут на подтверждение, потом состояние протухает


KEVIN_SYSTEM = """Ты — Кевин, разработчик ИИ-офиса с доступом к GitHub.

Получив задачу — СРАЗУ вызывай инструменты в порядке 1-5.
Не пересказывай контекст, не объясняй что будешь делать.
Первое действие — create_repo. Второе — create_branch.
Третье — create_file с готовым HTML (пиши компактно, без лишних пробелов).
Не думай долго — действуй.

Пишешь код (Python, JS, TS, HTML/CSS), создаёшь репо, коммитишь в ветку feature/..., открываешь PR.
Для веб-проектов (лендинг, сайт) — деплоишь на GitHub Pages.
Код пиши полностью, не сокращай. HTML файл всегда называй index.html.

ОБЯЗАТЕЛЬНЫЙ ПОРЯДОК ДЕЙСТВИЙ для каждой задачи:
1. create_repo — создать репо (или использовать существующее если 422)
2. create_branch — создать ветку feature/название-задачи
3. create_file — закоммитить все файлы в эту ветку
4. create_pull_request — открыть PR из feature-ветки в main
5. enable_pages — включить GitHub Pages (только для сайтов и лендингов). При вызове enable_pages всегда передавай source_branch — название ветки куда ты закоммитил index.html (та же ветка что использовалась в create_file).

ВАЖНО — порядок вызова инструментов:
Шаг 1: create_repo — создать репозиторий
Шаг 2: create_branch — создать ветку feature/...
Шаг 3: create_file — закоммитить файлы (можно несколько вызовов подряд)
Шаг 4: create_pull_request — открыть Pull Request
Шаг 5: enable_pages — задеплоить на GitHub Pages

Никогда не останавливайся после create_branch.
После создания ветки ВСЕГДА следует create_file с содержимым файлов.
Генерируй файлы компактно — без лишних комментариев и отступов,
чтобы уложиться в лимит токенов.
Все 5 шагов должны быть выполнены за одну сессию.

ВАЖНО: ошибка 422 от create_repo означает что репо уже существует.
Это НЕ ошибка — продолжай работу используя это репо.
Никогда не останавливайся после 422. Выполни шаги 2-5.

ГЕНЕРАЦИЯ ФАЙЛОВ — стратегия:
Не пытайся сгенерировать весь HTML в одном блоке размышлений.
Вызывай create_file сразу как только готов контент файла.

Для лендинга структура такая:
- Один вызов create_file для index.html
- HTML должен быть минимальным но рабочим:
  * Inline CSS в теге <style> — никаких внешних файлов
  * Все секции в одном файле
  * Без комментариев в коде
  * Без лишних отступов — минифицированный стиль
  * Максимум 200 строк
- Контекст от предыдущих агентов использовать только для наполнения
  (тексты, УТП, названия) — не пересказывать, сразу применять в HTML.

Для работы с GitHub используй доступные инструменты.
Можешь вызывать несколько инструментов последовательно в одном ответе.

Форматируй ответы пользователю в Rich Markdown для Telegram:
- **текст** — заголовки этапов
- `текст` — названия репо, ветки, URL
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- Длина ответа до 30 000 символов
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

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
        "description": "Включает GitHub Pages для репозитория. Деплоит из ветки с кодом на gh-pages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo":          {"type": "string", "description": "Название репозитория"},
                "source_branch": {"type": "string", "description": "Ветка с index.html (например feature/coffee-landing-page)"},
            },
            "required": ["repo", "source_branch"],
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

    async def _execute_response(self, content_blocks: list) -> tuple[list[dict], str]:
        """Выполнить tool_use блоки. content_blocks — response.content живого цикла
        (SDK-объекты) ИЛИ сериализованные dict-блоки, восстановленные из Redis после
        подтверждения кнопкой (см. _handle_gate_callback) — поддерживаем оба варианта."""
        tool_results: list[dict] = []
        text = ""
        for block in content_blocks:
            b_type = block.type if hasattr(block, "type") else block.get("type")
            if b_type == "text":
                text = block.text if hasattr(block, "text") else block.get("text", "")
            elif b_type == "tool_use":
                name  = block.name  if hasattr(block, "name")  else block.get("name")
                input_ = block.input if hasattr(block, "input") else block.get("input", {})
                tool_use_id = block.id if hasattr(block, "id") else block.get("id")
                try:
                    result = await self._call_github_tool(name, input_)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result,
                    })
                except Exception as e:
                    logger.error(f"kevin_tool_error | tool={name} | error={e}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"Ошибка: {e}",
                        "is_error": True,
                    })
        return tool_results, text

    @staticmethod
    def _serialize_blocks(content_blocks: list) -> list[dict]:
        """SDK-блоки ответа Claude → plain dict, чтобы messages можно было сохранить в Redis (JSON)."""
        return [b.model_dump() if hasattr(b, "model_dump") else b for b in content_blocks]

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Написать код и выполнить GitHub операции через agentic tool_use loop."""
        logger.info(f"[Кевин] Задача от {from_agent}: {task!r}")

        messages: list[dict] = [{"role": "user", "content": (
            f"Задача на разработку от {from_agent}: {task}\n\n"
            f"GitHub username: {config.GITHUB_USERNAME}\n"
            f"Создай полноценный проект: репо, ветку feature/..., закоммить файлы, открой PR."
        )}]
        return await self._run_tool_loop(messages, iteration_start=1)

    async def _run_tool_loop(self, messages: list[dict], iteration_start: int) -> str:
        """Тело agentic tool_use цикла. Вынесено из handle_task, чтобы можно было
        возобновить цикл после подтверждения критичного действия кнопкой
        (см. _handle_gate_callback) — тем же кодом, с той же точки."""
        final_text = ""

        try:
            for iteration in range(iteration_start, 11):
                response = await self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=16000,
                    system=KEVIN_SYSTEM,
                    messages=messages,
                    tools=GITHUB_TOOLS,
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

                    critical_block = next(
                        (b for b in response.content if b.type == "tool_use" and b.name in _CRITICAL_TOOLS),
                        None,
                    )
                    if critical_block is not None:
                        return await self._request_gate_confirmation(messages, response, iteration, critical_block)

                    tool_results, text = await self._execute_response(response.content)
                    if text:
                        final_text = text

                    messages.append({"role": "assistant", "content": self._serialize_blocks(response.content)})
                    messages.append({"role": "user", "content": tool_results})
                else:
                    logger.warning(f"[Кевин] stop_reason={response.stop_reason!r} — выход из цикла")
                    break

        except Exception as e:
            logger.error(f"[Кевин] Claude API error: {e}")
            return f"⚠️ Ошибка вызова Claude: {e}"

        await self.post_to_group(f"💻 Кевин выполнил задачу: {final_text[:80] if final_text else ''}")
        return final_text or "Задача выполнена."

    async def _request_gate_confirmation(self, messages, response, iteration: int, critical_block) -> str:
        """Критичный инструмент (сейчас: create_pr) запрошен Клодом — не выполняем молча,
        сохраняем состояние цикла в Redis и ждём подтверждения кнопкой в Telegram."""
        chat_id = getattr(self, "_current_chat_id", None)
        key = uuid.uuid4().hex[:12]
        state = {
            "messages": messages,
            "response_content": self._serialize_blocks(response.content),
            "iteration": iteration,
        }
        await self._redis_set(f"kevin_pending:{key}", json.dumps(state, ensure_ascii=False), ttl=_PENDING_TTL)

        repo  = critical_block.input.get("repo", "")
        title = critical_block.input.get("title", "")
        logger.info(f"[Кевин] critical_tool={critical_block.name} repo={repo!r} title={title!r} → ждём подтверждения")

        if chat_id:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Создать PR", callback_data=f"kevin_pr:confirm:{key}"),
                InlineKeyboardButton("❌ Отменить",   callback_data=f"kevin_pr:cancel:{key}"),
            ]])
            try:
                await self.app.bot.send_message(
                    chat_id,
                    f"🔀 Кевин хочет открыть Pull Request\n<b>{repo}</b> — {title}\nПодтвердить?",
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.error(f"[Кевин] не удалось отправить запрос подтверждения: {e}")

        return f"⏳ Жду подтверждения создания PR: {title}"

    async def _handle_gate_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        _, action, key = query.data.split(":", 2)
        redis_key = f"kevin_pending:{key}"

        raw = await self._redis_get(redis_key)
        if not raw:
            await query.edit_message_text("⏳ Запрос устарел или уже обработан.")
            return
        await self._redis_set(redis_key, "", ttl=1)  # снимаем сразу — не обработать дважды

        if action == "cancel":
            await query.edit_message_text("❌ Создание PR отменено.")
            return

        await query.edit_message_text("⏳ Создаю PR…")
        state = json.loads(raw)
        messages = state["messages"]
        content_blocks = state["response_content"]

        tool_results, text = await self._execute_response(content_blocks)
        messages.append({"role": "assistant", "content": content_blocks})
        messages.append({"role": "user", "content": tool_results})

        chat_id = query.message.chat_id
        self._current_chat_id = chat_id
        final_text = await self._run_tool_loop(messages, iteration_start=state["iteration"] + 1)

        try:
            await self.app.bot.send_message(chat_id, final_text or "Готово.")
        except Exception as e:
            logger.error(f"[Кевин] не удалось отправить результат после подтверждения PR: {e}")

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
        self._current_chat_id = update.effective_chat.id
        result = await self.handle_task(task, from_agent="команды /code")
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    def _help_text(self) -> str:
        return (
            "👨‍💻 **Кевин** — разработчик\n\n"
            "Пишу код, создаю PR на GitHub, разбираю баги.\n\n"
            "📌 **Команды:**\n"
            "/code — написать код и создать PR\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: /code «добавь логирование в max.py»"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("code", "Написать код и создать PR"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("code", self.cmd_code))
        self.app.add_handler(CallbackQueryHandler(self._handle_gate_callback, pattern=r"^kevin_pr:"))
