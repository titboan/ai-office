from __future__ import annotations

import json
import re
import traceback
import uuid

import anthropic
from loguru import logger
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from config import config
from task_queue import create_task as enqueue_task, get_active_tasks, enqueue_chain_task
from tools import create_project
from .base_agent import BaseAgent, _AGENT_NAMES

def _detect_priority(text: str) -> int:
    """Определить приоритет задачи из текста запроса.

    Returns:
        20 — срочно/urgent/asap/немедленно
        10 — важно/важная/high priority
         0 — всё остальное
    """
    t = text.lower()
    if any(w in t for w in ("срочно", "срочная", "срочный", "urgent", "asap", "немедленно")):
        return 20
    if any(w in t for w in ("важно", "важная", "важный", "high priority", "приоритет")):
        return 10
    return 0


# ── Парсинг блока делегирования из ответа Клода ──────────────────────────────
# Клод должен вставить такой блок когда хочет делегировать задачу:
#
#   ##DELEGATE##
#   agent: kasper
#   task: исследуй последние новости о Python 3.13
#   ##END##
#
_DELEGATE_RE = re.compile(
    r"##DELEGATE##\s*agent:\s*(\w+)\s*task:\s*(.+?)\s*##END##",
    re.DOTALL | re.IGNORECASE,
)

# ── Детектирование запроса на создание проекта ────────────────────────────────
# Срабатывает когда пользователь явно просит создать/запустить/открыть проект
_PROJECT_TRIGGER_RE = re.compile(
    r"(создай|создать|новый|запусти|запустить|открой|открыть|начни|начать|стартуй|стартовать)"
    r"\s+проект",
    re.IGNORECASE,
)


