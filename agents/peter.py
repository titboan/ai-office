from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from loguru import logger
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import save_research
from .base_agent import BaseAgent

_UTC = timezone.utc

PETER_SYSTEM = """Ты Питер, бизнес-аналитик команды AI Office.
Анализируешь продажи на Wildberries и Ozon, считаешь юнит-экономику, помогаешь выйти на цели по обороту.

Данные которые ты получаешь — реальные цифры из БД: заказы, себестоимость, рекламные расходы, остатки.
Важно: данные по заказам, не по выкупам — реальная выручка ниже на процент возвратов (обычно 10-30% на WB).

Формат ответа ВСЕГДА — короткий, читаемый в Telegram с телефона. Весь ответ — не длиннее 25 строк.
Используй display_name товаров (короткие коды: КБ50, ТГ100 и т.д.), не SKU и не длинные названия.
Никаких упоминаний возвратов.

Форматируй в HTML для Telegram:
- <b>текст</b> для заголовков разделов и ключевых метрик
- <code>артикул</code> для кодов товаров
- <blockquote>вывод</blockquote> для ключевого инсайта
- Эмодзи в начале строк (📊 📈 🎯 ⚠️)
- НЕ используй Markdown: никаких *звёздочек*, ##заголовков, |таблиц|

Пример структуры:
📊 <b>Оборот за N дней:</b> X ₽ (Y ₽/день)
WB: X ₽ (ДРР X%) | Ozon: X ₽ (ДРР X%)

Топ-3: <code>КБ50</code> — X ₽/день, <code>ТГ100</code> — X ₽/день

📈 Сейчас: X ₽/день → цель: Y ₽/день → не хватает: Z ₽/день

🎯 <b>План (топ-3 действия):</b>
1. Увеличь рекламу на <code>артикул</code> на X ₽ → при ДРР X% даст +Y ₽/день

<blockquote>Главный инсайт одной строкой</blockquote>"""


from utils.tg_format import strip_html as _strip_html


