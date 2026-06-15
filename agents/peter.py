from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import CommandHandler, ContextTypes

from config import config
from tools import save_research
from utils.tg_format import strip_html as _strip_html
from utils.tg_rich import send_rich_or_fallback as _send_rich
from .base_agent import BaseAgent

_UTC = timezone.utc

PETER_SYSTEM = f"""Ты Питер, бизнес-аналитик команды AI Office.
Анализируешь продажи на Wildberries и Ozon, считаешь юнит-экономику, помогаешь выйти на цели по обороту.

Данные которые ты получаешь — реальные цифры из БД: заказы, себестоимость, рекламные расходы, остатки.
Важно: данные по заказам, не по выкупам — реальная выручка ниже на процент возвратов (обычно 10-30% на WB).

Формат ответа ВСЕГДА — короткий, читаемый в Telegram с телефона. Весь ответ — не длиннее 30 строк.
Используй display_name товаров (короткие коды: КБ50, ТГ100 и т.д.), не SKU и не длинные названия.
Никаких упоминаний возвратов.

Форматируй ответы в Rich Markdown для Telegram:
- **текст** — заголовки разделов и ключевые числа
- *текст* — пояснения и уточнения
- `текст` — артикулы, коды товаров
- > текст — главный инсайт
- Таблица: первая строка начинается с `|`, перед ней — пустая строка.
  НЕ ставь текст перед `|` в той же строке. Пример:
  `**WB — топ**\n\n| Товар | Выручка |\n|---|---|\n| ... | ... |`
- Эмодзи в начале разделов: 📊 📈 🎯 ⚠️ ⬆️ 📸 📦 💰 🔄
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Пример структуры:
📊 **Оборот за N дней:** X ₽ (Y ₽/день)
WB: X ₽ (ДРР X%) | Ozon: X ₽ (ДРР X%)
Тренд: WB ↑X% | Ozon ↓X% (неделя к неделе)

Топ-3: `КБ50` — X ₽/день, `ТГ100` — X ₽/день

📈 Сейчас: X ₽/день → цель: Y ₽/день → не хватает: Z ₽/день

🎯 **Plan (топ-5 действий):**
1. ⬆️ Реклама `КБ50` +5 000₽/нед → ROAS 4.2x → +Y₽/день
2. 📸 Переделать фото `ТГ100` — CTR 0.7% (норма 2-3%), теряем X кликов/день
3. 📦 Заказать `КБ30` — осталось 8 дней, провал стока = -Y₽
4. 💰 Снизить ставку `ДС200` — ROAS 1.1, тратим X₽ в минус
5. 🔄 Перенести бюджет Ozon→WB — ДРР Ozon 35% vs WB 18%

> Главный инсайт одной строкой

ПРИ АНАЛИЗЕ:
- CTR < 1% — плохая инфографика → рекомендуй замену конкретных карточек
- CTR > 4% — хорошая карточка, масштабируй бюджет
- ROAS = revenue/spend. ROAS > 5 → увеличь бюджет. ROAS < 2 → стоп
- days_left < 14 → срочный дозаказ, назови товар
- ДРР норма: WB ~15-20%, Ozon ~10-15%
- Конкретные суммы, не абстрактные советы"""

