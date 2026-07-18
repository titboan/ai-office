from __future__ import annotations

import json as _json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from db import (
    create_user_plan,
    delete_user_plan,
    get_user_plans,
    update_user_plan,
)
from task_queue import create_reminder
from tools.ntfy import send_push
from utils.tg_rich import send_rich_or_fallback as _send_rich
from .base_agent import BaseAgent, with_company_context


_PRIORITY_LABELS = {"low": "🟢 Низкий", "medium": "🟡 Средний", "high": "🔴 Высокий", "urgent": "🚨 Срочно"}
_STATUS_LABELS   = {"active": "📋 Активен", "in_progress": "⚡ В работе", "done": "✅ Выполнен", "archived": "📦 Архив"}

ALEX_SYSTEM = """Ты Алекс, планировщик и личный ассистент ИИ-офиса.

Твои функции:
1. НАПОМИНАНИЯ — принимаешь запросы вида 'напомни через час', 'напомни в 18:00'.
   Парсишь время и создаёшь напоминание. Подтверждаешь: '⏰ Напомню в [время]'
   Если время сформулировано нестандартно (конкретная дата, 'напомни завтра в 9 утра'
   без явного 'через'/'вечером'/'утром' и т.п.) и не было создано автоматически —
   используй инструмент create_reminder, передав remind_at в формате ISO datetime
   (YYYY-MM-DDTHH:MM:SS) или понятным текстом, если не можешь определить точную дату сам.

2. УПРАВЛЕНИЕ ПЛАНАМИ — у тебя есть инструмент manage_plans для работы с базой данных планов.
   Используй его ВСЕГДА когда пользователь хочет: показать планы, добавить план, изменить,
   отметить выполненным, удалить. Не выдумывай данные — читай из базы через инструмент.

   Поля плана:
   - title: краткое название (обязательно)
   - notes: описание, шаги, детали
   - priority: low / medium / high / urgent
   - category: произвольная категория (маркетплейсы, финансы, разработка, маркетинг...)
   - deadline: дата в формате YYYY-MM-DD
   - status: active → in_progress → done / archived

3. ДЕКОМПОЗИЦИЯ — помогаешь разбить большую цель на шаги, создать OKR, roadmap.

Форматируй ответы в Rich Markdown для Telegram:
- **текст** — заголовки разделов
- `текст` — даты, ID, артикулы
- Эмодзи: ⏰ 📅 🎯 ✅ 📋 ⚡ 🔴 🟡 🟢
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- НЕ используй HTML-теги
- Отвечай по-русски, структурированно."""

ALEX_TOOLS = [
    {
        "name": "manage_plans",
        "description": "Управление планами и задачами пользователя в базе данных.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "update", "done", "archive", "delete"],
                    "description": "list — показать планы; add — добавить; update — изменить поля; done — отметить выполненным; archive — архивировать; delete — удалить",
                },
                "plan_id": {
                    "type": "integer",
                    "description": "ID плана (для update/done/archive/delete)",
                },
                "title":    {"type": "string", "description": "Название плана (обязательно для add)"},
                "notes":    {"type": "string", "description": "Описание, шаги, детали"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "urgent"],
                    "description": "Приоритет",
                },
                "category": {"type": "string", "description": "Категория: маркетплейсы, финансы, разработка, маркетинг..."},
                "deadline": {"type": "string", "description": "Дедлайн YYYY-MM-DD"},
                "status_filter": {
                    "type": "string",
                    "enum": ["active", "in_progress", "done", "archived", "all"],
                    "description": "Фильтр для list (по умолчанию active+in_progress)",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "create_reminder",
        "description": (
            "Создать напоминание на конкретную дату/время, когда автоматический "
            "парсинг не смог распознать формулировку (например конкретная дата "
            "'25.07 в 10:00', 'напомни завтра в 9 утра')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "remind_text": {
                    "type": "string",
                    "description": "Текст напоминания, который увидит пользователь",
                },
                "remind_at": {
                    "type": "string",
                    "description": (
                        "Дата и время срабатывания в формате ISO (YYYY-MM-DDTHH:MM:SS), "
                        "по возможности рассчитанные тобой из текущей даты"
                    ),
                },
            },
            "required": ["remind_text", "remind_at"],
        },
    },
]

