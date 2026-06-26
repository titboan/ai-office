from __future__ import annotations

import json
import re
import traceback
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import anthropic
from loguru import logger
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from config import config
from db import save_project, find_project, list_projects
from utils.tg_format import clean_agent_output as _clean_output
from utils.tg_rich import send_rich_or_fallback as _send_rich
from task_queue import create_task as enqueue_task, get_active_tasks, get_recent_tasks, enqueue_chain_task
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

_CONTINUE_PROJECT_RE = re.compile(
    r"(?:продолжи\s+проект|continue\s+project|проект\s*:)\s*([^,\n]+)",
    re.IGNORECASE,
)

# ── Детектирование «покажи остальные» — продолжение алерта остатков ──────────
_SHOW_MORE_RE = re.compile(
    r"остальн|покажи\s+(?:все|всё|ещё)|показать\s+(?:все|всё)|следующ|ещё\s+позиц|все\s+позиц",
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


def _detect_image_type(data: bytes) -> str:
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "image/webp"
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if data[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    return "image/jpeg"  # fallback


MARTA_SYSTEM = """Ты — Марта, координатор ИИ-офиса.

Команда:
• kasper — исследователь (поиск, анализ)
• kevin  — разработчик (код, GitHub, PR)
• peter  — бизнес-аналитик WB+Ozon: продажи, маржа, ДРР, план поставок (/supply), заказ у поставщика (/order), настройки поставок (срок доставки, буфер запаса)
• elina  — копирайтер (тексты, посты)
• alex   — планировщик (roadmap, напоминания, Notion Tasks)

Правила:
1. Специализированные задачи — делегируй нужному агенту.
2. Приветствия и общие вопросы — отвечай сама.
3. Напоминания и дедлайны — всегда alex. Не отказывай ссылаясь на отсутствие календаря.
4. Любые упоминания «срок поставки», «время доставки», «поставка идёт N дней», «буфер запаса» — всегда peter, делегируй без уточняющих вопросов.
5. При делегировании ОБЯЗАТЕЛЬНО добавь блок:

##DELEGATE##
agent: kasper/kevin/peter/elina/alex
task: конкретная задача для агента
##END##

Форматируй собственные ответы в Rich Markdown для Telegram:
- **текст** — заголовки и акценты
- *текст* — пояснения
- `текст` — команды, ID
- Эмодзи в начале разделов
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- Длина ответа до 30 000 символов, можно делать подробные списки
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

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

    def _main_keyboard(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            ["📊 Отчёт", "📈 Дашборд"],
            ["🗂️ Меню", "❓ Помощь"],
        ], resize_keyboard=True)

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
            from .max    import MaxAgent
            from .dan    import DanAgent

            registry: dict[str, type[BaseAgent]] = {
                "kevin":  KevinAgent,
                "kasper": KasperAgent,
                "peter":  PeterAgent,
                "elina":  ElinaAgent,
                "alex":   AlexAgent,
                "max":    MaxAgent,
                "dan":    DanAgent,
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
        "Ты планировщик задач. Определи нужна ли цепочка агентов.\n\n"
        "Доступные агенты:\n"
        "  kasper — исследование, поиск в интернете, анализ внешних данных\n"
        "  kevin  — разработка: сайты, боты, скрипты, код, деплой, GitHub\n"
        "  peter  — аналитик магазина WB+Ozon: продажи, ДРР, ROAS, рентабельность, план поставок, заказ у поставщика, настройки срока доставки и буфера\n"
        "  elina  — тексты, посты, контент, копирайтинг\n"
        "  alex   — планирование, roadmap, задачи, Notion\n"
        "  dan    — дизайнер: генерация изображений, hero-картинки, иконки для лендингов\n"
        "  max    — синхронизация данных магазина: отзывы, остатки, реклама WB/Ozon, товары\n\n"
        "ПРАВИЛА ДЛЯ PETER (АНАЛИТИКА МАГАЗИНА):\n"
        "Peter — это аналитик НАШЕГО магазина на WB и Ozon. Он работает с реальными данными из БД.\n"
        "Всегда routing на peter (одиночный агент) для запросов:\n"
        "  - аналитика магазина / продаж / заказов\n"
        "  - как достичь цели по обороту / выручке\n"
        "  - ДРР, ROAS, рентабельность товаров\n"
        "  - какие товары слабые / сильные\n"
        "  - отчёт по WB, отчёт по Ozon\n"
        "  - куда направить усилия / бюджет\n"
        "  - анализ воронки, остатки, стоки\n\n"
        "ПРАВИЛА ДЛЯ KASPER:\n"
        "Каспер нужен только когда требуется реальный поиск ВНЕШНЕЙ информации:\n"
        "  - исследовать рынок / конкурентов в интернете\n"
        "  - найти внешние данные / статистику\n"
        "  - изучить технологии перед разработкой\n"
        "Каспер НЕ нужен для: аналитики нашего магазина, лендингов, кода, контента.\n\n"
        "ПРАВИЛА ДЛЯ KEVIN:\n"
        "Всегда включай kevin и ставь is_chain:true, needs_project_page:true если задача содержит:\n"
        "  - создание сайта, лендинга, веб-интерфейса\n"
        "  - написание бота, скрипта, приложения\n"
        "  - любой код для деплоя или коммита в репо\n\n"
        "ПРАВИЛА ДЛЯ DAN:\n"
        "  - Включай Дэна когда нужны изображения для сайта или лендинга\n"
        "  - Дэн всегда идёт ДО Кевина\n"
        "  - НЕ включай Дэна для задач без визуального контента\n\n"
        "ПАРАЛЛЕЛЬНЫЕ ГРУППЫ:\n"
        "Добавляй поле 'group' (int) чтобы запустить агентов одновременно.\n"
        "Агенты с одинаковым group выполняются параллельно; следующий group стартует когда все готовы.\n"
        "Пример: kasper(group:0) → elina(group:1) + kevin(group:1) → kevin(group:2 деплой)\n"
        "Используй параллельность когда задачи независимы (тексты ≠ структура кода).\n\n"
        "ТИПОВЫЕ ЦЕПОЧКИ:\n"
        "  Аналитика магазина / цели по обороту: [peter]\n"
        "  Лендинг/сайт по готовому референсу или макету: [kevin]\n"
        "  Лендинг/сайт с изображениями: [dan(group:0), kevin(group:1)]\n"
        "  Лендинг с исследованием рынка: [kasper(group:0), kevin(group:1)]\n"
        "  Лендинг: исследование → (тексты + дизайн параллельно) → деплой: [kasper(0), elina(1)+dan(1), kevin(2)]\n"
        "  Технический проект (бот, приложение): [kevin] или [kasper(0), kevin(1)]\n"
        "  Контентный проект: [elina] или [kasper(0), elina(1)]\n"
        "  Бизнес-исследование внешнего рынка: [kasper(0), peter(1)]\n"
        "  Полный проект (исследование + дизайн + разработка): [kasper(0), dan(1), kevin(2)]\n\n"
        "ПРАВИЛА ДЛЯ needs_project_page:\n"
        "  true  — проект: сайт, бот, исследование рынка, продукт, приложение, контент-пакет\n"
        "  false — разовый вопрос, справка, простая задача\n\n"
        "ПРАВИЛА ВЫБОРА АГЕНТА ДЛЯ is_chain:false:\n"
        "  - сайт, лендинг, репозиторий, код, бот, деплой → agent: 'kevin'\n"
        "  - тексты, посты, контент → agent: 'elina'\n"
        "  - исследование, поиск данных → agent: 'kasper'\n"
        "  - бизнес-анализ, рынок → agent: 'peter'\n"
        "  - планирование, roadmap → agent: 'alex'\n"
        "ВАЖНО: для сайта/лендинга/кода НИКОГДА не указывай elina или других — только kevin.\n\n"
        "КОГДА НУЖНО УТОЧНЕНИЕ:\n"
        "Если запрос содержит write-операцию (изменить/обновить/удалить/поставить) без конкретики "
        "(не указан товар, цена, количество) — верни:\n"
        '{"clarification_needed": "Уточняющий вопрос"}\n'
        "Примеры когда уточнять: 'измени цены', 'обнови все товары', 'удали карточки'.\n"
        "НЕ уточняй для: отчётов, аналитики, поиска, синхронизации, просмотра данных.\n\n"
        "Отвечай ТОЛЬКО валидным JSON без markdown."
    )

    async def _plan_chain(self, user_request: str, chat_id: int) -> dict | None:
        """Вызвать Claude чтобы решить: одиночная задача или цепочка."""
        prompt = (
            f"Запрос: {user_request}\n\n"
            "Если нужна цепочка (2+ агентов) — верни JSON:\n"
            '{"is_chain": true, "needs_project_page": true, "steps": ['
            '{"agent": "kasper", "task": "исследуй конкурентов", "required": true}, '
            '{"agent": "kevin", "task": "создай лендинг на основе исследования", "required": true}'
            "]}\n"
            "Если достаточно одного агента — верни:\n"
            '{"is_chain": false, "agent": "agent_key", "task": "..."}\n\n'
            "Если запрос — расплывчатая write-операция без конкретики — верни:\n"
            '{"clarification_needed": "Короткий уточняющий вопрос"}'
        )
        try:
            response = await self.claude.messages.create(
                model=config.CLAUDE_OPUS_MODEL,
                max_tokens=2000,
                system=self._CHAIN_PLANNER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            logger.debug(f"[Марта] _plan_chain raw response: {raw[:300]}")
            plan = json.loads(raw)
            logger.debug(f"[Марта] _plan_chain result: {raw[:500]}")
            if plan.get("is_chain"):
                logger.info(
                    f"chain_plan | steps={len(plan.get('steps', []))} | request={user_request[:60]!r}"
                )
            return plan
        except Exception as e:
            logger.warning(f"[Марта] _plan_chain error: {e} | raw: {raw[:200] if 'raw' in locals() else 'no response'}")
            return None

    @staticmethod
    def _normalize_chain_steps(steps: list[dict]) -> tuple[list[dict], int]:
        """Нормализовать шаги цепочки: добавить group если нет, вернуть (steps, total_groups).

        Шаги с одинаковым group выполняются параллельно.
        Шаги без group получают group=index (последовательно, обратная совместимость).
        """
        if not any("group" in s for s in steps):
            normalized = [{**s, "group": i} for i, s in enumerate(steps)]
        else:
            normalized = [dict(s) for s in steps]
            # Шагам без group присваиваем уникальный номер после максимального
            max_g = max((s.get("group", 0) for s in normalized), default=0)
            for s in normalized:
                if "group" not in s:
                    max_g += 1
                    s["group"] = max_g
        total_groups = max(s["group"] for s in normalized) + 1
        return normalized, total_groups

    async def _start_chain(self, plan: dict, user_request: str, chat_id: int) -> None:
        """Запустить цепочку: enqueue шагов первой группы + настроить Redis-барьер."""
        steps    = plan.get("steps", [])
        chain_id = str(uuid.uuid4())
        resume_page_id = None

        # Нормализуем шаги: добавляем group-поле
        steps, total_groups = self._normalize_chain_steps(steps)
        plan = {**plan, "steps": steps}  # сохраняем нормализованный план

        # Первая группа: все шаги с group=0
        group0_steps = [s for s in steps if s["group"] == 0]
        is_parallel_start = len(group0_steps) > 1
        corr_id = str(uuid.uuid4())

        enqueued_ids: list[int] = []
        for step in group0_steps:
            task_id = await enqueue_chain_task(
                pool=None,
                agent_key=step["agent"],
                payload=step["task"],
                chat_id=chat_id,
                chain_id=chain_id,
                chain_index=0,
                chain_total=total_groups,
                chain_plan=plan,
                from_agent="marta",
                correlation_id=corr_id,
                priority=_detect_priority(user_request),
                timeout_seconds=600 if step["agent"] == "dan" else 300,
                parallel_group=0 if is_parallel_start else None,
            )
            if task_id:
                enqueued_ids.append(task_id)

        # Redis-барьер для параллельного старта
        if is_parallel_start:
            redis = await self._get_redis()
            if redis:
                await redis.set(f"chain_barrier:{chain_id}:0", len(group0_steps), ex=86_400)

        logger.info(
            f"chain_start | chain_id={chain_id[:8]} | groups={total_groups} | "
            f"group0_agents={[s['agent'] for s in group0_steps]} | tasks={enqueued_ids} | corr={corr_id[:8]}"
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
        chat_id = query.message.chat_id

        if query.data == "chain_confirm":
            plan, user_text = await self._load_pending_chain(chat_id)
            await self._delete_pending_chain(chat_id)
            if plan:
                await self._start_chain(plan, user_text, chat_id)
                _ce = {
                    "kasper": "🔍", "kevin": "👨‍💻", "peter": "📊",
                    "elina": "✍️", "alex": "🗓️", "marta": "👩‍💼",
                    "dan": "🎨", "tina": "📋", "digest": "📰",
                }
                _cn = {
                    "kasper": "Каспер", "kevin": "Кевин", "peter": "Питер",
                    "elina": "Элина", "alex": "Алекс", "marta": "Марта",
                    "dan": "Дэн", "tina": "Тина", "digest": "Дайджест",
                }
                chain_line = " → ".join(
                    f"{_ce.get(s['agent'], '🤖')} {_cn.get(s['agent'], s['agent'])}"
                    for s in plan.get("steps", [])
                )
                await query.edit_message_text(
                    f"✅ **Принято в работу!**\n\n"
                    f"🔗 {chain_line}\n\n"
                    f"Буду сообщать о каждом шаге.",
                )
            else:
                await query.edit_message_text("⏰ План устарел, повтори запрос.")

        elif query.data == "chain_cancel":
            plan, user_text = await self._load_pending_chain(chat_id)
            await self._delete_pending_chain(chat_id)
            await query.edit_message_text("Хорошо, отвечу сам!")
            if user_text:
                _kb = self._main_keyboard()

                async def reply(text, parse_mode=None, **kw):
                    markup = _kb.to_dict() if _kb else None
                    await _send_rich(self.bot_token, chat_id, text, reply_markup_dict=markup)

                await self._process_text(user_text, chat_id, reply, skip_chain=True)

    # ------------------------------------------------------------------ #
    #  Общая логика обработки текста                                       #
    # ------------------------------------------------------------------ #

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        keyboard = self._main_keyboard()
        await _send_rich(
            self.bot_token, update.effective_chat.id,
            f"{self.emoji} Привет! Я **{self.name}** — {self.role}.\nНапиши задачу или выбери действие:",
            reply_markup_dict=keyboard.to_dict(),
            reply_to_message_id=update.message.message_id,
        )

    async def _process_text(
        self,
        user_text: str,
        chat_id: int,
        reply_func,  # callable: async def reply(text, parse_mode=None)
        skip_chain: bool = False,
    ) -> None:
        """Общая логика обработки текста — используется из handle_message и handle_voice."""
        plan: dict | None = None  # инициализируем чтобы не получить NameError при skip_chain=True

        # Обработка кнопок клавиатуры
        _QUICK_ACTIONS = {
            "📊 Отчёт":  ("peter", "Дай сводный отчёт по продажам WB и Ozon за последние 7 дней"),
            "⭐ Отзывы": ("max",   "Обработай новые отзывы на маркетплейсах"),
        }
        _AGENT_QUICK_LABEL = {
            "peter": "📊 Питер", "max": "🛒 Макс",
        }
        btn = user_text.strip()

        if btn == "📈 Дашборд":
            if config.DASHBOARD_URL:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 Открыть дашборд", web_app=WebAppInfo(url=config.DASHBOARD_URL))
                ]])
                await _send_rich(
                    self.bot_token, chat_id,
                    "📈 **Дашборд по заказам**\n\nЗаказы, выручка, топ-товары — в реальном времени:",
                    reply_markup_dict=markup.to_dict(),
                )
            else:
                await reply_func(
                    "⚠️ Дашборд не настроен. Добавь `DASHBOARD_URL` в переменные Railway.",
                )
            return

        if btn == "🔄 Синхронизация":
            await enqueue_task(
                assigned_agent="max",
                payload="Синхронизируй данные: заказы, остатки, отзывы",
                from_agent="marta",
                chat_id=chat_id,
                priority=0,
            )
            await reply_func(
                "🔄 **Синхронизация запущена**\n\n"
                "Макс подтянет: заказы, остатки, отзывы — пришлю сводку когда готово.\n\n"
                "⚠️ **Финансы и реклама синхронизируются отдельно у Макса:**\n"
                "/sync_adv — рекламная статистика (CTR, ROAS)\n"
                "/sync_fin — выплаты и комиссии маркетплейсов\n\n"
                "*Финансовые данные МП обновляются с задержкой до 24 ч.*",
            )
            return

        if btn in _QUICK_ACTIONS:
            agent_key, task_text = _QUICK_ACTIONS[btn]
            await enqueue_task(
                assigned_agent=agent_key,
                payload=task_text,
                from_agent="marta",
                chat_id=chat_id,
                priority=0,
            )
            label = _AGENT_QUICK_LABEL.get(agent_key, agent_key)
            await reply_func(
                f"⏳ Передала задачу {label} — пришлю результат когда готово.",
            )
            return

        if user_text.strip() == "🗂️ Меню":
            await _send_rich(
                self.bot_token, chat_id,
                "🏢 AI Office — Быстрое меню\n\nВыбери раздел:",
                reply_markup_dict=self._MARTA_MENU_KEYBOARD.to_dict(),
            )
            return

        if user_text.strip() == "❓ Помощь":
            await reply_func(self._help_text())
            return

        if user_text.strip() == "📋 Статус":
            tasks = await get_active_tasks()
            if not tasks:
                await reply_func("✅ Очередь пуста — нет активных задач.")
                return
            lines = ["📋 **Активные задачи:**\n"]
            for t in tasks:
                status_emoji = {"queued": "🟡", "acknowledged": "🔵", "running": "🔵"}.get(t["status"], "⚪")
                priority_label = {20: "🔴", 10: "🟠", 0: ""}.get(t.get("priority", 0), "")
                created = t["created_at"].strftime("%H:%M:%S")
                short_payload = (t["payload"][:50] + "…") if len(t["payload"]) > 50 else t["payload"]
                lines.append(
                    f"{status_emoji}{priority_label} **{t['assigned_agent']}** | id={t['id']}\n"
                    f"    `{short_payload}`\n"
                    f"    corr={t['correlation_id'][:8]} | {created}"
                )
            await reply_func("\n".join(lines))
            return

        if user_text.strip() == "📜 История":
            from task_queue import get_recent_tasks
            tasks = await get_recent_tasks(10)
            if not tasks:
                await reply_func("📭 История задач пуста.")
                return
            lines = ["📜 **Последние задачи:**\n"]
            for t in tasks:
                status_emoji = {"completed": "✅", "failed": "❌", "timeout": "⏱️"}.get(t["status"], "⚪")
                finished = t["finished_at"].strftime("%d.%m %H:%M") if t["finished_at"] else "—"
                short_payload = (t["payload"][:50] + "…") if len(t["payload"]) > 50 else t["payload"]
                lines.append(
                    f"{status_emoji} **{t['assigned_agent']}** | id={t['id']}\n"
                    f"    `{short_payload}`\n"
                    f"    {finished} | corr={t['correlation_id'][:8]}"
                )
            await reply_func("\n".join(lines))
            return

        if user_text.strip() == "❌ Отмена задачи":
            await reply_func(
                "Напиши номер задачи которую отменить:\n`/cancel <task_id>`\n\n"
                "Узнать номера: нажми 📋 Статус",
            )
            return

        # ── Детект команды "продолжи проект" ─────────────────────────────────
        cm = _CONTINUE_PROJECT_RE.search(user_text)
        if cm:
            proj_name = cm.group(1).strip()
            project = await find_project(chat_id, proj_name)
            if project is None:
                projects = await list_projects(chat_id)
                if projects:
                    proj_list = "\n".join(f"• {p['name']}" for p in projects)
                else:
                    proj_list = "(нет сохранённых проектов)"
                await reply_func(f"Проект '{proj_name}' не найден. Доступные проекты:\n{proj_list}")
                return

        # ── «Остальные покажи» — продолжение алерта остатков ─────────────────
        if _SHOW_MORE_RE.search(user_text):
            overflow_raw = await self._redis_get(f"stock_overflow:{chat_id}")
            if overflow_raw:
                import json as _json
                from telegram import Bot as _TGBot
                try:
                    overflow_items = _json.loads(overflow_raw)
                    await self._redis_set(f"stock_overflow:{chat_id}", "", ttl=1)
                    text = "📋 <b>Ещё позиции по остаткам:</b>\n\n" + "\n\n".join(overflow_items)
                    tg_bot = _TGBot(token=self.bot_token)
                    await tg_bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                    return
                except Exception as _e:
                    logger.warning(f"[Марта] stock_overflow parse error: {_e}")

        # ── Проверяем нужна ли цепочка агентов ───────────────────────────────
        if not skip_chain:
            plan = await self._plan_chain(user_text, chat_id)

            # Агент просит уточнения перед выполнением
            if plan and plan.get("clarification_needed"):
                question = plan["clarification_needed"]
                await self._save_clarification(chat_id, question, user_text)
                await reply_func(f"❓ {question}")
                return

            if plan and plan.get("is_chain"):
                _CHAIN_AGENT_EMOJI = {
                    "kasper": "🔍", "kevin": "👨‍💻", "peter": "📊",
                    "elina": "✍️", "alex": "🗓️", "marta": "👩‍💼",
                    "dan": "🎨", "tina": "📋", "digest": "📰",
                }
                _CHAIN_AGENT_NAMES = {
                    "kasper": "Каспер", "kevin": "Кевин", "peter": "Питер",
                    "elina": "Элина", "alex": "Алекс", "marta": "Марта",
                    "dan": "Дэн", "tina": "Тина", "digest": "Дайджест",
                }
                steps = plan.get("steps", [])
                steps_lines = ""
                for i, step in enumerate(steps, 1):
                    a_key   = step.get("agent", "")
                    emoji   = _CHAIN_AGENT_EMOJI.get(a_key, "🤖")
                    name    = _CHAIN_AGENT_NAMES.get(a_key, a_key)
                    task_str = step.get("task", "")
                    task_short = task_str[:55] + ("..." if len(task_str) > 55 else "")
                    steps_lines += f"{i}. {emoji} **{name}** — {task_short}\n"

                await self._save_pending_chain(chat_id, plan, user_text)
                if self.app:
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("🚀 Запустить", callback_data="chain_confirm"),
                        InlineKeyboardButton("💬 Просто ответь", callback_data="chain_cancel"),
                    ]])
                    await _send_rich(
                        self.bot_token, chat_id,
                        f"🗂️ **Задача для команды**\n\n{steps_lines}\nЗапустить цепочку?",
                        reply_markup_dict=kb.to_dict(),
                    )
                    return  # НЕ делегируем дальше — ждём ответа пользователя

        if plan and not plan.get("is_chain") and plan.get("agent"):
            agent_key = plan.get("agent")
            task_text = plan.get("task") or user_text
            agent = self._get_agent(agent_key)
            if agent:
                prio = _detect_priority(user_text)
                prio_label = {20: " 🔴 СРОЧНО", 10: " 🟠 ВАЖНО", 0: ""}.get(prio, "")
                _AGENT_LABEL_MAP = {
                    "kasper": "🔍 Каспер", "kevin": "👨‍💻 Кевин", "peter": "📊 Питер",
                    "elina": "✍️ Элина", "alex": "🗓️ Алекс", "dan": "🎨 Дэн",
                    "max": "🛒 Макс", "tina": "📋 Тина",
                }
                label = _AGENT_LABEL_MAP.get(agent_key, agent_key)
                task_id, corr_id = await enqueue_task(
                    assigned_agent=agent_key,
                    payload=task_text,
                    from_agent="marta",
                    chat_id=chat_id,
                    priority=prio,
                    timeout_seconds=600 if agent_key == "dan" else 300,
                )
                await reply_func(
                    f"⏳ Передала задачу {label}{prio_label} — пришлю результат когда готово.",
                )
                return

        marta_response = await self.think(user_text, chat_id)
        delegation = self._parse_delegation(marta_response)

        if delegation is None:
            cleaned = _clean_output(marta_response)
            await reply_func(cleaned)
            await self.post_to_group(marta_response)
            return

        agent_key, subtask = delegation
        preamble = self._strip_delegate_block(marta_response)
        if preamble:
            await reply_func(_clean_output(preamble))

        agent = self._get_agent(agent_key)
        if agent is None:
            await reply_func(f"⚠️ Не могу найти агента `{agent_key}`.")
            return

        short_task = (subtask[:80] + "…") if len(subtask) > 80 else subtask

        task_id, corr_id = await enqueue_task(
            assigned_agent=agent_key,
            payload=subtask,
            from_agent="marta",
            chat_id=chat_id,
            priority=_detect_priority(user_text),
            timeout_seconds=600 if agent_key == "dan" else 300,
        )

        if task_id:
            await self.post_to_group(f"🟡 Задача #{task_id} → {agent.name}: {short_task}")
            logger.info(f"[Марта] Задача #{task_id} → {agent.name} (priority={prio})")
        else:
            logger.warning("[Марта] task_queue недоступен — fallback на прямой вызов")
            await reply_func(f"⏳ {agent.emoji} **{agent.name}** работает…")
            await self.post_to_group(f"🔀 Делегирую → {agent.name}: {short_task}")
            agent._current_chat_id = chat_id
            try:
                result = await agent.run_task(subtask, from_agent="Марты")
            finally:
                agent._current_chat_id = None
            header = f"📬 {agent.emoji} **{agent.name}** выполнил задачу:\n\n"
            await reply_func(header + _clean_output(result))

    # ------------------------------------------------------------------ #
    #  Telegram — обработчик сообщений (переопределяем BaseAgent)          #
    # ------------------------------------------------------------------ #

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, override_text: str | None = None) -> None:
        if not update.message or (not update.message.text and not override_text):
            return

        chat_id   = update.effective_chat.id
        user_text = override_text or update.message.text
        user_name = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )
        logger.info(f"[Марта] Сообщение от @{user_name} (chat={chat_id}): {user_text!r}")

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            # Если пользователь в онбординг-флоу Макса — передаём ему текст (ввод токенов и т.д.)
            max_agent = getattr(self, "_max_agent", None)
            if max_agent:
                try:
                    onboard_raw = await max_agent._redis_get(max_agent._onboard_key(chat_id))
                    if onboard_raw and onboard_raw not in (b"", ""):
                        await max_agent._handle_onboard_text.__func__(max_agent, update, context)
                        return
                except Exception:
                    pass

            _kb = self._main_keyboard()

            async def reply(text, parse_mode=None, **kw):
                markup = _kb.to_dict() if _kb else None
                await _send_rich(self.bot_token, chat_id, text, reply_markup_dict=markup)

            # Проверяем: пользователь отвечает на уточняющий вопрос агента
            clari = await self._pop_clarification(chat_id)
            if clari:
                agent_key = clari.get("agent_key", "marta")
                question  = clari.get("question", "")
                orig      = clari.get("original_payload", "")
                if agent_key == "marta":
                    # Марта спрашивала сама → переобрабатываем с уточнением
                    enhanced = f"{orig}\n\n[Уточнение пользователя: {user_text}]"
                    await reply(f"✅ Продолжаю с учётом уточнения…")
                    await self._process_text(enhanced, chat_id, reply)
                else:
                    # Другой агент (Макс и др.) → повторно ставим задачу с ответом
                    await enqueue_task(
                        assigned_agent=agent_key,
                        payload=f"{orig}\n\n[Ответ на вопрос «{question}»]: {user_text}",
                        from_agent="user",
                        chat_id=chat_id,
                    )
                    _AGENT_LABEL = {
                        "max": "🛒 Макс", "peter": "📊 Питер", "kasper": "🔍 Каспер",
                        "elina": "✍️ Элина", "alex": "🗓️ Алекс", "kevin": "👨‍💻 Кевин",
                    }
                    label = _AGENT_LABEL.get(agent_key, agent_key)
                    await reply(f"✅ Передала ответ {label} — пришлю результат когда готово.")
                return

            if user_text.strip() == "📂 Проекты":
                await self._cmd_projects(update, context)
                return

            await self._process_text(user_text, chat_id, reply)

        except Exception as e:
            logger.error(f"[Марта] Ошибка в handle_message: {e}\n{traceback.format_exc()}")
            try:
                # Пробуем ответить напрямую без цепочки
                fallback = await self.think(user_text, chat_id)
                await _send_rich(self.bot_token, chat_id, fallback)
            except Exception as e2:
                logger.error(f"[Марта] Fallback тоже упал: {e2}")
                try:
                    await update.message.reply_text(
                        "⚠️ Не смогла обработать запрос. Попробуй переформулировать "
                        "или обратись напрямую к Питеру (/drr, /report) или Максу (/sync_adv)."
                    )
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

            _kb = self._main_keyboard()

            async def reply(text, parse_mode=None):
                await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=_kb)

            await self._process_text(user_text, chat_id, reply)

        except Exception as e:
            logger.error(f"[Марта] handle_voice ошибка: {e}")
            await update.message.reply_text(f"⚠️ Ошибка распознавания голоса: {e}")

    async def _handle_infographic_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработка inline-кнопок выбора товара для инфографики."""
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_chat.id
        data = query.data  # "infographic_confirm:0" или "infographic_skip"

        if data == "infographic_skip":
            await self._redis_set(f"pending_photo_file:{chat_id}", "", ttl=1)
            await query.edit_message_text("Понял, обрабатываю как обычное фото.")
            return

        try:
            idx = int(data.split(":")[1])
        except (IndexError, ValueError):
            await query.edit_message_text("Ошибка: неверный формат кнопки.")
            return

        pending_raw = await self._redis_get(f"pending_infographic:{chat_id}")
        if not pending_raw:
            await query.edit_message_text("Сессия истекла — пришли фото ещё раз.")
            return

        try:
            items = json.loads(pending_raw)
        except Exception:
            await query.edit_message_text("Ошибка данных — пришли фото ещё раз.")
            return

        if idx >= len(items):
            await query.edit_message_text("Товар не найден.")
            return

        item = items[idx]
        file_id = await self._redis_get(f"pending_photo_file:{chat_id}")
        if not file_id:
            await query.edit_message_text("Фото устарело (> 10 мин) — пришли ещё раз.")
            return

        from task_queue import create_task
        payload = json.dumps({
            "action": "upload_photo",
            "article": item["article"],
            "marketplace": item["marketplace"],
            "file_id": file_id,
            "name": item["name"],
        }, ensure_ascii=False)
        await create_task(
            assigned_agent="max",
            payload=payload,
            from_agent="user",
            chat_id=chat_id,
            task_type="upload_photo",
        )

        items.pop(idx)
        if items:
            await self._redis_set(
                f"pending_infographic:{chat_id}",
                json.dumps(items, ensure_ascii=False),
                ttl=7 * 86_400,
            )
        else:
            await self._redis_set(f"pending_infographic:{chat_id}", "", ttl=1)
        await self._redis_set(f"pending_photo_file:{chat_id}", "", ttl=1)

        await query.edit_message_text(
            f"✅ Передала Максу — загружу на {item['marketplace'].upper()} в ближайшие минуты.\n"
            f"Через 14 дней Питер покажет CTR до/после."
        )

    async def handle_photo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Фото + подпись: анализ через Claude Vision → _process_text()."""
        if not update.message or not update.message.photo:
            return

        import base64

        chat_id = update.effective_chat.id
        user_name = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )
        caption = update.message.caption or ""
        logger.info(f"[Марта] Фото от @{user_name} (chat={chat_id}), caption={caption!r}")

        # Проверяем: ожидаем ли инфографику для загрузки
        pending_raw = await self._redis_get(f"pending_infographic:{chat_id}")
        if pending_raw:
            try:
                items = json.loads(pending_raw)
                if items:
                    file_id = update.message.photo[-1].file_id
                    await self._redis_set(f"pending_photo_file:{chat_id}", file_id, ttl=600)
                    if len(items) == 1:
                        item = items[0]
                        kb = InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                f"✅ {item['name']} ({item['marketplace'].upper()})",
                                callback_data="infographic_confirm:0"
                            ),
                            InlineKeyboardButton("❌ Не инфографика", callback_data="infographic_skip"),
                        ]])
                        await update.message.reply_text("📸 Загружаем инфографику?", reply_markup=kb)
                    else:
                        rows = [
                            [InlineKeyboardButton(
                                f"📦 {it['name']} ({it['marketplace'].upper()})",
                                callback_data=f"infographic_confirm:{i}"
                            )]
                            for i, it in enumerate(items)
                        ]
                        rows.append([InlineKeyboardButton("❌ Не инфографика", callback_data="infographic_skip")])
                        await update.message.reply_text(
                            "📸 Для какого товара эта инфографика?",
                            reply_markup=InlineKeyboardMarkup(rows)
                        )
                    return
            except Exception as _e:
                logger.warning(f"[Марта] pending_infographic parse error: {_e}")

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            tg_file = await context.bot.get_file(update.message.photo[-1].file_id)
            photo_bytes = await tg_file.download_as_bytearray()
            media_type = _detect_image_type(bytes(photo_bytes))
            photo_b64 = base64.b64encode(photo_bytes).decode("utf-8")

            vision_response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": photo_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Ты анализируешь референс веб-страницы. Опиши ТОЛЬКО структуру и компоновку: "
                                "какие секции есть и в каком порядке, как расположены элементы внутри каждой секции "
                                "(сколько колонок, что слева/справа/по центру), какие типы блоков присутствуют "
                                "(хедер, hero, карточки, таблица, форма, футер и т.д.), "
                                "какие UI-элементы есть (кнопки, иконки, фото, навигация). "
                                "НЕ описывай цвета, шрифты и тематику — их зададут отдельно. "
                                "Пиши структурированно, по секциям. На русском языке."
                            ),
                        },
                    ],
                }],
            )
            image_description = vision_response.content[0].text

            if caption:
                combined_text = (
                    f"[Структура и компоновка по референсу — строго соблюдать layout]\n"
                    f"{image_description}\n\n"
                    f"[Задача пользователя — цвета, тематика, контент]\n"
                    f"{caption}\n\n"
                    f"[Инструкция]\n"
                    f"Реализуй лендинг точно повторяя структуру и компоновку референса. "
                    f"Цвета, тематику и контент бери из задачи пользователя, НЕ из референса."
                )
            else:
                await self._redis_set(f"pending_image:{chat_id}", image_description, ttl=600)
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🌐 Сделать лендинг", callback_data=f"img_action:landing:{chat_id}"),
                        InlineKeyboardButton("🔍 Исследовать нишу", callback_data=f"img_action:research:{chat_id}"),
                    ],
                    [
                        InlineKeyboardButton("📝 Написать текст", callback_data=f"img_action:copy:{chat_id}"),
                        InlineKeyboardButton("💬 Описать своими словами", callback_data=f"img_action:custom:{chat_id}"),
                    ],
                ])
                await update.message.reply_text("🖼️ Отличный референс! Что с ним делаем?", reply_markup=keyboard)
                return

            _kb = self._main_keyboard()

            async def reply(text, parse_mode=None):
                await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=_kb)

            await self._process_text(combined_text, chat_id, reply)

        except Exception as e:
            logger.error(f"[Марта] handle_photo ошибка: {e}")
            await update.message.reply_text(f"⚠️ Ошибка обработки фото: {e}")

    async def _handle_image_action(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обрабатывает выбор действия для загруженного изображения."""
        query = update.callback_query
        await query.answer()

        parts = query.data.split(":")  # "img_action:landing:chat_id"
        action = parts[1]
        chat_id = int(parts[2])

        image_description = await self._redis_get(f"pending_image:{chat_id}")
        if not image_description:
            await query.edit_message_text("⚠️ Референс устарел, отправь изображение ещё раз.")
            return

        action_map = {
            "landing":  "Создай лендинг на основе этого референса",
            "research": "Исследуй нишу и рынок для продукта из этого референса",
            "copy":     "Напиши тексты для сайта на основе этого референса",
        }

        if action == "custom":
            await query.edit_message_text("✏️ Напиши что нужно сделать с этим референсом:")
            await self._redis_set(f"awaiting_image_task:{chat_id}", "1", ttl=600)
            return

        task_text = action_map.get(action, "Обработай этот референс")
        combined_text = (
            f"[Структура и компоновка по референсу — строго соблюдать layout]\n"
            f"{image_description}\n\n"
            f"[Задача пользователя — цвета, тематика, контент]\n"
            f"{task_text}\n\n"
            f"[Инструкция]\n"
            f"Реализуй задачу точно повторяя структуру и компоновку референса. "
            f"Цвета, тематику и контент бери из задачи пользователя, НЕ из референса."
        )

        await query.edit_message_text("⚙️ Принято! Делегирую команде...")

        _kb = self._main_keyboard()

        async def reply(text, parse_mode=None):
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=_kb)

        await self._process_text(combined_text, chat_id, reply)

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
                agent._current_chat_id = getattr(self, "_current_chat_id", None)
                try:
                    result = await agent.run_task(subtask, from_agent="Марты")
                finally:
                    agent._current_chat_id = None
                return result

        # Fallback: Марта отвечает сама
        clean = self._strip_delegate_block(marta_response)
        await self.post_to_group(f"📋 {clean[:200]}")

        return clean

    # ------------------------------------------------------------------ #
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/status — показать состояние офиса."""
        from task_queue import get_recent_tasks

        _AGENT_EMOJI = {
            "kasper": "🔍", "kevin": "👨‍💻", "peter": "📊",
            "elina": "✍️", "alex": "🗓️", "marta": "👩‍💼",
            "dan": "🎨", "tina": "📋", "digest": "📰",
        }
        _STATUS_EMOJI = {
            "queued": "🕐", "acknowledged": "👀",
            "running": "⚙️", "failed": "🔴", "timeout": "⏱️",
        }

        now_msk = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M")
        lines = [f"📋 **Статус офиса** — {now_msk} МСК\n"]

        tasks = await get_active_tasks()
        if not tasks:
            lines.append("✅ Все агенты свободны")
        else:
            for t in tasks:
                agent_emoji  = _AGENT_EMOJI.get(t["assigned_agent"], "🤖")
                status_emoji = _STATUS_EMOJI.get(t["status"], "❓")
                short_task   = t["payload"][:60].strip() + ("..." if len(t["payload"]) > 60 else "")
                created_at = t["created_at"]
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                wait = datetime.now(timezone.utc) - created_at
                wait_str = (
                    f"{int(wait.total_seconds() // 60)} мин"
                    if wait.total_seconds() >= 60
                    else f"{int(wait.total_seconds())} сек"
                )
                lines.append(
                    f"{status_emoji} {agent_emoji} **{t['assigned_agent']}** — {short_task}\n"
                    f"⏳ В работе: {wait_str}\n"
                )

        recent = await get_recent_tasks(5)
        if recent:
            lines.append("\n📜 **Последние выполненные:**")
            for t in recent:
                agent_emoji = _AGENT_EMOJI.get(t["assigned_agent"], "🤖")
                short_task  = t["payload"][:60].strip() + ("..." if len(t["payload"]) > 60 else "")
                lines.append(f"✅ {agent_emoji} {t['assigned_agent']} — {short_task}")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📜 История", callback_data="status:history"),
            InlineKeyboardButton("❌ Отмена задачи", callback_data="status:cancel"),
        ]])

        await _send_rich(
            self.bot_token, update.effective_chat.id, "\n".join(lines),
            reply_markup_dict=keyboard.to_dict(),
            reply_to_message_id=update.message.message_id,
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

        lines = ["📜 **Последние задачи:**\n"]
        for t in tasks:
            status_emoji = {
                "completed": "✅",
                "failed":    "❌",
                "timeout":   "⏱️",
            }.get(t["status"], "⚪")

            finished = t["finished_at"].strftime("%d.%m %H:%M") if t["finished_at"] else "—"
            short_payload = (t["payload"][:50] + "…") if len(t["payload"]) > 50 else t["payload"]

            lines.append(
                f"{status_emoji} **{t['assigned_agent']}** | id={t['id']}\n"
                f"    `{short_payload}`\n"
                f"    {finished} | corr={t['correlation_id'][:8]}"
            )

        await _send_rich(
            self.bot_token, update.effective_chat.id, "\n".join(lines),
            reply_to_message_id=update.message.message_id,
        )

    _LOGS_KEYBOARD = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔴 Все ошибки",    callback_data="logs:all"),
            InlineKeyboardButton("📡 Реклама WB",    callback_data="logs:adv"),
        ],
        [
            InlineKeyboardButton("🔄 Синхронизация", callback_data="logs:sync"),
            InlineKeyboardButton("🤖 Claude API",    callback_data="logs:claude"),
        ],
        [
            InlineKeyboardButton("🛒 Макс",          callback_data="logs:макс"),
            InlineKeyboardButton("📊 Питер",         callback_data="logs:питер"),
        ],
        [
            InlineKeyboardButton("🔁 Обновить",      callback_data="logs:all"),
            InlineKeyboardButton("◀️ Меню",          callback_data="logs:back"),
        ],
    ])

    async def _render_logs(self, keyword: str | None, hours: int = 24) -> str:
        """Формирует текст лога для отправки."""
        from db import get_recent_errors
        rows = await get_recent_errors(hours=hours, limit=50)
        if keyword:
            kw = keyword.lower()
            rows = [r for r in rows if kw in (r["message"] or "").lower()
                    or kw in (r["logger_name"] or "").lower()]
        if not rows:
            note = f" по «{keyword}»" if keyword else ""
            return f"✅ Ошибок за {hours}ч{note} нет — всё чисто."
        label = f"«{keyword}»" if keyword else "все"
        lines = [f"🔴 <b>Ошибки {label} за {hours}ч</b> — {len(rows)} записей:\n"]
        for r in rows[:12]:
            ts = r["ts"].strftime("%d.%m %H:%M") if r["ts"] else "?"
            msg = (r["message"] or "")[:250]
            lines.append(f"<code>{ts}</code> {msg}")
        return "\n\n".join(lines)

    async def cmd_logs(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/logs [фильтр] — последние ошибки. Пример: /logs adv"""
        keyword = context.args[0].lower() if context.args else None
        text = await self._render_logs(keyword)
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=self._LOGS_KEYBOARD,
        )

    async def _handle_logs_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Callback для кнопок /logs (logs:all, logs:adv, …)."""
        query = update.callback_query
        await query.answer()
        _, keyword = query.data.split(":", 1)

        if keyword == "back":
            office_text, office_buttons = self._MARTA_MENU_SECTIONS["office"]
            try:
                await query.message.edit_text(
                    office_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(office_buttons),
                )
            except Exception:
                await query.message.delete()
                await query.get_bot().send_message(
                    chat_id=query.message.chat_id,
                    text=office_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(office_buttons),
                )
            return

        kw = None if keyword == "all" else keyword
        text = await self._render_logs(kw)
        try:
            await query.message.edit_text(
                text, parse_mode="HTML", reply_markup=self._LOGS_KEYBOARD,
            )
        except Exception:
            await query.message.reply_text(
                text, parse_mode="HTML", reply_markup=self._LOGS_KEYBOARD,
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
        result = await self.handle_task(task, from_agent="команды /delegate")
        result_clean = _clean_output(result)
        await _send_rich(
            self.bot_token, update.effective_chat.id, f"✅ Готово:\n\n{result_clean}",
            reply_to_message_id=update.message.message_id,
        )

    async def _cmd_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать список сохранённых проектов пользователя."""
        chat_id = update.effective_chat.id
        projects = await list_projects(chat_id)
        if not projects:
            await update.message.reply_text(
                "📂 Проектов пока нет.\n\nЗапусти цепочку агентов — проект сохранится автоматически.",
                reply_markup=self._main_keyboard(),
            )
            return
        lines = ["📂 **Проекты:**\n"]
        for p in projects:
            lines.append(f"• {p['name']}")
        await _send_rich(
            self.bot_token, update.effective_chat.id, "\n".join(lines),
            reply_markup_dict=self._main_keyboard().to_dict(),
            reply_to_message_id=update.message.message_id,
        )

    def _help_text(self) -> str:
        return (
            "👩‍💼 **Марта** — единая точка входа в AI Office\n\n"
            "Пиши задачу на русском — я разберусь кому передать и соберу результат.\n\n"
            "📌 **Мои команды:**\n"
            "/status — активные задачи и состояние офиса\n"
            "/history — последние 10 выполненных задач\n"
            "/delegate — явно передать задачу агенту\n"
            "/cancel <id> — отменить задачу из очереди\n"
            "/reset — очистить историю диалога\n\n"
            "Каждый день в 21:05 МСК — дайджест: сколько задач выполнено, "
            "ошибки и таймауты за сутки.\n\n"
            "🔍 **Каспер** — исследования и веб-поиск\n"
            "*«исследуй конкурентов», «найди статистику по рынку»*\n\n"
            "👨‍💻 **Кевин** — разработка и GitHub\n"
            "*«создай репозиторий», «напиши скрипт», «задеплой на Pages»*\n\n"
            "📊 **Питер** — аналитика маркетплейсов WB+Ozon\n"
            "*«отчёт по продажам», «ABC-анализ», «план поставок», «ДРР»*\n\n"
            "✍️ **Элина** — тексты и SEO-контент\n"
            "*«напиши описание товара», «улучши карточку», «создай пост»*\n\n"
            "🗓️ **Алекс** — планирование и push-напоминания\n"
            "*«напомни в 18:00», «составь roadmap»*\n\n"
            "🛒 **Макс** — маркетплейсы WB+Ozon\n"
            "*«синхронизируй данные», «обработай отзывы», «SEO-алерты», «применить цены»*\n\n"
            "🤝 **Тина** — тендеры и B2B продажи\n"
            "*«найди тендер», «подготовь КП»*\n\n"
            "🎨 **Дэн** — генерация изображений *(заморожен)*\n"
            "🗞️ **Ева** — дайджест Telegram-каналов *(заморожена)*\n\n"
            "💡 **Примеры запросов Марте:**\n"
            "• «исследуй рынок умных колонок и напиши пост»\n"
            "• «отчёт по продажам за 2 недели»\n"
            "• «напомни проверить склад в 15:00»"
        )

    # ── Inline-меню ──────────────────────────────────────────────────────────

    _MARTA_MENU_KEYBOARD = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Аналитика",      callback_data="mmenu:analytics"),
            InlineKeyboardButton("🛒 Маркетплейсы",   callback_data="mmenu:market"),
        ],
        [
            InlineKeyboardButton("🔄 Синхронизация",  callback_data="mmenu:sync"),
            InlineKeyboardButton("✍️ Контент",         callback_data="mmenu:content"),
        ],
        [
            InlineKeyboardButton("👨‍💻 Разработка",     callback_data="mmenu:dev"),
            InlineKeyboardButton("🏛️ Тендеры",        callback_data="mmenu:tenders"),
        ],
        [
            InlineKeyboardButton("⚙️ Офис",           callback_data="mmenu:office"),
        ],
    ])

    # command_key → (agent_key, payload) — команды без обязательных аргументов
    _MMENU_ACTIONS: dict[str, tuple[str, str]] = {
        # Питер
        "report":       ("peter", "Дай отчёт по продажам WB и Ozon за последние 14 дней"),
        "audit":        ("peter", "Проведи 30-дневный аудит магазина со SWOT-анализом"),
        "drr":          ("peter", "Покажи долю рекламных расходов (ДРР) и ROAS по товарам"),
        "abc":          ("peter", "Проведи ABC-анализ товаров по вкладу в выручку"),
        "funnel":       ("peter", "Покажи воронку конверсии: просмотры → корзина → заказы → выкупы"),
        "returns":      ("peter", "Покажи топ возвратов по ставке и причинам"),
        "supply":       ("peter", "Сделай план поставки по складам и кластерам"),
        "order":        ("peter", "Проверь нужно ли заказывать у поставщика (30/60/90 дней)"),
        "seo_audit":    ("peter", "Проведи SEO-аудит карточек: CTR, позиции, приоритеты правок"),
        "analyze":      ("peter", "Проведи произвольный бизнес-анализ по данным маркетплейсов"),
        # Макс
        "reviews":      ("max", "Обработай новые отзывы на WB и Ozon"),
        "questions":    ("max", "__questions__"),
        "pending":      ("max", "__pending__"),
        "products":     ("max", "__products__"),
        "shop_kpi":     ("max", "__shop_kpi__"),
        "data_status":  ("max", "__data_status__"),
        "shops":        ("max", "__shops__"),
        "seo_check":    ("max", "__seo_check__"),
        "bid_adjust":   ("max", "__bid_adjust__"),
        "campaigns":    ("max", "__campaigns__"),
        "promotions":   ("max", "__promotions__"),
        "new_campaign": ("max", "__new_campaign__"),
        "margin":       ("max", "__margin__"),
        "apply_prices": ("max", "применить рекомендованные цены от Питера — apply_prices"),
        "sync":         ("max", "__sync__"),
        "sync_fin":     ("max", "__sync_fin__"),
        "sync_adv":     ("max", "__sync_adv__"),
        "sync_funnel":  ("max", "__sync_funnel__"),
        "sync_returns": ("max", "__sync_returns__"),
        "sync_cards":   ("max", "__sync_cards__"),
        "sync_keywords":("max", "__sync_keywords__"),
        # Кевин
        "code":  ("kevin", "Реши задачу разработки"),
        "plan":  ("kevin", "Составь план реализации фичи"),
        # Элина
        "write":       ("elina", "Напиши текст"),
        "post":        ("elina", "Напиши пост для маркетплейса или соцсетей"),
        "seo_elina":   ("elina", "SEO-оптимизация карточки товара"),
        # Каспер
        "research": ("kasper", "Исследуй тему"),
        # Алекс
        "plans":  ("alex", "__plans__"),
        "remind": ("alex", "Установи напоминание"),
        # Тина
        "tenders":        ("tina", "tenders"),
        "tenders_report": ("tina", "tenders_report"),
    }

    _MMENU_AGENT_LABEL: dict[str, str] = {
        "peter": "📊 Питер", "max": "🛒 Макс",
        "tina": "🏛️ Тина",  "alex": "🗓️ Алекс",
    }

    # (заголовок, строки InlineKeyboard) для каждого раздела
    _MARTA_MENU_SECTIONS: dict[str, tuple[str, list]] = {
        "analytics": (
            "📊 <b>Аналитика — Питер</b>\nВыбери отчёт:",
            [
                [
                    InlineKeyboardButton("📊 Отчёт продаж",  callback_data="mmenu_run:report"),
                    InlineKeyboardButton("🔍 Аудит",          callback_data="mmenu_run:audit"),
                ],
                [
                    InlineKeyboardButton("📣 ДРР / ROAS",         callback_data="mmenu_run:drr"),
                    InlineKeyboardButton("📊 Воронка (анализ)",   callback_data="mmenu_run:funnel"),
                ],
                [
                    InlineKeyboardButton("📦 Поставки",           callback_data="mmenu_run:supply"),
                    InlineKeyboardButton("📬 Заказ",              callback_data="mmenu_run:order"),
                ],
                [
                    InlineKeyboardButton("🔤 ABC-анализ",         callback_data="mmenu_run:abc"),
                    InlineKeyboardButton("📊 Возвраты (анализ)",  callback_data="mmenu_run:returns"),
                ],
                [
                    InlineKeyboardButton("🔤 SEO-аудит (Питер)", callback_data="mmenu_run:seo_audit"),
                    InlineKeyboardButton("💬 Свободный вопрос",   callback_data="mmenu_run:analyze"),
                ],
                [InlineKeyboardButton("◀️ Назад",             callback_data="mmenu:back")],
            ],
        ),
        "market": (
            "🛒 <b>Маркетплейсы — Макс</b>\nВыбери действие:",
            [
                [InlineKeyboardButton("🔔 Обработать отзывы", callback_data="mmenu_run:reviews")],
                [
                    InlineKeyboardButton("❓ Вопросы",         callback_data="mmenu_run:questions"),
                    InlineKeyboardButton("⏳ Модерация",       callback_data="mmenu_run:pending"),
                ],
                [
                    InlineKeyboardButton("📊 KPI магазина",    callback_data="mmenu_run:shop_kpi"),
                    InlineKeyboardButton("🗄️ Статус данных",   callback_data="mmenu_run:data_status"),
                ],
                [
                    InlineKeyboardButton("🏪 Магазины",        callback_data="mmenu_run:shops"),
                    InlineKeyboardButton("📦 Товары",          callback_data="mmenu_run:products"),
                ],
                [
                    InlineKeyboardButton("🔻 Позиции ключей",  callback_data="mmenu_run:seo_check"),
                    InlineKeyboardButton("🎯 Ставки рекламы",  callback_data="mmenu_run:bid_adjust"),
                ],
                [
                    InlineKeyboardButton("📣 Кампании Ozon",   callback_data="mmenu_run:campaigns"),
                    InlineKeyboardButton("🎁 Акции Ozon",      callback_data="mmenu_run:promotions"),
                    InlineKeyboardButton("➕ Новая кампания",   callback_data="mmenu_run:new_campaign"),
                ],
                [
                    InlineKeyboardButton("📐 Маржа товара",   callback_data="mmenu_run:margin"),
                    InlineKeyboardButton("💲 Применить цены", callback_data="mmenu_run:apply_prices"),
                ],
                [InlineKeyboardButton("◀️ Назад",             callback_data="mmenu:back")],
            ],
        ),
        "sync": (
            "🔄 <b>Синхронизация данных — Макс</b>\nВыбери тип:\n<i>💡 Финансы и реклама синхронизируются автоматически ночью.</i>",
            [
                [InlineKeyboardButton("🔄 Полный синк — заказы · остатки · продажи", callback_data="mmenu_run:sync")],
                [
                    InlineKeyboardButton("💰 Финотчёт",   callback_data="mmenu_run:sync_fin"),
                    InlineKeyboardButton("📣 Реклама",    callback_data="mmenu_run:sync_adv"),
                ],
                [
                    InlineKeyboardButton("🔄 Воронка (данные)",   callback_data="mmenu_run:sync_funnel"),
                    InlineKeyboardButton("🔄 Возвраты (данные)",  callback_data="mmenu_run:sync_returns"),
                ],
                [
                    InlineKeyboardButton("🃏 Карточки",   callback_data="mmenu_run:sync_cards"),
                    InlineKeyboardButton("🔑 Ключевые слова", callback_data="mmenu_run:sync_keywords"),
                ],
                [InlineKeyboardButton("◀️ Назад",        callback_data="mmenu:back")],
            ],
        ),
        "content": (
            "✍️ <b>Контент и исследования</b>\nВыбери действие:",
            [
                [InlineKeyboardButton("✍️ Написать текст (Элина)",    callback_data="mmenu_run:write")],
                [
                    InlineKeyboardButton("📝 Пост",                    callback_data="mmenu_run:post"),
                    InlineKeyboardButton("✍️ SEO тексты (Элина)",       callback_data="mmenu_run:seo_elina"),
                ],
                [InlineKeyboardButton("🔍 Исследование (Каспер)",      callback_data="mmenu_run:research")],
                [InlineKeyboardButton("⏰ Напоминание (Алекс)",        callback_data="mmenu_run:remind")],
                [InlineKeyboardButton("◀️ Назад",                      callback_data="mmenu:back")],
            ],
        ),
        "dev": (
            "👨‍💻 <b>Разработка — Кевин</b>\nВыбери действие:",
            [
                [InlineKeyboardButton("👨‍💻 Написать код / скрипт",   callback_data="mmenu_run:code")],
                [InlineKeyboardButton("📋 Спланировать фичу",         callback_data="mmenu_run:plan")],
                [InlineKeyboardButton("◀️ Назад",                     callback_data="mmenu:back")],
            ],
        ),
        "tenders": (
            "🏛️ <b>Тендеры 44-ФЗ — Тина</b>\nВыбери действие:\n<i>💡 Тина присылает дайджест автоматически в 08:00 МСК.</i>",
            [
                [InlineKeyboardButton("🏛️ Дайджест тендеров",    callback_data="mmenu_run:tenders")],
                [InlineKeyboardButton("📊 Аналитика по тендерам", callback_data="mmenu_run:tenders_report")],
                [InlineKeyboardButton("◀️ Назад",                 callback_data="mmenu:back")],
            ],
        ),
        "office": (
            "⚙️ <b>Офис — Марта + Алекс</b>\nВыбери действие:",
            [
                [InlineKeyboardButton("📋 Активные планы",  callback_data="mmenu_run:plans")],
                [
                    InlineKeyboardButton("📊 Статус очереди", callback_data="mmenu_run:queue_status"),
                    InlineKeyboardButton("📜 История задач",  callback_data="mmenu_run:queue_history"),
                ],
                [
                    InlineKeyboardButton("🔴 Ошибки (24ч)",   callback_data="mmenu_run:logs_all"),
                    InlineKeyboardButton("📡 Ошибки WB рекл", callback_data="mmenu_run:logs_adv"),
                ],
                [InlineKeyboardButton("◀️ Назад",           callback_data="mmenu:back")],
            ],
        ),
    }

    _MARTA_MENU_HEADER = "🏢 <b>AI Office — Быстрое меню</b>\n\nВыбери раздел:"

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/menu — быстрое меню всех команд AI Office."""
        await update.message.reply_text(
            self._MARTA_MENU_HEADER,
            parse_mode="HTML",
            reply_markup=self._MARTA_MENU_KEYBOARD,
        )

    async def _handle_marta_menu_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Inline-кнопки меню Марты (mmenu:* и mmenu_run:*)."""
        query = update.callback_query
        await query.answer()
        data = query.data
        chat_id = query.message.chat_id

        # ── Назад → главное меню ─────────────────────────────────────────
        if data == "mmenu:back":
            await query.edit_message_text(
                self._MARTA_MENU_HEADER,
                parse_mode="HTML",
                reply_markup=self._MARTA_MENU_KEYBOARD,
            )
            return

        # ── Открыть раздел ───────────────────────────────────────────────
        if data.startswith("mmenu:"):
            section = data.split(":", 1)[1]
            info = self._MARTA_MENU_SECTIONS.get(section)
            if not info:
                await query.answer("❓ Раздел не найден", show_alert=True)
                return
            header, rows = info
            await query.edit_message_text(
                header, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return

        # ── Выполнить действие (mmenu_run:cmd) ───────────────────────────
        if data.startswith("mmenu_run:"):
            cmd = data.split(":", 1)[1]

            # Статус и история — отвечаем локально, без очереди
            if cmd == "queue_status":
                tasks = await get_active_tasks()
                _STATUS_EMOJI = {
                    "queued": "🕐", "acknowledged": "👀",
                    "running": "⚙️", "failed": "🔴", "timeout": "⏱️",
                }
                _AGENT_EMOJI = {
                    "peter": "📊", "max": "🛒", "elina": "✍️", "alex": "🗓️",
                    "kasper": "🔍", "kevin": "👨‍💻", "tina": "🏛️", "marta": "👩‍💼",
                }
                if not tasks:
                    text = "✅ <b>Статус очереди</b>\n\nВсе агенты свободны."
                else:
                    lines = ["📋 <b>Статус очереди</b>\n"]
                    for t in tasks:
                        ae = _AGENT_EMOJI.get(t["assigned_agent"], "🤖")
                        se = _STATUS_EMOJI.get(t["status"], "❓")
                        short = t["payload"][:60] + ("…" if len(t["payload"]) > 60 else "")
                        lines.append(f"{ae} {se} <b>{t['assigned_agent']}</b>: {short}")
                    text = "\n".join(lines)
                await query.message.reply_text(text, parse_mode="HTML")
                return

            if cmd == "queue_history":
                rows_db = await get_recent_tasks(limit=10)
                if not rows_db:
                    text = "📜 <b>История задач</b>\n\nЗадач пока нет."
                else:
                    lines = ["📜 <b>Последние задачи</b>\n"]
                    _AGENT_EMOJI = {
                        "peter": "📊", "max": "🛒", "elina": "✍️", "alex": "🗓️",
                        "kasper": "🔍", "kevin": "👨‍💻", "tina": "🏛️", "marta": "👩‍💼",
                    }
                    for t in rows_db:
                        ae = _AGENT_EMOJI.get(t["assigned_agent"], "🤖")
                        short = t["payload"][:60] + ("…" if len(t["payload"]) > 60 else "")
                        lines.append(f"{ae} {t['assigned_agent']}: {short}")
                    text = "\n".join(lines)
                await query.message.reply_text(text, parse_mode="HTML")
                return

            if cmd in ("logs_all", "logs_adv"):
                kw = "adv" if cmd == "logs_adv" else None
                text = await self._render_logs(kw)
                await query.message.reply_text(
                    text, parse_mode="HTML", reply_markup=self._LOGS_KEYBOARD,
                )
                return

            # Все остальные команды — передаём агенту
            action = self._MMENU_ACTIONS.get(cmd)
            if not action:
                await query.answer("❓ Команда не найдена", show_alert=True)
                return
            agent_key, payload = action
            await enqueue_task(
                assigned_agent=agent_key,
                payload=payload,
                from_agent="marta",
                chat_id=chat_id,
                priority=0,
            )
            label = self._MMENU_AGENT_LABEL.get(agent_key, agent_key)
            await query.message.reply_text(
                f"⏳ Передала задачу {label} — пришлю результат когда готово."
            )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Главное меню и помощь"),
            BotCommand("menu", "🗂️ Быстрое меню всех команд"),
            BotCommand("status", "Состояние офиса и активные задачи"),
            BotCommand("history", "Последние 10 задач"),
            BotCommand("logs", "🔴 Ошибки за 24ч (/logs adv — только реклама)"),
            # ── Питер ─────────────────────────────────────────────────────
            BotCommand("report", "📊 Отчёт по продажам"),
            BotCommand("order", "📬 Заказать ли у поставщика (30/60/90 дней)"),
            BotCommand("supply", "🚚 План поставки по складам"),
            BotCommand("drr", "💸 Доля рекламных расходов"),
            BotCommand("abc", "🔠 ABC-анализ товаров"),
            BotCommand("funnel", "🔽 Воронка конверсии"),
            BotCommand("returns", "↩️ Анализ возвратов"),
            BotCommand("audit", "🔍 30-дневный аудит магазина"),
            BotCommand("seo_audit", "🔤 SEO-аудит карточек"),
            BotCommand("analyze", "🧠 Произвольный бизнес-анализ"),
            # ── Макс ──────────────────────────────────────────────────────
            BotCommand("sync", "🔄 Синхронизировать данные"),
            BotCommand("sync_fin", "💰 Финансовый отчёт за 90 дней"),
            BotCommand("sync_adv", "📣 Статистика рекламы"),
            BotCommand("sync_funnel", "🔽 Синхронизация воронки"),
            BotCommand("sync_returns", "↩️ Синхронизация возвратов"),
            BotCommand("sync_cards", "🃏 Контент карточек"),
            BotCommand("sync_keywords", "🔑 Позиции ключевых слов"),
            BotCommand("reviews", "⭐ Обработать отзывы"),
            BotCommand("questions", "❓ Вопросы покупателей"),
            BotCommand("pending", "⏳ Ожидают модерации"),
            BotCommand("products", "📦 Список товаров"),
            BotCommand("shop_kpi", "📊 KPI магазина"),
            BotCommand("data_status", "🗂️ Статус данных"),
            BotCommand("shops", "🏪 Мои магазины"),
            BotCommand("seo_check", "🔻 Падение позиций ключей"),
            BotCommand("bid_adjust", "🎯 Рекомендации по ставкам"),
            BotCommand("campaigns",    "📣 Управление кампаниями Ozon"),
            BotCommand("promotions",   "🎁 Акции Ozon — анализ маржи"),
            BotCommand("new_campaign", "➕ Создать кампанию Ozon из топ-товаров"),
            BotCommand("margin", "📐 Проверить маржу товара"),
            BotCommand("apply_prices", "✅ Применить рекомендованные цены"),
            # ── Другие агенты ─────────────────────────────────────────────
            BotCommand("code", "👨‍💻 Кевин: написать код"),
            BotCommand("plan", "📋 Кевин: спланировать фичу"),
            BotCommand("write", "✍️ Элина: написать текст"),
            BotCommand("post", "📝 Элина: написать пост"),
            BotCommand("research", "🔍 Каспер: исследовать тему"),
            BotCommand("plans",  "📋 Алекс: мои планы и задачи"),
            BotCommand("remind", "⏰ Алекс: напоминание"),
            BotCommand("tenders", "🏛️ Тина: дайджест тендеров"),
            BotCommand("tenders_report", "📑 Тина: аналитика тендеров"),
            # ── Служебные ─────────────────────────────────────────────────
            BotCommand("delegate", "Передать задачу конкретному агенту"),
            BotCommand("cancel", "Отменить задачу из очереди"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    # ------------------------------------------------------------------ #
    #  Proxy-команды — ярлыки для других агентов                         #
    # ------------------------------------------------------------------ #

    async def _proxy_cmd(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        agent_key: str,
        default_task: str,
    ) -> None:
        """Общий обработчик proxy-команд: ставит задачу в очередь нужного агента."""
        chat_id = update.effective_chat.id
        args_text = " ".join(context.args) if context.args else ""
        task = args_text or default_task
        await enqueue_task(
            assigned_agent=agent_key,
            payload=task,
            from_agent="marta",
            chat_id=chat_id,
            priority=0,
        )
        _AGENT_LABEL = {
            "peter": "📊 Питер", "max": "🛒 Макс", "kasper": "🔍 Каспер",
            "elina": "✍️ Элина", "alex": "🗓️ Алекс", "kevin": "👨‍💻 Кевин",
            "tina": "🏛️ Тина",
        }
        label = _AGENT_LABEL.get(agent_key, agent_key)
        await _send_rich(
            self.bot_token, update.effective_chat.id,
            f"✅ Задача передана {label}\n`{task[:80]}`",
            reply_markup_dict=self._main_keyboard().to_dict(),
            reply_to_message_id=update.message.message_id,
        )

    # ── Питер ────────────────────────────────────────────────────────────────

    async def cmd_proxy_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "Дай сводный отчёт по продажам WB и Ozon за последние 7 дней")

    async def cmd_proxy_order(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = " ".join(context.args) if context.args else ""
        await self._proxy_cmd(update, context, "peter", f"__order__ {args}".strip())

    async def cmd_proxy_supply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "план поставки по складам и регионам")

    async def cmd_proxy_drr(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = " ".join(context.args) if context.args else ""
        period = args if args else "за 30 дней"
        await self._proxy_cmd(update, context, "peter", f"анализ доли рекламных расходов ДРР {period}")

    async def cmd_proxy_abc(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "ABC-анализ товаров по вкладу в выручку за 30 дней")

    async def cmd_proxy_funnel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "воронка конверсии карточек: просмотры, корзина, заказы, выкупы")

    async def cmd_proxy_returns(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "анализ возвратов по товарам за 30 дней: ставка и причины")

    async def cmd_proxy_audit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "аудит магазина за 30 дней: здоровье показателей, SWOT, KPI")

    async def cmd_proxy_seo_audit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "seo аудит карточек: CTR, позиции, приоритеты улучшений")

    async def cmd_proxy_analyze(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "peter", "Проведи произвольный бизнес-анализ по данным маркетплейсов")

    # ── Макс ─────────────────────────────────────────────────────────────────

    async def cmd_proxy_reviews(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "Обработай новые отзывы на маркетплейсах")

    async def cmd_proxy_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync__")

    async def cmd_proxy_sync_fin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync_fin__")

    async def cmd_proxy_sync_adv(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync_adv__")

    async def cmd_proxy_sync_funnel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync_funnel__")

    async def cmd_proxy_sync_returns(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync_returns__")

    async def cmd_proxy_sync_cards(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync_cards__")

    async def cmd_proxy_sync_keywords(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync_keywords__")

    async def cmd_proxy_sync_sku(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__sync_sku__")

    async def cmd_proxy_questions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__questions__")

    async def cmd_proxy_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__pending__")

    async def cmd_proxy_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__products__")

    async def cmd_proxy_shop_kpi(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__shop_kpi__")

    async def cmd_proxy_data_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__data_status__")

    async def cmd_proxy_shops(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__shops__")

    async def cmd_proxy_seo_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__seo_check__")

    async def cmd_proxy_bid_adjust(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__bid_adjust__")

    async def cmd_proxy_promotions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает акции Ozon с анализом маржи через Марту."""
        chat_id = update.effective_chat.id
        max_agent = getattr(self, "_max_agent", None)
        if max_agent is None:
            await self._proxy_cmd(update, context, "max", "__promotions__")
            return
        await update.message.reply_text("⏳ Загружаю акции Ozon…")
        await max_agent.cmd_promotions.__func__(max_agent, update, context)

    async def _handle_promo_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка кнопок promo: через бот Марты."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent._handle_promo_callback(update, context)
        else:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(
                query.message.text + "\n\n❌ Агент Макс недоступен", parse_mode="HTML"
            )

    async def cmd_proxy_campaigns(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показывает кампании Ozon с кнопками через Марту (прямой вызов без очереди)."""
        chat_id = update.effective_chat.id
        max_agent = getattr(self, "_max_agent", None)
        if max_agent is None:
            await self._proxy_cmd(update, context, "max", "__campaigns__")
            return

        await update.message.reply_text("⏳ Загружаю кампании…")
        cards = await max_agent._get_campaign_cards(chat_id)
        for text, kb in cards:
            markup_dict = kb.to_dict() if kb else None
            await _send_rich(self.bot_token, chat_id, text, reply_markup_dict=markup_dict)

    async def _handle_camp_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка кнопок camp: через бот Марты — делегируем Максу (включая подтверждение удаления)."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent._handle_camp_callback(update, context)
        else:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(query.message.text + "\n\n❌ Агент Макс недоступен", parse_mode="HTML")

    async def _handle_ozbid_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка кнопок ozbid: через бот Марты — корректировка ставок Ozon per-SKU."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent._handle_ozbid_callback(update, context)
        else:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(query.message.text + "\n\n❌ Агент Макс недоступен", parse_mode="HTML")

    async def cmd_proxy_new_campaign(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Создать новую рекламную кампанию Ozon через Марту."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_new_campaign.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__new_campaign__")

    async def _handle_campnew_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка кнопок campnew: через бот Марты."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent._handle_campnew_callback(update, context)
        else:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(query.message.text + "\n\n❌ Агент Макс недоступен", parse_mode="HTML")

    async def cmd_proxy_margin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "__margin__")

    async def cmd_proxy_apply_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "max", "применить рекомендованные цены от Питера — apply_prices")

    # ── Онбординг и управление магазинами (все входы через Марту) ────────────

    async def cmd_proxy_add_shop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Добавление нового магазина WB/Ozon — делегируем Максу напрямую (интерактивный флоу)."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_add_shop.__func__(max_agent, update, context)
        else:
            await update.message.reply_text("⚠️ Агент Макс недоступен, попробуй позже.")

    async def cmd_proxy_set_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Привязать Ozon Performance credentials — делегируем Максу."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_set_performance.__func__(max_agent, update, context)
        else:
            await update.message.reply_text("⚠️ Агент Макс недоступен, попробуй позже.")

    async def _handle_onboard_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка кнопок онбординга onboard: — делегируем Максу."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent._handle_onboard_callback(update, context)
        else:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text("❌ Агент Макс недоступен")

    async def cmd_proxy_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Справка — делегируем Максу (у него полный список команд)."""
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_help.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__help__")

    async def cmd_proxy_dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_dashboard.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__dashboard__")

    async def cmd_proxy_map(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_map.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__map__")

    async def cmd_proxy_camp(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_camp.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__camp__")

    async def cmd_proxy_cost(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_cost_wizard.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__cost__")

    async def cmd_proxy_reprice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_reprice.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__reprice__")

    async def cmd_proxy_add_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_add.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__add__")

    async def cmd_proxy_sync_promotions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_sync_promotions.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__sync_promotions__")

    async def cmd_proxy_reset_checked(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_reset_checked.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__reset_checked__")

    async def cmd_proxy_reset_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        max_agent = getattr(self, "_max_agent", None)
        if max_agent:
            await max_agent.cmd_reset_orders.__func__(max_agent, update, context)
        else:
            await self._proxy_cmd(update, context, "max", "__reset_orders__")

    # ── Кевин ────────────────────────────────────────────────────────────────

    async def cmd_proxy_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = " ".join(context.args) if context.args else ""
        await self._proxy_cmd(update, context, "kevin", f"Напиши код: {args}" if args else "Реши задачу разработки")

    async def cmd_proxy_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = " ".join(context.args) if context.args else ""
        await self._proxy_cmd(update, context, "kevin", f"Составь план реализации: {args}" if args else "Составь план реализации фичи")

    # ── Элина ────────────────────────────────────────────────────────────────

    async def cmd_proxy_write(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "elina", "Напиши текст")

    async def cmd_proxy_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = " ".join(context.args) if context.args else ""
        await self._proxy_cmd(update, context, "elina", f"Напиши пост на тему: {args}" if args else "Напиши пост для маркетплейса или соцсетей")

    async def cmd_proxy_seo_elina(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """SEO через Марту: если Элина доступна — вызываем напрямую (с кнопкой Ozon), иначе через очередь."""
        elina = getattr(self, "_elina_agent", None)
        if elina is not None:
            await elina.cmd_seo.__func__(elina, update, context)
        else:
            args = " ".join(context.args) if context.args else ""
            await self._proxy_cmd(update, context, "elina", f"SEO-оптимизация карточки товара: {args}" if args else "SEO-оптимизация карточки товара")

    async def _handle_seoapp_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка кнопок seoapp: через бот Марты — применить SEO описание к Ozon."""
        elina = getattr(self, "_elina_agent", None)
        if elina:
            await elina._handle_seo_apply_callback(update, context)
        else:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(query.message.text + "\n\n❌ Агент Элина недоступен", parse_mode="HTML")

    # ── Алекс ────────────────────────────────────────────────────────────────

    async def cmd_proxy_remind(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "alex", "Установи напоминание")

    async def cmd_proxy_testpush(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "alex", "testpush")

    async def cmd_proxy_plans(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = " ".join(context.args) if context.args else ""
        if args:
            await self._proxy_cmd(update, context, "alex", args)
        else:
            await self._proxy_cmd(update, context, "alex", "__plans__")

    # ── Каспер ───────────────────────────────────────────────────────────────

    async def cmd_proxy_research(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "kasper", "Исследуй тему")

    # ── Тина ─────────────────────────────────────────────────────────────────

    async def cmd_proxy_tenders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "tina", "tenders")

    async def cmd_proxy_tenders_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._proxy_cmd(update, context, "tina", "tenders_report")

    async def send_daily_digest(self, chat_id: int) -> None:
        """Ежедневный дайджест задач — отправляется в 21:00 МСК (18:00 UTC)."""
        from task_queue import get_daily_task_summary
        stats = await get_daily_task_summary(hours=24)
        if not stats:
            return

        emoji_map = {
            "marta": "👩‍💼", "kevin": "👨‍💻", "kasper": "🔍",
            "peter": "📊", "elina": "✍️", "alex": "🗓️",
            "dan": "🎨", "eva": "📰", "max": "🛒",
        }

        total_ok = sum(v["completed"] for v in stats.values())
        total_fail = sum(v["failed"] + v["timeout"] for v in stats.values())

        lines = [
            f"📋 <b>Дайджест за сегодня</b>\n",
            f"✅ Выполнено: <b>{total_ok}</b>  ❌ Ошибок: <b>{total_fail}</b>\n",
        ]

        # Активность по агентам
        active = {k: v for k, v in stats.items() if v["completed"] + v["failed"] + v["timeout"] > 0}
        if active:
            lines.append("<b>По агентам:</b>")
            for agent, v in sorted(active.items(), key=lambda x: x[1]["completed"], reverse=True):
                icon = emoji_map.get(agent, "🤖")
                ok = v["completed"]
                fail = v["failed"] + v["timeout"]
                line = f"{icon} {agent}: {ok} ✅"
                if fail:
                    line += f" / {fail} ❌"
                lines.append(line)

        # Ошибки — топ-3
        all_errors = []
        for agent, v in stats.items():
            for err in v["errors"]:
                all_errors.append(f"<i>{agent}:</i> {err}")
        if all_errors:
            lines.append("\n<b>Ошибки:</b>")
            lines.extend(all_errors[:3])
            if len(all_errors) > 3:
                lines.append(f"<i>…ещё {len(all_errors) - 3}</i>")

        try:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"[Марта/digest] chat_id={chat_id}: {e}")

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("delegate", self.cmd_delegate))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        self.app.add_handler(CommandHandler("history", self.cmd_history))
        self.app.add_handler(CommandHandler("logs", self.cmd_logs))
        # ── Proxy-команды Питера ─────────────────────────────────────────
        self.app.add_handler(CommandHandler("report", self.cmd_proxy_report))
        self.app.add_handler(CommandHandler("order", self.cmd_proxy_order))
        self.app.add_handler(CommandHandler("supply", self.cmd_proxy_supply))
        self.app.add_handler(CommandHandler("drr", self.cmd_proxy_drr))
        self.app.add_handler(CommandHandler("abc", self.cmd_proxy_abc))
        self.app.add_handler(CommandHandler("funnel", self.cmd_proxy_funnel))
        self.app.add_handler(CommandHandler("returns", self.cmd_proxy_returns))
        self.app.add_handler(CommandHandler("audit", self.cmd_proxy_audit))
        self.app.add_handler(CommandHandler("seo_audit", self.cmd_proxy_seo_audit))
        self.app.add_handler(CommandHandler("analyze", self.cmd_proxy_analyze))
        # ── Proxy-команды Макса ──────────────────────────────────────────
        self.app.add_handler(CommandHandler("reviews", self.cmd_proxy_reviews))
        self.app.add_handler(CommandHandler("sync", self.cmd_proxy_sync))
        self.app.add_handler(CommandHandler("sync_fin", self.cmd_proxy_sync_fin))
        self.app.add_handler(CommandHandler("sync_adv", self.cmd_proxy_sync_adv))
        self.app.add_handler(CommandHandler("sync_funnel", self.cmd_proxy_sync_funnel))
        self.app.add_handler(CommandHandler("sync_returns", self.cmd_proxy_sync_returns))
        self.app.add_handler(CommandHandler("sync_cards", self.cmd_proxy_sync_cards))
        self.app.add_handler(CommandHandler("sync_keywords", self.cmd_proxy_sync_keywords))
        self.app.add_handler(CommandHandler("sync_sku", self.cmd_proxy_sync_sku))
        self.app.add_handler(CommandHandler("questions", self.cmd_proxy_questions))
        self.app.add_handler(CommandHandler("pending", self.cmd_proxy_pending))
        self.app.add_handler(CommandHandler("products", self.cmd_proxy_products))
        self.app.add_handler(CommandHandler("shop_kpi", self.cmd_proxy_shop_kpi))
        self.app.add_handler(CommandHandler("data_status", self.cmd_proxy_data_status))
        self.app.add_handler(CommandHandler("shops", self.cmd_proxy_shops))
        self.app.add_handler(CommandHandler("seo_check", self.cmd_proxy_seo_check))
        self.app.add_handler(CommandHandler("bid_adjust", self.cmd_proxy_bid_adjust))
        self.app.add_handler(CommandHandler("campaigns",    self.cmd_proxy_campaigns))
        self.app.add_handler(CommandHandler("promotions",   self.cmd_proxy_promotions))
        self.app.add_handler(CommandHandler("new_campaign", self.cmd_proxy_new_campaign))
        self.app.add_handler(
            CallbackQueryHandler(self._handle_camp_callback, pattern=r"^camp:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_ozbid_callback, pattern=r"^ozbid:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_campnew_callback, pattern=r"^campnew:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_seoapp_callback, pattern=r"^seoapp:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_promo_callback, pattern=r"^promo:")
        )
        self.app.add_handler(CommandHandler("margin", self.cmd_proxy_margin))
        self.app.add_handler(CommandHandler("apply_prices", self.cmd_proxy_apply_prices))
        # ── Онбординг и управление магазинами (все входы через Марту) ────
        self.app.add_handler(CommandHandler("add_shop",          self.cmd_proxy_add_shop))
        self.app.add_handler(CommandHandler("set_performance",   self.cmd_proxy_set_performance))
        self.app.add_handler(CommandHandler("help",              self.cmd_proxy_help))
        self.app.add_handler(CommandHandler("dashboard",         self.cmd_proxy_dashboard))
        self.app.add_handler(CommandHandler("map",               self.cmd_proxy_map))
        self.app.add_handler(CommandHandler("camp",              self.cmd_proxy_camp))
        self.app.add_handler(CommandHandler("cost",              self.cmd_proxy_cost))
        self.app.add_handler(CommandHandler("reprice",           self.cmd_proxy_reprice))
        self.app.add_handler(CommandHandler("add",               self.cmd_proxy_add_product))
        self.app.add_handler(CommandHandler("sync_promotions",   self.cmd_proxy_sync_promotions))
        self.app.add_handler(CommandHandler("reset_checked",     self.cmd_proxy_reset_checked))
        self.app.add_handler(CommandHandler("reset_orders",      self.cmd_proxy_reset_orders))
        self.app.add_handler(
            CallbackQueryHandler(self._handle_onboard_callback, pattern=r"^onboard:")
        )
        # ── Proxy-команды других агентов ─────────────────────────────────
        self.app.add_handler(CommandHandler("code", self.cmd_proxy_code))
        self.app.add_handler(CommandHandler("plan", self.cmd_proxy_plan))
        self.app.add_handler(CommandHandler("write", self.cmd_proxy_write))
        self.app.add_handler(CommandHandler("post", self.cmd_proxy_post))
        self.app.add_handler(CommandHandler("seo", self.cmd_proxy_seo_elina))
        self.app.add_handler(CommandHandler("research", self.cmd_proxy_research))
        self.app.add_handler(CommandHandler("remind",   self.cmd_proxy_remind))
        self.app.add_handler(CommandHandler("testpush", self.cmd_proxy_testpush))
        self.app.add_handler(CommandHandler("plans",    self.cmd_proxy_plans))
        self.app.add_handler(CommandHandler("tenders", self.cmd_proxy_tenders))
        self.app.add_handler(CommandHandler("tenders_report", self.cmd_proxy_tenders_report))
        # ── Меню ─────────────────────────────────────────────────────────
        self.app.add_handler(CommandHandler("menu", self.cmd_menu))
        # ── Callbacks Марты ──────────────────────────────────────────────
        self.app.add_handler(CallbackQueryHandler(
            self._handle_marta_menu_callback,
            pattern="^mmenu",
        ))
        self.app.add_handler(CallbackQueryHandler(
            self._handle_chain_callback,
            pattern="^chain_(confirm|cancel)$",
        ))
        self.app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.app.add_handler(CallbackQueryHandler(self._handle_image_action, pattern="^img_action:"))
        self.app.add_handler(CallbackQueryHandler(self._handle_logs_callback, pattern="^logs:"))
        self.app.add_handler(CallbackQueryHandler(self._handle_infographic_callback, pattern="^infographic_"))