PETER_AUDIT_PROMPT = """Ты проводишь полный аудит магазина на маркетплейсах.

ФОРМАТ ОТВЕТА (не длиннее 45 строк, HTML Telegram):

🏪 <b>Оценка магазина: X/10</b>
Оборот: X ₽/мес | Тренд: ↑/↓ X% | ДРР: X%

💪 <b>Сильные стороны:</b>
1. [факт из данных с цифрой]
2. [факт из данных с цифрой]
3. [факт из данных с цифрой]

⚠️ <b>Слабые стороны:</b>
1. [факт из данных с цифрой]
2. [факт из данных с цифрой]
3. [факт из данных с цифрой]

🎯 <b>Приоритет №1 на ближайший месяц:</b>
[направление] — [конкретное обоснование с числами]

📋 <b>Топ-5 действий прямо сейчас:</b>
1. [действие с конкретным товаром/суммой] — [ожидаемый эффект]
2. ...
5. ...

📊 <b>KPI-дашборд:</b>
Заказов/день: X | ДРР WB: X% | ДРР Ozon: X%
Топ-CTR: <code>арт</code> X% | Худший-CTR: <code>арт</code> X%
Стоков < 14 дней: X позиций

<blockquote>Главный вывод: [почему магазин там где он есть и куда идти]</blockquote>

ПРАВИЛА:
- Оценка X/10: считай по факторам (рост оборота, CTR, ROAS, маржа, стоки, ДРР)
- Каждое действие — конкретный товар <code>код</code> + конкретная сумма/процент
- CTR < 1% → "переделать инфографику на <code>арт</code> — CTR X%, теряем X кликов/день"
- ROAS < 2 → "отключить рекламу <code>арт</code> — убыток X₽/день"
- days_left < 14 → "срочно заказать <code>арт</code> — осталось X дней"
- Если тренд недели +20% — отдельно отметь рост"""

PETER_DRR_PROMPT = """Ты анализируешь рекламную эффективность магазина. Выдай краткий ДРР-отчёт.

ФОРМАТ (не длиннее 30 строк, HTML Telegram):

📊 <b>ДРР-отчёт за N дней</b>

<b>По площадкам:</b>
WB: расход X₽ / оборот X₽ → ДРР X%  [норма 15-20%]
Ozon: расход X₽ / оборот X₽ → ДРР X%  [норма 10-15%]

<b>Топ-5 товаров по расходу:</b>
<code>КБ50</code> — ДРР X%, ROAS X.X, CTR X% → [вердикт: 🟢 эффективно / 🟡 пересмотреть / 🔴 отключить]
...

⚠️ <b>Проблемные:</b>
[Товары с ДРР > 25% или ROAS < 2]

✅ <b>Лидеры эффективности:</b>
[Товары с ROAS > 5]

<blockquote>Рекомендация: [одно главное действие с суммой/товаром]</blockquote>

ПРАВИЛА:
- ДРР = (рекл. расход / оборот от заказов) × 100%
- ROAS = оборот / рекл. расход
- 🟢 ROAS > 5 (ДРР < 20%)
- 🟡 ROAS 2-5 (ДРР 20-50%)
- 🔴 ROAS < 2 (ДРР > 50%)
- Если данных по товару нет в product_adv_stats — укажи суммарный ДРР по площадке"""