_DATE_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_RU_RE  = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
# Дата без года (например «25.07») — не хватает данных, чтобы точно её разобрать,
# лучше отдать Claude (инструмент create_reminder), чем молча подставить не тот день
_DATE_PARTIAL_RU_RE = re.compile(r"\b\d{1,2}\.\d{2}\b")


_TIME_RE = re.compile(r'\b(\d{1,2}):(\d{2})\b')


def _parse_remind_at(text: str) -> datetime | None:
    MSK = ZoneInfo("Europe/Moscow")
    now = datetime.now(MSK)
    t = text.lower()
    has_remind_word = "напомни" in t or "напоминание" in t

    if "через час" in t:
        return now + timedelta(hours=1)
    if "через 30 минут" in t or "через полчаса" in t:
        return now + timedelta(minutes=30)
    if "через 15 минут" in t:
        return now + timedelta(minutes=15)
    if "через 5 минут" in t:
        return now + timedelta(minutes=5)
    if "сегодня вечером" in t:
        return now.replace(hour=18, minute=0, second=0, microsecond=0)
    if "завтра утром" in t:
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    # Даты и «завтра в HH:MM» — как и «в HH:MM» ниже, это неоднозначные формулировки,
    # которые встречаются в обычных сообщениях без намерения создать напоминание
    # («отчёт до 25.07.2026», «завтра в 15:00 совещание») — требуем явное намерение.
    if has_remind_word:
        # Конкретная дата: YYYY-MM-DD или DD.MM.YYYY, время рядом (HH:MM) или 09:00 по умолчанию
        m_iso = _DATE_ISO_RE.search(t)
        m_ru = _DATE_RU_RE.search(t)
        if m_iso or m_ru:
            try:
                if m_iso:
                    d = date.fromisoformat(m_iso.group(1))
                else:
                    day, month, year = m_ru.groups()
                    d = date(int(year), int(month), int(day))
            except ValueError:
                d = None
            if d is not None:
                m_time = _TIME_RE.search(t)
                h, mn = (int(m_time.group(1)), int(m_time.group(2))) if m_time else (9, 0)
                return datetime(d.year, d.month, d.day, h, mn, tzinfo=MSK)
        elif _DATE_PARTIAL_RU_RE.search(t):
            # дата без года («25.07») — не гадаем, пусть разбирается Claude через инструмент
            return None

        # «завтра в HH:MM»
        if "завтра" in t:
            m_time = _TIME_RE.search(t)
            if m_time:
                h, mn = int(m_time.group(1)), int(m_time.group(2))
                return (now + timedelta(days=1)).replace(hour=h, minute=mn, second=0, microsecond=0)

        m = re.search(r'\bв\s+(\d{1,2}):(\d{2})\b', t)
        if m:
            h, mn = int(m.group(1)), int(m.group(2))
            result = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            if result <= now:
                result += timedelta(days=1)
            return result
    return None


def _format_plans(plans: list[dict]) -> str:
    if not plans:
        return "📋 Планов нет. Добавь: /plans добавь <название>"

    by_status: dict[str, list] = {}
    for p in plans:
        by_status.setdefault(p["status"], []).append(p)

    lines = ["📋 **Планы и задачи**\n"]
    for status in ("urgent", "active", "in_progress", "done", "archived"):
        items = by_status.get(status, [])
        if not items:
            continue
        label = _STATUS_LABELS.get(status, status)
        lines.append(f"\n**{label}**")
        for p in items:
            pri   = _PRIORITY_LABELS.get(p["priority"], p["priority"])
            dl    = f" · 📅 {p['deadline']}" if p.get("deadline") else ""
            cat   = f" · #{p['category']}" if p.get("category") else ""
            lines.append(f"• `#{p['id']}` **{p['title']}** {pri}{dl}{cat}")
            if p.get("notes"):
                first_line = p["notes"].split("\n")[0][:80]
                lines.append(f"  _{first_line}_")
    return "\n".join(lines)


