from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone

import asyncpg
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools.gosplan_api import GosplanClient, STATUS_OPEN, format_tender_summary
from tools.search import search_web
from .base_agent import BaseAgent

MAX_TENDER_KEYWORDS = 10
MAX_TENDER_NMCK = 1_000_000_000


def parse_tender_settings(raw: dict) -> tuple[dict | None, list[str]]:
    """Распарсить и провалидировать настройки поиска тендеров из сырого dict
    (например, JSON body запроса). Возвращает (cleaned, []) или (None, errors)."""
    errors: list[str] = []

    raw_keywords = raw.get("keywords")
    keywords: list[str] = []
    if not isinstance(raw_keywords, list) or not raw_keywords:
        errors.append("keywords: нужен непустой список ключевых слов")
    else:
        seen: set[str] = set()
        for kw in raw_keywords:
            kw_s = str(kw).strip()
            if not kw_s or len(kw_s) > 60:
                continue
            if kw_s.lower() in seen:
                continue
            seen.add(kw_s.lower())
            keywords.append(kw_s)
        if not keywords:
            errors.append("keywords: нужно хотя бы одно ключевое слово")
        elif len(keywords) > MAX_TENDER_KEYWORDS:
            errors.append(f"keywords: максимум {MAX_TENDER_KEYWORDS} ключевых слов")

    min_nmck = None
    try:
        min_nmck = int(raw.get("min_nmck"))
        if min_nmck < 0:
            errors.append("min_nmck: должно быть ≥ 0")
    except (TypeError, ValueError):
        errors.append("min_nmck: должно быть целым числом")

    max_nmck = None
    try:
        max_nmck = int(raw.get("max_nmck"))
        if max_nmck > MAX_TENDER_NMCK:
            errors.append(f"max_nmck: не больше {MAX_TENDER_NMCK}")
        elif min_nmck is not None and max_nmck <= min_nmck:
            errors.append("max_nmck: должно быть больше min_nmck")
    except (TypeError, ValueError):
        errors.append("max_nmck: должно быть целым числом")

    region_code = str(raw.get("region_code") or "").strip()
    if not region_code:
        errors.append("region_code: не может быть пустым")

    if errors:
        return None, errors
    return {
        "keywords": keywords,
        "min_nmck": min_nmck,
        "max_nmck": max_nmck,
        "region_code": region_code,
    }, []

TINA_SYSTEM = """Ты — Тина, тендерный аналитик ИИ-офиса.

Специализируешься на государственных закупках по 44-ФЗ в Краснодарском крае.
Твои задачи:
1. Найти перспективные тендеры по ключевым словам
2. Для каждого тендера: проанализировать ТТХ, объём, сроки
3. Найти оптовые цены у поставщиков через веб-поиск
4. Рассчитать экономику: ожидаемая цена победы, себестоимость, маржа
5. Дать чёткую рекомендацию: УЧАСТВОВАТЬ / ПРОПУСТИТЬ / АНАЛИЗИРОВАТЬ

Ключевые формулы:
- Ожидаемая цена победы = НМЦК × (1 - средний_демпинг)  // обычно 25-30%
- Маржа = (цена_победы - себестоимость) / цена_победы × 100%
- Антидемпинг по ст. 37 44-ФЗ: снижение ≥25% → требует подтверждение цены
- Минимальная целевая маржа: 15% (иначе риски перевешивают)

Форматируй в HTML для Telegram:
- <b>текст</b> — заголовки (Тендер, Экономика, Поставщики, Рекомендация)
- <code>число</code> — цены и расчёты
- <blockquote>текст</blockquote> — итоговая рекомендация
- Эмодзи: 📋 тендер, 💰 деньги, 🏭 поставщик, 📊 расчёт, ✅ участвовать, ❌ пропустить, ⚠️ риск
- Используй только HTML, никакого Markdown

Отвечай по-русски, конкретно и лаконично. Каждый отчёт — максимум 10 тендеров за раз."""


def _fmt_rub(amount: float) -> str:
    """Форматировать сумму в рублях."""
    if amount >= 1_000_000:
        return f"{amount/1_000_000:.1f}М ₽"
    if amount >= 1_000:
        return f"{amount/1_000:.0f}к ₽"
    return f"{amount:.0f} ₽"