class PeterAgent(BaseAgent):
    name = "Питер"
    agent_key = "peter"
    role = "Бизнес-аналитик"
    emoji = "📊"
    system_prompt = PETER_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.PETER_BOT_TOKEN)

    async def _collect_data(self, chat_id: int, days: int = 14) -> dict:
        """Собрать базовый аналитический срез из БД за последние N дней."""
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

            # 5. NET-маржа из финансовых отчётов (реальные выплаты минус себестоимость)
            net_margin = await conn.fetch("""
                SELECT
                    f.marketplace,
                    f.product_id,
                    COALESCE(m.display_name, f.product_id) AS product_name,
                    SUM(f.quantity)::int                    AS qty,
                    SUM(f.revenue)::numeric(12,2)           AS revenue,
                    SUM(f.payout)::numeric(12,2)            AS payout,
                    SUM(f.commission)::numeric(12,2)        AS commission,
                    SUM(f.logistics)::numeric(12,2)         AS logistics,
                    SUM(f.storage)::numeric(12,2)           AS storage,
                    SUM(f.penalty)::numeric(12,2)           AS penalty,
                    COALESCE(MAX(c.cost), 0)::numeric(12,2) AS cost_per_unit,
                    (SUM(f.payout) - SUM(f.quantity) * COALESCE(MAX(c.cost), 0))::numeric(12,2) AS net_profit,
                    CASE WHEN SUM(f.payout) > 0
                         THEN ROUND((SUM(f.payout) - SUM(f.quantity) * COALESCE(MAX(c.cost), 0))
                                    / SUM(f.payout) * 100, 1)
                         ELSE 0 END                         AS net_margin_pct
                FROM marketplace_financial_report f
                LEFT JOIN product_mapping m
                       ON m.wb_article = f.product_id
                       OR m.ozon_offer_id = f.product_id
                LEFT JOIN product_costs c ON c.mapping_id = m.id
                WHERE f.chat_id = $1 AND f.report_date >= $2
                GROUP BY f.marketplace, f.product_id, m.display_name
                ORDER BY net_profit DESC
                LIMIT 15
            """, chat_id, date_from)

            # 6. Рекламные расходы
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
            "net_margin":  [dict(r) for r in net_margin],
            "adv":         [dict(r) for r in adv],
            "low_stocks":  [dict(r) for r in low_stocks],
        }

    async def _collect_advanced_data(self, chat_id: int, days: int = 14) -> dict:
        """Расширенные метрики: тренд, CTR/ROAS по товарам, stock velocity."""
        from db import get_pool
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        async with pool.acquire() as conn:

            # 1. Тренд: текущие 7 дней vs предыдущие 7 дней
            trend = await conn.fetch("""
                SELECT marketplace,
                       SUM(CASE WHEN order_date >= NOW() - INTERVAL '7 days'
                                THEN seller_price * quantity ELSE 0 END)::numeric(12,2) AS week_current,
                       SUM(CASE WHEN order_date <  NOW() - INTERVAL '7 days'
                                THEN seller_price * quantity ELSE 0 END)::numeric(12,2) AS week_prev
                FROM marketplace_orders
                WHERE chat_id = $1 AND order_date >= NOW() - INTERVAL '14 days'
                GROUP BY marketplace
            """, chat_id)

            # 2. CTR, ROAS, расход по товарам (из product_adv_stats + orders)
            product_metrics = await conn.fetch("""
                SELECT
                    p.product_id,
                    COALESCE(m.display_name, p.product_id) AS name,
                    p.marketplace,
                    SUM(p.views)::bigint                            AS views,
                    SUM(p.clicks)::bigint                           AS clicks,
                    CASE WHEN SUM(p.views) > 0
                         THEN ROUND(SUM(p.clicks)::numeric / SUM(p.views) * 100, 2)
                         ELSE 0 END                                 AS avg_ctr,
                    SUM(p.spend)::numeric(12,2)                     AS adv_spend,
                    SUM(p.orders_count)::integer                    AS adv_orders,
                    COALESCE(o.revenue, 0)::numeric(12,2)           AS revenue,
                    CASE WHEN SUM(p.spend) > 0
                         THEN ROUND(COALESCE(o.revenue, 0) / SUM(p.spend), 2)
                         ELSE 0 END                                 AS roas,
                    CASE WHEN COALESCE(o.revenue, 0) > 0
                         THEN ROUND(SUM(p.spend) / COALESCE(o.revenue, 0) * 100, 2)
                         ELSE NULL END                              AS drr
                FROM product_adv_stats p
                LEFT JOIN product_mapping m
                       ON m.wb_article = p.product_id
                       OR m.ozon_sku   = p.product_id
                LEFT JOIN (
                    SELECT product_id,
                           SUM(seller_price * quantity)::numeric(12,2) AS revenue
                    FROM marketplace_orders
                    WHERE chat_id = $1 AND order_date >= $2
                    GROUP BY product_id
                ) o ON o.product_id = p.product_id
                WHERE p.chat_id = $1 AND p.stat_date >= $2
                GROUP BY p.product_id, m.display_name, p.marketplace, o.revenue
                ORDER BY adv_spend DESC
                LIMIT 20
            """, chat_id, date_from)

            # 3. Stock velocity — дней осталось при текущих продажах
            stock_velocity = await conn.fetch("""
                SELECT
                    s.marketplace,
                    s.product_id,
                    COALESCE(m.display_name, MAX(s.product_name)) AS name,
                    SUM(s.stock)::integer                          AS stock,
                    COALESCE(v.daily_orders, 0)                    AS daily_orders,
                    CASE WHEN COALESCE(v.daily_orders, 0) > 0
                         THEN ROUND(SUM(s.stock) / v.daily_orders)
                         ELSE 999 END                              AS days_left
                FROM marketplace_stocks s
                LEFT JOIN product_mapping m
                       ON m.wb_article = s.product_id
                       OR m.ozon_offer_id = s.product_id
                LEFT JOIN (
                    SELECT product_id,
                           ROUND(SUM(quantity)::numeric / $3, 2) AS daily_orders
                    FROM marketplace_orders
                    WHERE chat_id = $1 AND order_date >= $2
                    GROUP BY product_id
                ) v ON v.product_id = s.product_id
                WHERE s.chat_id = $1
                GROUP BY s.marketplace, s.product_id, m.display_name, v.daily_orders
                ORDER BY days_left ASC
                LIMIT 15
            """, chat_id, date_from, days)

        return {
            "period_days":     days,
            "date_from":       date_from,
            "trend":           [dict(r) for r in trend],
            "product_metrics": [dict(r) for r in product_metrics],
            "stock_velocity":  [dict(r) for r in stock_velocity],
        }

    async def _send_answer(
        self,
        answer: str,
        *,
        notion_title: str,
        notion_source: str,
        notion_link_text: str = "Сохранено в Notion",
        show_dashboard: bool = True,
        update: Update | None = None,
        chat_id: int | None = None,
        bot=None,
        parse_mode: str = "MarkdownV2",
    ) -> None:
        notion_url = await save_research(
            title=notion_title,
            content=_strip_html(answer),
            source=notion_source,
            agent="Питер",
        )
        if notion_url:
            if parse_mode == "HTML":
                answer = f'{answer}\n\n📄 <a href="{notion_url}">{notion_link_text}</a>'
            else:
                answer = f'{answer}\n\n📄 [{notion_link_text}]({notion_url})'

        if parse_mode == "HTML":
            # HTML-пути (audit, drr, weekly_audit) — отправляем через PTB
            async def _send_chunk(send_fn, chunk: str, **kwargs) -> None:
                try:
                    await send_fn(chunk, parse_mode="HTML", **kwargs)
                except Exception:
                    await send_fn(chunk, **kwargs)

            if update:
                for chunk in [answer[i:i+4000] for i in range(0, len(answer), 4000)]:
                    await _send_chunk(update.message.reply_text, chunk)
                if show_dashboard and config.DASHBOARD_URL:
                    markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton("📊 Дашборд", web_app=WebAppInfo(url=config.DASHBOARD_URL))
                    ]])
                    await update.message.reply_text("Открыть интерактивный дашборд:", reply_markup=markup)
            else:
                _bot = bot or self.app.bot
                for chunk in [answer[i:i+4000] for i in range(0, len(answer), 4000)]:
                    await _send_chunk(
                        lambda text, **kw: _bot.send_message(chat_id=chat_id, text=text, **kw),
                        chunk,
                    )
        else:
            # Rich Markdown путь (по умолчанию)
            _cid = chat_id or (update.effective_chat.id if update else None)
            if _cid:
                markup_dict = None
                if show_dashboard and config.DASHBOARD_URL and update:
                    markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton("📊 Дашборд", web_app=WebAppInfo(url=config.DASHBOARD_URL))
                    ]])
                    markup_dict = markup.to_dict()
                await _send_rich(self.bot_token, _cid, answer, reply_markup_dict=markup_dict)

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        logger.info(f"[Питер] Задача от {from_agent}: {task!r}")

        chat_id = getattr(self, "_current_chat_id", None) or 0
        data_str = ""
        if chat_id:
            try:
                data = await self._collect_data(chat_id, days=14)
                adv_data = await self._collect_advanced_data(chat_id, days=14)
                data_str = (
                    f"\n\nБАЗОВЫЕ ДАННЫЕ (14 дней):\n"
                    f"{json.dumps(data, ensure_ascii=False, default=str, indent=2)}\n\n"
                    f"РАСШИРЕННЫЕ ДАННЫЕ (тренд, CTR/ROAS, остатки):\n"
                    f"{json.dumps(adv_data, ensure_ascii=False, default=str, indent=2)}"
                )
            except Exception as e:
                logger.warning(f"[Питер] handle_task: ошибка сбора данных: {e}")

        prompt = f"Аналитическая задача от {from_agent}: {task}{data_str}"
        answer = await self.think(prompt, chat_id=chat_id, is_task=True)
        notion_url = await save_research(
            title=task[:50],
            content=_strip_html(answer),
            source=f"agent:{from_agent}",
            agent="Питер",
        )
        if notion_url:
            answer = f'{answer}\n\n📄 [Анализ сохранён в Notion]({notion_url})'
        await self.post_to_group(f"📊 Анализ готов: {answer[:200]}…")
        return answer

    async def cmd_report(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/report [цель=200000] [период=14] — анализ магазинов и план роста."""
        chat_id = update.effective_user.id
        args_raw = " ".join(context.args) if context.args else ""

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
            adv_data = await self._collect_advanced_data(chat_id, days=days)
        except Exception as e:
            logger.error(f"[Питер/report] ошибка сбора данных: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка сбора данных: {e}")
            return

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

БАЗОВЫЕ ДАННЫЕ:
{json.dumps(data, ensure_ascii=False, default=str, indent=2)}

РАСШИРЕННЫЕ ДАННЫЕ (тренд, CTR/ROAS по товарам, остатки):
{json.dumps(adv_data, ensure_ascii=False, default=str, indent=2)}

ВАЖНО:
- Данные по заказам, не по выкупам. Реальная выручка ниже на % возвратов.
- margin_wb / margin_ozon — GROSS-маржа (выручка − себестоимость, БЕЗ комиссий МП).
- net_margin — РЕАЛЬНАЯ маржа из финансовых отчётов МП (payout − себестоимость). Если пустой — запусти /sync_fin у Макса.
- net_margin_pct = (payout − cost) / payout × 100 — то, что реально остаётся после МП.
- Если net_margin НЕ пустой — используй его как основной показатель прибыльности, не GROSS.
- Комиссия WB ~15-25%, логистика ~50-150₽/заказ; Ozon ~5-15%.
- product_metrics.avg_ctr — CTR из рекламы (если 0 — данные ещё не накоплены после /sync_adv).
- product_metrics.roas — ROAS = оборот/расход. Если 0 — данные не синхронизированы.
- stock_velocity.days_left — дней осталось стока при текущем темпе продаж. 999 = нет продаж.
- Если margin_ozon пустой — Ozon-заказы есть, но маппинг SKU не позволил посчитать маржу.
{"- Цель: " + str(goal) + " ₽/день суммарно WB+Ozon." if goal else ""}

Дай конкретный анализ по формату из system prompt с 5 практическими действиями."""

        await update.message.reply_text("🤔 Анализирую…")
        try:
            answer = await self.think(prompt, chat_id=chat_id, is_task=True, max_tokens=4096)
        except Exception as e:
            logger.error(f"[Питер/report] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            notion_title=f"Отчёт {datetime.now(_UTC).strftime('%d.%m.%Y')}",
            notion_source="cmd:report",
            update=update,
        )

    async def cmd_audit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/audit — оценка магазина: здоровье, SWOT, KPI-дашборд, топ-5 действий."""
        chat_id = update.effective_user.id
        await update.message.reply_text("🏪 Провожу аудит магазина за 30 дней…")

        try:
            data = await self._collect_data(chat_id, days=30)
            adv_data = await self._collect_advanced_data(chat_id, days=30)
        except Exception as e:
            logger.error(f"[Питер/audit] ошибка сбора данных: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка сбора данных: {e}")
            return

        total_revenue = sum(float(r["revenue"] or 0) for r in data["revenue"])
        total_adv_spend = sum(float(r["spend"] or 0) for r in data["adv"])
        avg_per_day = round(total_revenue / 30, 0)
        drr_overall = round(total_adv_spend / total_revenue * 100, 1) if total_revenue else 0

        prompt = f"""Проведи полный аудит магазина. Используй формат из PETER_AUDIT_PROMPT.

ПЕРИОД: 30 дней
Оборот: {total_revenue:,.0f} ₽ ({avg_per_day:,.0f} ₽/день)
Рекл. расход: {total_adv_spend:,.0f} ₽ | Общий ДРР: {drr_overall}%

БАЗОВЫЕ ДАННЫЕ (оборот, маржа, реклама по площадкам, остатки):
{json.dumps(data, ensure_ascii=False, default=str, indent=2)}

РАСШИРЕННЫЕ ДАННЫЕ (тренд 7д, CTR/ROAS по товарам, stock velocity):
{json.dumps(adv_data, ensure_ascii=False, default=str, indent=2)}

ВАЖНО:
- Оценка X/10 должна отражать реальное состояние (не завышай и не занижай)
- Каждое действие в топ-5 — конкретный товар <code>код</code> + цифры
- CTR данные в product_metrics.avg_ctr (0 = нет данных из рекламы)
- ROAS в product_metrics.roas (0 = нет данных)
- days_left в stock_velocity (999 = нет продаж по этому товару)
- trend показывает неделя к неделе по каждой площадке
- Маржа (op_profit) — без комиссий МП и логистики. Реальная прибыль ниже ~20-30%

Используй формат PETER_AUDIT_PROMPT."""

        try:
            # Передаём audit prompt как системный через кастомный вызов
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=4096,
                system=PETER_AUDIT_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/audit] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            notion_title=f"Аудит {datetime.now(_UTC).strftime('%d.%m.%Y')}",
            notion_source="cmd:audit",
            notion_link_text="Аудит сохранён в Notion",
            update=update,
            parse_mode="HTML",
        )

    async def cmd_drr(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/drr [период=30] — ДРР по товарам и площадкам."""
        chat_id = update.effective_user.id
        days = 30
        if context.args:
            try:
                days = int(context.args[0])
            except ValueError:
                pass

        await update.message.reply_text(f"💰 Считаю ДРР за {days} дней…")

        try:
            data = await self._collect_data(chat_id, days=days)
            adv_data = await self._collect_advanced_data(chat_id, days=days)
        except Exception as e:
            logger.error(f"[Питер/drr] ошибка сбора данных: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка сбора данных: {e}")
            return

        prompt = f"""Выдай ДРР-отчёт по товарам и площадкам. Используй формат PETER_DRR_PROMPT.

ПЕРИОД: {days} дней

РЕКЛАМНЫЕ РАСХОДЫ ПО ПЛОЩАДКАМ:
{json.dumps(data["adv"], ensure_ascii=False, default=str, indent=2)}

ОБОРОТ ПО ПЛОЩАДКАМ:
{json.dumps(data["revenue"], ensure_ascii=False, default=str, indent=2)}

МЕТРИКИ ПО ТОВАРАМ (CTR, ROAS, расход, оборот):
{json.dumps(adv_data["product_metrics"], ensure_ascii=False, default=str, indent=2)}

ВАЖНО:
- ДРР = adv_spend / revenue × 100%
- ROAS = revenue / adv_spend
- Если product_metrics пустой — данные ещё не синхронизированы (/sync_adv)
- avg_ctr в процентах (2.5 = 2.5%)
- Используй display_name товаров (поле "name") если есть

Используй формат PETER_DRR_PROMPT."""

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=2048,
                system=PETER_DRR_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/drr] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            notion_title=f"ДРР {datetime.now(_UTC).strftime('%d.%m.%Y')}",
            notion_source="cmd:drr",
            update=update,
            parse_mode="HTML",
        )

    async def cmd_funnel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/funnel [период=30] — воронка конверсии карточек: показы→корзина→заказ."""
        chat_id = update.effective_user.id
        days = 30
        if context.args:
            try:
                days = int(context.args[0])
            except ValueError:
                pass

        await update.message.reply_text(f"📈 Анализирую воронку конверсии за {days} дней…")

        from db import get_pool
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        async with pool.acquire() as conn:
            funnel_rows = await conn.fetch("""
                SELECT
                    f.marketplace,
                    f.product_id,
                    COALESCE(m.display_name, f.product_id) AS name,
                    SUM(f.views)::bigint                        AS views,
                    SUM(f.add_to_cart)::bigint                  AS add_to_cart,
                    SUM(f.orders_count)::bigint                 AS orders_count,
                    SUM(f.buyouts)::bigint                      AS buyouts,
                    CASE WHEN SUM(f.views) > 0
                         THEN ROUND(SUM(f.add_to_cart)::numeric / SUM(f.views) * 100, 2)
                         ELSE 0 END                             AS conv_view_to_cart,
                    CASE WHEN SUM(f.add_to_cart) > 0
                         THEN ROUND(SUM(f.orders_count)::numeric / SUM(f.add_to_cart) * 100, 2)
                         ELSE 0 END                             AS conv_cart_to_order,
                    AVG(f.avg_position)::numeric(6,1)           AS avg_position
                FROM product_funnel_stats f
                LEFT JOIN product_mapping m
                       ON m.wb_article = f.product_id
                       OR m.ozon_sku   = f.product_id
                WHERE f.chat_id = $1 AND f.stat_date >= $2
                GROUP BY f.marketplace, f.product_id, m.display_name
                ORDER BY views DESC
                LIMIT 25
            """, chat_id, date_from)

        if not funnel_rows:
            await update.message.reply_text(
                "❌ Данных воронки нет. Запусти <code>/sync_funnel</code> у Макса для синхронизации.",
                parse_mode="HTML"
            )
            return

        funnel_data = [dict(r) for r in funnel_rows]

        prompt = f"""Период: {days} дней.