class AlexAgent(BaseAgent):
    name = "Алекс"
    agent_key = "alex"
    role = "Планировщик"
    emoji = "🗓️"
    system_prompt = ALEX_SYSTEM
    claude_model = config.CLAUDE_HAIKU_MODEL

    def __init__(self) -> None:
        super().__init__(config.ALEX_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  Tool execution                                                      #
    # ------------------------------------------------------------------ #

    async def _run_plans_tool(self, tool_name: str, tool_input: dict, chat_id: int) -> str:
        if tool_name == "create_reminder":
            return await self._run_create_reminder_tool(tool_input, chat_id)

        if tool_name != "manage_plans":
            return "Неизвестный инструмент"

        action = tool_input.get("action")

        if action == "list":
            sf = tool_input.get("status_filter", "active")
            plans = await get_user_plans(chat_id, sf)
            if not plans:
                return "Планов нет."
            return _json.dumps(plans, ensure_ascii=False, default=str)

        if action == "add":
            title = tool_input.get("title", "").strip()
            if not title:
                return "Ошибка: нужен title"
            plan_id = await create_user_plan(
                chat_id=chat_id,
                title=title,
                notes=tool_input.get("notes"),
                priority=tool_input.get("priority", "medium"),
                category=tool_input.get("category"),
                deadline=tool_input.get("deadline"),
            )
            return f"Создан план #{plan_id}: {title}"

        plan_id = tool_input.get("plan_id")
        if not plan_id:
            return "Ошибка: нужен plan_id"

        if action == "done":
            ok = await update_user_plan(plan_id, chat_id, status="done")
        elif action == "archive":
            ok = await update_user_plan(plan_id, chat_id, status="archived")
        elif action == "delete":
            ok = await delete_user_plan(plan_id, chat_id)
        elif action == "update":
            fields = {k: v for k, v in tool_input.items()
                      if k not in ("action", "plan_id", "status_filter") and v is not None}
            ok = await update_user_plan(plan_id, chat_id, **fields) if fields else False
        else:
            return f"Неизвестное действие: {action}"

        return "OK" if ok else f"План #{plan_id} не найден или уже удалён"

    async def _run_create_reminder_tool(self, tool_input: dict, chat_id: int) -> str:
        remind_text = (tool_input.get("remind_text") or "").strip()
        remind_at_raw = (tool_input.get("remind_at") or "").strip()
        if not remind_text or not remind_at_raw:
            return "Ошибка: нужны remind_text и remind_at"
        if not chat_id:
            return "Ошибка: не определён chat_id"

        try:
            remind_at = datetime.fromisoformat(remind_at_raw)
        except ValueError:
            return f"Ошибка: не удалось разобрать дату/время {remind_at_raw!r}, нужен формат YYYY-MM-DDTHH:MM:SS"

        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=ZoneInfo("Europe/Moscow"))

        task_id, _ = await create_reminder(
            chat_id=chat_id,
            text=remind_text,
            remind_at=remind_at,
            from_agent="alex",
        )
        if task_id is None:
            logger.error(f"[{self.name}] create_reminder tool: не удалось сохранить {remind_text!r}")
            return "Ошибка: не удалось сохранить напоминание, попробуй ещё раз"

        time_str = remind_at.strftime("%Y-%m-%d %H:%M")
        logger.info(f"[{self.name}] Напоминание #{task_id} (через инструмент) на {time_str}")
        return f"Напоминание #{task_id} сохранено на {time_str}"

    # ------------------------------------------------------------------ #
    #  Claude tool_use loop                                                #
    # ------------------------------------------------------------------ #

    async def _run_with_tools(self, user_message: str, chat_id: int) -> str:
        today = date.today().isoformat()
        system = with_company_context(f"{ALEX_SYSTEM}\n\nТекущая дата: {today}")
        messages = [{"role": "user", "content": user_message}]

        for _ in range(5):
            response = await self.claude.messages.create(
                model=config.CLAUDE_HAIKU_MODEL,
                max_tokens=2048,
                system=system,
                tools=ALEX_TOOLS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = await self._run_plans_tool(block.name, block.input, chat_id)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        return "⚠️ Не удалось получить ответ."

    # ------------------------------------------------------------------ #
    #  handle_task                                                         #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        logger.info(f"[{self.name}] Задача от {from_agent}: {task!r}")
        chat_id = getattr(self, "_current_chat_id", None) or 0

        # Быстрый dispatch для prefixed задач от Марты
        if task == "__plans__":
            plans = await get_user_plans(chat_id)
            return _format_plans(plans)

        if task.startswith("__plan_add__ "):
            text = task[len("__plan_add__ "):].strip()
            return await self._run_with_tools(f"Добавь план: {text}", chat_id)

        # Напоминание — fast path без Claude
        remind_at = _parse_remind_at(task)
        if remind_at and chat_id:
            task_id, _ = await create_reminder(
                chat_id=chat_id,
                text=task,
                remind_at=remind_at,
                from_agent="alex",
            )
            if task_id is None:
                logger.error(f"[{self.name}] Не удалось сохранить напоминание: {task!r}")
                return "⚠️ Не удалось сохранить напоминание — попробуй ещё раз."
            time_str = remind_at.strftime("%H:%M")
            logger.info(f"[{self.name}] Напоминание #{task_id} на {time_str}")
            return f"⏰ Напомню в {time_str}."

        # Всё остальное — Claude с инструментом планов
        answer = await self._run_with_tools(
            f"Запрос от {from_agent}: {task}", chat_id
        )
        return answer

    # ------------------------------------------------------------------ #
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_plans(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/plans [текст] — показать планы или добавить/изменить через свободный текст."""
        chat_id = update.effective_user.id
        text = " ".join(context.args) if context.args else ""

        if not text:
            plans = await get_user_plans(chat_id)
            await _send_rich(self.bot_token, update.effective_chat.id, _format_plans(plans))
            return

        await update.message.reply_text("🗓️ Обрабатываю…")
        result = await self._run_with_tools(text, chat_id)
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    async def cmd_roadmap(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/roadmap <проект> — построить дорожную карту."""
        project = " ".join(context.args) if context.args else ""
        if not project:
            await update.message.reply_text(
                "Использование: /roadmap <название проекта>\n"
                "Пример: /roadmap мобильное приложение для фитнеса"
            )
            return
        await update.message.reply_text("🗺️ Строю roadmap…")
        result = await self.handle_task(f"Составь roadmap для проекта: {project}", from_agent="команды /roadmap")
        await _send_rich(context.bot.token, update.effective_chat.id, result)

    async def cmd_testpush(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/testpush — проверить отправку push через ntfy.sh."""
        if not config.NTFY_TOPIC:
            await update.message.reply_text("❌ NTFY_TOPIC не задан в Railway Variables.")
            return
        success = await send_push(
            title="Test push",
            message="Тестовое уведомление от Алекса",
            topic=config.NTFY_TOPIC,
            priority="high",
        )
        if success:
            await update.message.reply_text(
                f"✅ Пуш отправлен на топик: <code>{config.NTFY_TOPIC}</code>",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка отправки. Проверь Railway logs → <code>ntfy_response</code>.",
                parse_mode="HTML",
            )

    def _help_text(self) -> str:
        return (
            "🗓️ **Алекс** — планировщик\n\n"
            "Управляю планами в базе данных и отправляю напоминания.\n\n"
            "📌 **Команды:**\n"
            "/plans — показать все активные планы\n"
            "/plans <текст> — добавить, изменить, отметить выполненным\n"
            "/roadmap <проект> — построить дорожную карту\n"
            "/testpush — проверить push-уведомления\n\n"
            "💡 **Примеры:**\n"
            "/plans добавь план: подготовить карточки к акции, дедлайн 30.06\n"
            "/plans отметь #3 выполненным\n"
            "напомни завтра утром про встречу"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start",    "Запуск и помощь"),
            BotCommand("plans",    "Мои планы и задачи"),
            BotCommand("roadmap",  "Построить дорожную карту"),
            BotCommand("testpush", "Проверить push-уведомления"),
            BotCommand("reset",    "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("plans",    self.cmd_plans))
        self.app.add_handler(CommandHandler("roadmap",  self.cmd_roadmap))
        self.app.add_handler(CommandHandler("testpush", self.cmd_testpush))