class PeterAgent(BaseAgent):
    name = "Питер"
    agent_key = "peter"
    role = "Бизнес-аналитик"
    emoji = "📊"
    system_prompt = PETER_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.PETER_BOT_TOKEN)

    async def _collect_data(self, chat_id: int, days: int = 14) -> dict:
        """Собрать аналитический срез из БД за последние N дней."""
        from db import get_pool
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        async with pool.acquire() as conn:

            # 1. Оборот по площадкам
            revenue = await conn.fetch("""
                SELECT marketplace,
                       SUM(seller_price * quantity)::numeric(12,2) AS revenue,
                       COUNT(*)                              AS orders,
                       COUNT(DISTINCT product_id)           AS skus
                FROM marketplace_orders
                WHERE chat_id = $1 AND order_date >= $2
                GROUP BY marketplace
            """, chat_id, date_from)

            # 2. Топ-10 товаров по обороту — с display_name из реестра
            top_products = await conn.fetch("""
                SELECT o.marketplace, o.product_id,
                       COALESCE(m.display_name, MAX(o.product_name)) AS product_name,
                       SUM(o.seller_price * o.quantity)::numeric(12,2)      AS revenue,
                       SUM(o.quantity)                                AS qty
                FROM marketplace_orders o
                LEFT JOIN product_mapping m
                       ON m.wb_article = o.product_id
                       OR m.ozon_sku   = o.product_id
                WHERE o.chat_id = $1 AND o.order_date >= $2
                GROUP BY o.marketplace, o.product_id, m.display_name
                ORDER BY revenue DESC
                LIMIT 10
            """, chat_id, date_from)

            # 3. Рентабельность WB (комиссия ~20%, логистика ~100₽/заказ)
            margin_wb = await conn.fetch("""
                SELECT
                    o.product_id,
                    COALESCE(m.display_name, MAX(o.product_name)) AS product_name,
                    SUM(o.seller_price * o.quantity)::numeric(12,2)      AS revenue,
                    SUM(o.quantity)                               AS qty,
                    MAX(c.cost)::numeric(12,2)                    AS cost,
                    (SUM(o.seller_price * o.quantity)
                     - SUM(o.quantity) * MAX(c.cost)
                    )::numeric(12,2)                              AS op_profit,
                    CASE WHEN SUM(o.seller_price * o.quantity) > 0 THEN
                        ROUND((SUM(o.seller_price * o.quantity)
                               - SUM(o.quantity) * MAX(c.cost)
                              ) / SUM(o.seller_price * o.quantity) * 100, 1)
                    ELSE 0 END                                    AS profitability
                FROM marketplace_orders o
                JOIN product_mapping m ON m.wb_article = o.product_id
                JOIN product_costs c   ON c.mapping_id = m.id
                WHERE o.chat_id = $1 AND o.marketplace = 'wb' AND o.order_date >= $2
                GROUP BY o.product_id, m.display_name
                ORDER BY op_profit DESC
            """, chat_id, date_from)

            # 4. Рентабельность Ozon (комиссия ~10%, логистика ~80₽/заказ)
            margin_ozon = await conn.fetch("""
                SELECT
                    o.product_id,
                    COALESCE(m.display_name, MAX(o.product_name)) AS product_name,
                    SUM(o.seller_price * o.quantity)::numeric(12,2)      AS revenue,
                    SUM(o.quantity)                               AS qty,
                    MAX(c.cost)::numeric(12,2)                    AS cost,
                    (SUM(o.seller_price * o.quantity)
                     - SUM(o.quantity) * MAX(c.cost)
                    )::numeric(12,2)                              AS op_profit,
                    CASE WHEN SUM(o.seller_price * o.quantity) > 0 THEN
                        ROUND((SUM(o.seller_price * o.quantity)
                               - SUM(o.quantity) * MAX(c.cost)
                              ) / SUM(o.seller_price * o.quantity) * 100, 1)
                    ELSE 0 END                                    AS profitability
                FROM marketplace_orders o
                JOIN product_mapping m ON m.ozon_sku = o.product_id
                JOIN product_costs c   ON c.mapping_id = m.id
                WHERE o.chat_id = $1 AND o.marketplace = 'ozon' AND o.order_date >= $2
                GROUP BY o.product_id, m.display_name
                ORDER BY op_profit DESC
            """, chat_id, date_from)

            # 5. Рекламные расходы
            adv = await conn.fetch("""
                SELECT marketplace,
                       SUM(spend)::numeric(12,2) AS spend,
                       SUM(views)                AS views,
                       SUM(clicks)               AS clicks
                FROM marketplace_adv_stats
                WHERE chat_id = $1 AND stat_date >= $2
                GROUP BY marketplace
            """, chat_id, date_from)

            # 6. Остатки — товары с низким стоком
            low_stocks = await conn.fetch("""
                SELECT marketplace, product_id,
                       MAX(product_name) AS product_name,
                       SUM(stock)        AS stock
                FROM marketplace_stocks
                WHERE chat_id = $1
                GROUP BY marketplace, product_id
                HAVING SUM(stock) < 10
                ORDER BY stock ASC
                LIMIT 10
            """, chat_id)

        return {
            "period_days": days,
            "date_from":   date_from,
            "revenue":     [dict(r) for r in revenue],
            "top_products":[dict(r) for r in top_products],
            "margin_wb":   [dict(r) for r in margin_wb],
            "margin_ozon": [dict(r) for r in margin_ozon],
            "adv":         [dict(r) for r in adv],
            "low_stocks":  [dict(r) for r in low_stocks],
        }

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        logger.info(f"[Питер] Задача от {from_agent}: {task!r}")
        answer = await self.think(
            f"Аналитическая задача от {from_agent}: {task}",
            chat_id=0,
            is_task=True,
        )
        notion_url = await save_research(
            title=task[:50],
            content=_strip_html(answer),
            source=f"agent:{from_agent}",
            agent="Питер",
        )
        if notion_url:
            answer = f'{answer}\n\n📄 <a href="{notion_url}">Анализ сохранён в Notion</a>'
        await self.post_to_group(f"📊 Анализ готов: {answer[:200]}…")
        return answer

    async def cmd_report(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/report [цель=200000] [период=14] — анализ магазинов и план роста."""
        chat_id = update.effective_user.id
        args_raw = " ".join(context.args) if context.args else ""

        # Парсим параметры
        goal = None
        days = 14
        for tok in args_raw.split():
            if tok.startswith("цель="):
                try:
                    goal = float(tok.split("=", 1)[1].replace(" ", ""))
                except ValueError:
                    pass
            elif tok.startswith("период="):
                try:
                    days = int(tok.split("=", 1)[1])
                except ValueError:
                    pass

        await update.message.reply_text(
            f"📊 Собираю данные за {days} дней…"
            + (f" Цель: {goal:,.0f} ₽/день" if goal else "")
        )

        try:
            data = await self._collect_data(chat_id, days=days)
        except Exception as e:
            logger.error(f"[Питер/report] ошибка сбора данных: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка сбора данных: {e}")
            return

        # Считаем средний оборот/день
        total_revenue = sum(float(r["revenue"] or 0) for r in data["revenue"])
        avg_per_day = round(total_revenue / days, 0) if days else 0

        goal_str = ""
        if goal:
            gap = goal - avg_per_day
            goal_str = (
                f"\nЦЕЛЬ: {goal:,.0f} ₽/день | "
                f"Сейчас: {avg_per_day:,.0f} ₽/день | "
                f"Разрыв: {gap:+,.0f} ₽/день"
            )

        prompt = f"""Проанализируй данные магазинов за последние {days} дней.
{goal_str}

ДАННЫЕ:
{json.dumps(data, ensure_ascii=False, default=str, indent=2)}

ВАЖНО:
- Данные по заказам, не по выкупам. Реальная выручка ниже на % возвратов.
- Маржа считается как выручка минус себестоимость (без комиссии МП и логистики МП).
- Комиссия WB ~15-25%, логистика ~50-150₽/заказ — учитывай в выводах.
- Комиссия Ozon ~5-15% в зависимости от категории.
- Если margin_ozon пустой — Ozon-заказы есть, но маппинг SKU не позволил посчитать маржу.
- Не упоминай возвраты — у продавца выкупаемость 95-100%.
{"- Цель: " + str(goal) + " ₽/день суммарно WB+Ozon." if goal else ""}

Дай конкретный анализ по формату из system prompt."""

        await update.message.reply_text("🤔 Анализирую…")
        try:
            answer = await self.think(prompt, chat_id=chat_id, is_task=True, max_tokens=4096)
        except Exception as e:
            logger.error(f"[Питер/report] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        # Сохраняем в Notion (strip HTML тегов — Notion ожидает plain text)
        notion_url = await save_research(
            title=f"Отчёт {datetime.now(_UTC).strftime('%d.%m.%Y')}",
            content=_strip_html(answer),
            source="cmd:report",
            agent="Питер",
        )
        if notion_url:
            answer = f'{answer}\n\n📄 <a href="{notion_url}">Сохранено в Notion</a>'

        for chunk in [answer[i:i+4000] for i in range(0, len(answer), 4000)]:
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

    async def cmd_analyze(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/analyze <данные> — бизнес-анализ произвольных данных."""
        data = " ".join(context.args) if context.args else ""
        if not data:
            await update.message.reply_text(
                "Использование: /analyze <данные или вопрос>\n"
                "Для анализа магазинов используй /report"
            )
            return
        await update.message.reply_text("📊 Анализирую…")
        result = await self.handle_task(data, from_agent="команды /analyze")
        for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
            await update.message.reply_text(chunk)

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("report",  self.cmd_report))
        self.app.add_handler(CommandHandler("analyze", self.cmd_analyze))