ВОРОНКА КОНВЕРСИИ ПО ТОВАРАМ:
{json.dumps(funnel_data, ensure_ascii=False, default=str, indent=2)}

Формат ответа (HTML Telegram, не длиннее 35 строк):

📈 <b>Воронка конверсии за N дней</b>

<b>Топ по показам:</b>
<code>Название</code> — показов: N | в корзину: N% | заказов: N% | выкуп: N%
[ещё 4-6 товаров]

⚠️ <b>Слабые карточки (мало показов):</b>
[Товары с показами < медианы — проблема с SEO/позицией]

🛒 <b>Плохая конверсия в корзину (&lt;3%):</b>
[Товары — проблема с карточкой/фото/ценой]

✅ <b>Лидеры конверсии:</b>
[Товары с view→cart > 10% и cart→order > 50%]

<blockquote>Главный вывод: почему какой-то товар не продаётся — мало показов или плохая карточка?</blockquote>

Если avg_position не null — упомяни среднюю позицию в поиске для WB-товаров."""

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=2048,
                system=PETER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/funnel] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            notion_title=f"Воронка {datetime.now(_UTC).strftime('%d.%m.%Y')}",
            notion_source="cmd:funnel",
            show_dashboard=False,
            update=update,
        )

    async def cmd_analyze(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/analyze <данные> — бизнес-анализ произвольных данных."""
        data = " ".join(context.args) if context.args else ""
        if not data:
            await update.message.reply_text(
                "<b>Использование:</b> /analyze &lt;данные или вопрос&gt;\n"
                "Для анализа магазинов: /report\n"
                "Для аудита: /audit\n"
                "Для ДРР: /drr",
                parse_mode="HTML",
            )
            return
        await update.message.reply_text("📊 Анализирую…")
        result = await self.handle_task(data, from_agent="команды /analyze")
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    async def run_weekly_audit(self, chat_id: int) -> None:
        """Автоматический еженедельный аудит — вызывается из планировщика."""
        logger.info(f"[Питер/weekly_audit] Запуск для chat_id={chat_id}")
        try:
            data = await self._collect_data(chat_id, days=30)
            adv_data = await self._collect_advanced_data(chat_id, days=30)
        except Exception as e:
            logger.error(f"[Питер/weekly_audit] ошибка данных: {e}")
            return

        total_revenue = sum(float(r["revenue"] or 0) for r in data["revenue"])
        total_adv_spend = sum(float(r["spend"] or 0) for r in data["adv"])
        avg_per_day = round(total_revenue / 30, 0)
        drr_overall = round(total_adv_spend / total_revenue * 100, 1) if total_revenue else 0

        prompt = f"""Еженедельный автоаудит магазина (понедельник). Краткий вариант — не более 25 строк.

Оборот: {total_revenue:,.0f} ₽ ({avg_per_day:,.0f} ₽/день) | ДРР: {drr_overall}%

ДАННЫЕ:
{json.dumps({**data, **adv_data}, ensure_ascii=False, default=str, indent=2)}

Сделай акцент на изменениях за прошлую неделю (тренд) и 3 главных действиях.
Используй формат PETER_AUDIT_PROMPT, но сокращённо."""

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=2048,
                system=PETER_AUDIT_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/weekly_audit] ошибка Claude: {e}")
            return

        try:
            await self._send_answer(
                answer,
                notion_title=f"Еженедельный аудит {datetime.now(_UTC).strftime('%d.%m.%Y')}",
                notion_source="scheduler:weekly_audit",
                show_dashboard=False,
                chat_id=chat_id,
                parse_mode="HTML",
            )
            logger.info(f"[Питер/weekly_audit] отправлен в chat_id={chat_id}")
        except Exception as e:
            logger.error(f"[Питер/weekly_audit] ошибка отправки: {e}")

    def _help_text(self) -> str:
        return (
            "📊 <b>Питер</b> — бизнес-аналитик\n\n"
            "Анализирую продажи WB и Ozon, считаю ДРР и рентабельность,\n"
            "даю конкретные рекомендации по росту.\n\n"
            "📌 <b>Команды:</b>\n"
            "/report [цель=X] [период=14] — отчёт о продажах и план роста\n"
            "/audit — полная оценка магазина (SWOT, KPI, топ-5 действий)\n"
            "/drr [период=30] — ДРР и ROAS по товарам с вердиктами\n"
            "/funnel [период=30] — воронка конверсии карточек (показы→корзина→заказ)\n"
            "/analyze &lt;вопрос&gt; — произвольный бизнес-анализ\n"
            "/reset — очистить историю\n\n"
            "💡 Пример: /report цель=100000 период=14"
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("report", "Отчёт о продажах и план роста"),
            BotCommand("audit", "Полная оценка магазина (SWOT, KPI)"),
            BotCommand("drr", "ДРР и ROAS по товарам"),
            BotCommand("funnel", "Воронка конверсии карточек"),
            BotCommand("analyze", "Произвольный бизнес-анализ"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("report",  self.cmd_report))
        self.app.add_handler(CommandHandler("analyze", self.cmd_analyze))
        self.app.add_handler(CommandHandler("audit",   self.cmd_audit))
        self.app.add_handler(CommandHandler("drr",     self.cmd_drr))
        self.app.add_handler(CommandHandler("funnel",  self.cmd_funnel))