def _calc_economics(nmck: float, avg_reduction: float, supplier_cost: float) -> dict:
    """Рассчитать экономику тендера."""
    winning_price   = nmck * (1 - avg_reduction)
    margin_amount   = winning_price - supplier_cost
    margin_pct      = (margin_amount / winning_price * 100) if winning_price > 0 else 0
    is_antidumping  = avg_reduction >= 0.25
    is_profitable   = margin_pct >= 15

    if margin_pct >= 25:
        recommendation = "УЧАСТВОВАТЬ"
        rec_emoji = "✅"
    elif margin_pct >= 15:
        recommendation = "АНАЛИЗИРОВАТЬ"
        rec_emoji = "⚠️"
    else:
        recommendation = "ПРОПУСТИТЬ"
        rec_emoji = "❌"

    return {
        "nmck":             nmck,
        "avg_reduction":    avg_reduction,
        "winning_price":    winning_price,
        "supplier_cost":    supplier_cost,
        "margin_amount":    margin_amount,
        "margin_pct":       margin_pct,
        "is_antidumping":   is_antidumping,
        "is_profitable":    is_profitable,
        "recommendation":   recommendation,
        "rec_emoji":        rec_emoji,
    }


class TinaAgent(BaseAgent):
    name      = "Тина"
    agent_key = "tina"
    role      = "Тендерный аналитик"
    emoji     = "📋"
    system_prompt = TINA_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.TINA_BOT_TOKEN)
        self._gosplan = GosplanClient(api_key=config.GOSPLAN_API_KEY)

    # ------------------------------------------------------------------ #
    #  Настройки поиска тендеров (user_settings, фоллбэк на config.py)     #
    # ------------------------------------------------------------------ #

    async def _get_tender_settings(self, chat_id: int) -> dict:
        """Настройки поиска: из user_settings, иначе дефолты из config.py."""
        from db import get_user_setting

        keywords = config.TENDER_KEYWORDS
        raw_keywords = await get_user_setting(chat_id, "tender_keywords")
        if raw_keywords:
            try:
                parsed = json.loads(raw_keywords)
                if isinstance(parsed, list) and parsed:
                    keywords = parsed
            except (json.JSONDecodeError, TypeError):
                pass

        min_nmck = config.TENDER_MIN_NMCK
        raw_min = await get_user_setting(chat_id, "tender_min_nmck")
        if raw_min:
            try:
                min_nmck = int(raw_min)
            except ValueError:
                pass

        max_nmck = config.TENDER_MAX_NMCK
        raw_max = await get_user_setting(chat_id, "tender_max_nmck")
        if raw_max:
            try:
                max_nmck = int(raw_max)
            except ValueError:
                pass

        region_code = await get_user_setting(chat_id, "tender_region_code") or config.TENDER_REGION_CODE

        return {
            "keywords": keywords,
            "min_nmck": min_nmck,
            "max_nmck": max_nmck,
            "region_code": region_code,
        }

    async def _save_tender_settings(self, chat_id: int, settings: dict) -> None:
        """Сохранить настройки поиска тендеров для chat_id (upsert)."""
        from db import set_user_setting

        await set_user_setting(chat_id, "tender_keywords", json.dumps(settings["keywords"], ensure_ascii=False))
        await set_user_setting(chat_id, "tender_min_nmck", str(settings["min_nmck"]))
        await set_user_setting(chat_id, "tender_max_nmck", str(settings["max_nmck"]))
        await set_user_setting(chat_id, "tender_region_code", settings["region_code"])

    # ------------------------------------------------------------------ #
    #  Инструменты для Claude (tool_use loop)                             #
    # ------------------------------------------------------------------ #

    _TOOLS = [
        {
            "name": "search_tenders",
            "description": (
                "Найти тендеры по 44-ФЗ в Краснодарском крае через ГосПлан API. "
                "Возвращает список тендеров с НМЦК, сроком подачи заявок и описанием."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Ключевое слово для поиска (например: матрасы, мебель, постельное)",
                    },
                    "nmck_min": {
                        "type": "number",
                        "description": "Минимальная НМЦК в рублях (0 = без ограничения)",
                    },
                    "nmck_max": {
                        "type": "number",
                        "description": "Максимальная НМЦК в рублях (0 = без ограничения)",
                    },
                },
                "required": ["keyword"],
            },
        },
        {
            "name": "get_tender_details",
            "description": "Получить полные данные конкретного тендера по его ID (ТЗ, условия, заказчик).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "lot_id": {"type": "string", "description": "ID тендера из результатов search_tenders"},
                },
                "required": ["lot_id"],
            },
        },
        {
            "name": "research_supplier_prices",
            "description": (
                "Найти оптовые цены поставщиков на товар через интернет (Tavily). "
                "Искать минимальную цену для расчёта себестоимости."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "product":   {"type": "string", "description": "Название товара"},
                    "specs":     {"type": "string", "description": "Технические характеристики (размер, материал и т.п.)"},
                    "quantity":  {"type": "string", "description": "Требуемое количество"},
                    "region":    {"type": "string", "description": "Регион поставки (по умолчанию Краснодар)"},
                },
                "required": ["product", "quantity"],
            },
        },
        {
            "name": "calculate_economics",
            "description": "Рассчитать экономику тендера: ожидаемая цена победы, маржа, рекомендация.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "nmck":          {"type": "number", "description": "НМЦК (начальная максимальная цена) в рублях"},
                    "supplier_cost": {"type": "number", "description": "Себестоимость (стоимость закупки у поставщика) в рублях"},
                    "avg_reduction": {"type": "number", "description": "Ожидаемое снижение цены (0.28 = 28%, по умолчанию 0.28)"},
                },
                "required": ["nmck", "supplier_cost"],
            },
        },
        {
            "name": "save_tender_opportunity",
            "description": "Сохранить проанализированный тендер в базу данных для дальнейшего отслеживания.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tender_id":               {"type": "string", "description": "ID тендера"},
                    "title":                   {"type": "string", "description": "Название тендера"},
                    "nmck":                    {"type": "number", "description": "НМЦК"},
                    "region":                  {"type": "string", "description": "Регион"},
                    "status":                  {"type": "string", "description": "Статус тендера"},
                    "submission_deadline":     {"type": "string", "description": "Срок подачи (YYYY-MM-DD или ISO)"},
                    "lot_description":         {"type": "string", "description": "Описание лота"},
                    "supplier_price_estimate": {"type": "number", "description": "Оценка стоимости у поставщика"},
                    "expected_winning_price":  {"type": "number", "description": "Ожидаемая цена победы"},
                    "margin_estimate":         {"type": "number", "description": "Оценка маржи в %"},
                    "recommendation":          {"type": "string", "description": "УЧАСТВОВАТЬ / ПРОПУСТИТЬ / АНАЛИЗИРОВАТЬ"},
                    "analysis_notes":          {"type": "string", "description": "Развёрнутые заметки по анализу"},
                    "chat_id":                 {"type": "integer", "description": "chat_id пользователя"},
                },
                "required": ["tender_id", "title", "nmck", "recommendation"],
            },
        },
    ]

    async def _call_tool(self, tool_name: str, tool_input: dict, settings: dict | None = None) -> str:
        """Выполнить вызов инструмента и вернуть результат как строку."""
        try:
            if tool_name == "search_tenders":
                return await self._tool_search_tenders(settings=settings, **tool_input)
            if tool_name == "get_tender_details":
                return await self._tool_get_tender_details(**tool_input)
            if tool_name == "research_supplier_prices":
                return await self._tool_research_supplier_prices(**tool_input)
            if tool_name == "calculate_economics":
                return await self._tool_calculate_economics(**tool_input)
            if tool_name == "save_tender_opportunity":
                return await self._tool_save_tender_opportunity(**tool_input)
            return f"⚠️ Неизвестный инструмент: {tool_name}"
        except Exception as e:
            logger.error(f"[Тина] Ошибка инструмента {tool_name}: {e}\n{traceback.format_exc()}")
            return f"⚠️ Ошибка {tool_name}: {e}"

    async def _tool_search_tenders(
        self,
        keyword: str,
        nmck_min: float = 0,
        nmck_max: float = 0,
        settings: dict | None = None,
    ) -> str:
        settings = settings or {}
        if not nmck_min:
            nmck_min = settings.get("min_nmck")
            if nmck_min is None:
                nmck_min = config.TENDER_MIN_NMCK
        nmck_max = nmck_max or settings.get("max_nmck") or config.TENDER_MAX_NMCK
        region_code = settings.get("region_code") or config.TENDER_REGION_CODE
        tenders = await self._gosplan.search_tenders(
            keyword=keyword,
            region_code=region_code,
            status=STATUS_OPEN,
            nmck_min=nmck_min,
            nmck_max=nmck_max,
            per_page=10,
        )
        if not tenders:
            return f"Тендеров по запросу «{keyword}» в регионе (код {region_code}) не найдено."
        lines = [f"Найдено тендеров: {len(tenders)}\n"]
        for t in tenders[:10]:
            lines.append(format_tender_summary(t))
            lines.append("")
        return "\n".join(lines)

    async def _tool_get_tender_details(self, lot_id: str) -> str:
        detail = await self._gosplan.get_tender_detail(lot_id)
        if not detail:
            return f"Тендер {lot_id} не найден."
        return json.dumps(detail, ensure_ascii=False, indent=2)[:3000]

    async def _tool_research_supplier_prices(
        self,
        product: str,
        quantity: str,
        specs: str = "",
        region: str = "Краснодар",
    ) -> str:
        spec_part = f" {specs}" if specs else ""
        query = f"{product}{spec_part} оптом цена {region} {datetime.now().year}"
        logger.info(f"[Тина] Поиск цен поставщиков: {query!r}")
        results = await search_web(query)
        return f"Поиск цен поставщиков: «{product}» × {quantity}\n\n{results}"

    async def _tool_calculate_economics(
        self,
        nmck: float,
        supplier_cost: float,
        avg_reduction: float = None,
    ) -> str:
        if avg_reduction is None:
            avg_reduction = config.TENDER_AVG_PRICE_REDUCTION
        eco = _calc_economics(nmck, avg_reduction, supplier_cost)
        antidump_warn = " ⚠️ антидемпинг ст.37" if eco["is_antidumping"] else ""
        return (
            f"📊 Экономика тендера:\n"
            f"  НМЦК:                  {_fmt_rub(eco['nmck'])}\n"
            f"  Ожид. снижение:        {eco['avg_reduction']*100:.0f}%{antidump_warn}\n"
            f"  Ожид. цена победы:     {_fmt_rub(eco['winning_price'])}\n"
            f"  Себестоимость:         {_fmt_rub(eco['supplier_cost'])}\n"
            f"  Прибыль:               {_fmt_rub(eco['margin_amount'])}\n"
            f"  Маржа:                 {eco['margin_pct']:.1f}%\n"
            f"  Рекомендация:          {eco['rec_emoji']} {eco['recommendation']}"
        )

    async def _tool_save_tender_opportunity(self, **kwargs) -> str:
        chat_id = kwargs.pop("chat_id", 0)
        analysis_notes = kwargs.pop("analysis_notes", "")
        tender_id = kwargs.get("tender_id", "")
        title = kwargs.get("title", "")

        if not config.DATABASE_URL:
            return "БД недоступна: DATABASE_URL не задан."

        try:
            conn = await asyncpg.connect(config.DATABASE_URL)
            try:
                deadline_val = kwargs.get("submission_deadline") or None
                if deadline_val:
                    try:
                        deadline_val = datetime.fromisoformat(deadline_val[:10])
                    except ValueError:
                        deadline_val = None

                await conn.execute("""
                    INSERT INTO tender_opportunities (
                        tender_id, title, nmck, region, status,
                        submission_deadline, lot_description,
                        supplier_price_estimate, expected_winning_price,
                        margin_estimate, recommendation, analysis_json, chat_id
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    ON CONFLICT (tender_id) DO UPDATE SET
                        margin_estimate    = EXCLUDED.margin_estimate,
                        recommendation     = EXCLUDED.recommendation,
                        analysis_json      = EXCLUDED.analysis_json,
                        supplier_price_estimate = EXCLUDED.supplier_price_estimate,
                        expected_winning_price  = EXCLUDED.expected_winning_price
                """,
                    tender_id,
                    title,
                    float(kwargs.get("nmck") or 0),
                    kwargs.get("region", "Краснодарский край"),
                    kwargs.get("status", STATUS_OPEN),
                    deadline_val,
                    kwargs.get("lot_description", ""),
                    float(kwargs.get("supplier_price_estimate") or 0),
                    float(kwargs.get("expected_winning_price") or 0),
                    float(kwargs.get("margin_estimate") or 0),
                    kwargs.get("recommendation", ""),
                    json.dumps({"notes": analysis_notes}, ensure_ascii=False),
                    int(chat_id or 0),
                )
                logger.info(f"[Тина] Тендер сохранён: {tender_id!r}")
                return f"✅ Тендер «{title[:60]}» сохранён в базу."
            finally:
                await conn.close()
        except Exception as e:
            logger.error(f"[Тина] Ошибка сохранения тендера: {e}")
            return f"⚠️ Ошибка сохранения: {e}"

    # ------------------------------------------------------------------ #
    #  Tool-use loop                                                        #
    # ------------------------------------------------------------------ #

    async def _run_tool_loop(self, user_message: str, chat_id: int = 0, settings: dict | None = None) -> str:
        """Запустить Claude с инструментами и вернуть итоговый текст."""
        messages = [{"role": "user", "content": user_message}]
        max_iterations = 8

        for iteration in range(max_iterations):
            response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=4096,
                system=self._effective_system,
                tools=self._TOOLS,
                messages=messages,
            )

            # Собираем текстовые блоки
            text_parts = []
            tool_uses  = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)

            # Нет вызовов инструментов — Claude закончил
            if not tool_uses:
                return "\n".join(text_parts).strip()

            # Добавляем ответ ассистента в историю
            messages.append({"role": "assistant", "content": response.content})

            # Выполняем все tool_use параллельно
            tool_results = await asyncio.gather(*[
                self._call_tool(tu.name, tu.input, settings=settings)
                for tu in tool_uses
            ])

            tool_result_blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": str(result),
                }
                for tu, result in zip(tool_uses, tool_results)
            ]
            messages.append({"role": "user", "content": tool_result_blocks})

            logger.debug(
                f"[Тина] tool_loop iter={iteration+1} | "
                f"tools={[tu.name for tu in tool_uses]}"
            )

        return "⚠️ Превышен лимит итераций анализа."

    # ------------------------------------------------------------------ #
    #  Основная логика: анализ тендеров                                    #
    # ------------------------------------------------------------------ #

    async def scan_and_analyze(self, keywords: list[str], chat_id: int = 0, settings: dict | None = None) -> str:
        """Сканировать тендеры по ключевым словам и вернуть дайджест."""
        settings = settings or await self._get_tender_settings(chat_id)
        kw_str = ", ".join(f"«{k}»" for k in keywords)
        prompt = (
            f"Найди и проанализируй тендеры по ключевым словам: {kw_str}.\n\n"
            f"Для каждого найденного тендера:\n"
            f"1. Используй search_tenders с каждым ключевым словом\n"
            f"2. Для топ-3 самых перспективных тендеров (по НМЦК и срочности):\n"
            f"   - Используй research_supplier_prices чтобы найти оптовые цены\n"
            f"   - Используй calculate_economics для расчёта маржи\n"
            f"   - Используй save_tender_opportunity с chat_id={chat_id} для сохранения\n"
            f"3. Составь итоговый дайджест: топ тендеры с рекомендациями\n\n"
            f"Фокус: регион (код ОКТМО {settings['region_code']}), 44-ФЗ, статус «Подача заявок».\n"
            f"НМЦК от {_fmt_rub(settings['min_nmck'])} до {_fmt_rub(settings['max_nmck'])}."
        )
        return await self._run_tool_loop(prompt, chat_id=chat_id, settings=settings)

    async def analyze_specific_tender(self, lot_id: str, chat_id: int = 0) -> str:
        """Полный анализ конкретного тендера по ID."""
        settings = await self._get_tender_settings(chat_id)
        prompt = (
            f"Сделай полный анализ тендера с ID: {lot_id}\n\n"
            f"1. Получи детали: get_tender_details(lot_id={lot_id!r})\n"
            f"2. Найди цены поставщиков: research_supplier_prices\n"
            f"3. Рассчитай экономику: calculate_economics\n"
            f"4. Сохрани результат: save_tender_opportunity с chat_id={chat_id}\n"
            f"5. Дай развёрнутый отчёт с рекомендацией"
        )
        return await self._run_tool_loop(prompt, chat_id=chat_id, settings=settings)

    # ------------------------------------------------------------------ #
    #  Ежедневный дайджест                                                 #
    # ------------------------------------------------------------------ #

    async def send_daily_digest(self, chat_id: int) -> None:
        """Ежедневный 08:00 МСК: найти и проанализировать тендеры, уведомить пользователя."""
        logger.info(f"[Тина] Ежедневный дайджест для chat_id={chat_id}")
        settings = await self._get_tender_settings(chat_id)
        keywords = settings["keywords"]
        if not keywords:
            logger.warning(f"[Тина] Список ключевых слов пуст для chat_id={chat_id} — дайджест пропущен")
            return
        try:
            await self._notify_user(chat_id, "📋 <b>Тина</b>: начинаю поиск тендеров, подожди немного…")
            result = await self.scan_and_analyze(keywords, chat_id=chat_id, settings=settings)
            if not result:
                result = "Подходящих тендеров сегодня не найдено."
            header = "📋 <b>Тендерный дайджест</b> — " + datetime.now(timezone.utc).strftime("%d.%m.%Y") + "\n\n"
            await self._notify_user(chat_id, header + result)
            logger.info(f"[Тина] Дайджест отправлен | chat_id={chat_id} | len={len(result)}")
        except Exception as e:
            logger.error(f"[Тина] Ошибка дайджеста: {e}\n{traceback.format_exc()}")
            await self._notify_user(chat_id, f"⚠️ Тина: ошибка при поиске тендеров: {e}")

    # ------------------------------------------------------------------ #
    #  handle_task (worker loop)                                           #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        logger.info(f"[Тина] handle_task от {from_agent}: {task!r}")
        return await self._run_tool_loop(task)

    # ------------------------------------------------------------------ #
    #  Telegram-команды                                                    #
    # ------------------------------------------------------------------ #

    async def cmd_tenders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/tenders [ключевое слово] — поиск и анализ тендеров."""
        chat_id = update.effective_chat.id
        keyword_input = " ".join(context.args) if context.args else ""
        settings = await self._get_tender_settings(chat_id)

        if keyword_input:
            keywords = [keyword_input]
        else:
            keywords = settings["keywords"] or ["товары"]

        await update.message.reply_text(
            f"📋 Ищу тендеры по: {', '.join(keywords)}…\nЭто может занять 1-2 минуты.",
            parse_mode="HTML",
        )
        try:
            result = await self.scan_and_analyze(keywords, chat_id=chat_id, settings=settings)
            await self._send_html_chunks(context, chat_id, result or "Подходящих тендеров не найдено.")
        except Exception as e:
            logger.error(f"[Тина] cmd_tenders ошибка: {e}\n{traceback.format_exc()}")
            await update.message.reply_text(f"⚠️ Ошибка анализа: {e}")

    async def cmd_tender(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/tender <lot_id> — полный анализ конкретного тендера."""
        chat_id = update.effective_chat.id
        if not context.args:
            await update.message.reply_text("Использование: /tender <lot_id>")
            return
        lot_id = context.args[0]
        await update.message.reply_text(f"🔍 Анализирую тендер {lot_id}…")
        try:
            result = await self.analyze_specific_tender(lot_id, chat_id=chat_id)
            await self._send_html_chunks(context, chat_id, result)
        except Exception as e:
            await update.message.reply_text(f"⚠️ Ошибка: {e}")

    async def _render_tenders_report(self, chat_id: int) -> str:
        """Текст отчёта по сохранённым тендерам для chat_id."""
        if not config.DATABASE_URL:
            return "⚠️ БД недоступна."
        try:
            conn = await asyncpg.connect(config.DATABASE_URL)
            try:
                rows = await conn.fetch("""
                    SELECT title, nmck, margin_estimate, recommendation, submission_deadline
                    FROM tender_opportunities
                    WHERE (chat_id = $1 OR chat_id = 0)
                      AND recommendation IN ('УЧАСТВОВАТЬ','АНАЛИЗИРОВАТЬ')
                    ORDER BY margin_estimate DESC
                    LIMIT 10
                """, chat_id)
            finally:
                await conn.close()

            if not rows:
                return "📋 Сохранённых тендеров нет. Нажми «🔍 Найти тендеры» в меню (/start)."

            lines = ["📋 <b>Сохранённые тендеры</b>\n"]
            for r in rows:
                rec   = r["recommendation"]
                emoji = "✅" if rec == "УЧАСТВОВАТЬ" else "⚠️"
                nmck  = _fmt_rub(float(r["nmck"] or 0))
                margin = f"{float(r['margin_estimate'] or 0):.1f}%"
                dl    = str(r["submission_deadline"])[:10] if r["submission_deadline"] else "—"
                title = (r["title"] or "")[:60]
                lines.append(f"{emoji} {title}\n   💰 {nmck} | маржа {margin} | до {dl}")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠️ Ошибка: {e}"

    async def cmd_tenders_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/tenders_report — показать сохранённые тендеры из БД."""
        chat_id = update.effective_chat.id
        text = await self._render_tenders_report(chat_id)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_tender_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/tender_settings — открыть форму настроек поиска тендеров (Mini App)."""
        if not config.DASHBOARD_URL:
            await update.message.reply_text("⚠️ Дашборд не настроен (DASHBOARD_URL пуст).")
            return
        url = f"{config.DASHBOARD_URL}?screen=tender_settings"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⚙️ Настройки поиска тендеров", web_app=WebAppInfo(url=url))
        ]])
        await update.message.reply_text(
            "Настрой ключевые слова, бюджет (НМЦК) и регион поиска тендеров:",
            reply_markup=keyboard,
        )

    # ------------------------------------------------------------------ #
    #  Главное меню (кнопки вместо команд)                                #
    # ------------------------------------------------------------------ #

    def _menu_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("🔍 Найти тендеры", callback_data="tina_menu:find")],
            [InlineKeyboardButton("📋 Сохранённые тендеры", callback_data="tina_menu:report")],
        ]
        if config.DASHBOARD_URL:
            rows.append([InlineKeyboardButton(
                "⚙️ Настройки поиска", web_app=WebAppInfo(url=f"{config.DASHBOARD_URL}?screen=tender_settings")
            )])
        return InlineKeyboardMarkup(rows)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/start — главное меню с кнопками."""
        await update.message.reply_text(
            "📋 <b>Тина</b> — тендерный аналитик 44-ФЗ.\n\n"
            "🔍 <b>Найти тендеры</b> — поиск по твоим сохранённым ключевым словам\n"
            "📋 <b>Сохранённые тендеры</b> — что уже нашли и проанализировали\n"
            "⚙️ <b>Настройки поиска</b> — ключевые слова, бюджет, регион\n\n"
            "Для поиска по конкретному слову: <code>/tenders слово</code>\n"
            "Для анализа тендера по ID: <code>/tender ID</code>",
            parse_mode="HTML",
            reply_markup=self._menu_keyboard(),
        )

    async def _handle_menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id

        if query.data == "tina_menu:find":
            settings = await self._get_tender_settings(chat_id)
            keywords = settings["keywords"] or ["товары"]
            await query.edit_message_text(
                f"📋 Ищу тендеры по: {', '.join(keywords)}…\nЭто может занять 1-2 минуты."
            )
            try:
                result = await self.scan_and_analyze(keywords, chat_id=chat_id, settings=settings)
                await self._send_html_chunks(context, chat_id, result or "Подходящих тендеров не найдено.")
            except Exception as e:
                logger.error(f"[Тина] menu find ошибка: {e}\n{traceback.format_exc()}")
                await context.bot.send_message(chat_id, f"⚠️ Ошибка анализа: {e}")

        elif query.data == "tina_menu:report":
            text = await self._render_tenders_report(chat_id)
            await self._send_html_chunks(context, chat_id, text)

    @staticmethod
    async def _send_html_chunks(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
        """Отправить длинный HTML-текст частями по 4000 симв. (лимит Telegram)."""
        for chunk in [text[i:i+4000] for i in range(0, max(len(text), 1), 4000)]:
            try:
                await context.bot.send_message(chat_id, chunk, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id, chunk)

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start",           "Главное меню"),
            BotCommand("tenders",         "Поиск и анализ тендеров"),
            BotCommand("tender",          "Анализ тендера по ID"),
            BotCommand("tenders_report",  "Сохранённые тендеры"),
            BotCommand("tender_settings", "Настройки поиска (ключевые слова, бюджет)"),
            BotCommand("reset",           "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        from telegram.ext import CallbackQueryHandler
        self.app.add_handler(CommandHandler("tenders",         self.cmd_tenders))
        self.app.add_handler(CommandHandler("tender",          self.cmd_tender))
        self.app.add_handler(CommandHandler("tenders_report",  self.cmd_tenders_report))
        self.app.add_handler(CommandHandler("tender_settings", self.cmd_tender_settings))
        self.app.add_handler(CallbackQueryHandler(self._handle_menu_callback, pattern=r"^tina_menu:"))
