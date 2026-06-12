from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone

import asyncpg
from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools.gosplan_api import GosplanClient, STATUS_OPEN, format_tender_summary
from tools.search import search_web
from .base_agent import BaseAgent

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

    async def _call_tool(self, tool_name: str, tool_input: dict) -> str:
        """Выполнить вызов инструмента и вернуть результат как строку."""
        try:
            if tool_name == "search_tenders":
                return await self._tool_search_tenders(**tool_input)
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
    ) -> str:
        nmck_min = nmck_min or config.TENDER_MIN_NMCK
        nmck_max = nmck_max or config.TENDER_MAX_NMCK
        tenders = await self._gosplan.search_tenders(
            keyword=keyword,
            region_code=config.TENDER_REGION_CODE,
            status=STATUS_OPEN,
            nmck_min=nmck_min,
            nmck_max=nmck_max,
            per_page=10,
        )
        if not tenders:
            return f"Тендеров по запросу «{keyword}» в Краснодарском крае не найдено."
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

    async def _run_tool_loop(self, user_message: str, chat_id: int = 0) -> str:
        """Запустить Claude с инструментами и вернуть итоговый текст."""
        messages = [{"role": "user", "content": user_message}]
        max_iterations = 8

        for iteration in range(max_iterations):
            response = await self.claude.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=4096,
                system=self.system_prompt,
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
                self._call_tool(tu.name, tu.input)
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

    async def scan_and_analyze(self, keywords: list[str], chat_id: int = 0) -> str:
        """Сканировать тендеры по ключевым словам и вернуть дайджест."""
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
            f"Фокус: Краснодарский край, 44-ФЗ, статус «Подача заявок».\n"
            f"НМЦК от {_fmt_rub(config.TENDER_MIN_NMCK)} до {_fmt_rub(config.TENDER_MAX_NMCK)}."
        )
        return await self._run_tool_loop(prompt, chat_id=chat_id)

    async def analyze_specific_tender(self, lot_id: str, chat_id: int = 0) -> str:
        """Полный анализ конкретного тендера по ID."""
        prompt = (
            f"Сделай полный анализ тендера с ID: {lot_id}\n\n"
            f"1. Получи детали: get_tender_details(lot_id={lot_id!r})\n"
            f"2. Найди цены поставщиков: research_supplier_prices\n"
            f"3. Рассчитай экономику: calculate_economics\n"
            f"4. Сохрани результат: save_tender_opportunity с chat_id={chat_id}\n"
            f"5. Дай развёрнутый отчёт с рекомендацией"
        )
        return await self._run_tool_loop(prompt, chat_id=chat_id)

    # ------------------------------------------------------------------ #
    #  Ежедневный дайджест                                                 #
    # ------------------------------------------------------------------ #

    async def send_daily_digest(self, chat_id: int) -> None:
        """Ежедневный 08:00 МСК: найти и проанализировать тендеры, уведомить пользователя."""
        logger.info(f"[Тина] Ежедневный дайджест для chat_id={chat_id}")
        keywords = config.TENDER_KEYWORDS
        if not keywords:
            logger.warning("[Тина] TENDER_KEYWORDS пустой — дайджест пропущен")
            return
        try:
            await self._notify_user(chat_id, "📋 <b>Тина</b>: начинаю поиск тендеров, подожди немного…")
            result = await self.scan_and_analyze(keywords, chat_id=chat_id)
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

        if keyword_input:
            keywords = [keyword_input]
        else:
            keywords = config.TENDER_KEYWORDS or ["товары"]

        await update.message.reply_text(
            f"📋 Ищу тендеры по: {', '.join(keywords)}…\nЭто может занять 1-2 минуты.",
            parse_mode="HTML",
        )
        try:
            result = await self.scan_and_analyze(keywords, chat_id=chat_id)
            if not result:
                result = "Подходящих тендеров не найдено."
            for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
                try:
                    await update.message.reply_text(chunk, parse_mode="HTML")
                except Exception:
                    await update.message.reply_text(chunk)
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
            for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
                try:
                    await update.message.reply_text(chunk, parse_mode="HTML")
                except Exception:
                    await update.message.reply_text(chunk)
        except Exception as e:
            await update.message.reply_text(f"⚠️ Ошибка: {e}")

    async def cmd_tenders_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/tenders_report — показать сохранённые тендеры из БД."""
        chat_id = update.effective_chat.id
        if not config.DATABASE_URL:
            await update.message.reply_text("⚠️ БД недоступна.")
            return
        try:
            conn = await asyncpg.connect(config.DATABASE_URL)
            rows = await conn.fetch("""
                SELECT title, nmck, margin_estimate, recommendation, submission_deadline
                FROM tender_opportunities
                WHERE (chat_id = $1 OR chat_id = 0)
                  AND recommendation IN ('УЧАСТВОВАТЬ','АНАЛИЗИРОВАТЬ')
                ORDER BY margin_estimate DESC
                LIMIT 10
            """, chat_id)
            await conn.close()

            if not rows:
                await update.message.reply_text("📋 Сохранённых тендеров нет. Запусти /tenders для поиска.")
                return

            lines = ["📋 <b>Сохранённые тендеры</b>\n"]
            for r in rows:
                rec   = r["recommendation"]
                emoji = "✅" if rec == "УЧАСТВОВАТЬ" else "⚠️"
                nmck  = _fmt_rub(float(r["nmck"] or 0))
                margin = f"{float(r['margin_estimate'] or 0):.1f}%"
                dl    = str(r["submission_deadline"])[:10] if r["submission_deadline"] else "—"
                title = (r["title"] or "")[:60]
                lines.append(f"{emoji} {title}\n   💰 {nmck} | маржа {margin} | до {dl}")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Ошибка: {e}")

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("tenders",        self.cmd_tenders))
        self.app.add_handler(CommandHandler("tender",         self.cmd_tender))
        self.app.add_handler(CommandHandler("tenders_report", self.cmd_tenders_report))