def _extract_project_name(text: str) -> str:
    """Извлечь название проекта из текста запроса.

    Ищет паттерны: «проект X», "проект: X", "проект «X»".
    Если не найдено — берёт первые 80 символов запроса.
    """
    m = re.search(
        r'проект[:\s«"\']+([^»"\'\n]{3,100})',
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip().rstrip(".!?,;")
    # Fallback: весь запрос, обрезанный
    return text.strip()[:80].rstrip(".!?,;")


MARTA_SYSTEM = """Ты — Марта, координатор ИИ-офиса.

Команда:
• kasper — исследователь (поиск, анализ)
• kevin  — разработчик (код, GitHub, PR)
• peter  — аналитик (данные, SWOT)
• elina  — копирайтер (тексты, посты)
• alex   — планировщик (roadmap, напоминания, Notion Tasks)

Правила:
1. Специализированные задачи — делегируй нужному агенту.
2. Приветствия и общие вопросы — отвечай сама.
3. Напоминания и дедлайны — всегда alex. Не отказывай ссылаясь на отсутствие календаря.
4. При делегировании ОБЯЗАТЕЛЬНО добавь блок:

##DELEGATE##
agent: kasper/kevin/peter/elina/alex
task: конкретная задача для агента
##END##

Общайся по-русски."""


class MartaAgent(BaseAgent):
    name = "Марта"
    agent_key = "marta"
    role = "Координатор офиса"
    emoji = "👩‍💼"
    system_prompt = MARTA_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.MARTA_BOT_TOKEN)
        # Пул агентов-исполнителей — создаётся лениво при первом делегировании
        self._agent_pool: dict[str, BaseAgent] = {}

    # ------------------------------------------------------------------ #
    #  Реестр агентов-исполнителей                                         #
    # ------------------------------------------------------------------ #

    def _get_agent(self, key: str) -> BaseAgent | None:
        """Вернуть агента по ключу. Создаёт экземпляр при первом обращении."""
        if key not in self._agent_pool:
            # Импорт здесь, чтобы избежать циклических зависимостей на уровне модуля
            from .kevin  import KevinAgent
            from .kasper import KasperAgent
            from .peter  import PeterAgent
            from .elina  import ElinaAgent
            from .alex   import AlexAgent

            registry: dict[str, type[BaseAgent]] = {
                "kevin":  KevinAgent,
                "kasper": KasperAgent,
                "peter":  PeterAgent,
                "elina":  ElinaAgent,
                "alex":   AlexAgent,
            }
            agent_cls = registry.get(key)
            if agent_cls is None:
                logger.warning(f"[Марта] Неизвестный агент для делегирования: {key!r}")
                return None
            self._agent_pool[key] = agent_cls()
            logger.info(f"[Марта] Агент {key!r} создан в пуле делегирования")

        return self._agent_pool[key]

    # ------------------------------------------------------------------ #
    #  Парсинг ответа Клода                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_delegation(text: str) -> tuple[str, str] | None:
        """Извлечь (agent_key, subtask) из ответа Клода. None если блока нет."""
        m = _DELEGATE_RE.search(text)
        if not m:
            return None
        return m.group(1).strip().lower(), m.group(2).strip()

    @staticmethod
    def _strip_delegate_block(text: str) -> str:
        """Убрать блок ##DELEGATE## из текста для отправки пользователю."""
        return _DELEGATE_RE.sub("", text).strip()

    # ------------------------------------------------------------------ #
    #  Chain — планирование, запуск, pending state                        #
    # ------------------------------------------------------------------ #

    _CHAIN_PLANNER_SYSTEM = (
        "Ты планировщик задач. Определи нужна ли цепочка агентов.\n"
        "Доступные агенты: kasper (исследование), kevin (разработка), "
        "peter (аналитика — только если нужны данные/цифры), "
        "elina (тексты/контент), alex (планирование/задачи).\n"
        "Отвечай ТОЛЬКО валидным JSON без markdown."
    )

    async def _plan_chain(self, user_request: str, chat_id: int) -> dict | None:
        """Вызвать Claude чтобы решить: одиночная задача или цепочка."""
        prompt = (
            f"Запрос: {user_request}\n\n"
            "Если нужна цепочка (2+ агентов) — верни JSON:\n"
            '{"is_chain": true, "needs_project_page": false, "steps": ['
            '{"agent": "kasper", "task": "...", "required": true}, ...]}\n'
            "Если достаточно одного агента — верни:\n"
            '{"is_chain": false, "agent": "agent_key", "task": "..."}'
        )
        try:
            response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=600,
                system=self._CHAIN_PLANNER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            plan = json.loads(raw)
            if plan.get("is_chain"):
                logger.info(
                    f"chain_plan | steps={len(plan.get('steps', []))} | request={user_request[:60]!r}"
                )
            return plan
        except Exception as e:
            logger.warning(f"[Марта] _plan_chain error: {e}")
            return None

    async def _create_project_stub(self, user_request: str) -> str | None:
        """Заглушка создания Notion-страницы проекта (Phase 2)."""
        return None

    async def _start_chain(self, plan: dict, user_request: str, chat_id: int) -> None:
        """Запустить цепочку: enqueue первой задачи + уведомить пользователя."""
        steps    = plan.get("steps", [])
        chain_id = str(uuid.uuid4())

        notion_page_id = None
        if plan.get("needs_project_page"):
            notion_page_id = await self._create_project_stub(user_request)

        first = steps[0]
        corr_id = str(uuid.uuid4())
        task_id = await enqueue_chain_task(
            pool=None,
            agent_key=first["agent"],
            payload=first["task"],
            chat_id=chat_id,
            chain_id=chain_id,
            chain_index=0,
            chain_total=len(steps),
            chain_plan=plan,
            notion_page_id=notion_page_id,
            from_agent="marta",
            correlation_id=corr_id,
            priority=_detect_priority(user_request),
        )

        steps_preview = " → ".join(_AGENT_NAMES.get(s["agent"], s["agent"]) for s in steps)
        if task_id and self.app:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔗 Запускаю цепочку из {len(steps)} шагов:\n"
                    f"{steps_preview}\n\n"
                    f"Буду сообщать о каждом шаге. `chain_id: {chain_id[:8]}`"
                ),
                parse_mode="Markdown",
            )
        logger.info(
            f"chain_start | chain_id={chain_id[:8]} | steps={len(steps)} | task_id={task_id} | corr={corr_id[:8]}"
        )

    def _pending_chain_key(self, chat_id: int) -> str:
        return f"pending_chain:{chat_id}"

    async def _save_pending_chain(self, chat_id: int, plan: dict, user_text: str) -> None:
        redis = await self._get_redis()
        payload = json.dumps({"plan": plan, "user_text": user_text}, ensure_ascii=False)
        if redis:
            await redis.set(self._pending_chain_key(chat_id), payload, ex=300)
        else:
            self._history_fallback[-(chat_id)] = [{"role": "pending_chain", "content": payload}]

    async def _load_pending_chain(self, chat_id: int) -> tuple[dict | None, str]:
        redis = await self._get_redis()
        raw = None
        if redis:
            raw = await redis.get(self._pending_chain_key(chat_id))
        else:
            fb = self._history_fallback.get(-(chat_id), [])
            raw = fb[0]["content"] if fb else None
        if not raw:
            return None, ""
        data = json.loads(raw)
        return data.get("plan"), data.get("user_text", "")

    async def _delete_pending_chain(self, chat_id: int) -> None:
        redis = await self._get_redis()
        if redis:
            await redis.delete(self._pending_chain_key(chat_id))
        else:
            self._history_fallback.pop(-(chat_id), None)

    async def _handle_chain_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработчик кнопок chain_confirm / chain_cancel."""
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_chat.id

        if query.data == "chain_confirm":
            plan, user_text = await self._load_pending_chain(chat_id)
            await self._delete_pending_chain(chat_id)
            if plan:
                await self._start_chain(plan, user_text, chat_id)
            else:
                await query.edit_message_text("⚠️ План устарел, повтори запрос.")
        elif query.data == "chain_cancel":
            _, user_text = await self._load_pending_chain(chat_id)
            await self._delete_pending_chain(chat_id)
            await query.edit_message_text("Хорошо, отвечу как обычно.")
            if user_text:
                from telegram import ReplyKeyboardMarkup, KeyboardButton
                _kb = ReplyKeyboardMarkup(
                    [[KeyboardButton("📋 Статус"), KeyboardButton("📜 История")],
                     [KeyboardButton("❌ Отмена задачи")]],
                    resize_keyboard=True,
                )
                async def reply(text, parse_mode=None):
                    await context.bot.send_message(chat_id=chat_id, text=text,
                                                   parse_mode=parse_mode, reply_markup=_kb)
                await self._process_text(user_text, chat_id, reply, skip_chain=True)

    # ------------------------------------------------------------------ #
    #  Общая логика обработки текста                                       #
    # ------------------------------------------------------------------ #

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        from telegram import ReplyKeyboardMarkup, KeyboardButton
        keyboard = ReplyKeyboardMarkup(
            [
                [KeyboardButton("📋 Статус"), KeyboardButton("📜 История")],
                [KeyboardButton("❌ Отмена задачи")],
            ],
            resize_keyboard=True,
        )
        await update.message.reply_text(
            f"{self.emoji} Привет! Я *{self.name}* — {self.role}.\n"
            f"Напиши задачу или выбери действие:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    async def _process_text(
        self,
        user_text: str,
        chat_id: int,
        reply_func,  # callable: async def reply(text, parse_mode=None)
        skip_chain: bool = False,
    ) -> None:
        """Общая логика обработки текста — используется из handle_message и handle_voice."""

        # Обработка кнопок клавиатуры
        if user_text.strip() == "📋 Статус":
            tasks = await get_active_tasks()
            if not tasks:
                await reply_func("✅ Очередь пуста — нет активных задач.")
                return
            lines = ["📋 *Активные задачи:*\n"]
            for t in tasks:
                status_emoji = {"queued": "🟡", "acknowledged": "🔵", "running": "🔵"}.get(t["status"], "⚪")
                priority_label = {20: "🔴", 10: "🟠", 0: ""}.get(t.get("priority", 0), "")
                created = t["created_at"].strftime("%H:%M:%S")
                short_payload = (t["payload"][:50] + "…") if len(t["payload"]) > 50 else t["payload"]
                lines.append(
                    f"{status_emoji}{priority_label} *{t['assigned_agent']}* | id={t['id']}\n"
                    f"    `{short_payload}`\n"
                    f"    corr={t['correlation_id'][:8]} | {created}"
                )
            await reply_func("\n".join(lines), parse_mode="Markdown")
            return

        if user_text.strip() == "📜 История":
            from task_queue import get_recent_tasks
            tasks = await get_recent_tasks(10)
            if not tasks:
                await reply_func("📭 История задач пуста.")
                return
            lines = ["📜 *Последние задачи:*\n"]
            for t in tasks:
                status_emoji = {"completed": "✅", "failed": "❌", "timeout": "⏱️"}.get(t["status"], "⚪")
                finished = t["finished_at"].strftime("%d.%m %H:%M") if t["finished_at"] else "—"
                short_payload = (t["payload"][:50] + "…") if len(t["payload"]) > 50 else t["payload"]
                lines.append(
                    f"{status_emoji} *{t['assigned_agent']}* | id={t['id']}\n"
                    f"    `{short_payload}`\n"
                    f"    {finished} | corr={t['correlation_id'][:8]}"
                )
            await reply_func("\n".join(lines), parse_mode="Markdown")
            return

        if user_text.strip() == "❌ Отмена задачи":
            await reply_func(
                "Напиши номер задачи которую отменить:\n`/cancel <task_id>`\n\n"
                "Узнать номера: нажми 📋 Статус",
                parse_mode="Markdown",
            )
            return

        # ── Проверяем нужна ли цепочка агентов ───────────────────────────────
        if not skip_chain and self.app:
            plan = await self._plan_chain(user_text, chat_id)
            if plan and plan.get("is_chain") and len(plan.get("steps", [])) >= 2:
                steps_preview = " → ".join(
                    _AGENT_NAMES.get(s["agent"], s["agent"]) for s in plan["steps"]
                )
                await self._save_pending_chain(chat_id, plan, user_text)
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"Похоже, это задача для команды.\n\n"
                        f"Предлагаю запустить цепочку:\n{steps_preview}\n\n"
                        f"Запустить?"
                    ),
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🚀 Запустить", callback_data="chain_confirm"),
                        InlineKeyboardButton("💬 Просто ответь", callback_data="chain_cancel"),
                    ]]),
                )
                return

        marta_response = await self.think(user_text, chat_id)
        delegation = self._parse_delegation(marta_response)

        if delegation is None:
            await reply_func(marta_response)
            await self.post_to_group(marta_response)

            if _PROJECT_TRIGGER_RE.search(user_text):
                project_name = _extract_project_name(user_text)
                notion_url = await create_project(
                    name=project_name,
                    description=marta_response[:500],
                )
                if notion_url:
                    await reply_func(
                        f"📁 *Проект «{project_name}» создан в Notion:*\n{notion_url}",
                        parse_mode="Markdown",
                    )
            return

        agent_key, subtask = delegation
        preamble = self._strip_delegate_block(marta_response)
        if preamble:
            await reply_func(preamble)

        agent = self._get_agent(agent_key)
        if agent is None:
            await reply_func(
                f"⚠️ Не могу найти агента *{agent_key}*.",
                parse_mode="Markdown",
            )
            return

        short_task = (subtask[:80] + "…") if len(subtask) > 80 else subtask

        task_id, corr_id = await enqueue_task(
            assigned_agent=agent_key,
            payload=subtask,
            from_agent="marta",
            chat_id=chat_id,
            priority=_detect_priority(user_text),
        )

        if task_id:
            prio = _detect_priority(user_text)
            prio_label = {20: " 🔴 СРОЧНО", 10: " 🟠 ВАЖНО", 0: ""}.get(prio, "")
            await reply_func(
                f"🟡 {agent.emoji} *{agent.name}* принял задачу{prio_label}.\n"
                f"Результат придёт когда будет готов.\n"
                f"`task_id: {task_id} | corr: {corr_id[:8]}`",
                parse_mode="Markdown",
            )
            await self.post_to_group(f"🟡 Задача #{task_id} → {agent.name}: {short_task}")
            logger.info(f"[Марта] Задача #{task_id} → {agent.name} (priority={prio})")
        else:
            logger.warning("[Марта] task_queue недоступен — fallback на прямой вызов")
            await reply_func(
                f"⏳ {agent.emoji} *{agent.name}* работает…",
                parse_mode="Markdown",
            )
            await self.post_to_group(f"🔀 Делегирую → {agent.name}: {short_task}")
            result = await agent.run_task(subtask, from_agent="Марты")
            header = f"📬 *{agent.emoji} {agent.name}* выполнил задачу:\n\n"
            full = header + result
            if len(full) <= 4096:
                await reply_func(full, parse_mode="Markdown")
            else:
                await reply_func(header, parse_mode="Markdown")
                for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
                    await reply_func(chunk)

    # ------------------------------------------------------------------ #
    #  Telegram — обработчик сообщений (переопределяем BaseAgent)          #
    # ------------------------------------------------------------------ #

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return

        chat_id   = update.effective_chat.id
        user_text = update.message.text
        user_name = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )
        logger.info(f"[Марта] Сообщение от @{user_name} (chat={chat_id}): {user_text!r}")

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            from telegram import ReplyKeyboardMarkup, KeyboardButton
            _kb = ReplyKeyboardMarkup(
                [
                    [KeyboardButton("📋 Статус"), KeyboardButton("📜 История")],
                    [KeyboardButton("❌ Отмена задачи")],
                ],
                resize_keyboard=True,
            )

            async def reply(text, parse_mode=None):
                await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=_kb)

            await self._process_text(user_text, chat_id, reply)

        except Exception as e:
            logger.error(f"[Марта] Ошибка: {e}\n{traceback.format_exc()}")
            try:
                await update.message.reply_text("⚠️ Произошла внутренняя ошибка. Попробуй ещё раз.")
            except Exception:
                pass

    async def handle_voice(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Голосовые сообщения: транскрибируем → роутим через _process_text()."""
        if not update.message or not update.message.voice:
            return

        chat_id = update.effective_chat.id
        user_name = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            from groq import AsyncGroq as _AsyncGroq
            if not hasattr(self, "_groq") or self._groq is None:
                self._groq = _AsyncGroq(api_key=config.GROQ_API_KEY)

            tg_file = await context.bot.get_file(update.message.voice.file_id)
            voice_bytes = await tg_file.download_as_bytearray()

            transcript = await self._groq.audio.transcriptions.create(
                model="whisper-large-v3",
                file=("voice.ogg", bytes(voice_bytes)),
                language="ru",
            )
            user_text = transcript.text.strip()

            if not user_text:
                await update.message.reply_text("🎤 Не удалось распознать речь.")
                return

            logger.info(f"[Марта] Голос от @{user_name}: {user_text!r}")
            await update.message.reply_text(f"🎤 Распознано: {user_text}")

            from telegram import ReplyKeyboardMarkup, KeyboardButton
            _kb = ReplyKeyboardMarkup(
                [
                    [KeyboardButton("📋 Статус"), KeyboardButton("📜 История")],
                    [KeyboardButton("❌ Отмена задачи")],
                ],
                resize_keyboard=True,
            )

            async def reply(text, parse_mode=None):
                await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=_kb)

            await self._process_text(user_text, chat_id, reply)

        except Exception as e:
            logger.error(f"[Марта] handle_voice ошибка: {e}")
            await update.message.reply_text(f"⚠️ Ошибка распознавания голоса: {e}")

    # ------------------------------------------------------------------ #
    #  handle_task — для вызова Марты из других агентов                   #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Марта анализирует задачу, при необходимости делегирует и возвращает итог."""
        logger.info(f"[Марта] Задача от {from_agent}: {task!r}")

        marta_response = await self.think(
            f"Задача от {from_agent}: {task}",
            chat_id=0,
            is_task=True,
        )
        delegation = self._parse_delegation(marta_response)

        if delegation:
            agent_key, subtask = delegation
            agent = self._get_agent(agent_key)
            if agent:
                short_task = (subtask[:80] + "…") if len(subtask) > 80 else subtask
                logger.info(f"[Марта] Делегирую (handle_task) → {agent.name}")
                await self.post_to_group(f"🔀 Делегирую → {agent.name}: {short_task}")
                result = await agent.run_task(subtask, from_agent="Марты")
                return result

        # Fallback: Марта отвечает сама
        clean = self._strip_delegate_block(marta_response)
        await self.post_to_group(f"📋 {clean[:200]}")

        # Если задача — создание проекта, сохраняем в Notion
        if _PROJECT_TRIGGER_RE.search(task):
            project_name = _extract_project_name(task)
            logger.info(f"[Марта] Детектирован проект в handle_task: {project_name!r}")
            notion_url = await create_project(
                name=project_name,
                description=clean[:500],
            )
            if notion_url:
                logger.info(f"[Марта] Проект сохранён в Notion: {notion_url}")
                clean = f"{clean}\n\n📁 *Проект «{project_name}» создан в Notion:* {notion_url}"

        return clean

    # ------------------------------------------------------------------ #
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/status — показать активные задачи в очереди."""
        tasks = await get_active_tasks()

        if not tasks:
            await update.message.reply_text("✅ Очередь пуста — нет активных задач.")
            return

        lines = ["📋 *Активные задачи:*\n"]
        for t in tasks:
            status_emoji = {
                "queued":       "🟡",
                "acknowledged": "🔵",
                "running":      "🔵",
            }.get(t["status"], "⚪")

            created = t["created_at"].strftime("%H:%M:%S")
            short_payload = (t["payload"][:50] + "…") if len(t["payload"]) > 50 else t["payload"]
            prio = t.get("priority", 0)
            prio_label = {20: " 🔴", 10: " 🟠", 0: ""}.get(prio, "")

            lines.append(
                f"{status_emoji}{prio_label} *{t['assigned_agent']}* | "
                f"id={t['id']} | {t['status']}\n"
                f"    `{short_payload}`\n"
                f"    corr={t['correlation_id'][:8]} | {created}"
            )

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
        )

    async def cmd_history(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/history — последние 10 выполненных задач."""
        from task_queue import get_recent_tasks
        tasks = await get_recent_tasks(10)

        if not tasks:
            await update.message.reply_text("📭 История задач пуста.")
            return

        lines = ["📜 *Последние задачи:*\n"]
        for t in tasks:
            status_emoji = {
                "completed": "✅",
                "failed":    "❌",
                "timeout":   "⏱️",
            }.get(t["status"], "⚪")

            finished = t["finished_at"].strftime("%d.%m %H:%M") if t["finished_at"] else "—"
            short_payload = (t["payload"][:50] + "…") if len(t["payload"]) > 50 else t["payload"]

            lines.append(
                f"{status_emoji} *{t['assigned_agent']}* | id={t['id']}\n"
                f"    `{short_payload}`\n"
                f"    {finished} | corr={t['correlation_id'][:8]}"
            )

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
        )

    async def cmd_cancel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/cancel <task_id> — отменить задачу из очереди."""
        if not context.args:
            await update.message.reply_text(
                "Использование: /cancel <task_id>\n"
                "Узнать task_id: /status"
            )
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("task_id должен быть числом.")
            return

        from task_queue import cancel_task
        cancelled = await cancel_task(task_id)
        if cancelled:
            await update.message.reply_text(f"✅ Задача #{task_id} отменена.")
        else:
            await update.message.reply_text(
                f"⚠️ Задача #{task_id} не найдена или уже выполняется."
            )

    async def cmd_delegate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/delegate <задача> — явная передача задачи команде."""
        task = " ".join(context.args) if context.args else ""
        if not task:
            await update.message.reply_text("Использование: /delegate <описание задачи>")
            return

        await update.message.reply_text("🔄 Анализирую задачу и делегирую…")
        # Роутим через handle_message-логику повторно использовать весь цикл
        result = await self.handle_task(task, from_agent="команды /delegate")
        await update.message.reply_text(f"✅ Готово:\n\n{result}")

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("delegate", self.cmd_delegate))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CommandHandler("history", self.cmd_history))
        self.app.add_handler(CallbackQueryHandler(
            self._handle_chain_callback,
            pattern="^chain_(confirm|cancel)$",
        ))
