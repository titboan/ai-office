from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from config import config
from utils.tg_rich import send_rich_or_fallback as _send_rich
from task_queue import create_task as enqueue_task
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
- # Заголовок / ## Подраздел — крупные разделы
- --- — горизонтальный разделитель между блоками
- **текст** — ключевые числа и выводы
- *текст* — пояснения и уточнения
- `текст` — артикулы, коды товаров
- > текст — главный инсайт в рамке
- Таблица: строка `|---|---|` обязательна, перед таблицей — пустая строка
- Эмодзи в начале разделов: 📊 📈 🎯 ⚠️ ⬆️ 📸 📦 💰 🔄
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Пример структуры:
# 📊 Оборот за N дней
**Итого:** X ₽ (Y ₽/день)
**WB:** X ₽ (ДРР X%) | **Ozon:** X ₽ (ДРР X%)
*Тренд: WB ↑X% | Ozon ↓X%*

---

## 🎯 Топ-5 действий
1. ⬆️ Реклама `КБ50` +5 000₽/нед → ROAS 4.2x → +Y₽/день
2. 📸 Переделать фото `ТГ100` — CTR 0.7% (норма 2-3%), теряем X кликов/день
3. 📦 Заказать `КБ30` — осталось 8 дней, провал стока = -Y₽
4. 💰 Снизить ставку `ДС200` — ROAS 1.1, тратим X₽ в минус
5. 🔄 Перенести бюджет Ozon→WB — ДРР Ozon 35% vs WB 18%

> **Главный инсайт** одной строкой

ПРИ АНАЛИЗЕ:
- CTR < 1% — плохая инфографика → рекомендуй замену конкретных карточек
- CTR > 4% — хорошая карточка, масштабируй бюджет
- ROAS = revenue/spend. ROAS > 5 → увеличь бюджет. ROAS < 2 → стоп
- days_left < 14 → срочный дозаказ, назови товар
- ДРР норма: WB ~15-20%, Ozon ~10-15%
- Конкретные суммы, не абстрактные советы
- category — тип товара (корм, лакомства, лёгкое, корень и т.д.). Если пользователь спрашивает «по корму», «реклама на лакомства» и т.п. — анализируй ТОЛЬКО товары с нужной category, остальные не упоминай. Если category не задана у товара — укажи это.

ПРАВИЛО — маркетплейсы анализируются независимо:
- НИКОГДА не используй данные WB как прокси или оценку для Ozon, и наоборот.
- Если данных по одному МП нет — так и пиши: "нет данных по Ozon" (без подмены данными WB).
- Сравнение WB vs Ozon — ТОЛЬКО если пользователь явно попросил сравнить."""

PETER_AUDIT_PROMPT = """Ты проводишь полный аудит магазина на маркетплейсах.

Форматируй ответ в Rich Markdown для Telegram: **жирный**, *курсив*, `код`, > цитата, # Заголовок, --- разделитель.
Спецсимволы . ! ( ) - = писать как есть. Никаких HTML-тегов.

ФОРМАТ ОТВЕТА:

# 🏪 Оценка магазина: X/10
**Оборот:** X ₽/мес | **Тренд:** ↑/↓ X% | **ДРР:** X%

---

## 💪 Сильные стороны
1. [факт из данных с цифрой]
2. [факт из данных с цифрой]
3. [факт из данных с цифрой]

---

## ⚠️ Критические проблемы
1. [факт из данных с цифрой]
2. [факт из данных с цифрой]
3. [факт из данных с цифрой]

---

## 🎯 Топ-5 действий прямо сейчас
1. [действие с конкретным товаром/суммой] — [ожидаемый эффект]
2. ...
5. ...

---

## 📊 KPI-дашборд
| Метрика | Значение |
|---|---|
| Заказов/день | X |
| ДРР WB | X% |
| ДРР Ozon | X% |
| Топ-CTR | `арт` X% |
| Худший CTR | `арт` X% |
| Стоков < 14 дней | X поз. |

> **Главный вывод:** [почему магазин там где он есть и куда идти]

ПРАВИЛА:
- Оценка X/10: считай по факторам (рост оборота, CTR, ROAS, маржа, стоки, ДРР)
- Каждое действие — конкретный товар `код` + конкретная сумма/процент
- CTR < 1% → "переделать инфографику на `арт` — CTR X%, теряем X кликов/день"
- ROAS < 2 → "отключить рекламу `арт` — убыток X₽/день"
- days_left < 14 → "срочно заказать `арт` — осталось X дней"
- Если тренд недели +20% — отдельно отметь рост"""

PETER_DRR_PROMPT = """Ты анализируешь рекламную эффективность магазина. Выдай ДРР-отчёт.

Форматируй ответ в Rich Markdown для Telegram: **жирный**, *курсив*, `код`, > цитата, # Заголовок, --- разделитель.
Спецсимволы . ! ( ) - = писать как есть. Никаких HTML-тегов.

ФОРМАТ:

# 📊 ДРР-отчёт за N дней

---

## 🏪 По площадкам
| Площадка | Расход | Оборот | ДРР | Норма |
|---|---|---|---|---|
| WB | X ₽ | X ₽ | X% | 15–20% |
| Ozon | X ₽ | X ₽ | X% | 10–15% |

---

## 🔥 Топ-5 по расходу
| Товар | ДРР | ROAS | CTR | Вердикт |
|---|---|---|---|---|
| `КБ50` | X% | X.X | X% | 🟢/🟡/🔴 |

---

## ⚠️ Проблемные (ДРР > 25% или ROAS < 2)
- `Товар` — ДРР X%, убыток ~X ₽/мес → **действие**

## ✅ Лидеры (ROAS > 5)
- `Товар` — ROAS X.X, ДРР X%

---

> **Рекомендация:** [одно главное действие с суммой/товаром]

ПРАВИЛА:
- ДРР = (рекл. расход товара / выкупы товара) × 100%. Расход берётся из product_metrics[товар].adv_spend, НЕ из суммарного расхода площадки
- ROAS = выкупы / рекл. расход
- 🟢 ROAS > 5 (ДРР < 20%)
- 🟡 ROAS 2-5 (ДРР 20-50%)
- 🔴 ROAS < 2 (ДРР > 50%)
- Если данных по товару нет в product_adv_stats — укажи суммарный ДРР по площадке и добавь пояснение: "Разбивка по товарам недоступна: данные WB (nm-уровень) не поступают от API. Возможная причина: все активные WB кампании типа 4/5/6 (поиск/каталог/карточка), которые не возвращают nm-разбивку. Для получения данных нужны кампании типа 8 (Авто). Проверь в ЛК WB → Реклама → активные кампании."
- Если WB данные есть только суммарно (без разбивки по товарам) — показывай суммарный WB ДРР, но объясни почему нет детализации
- WB кампании типов 4/5/6 (поиск/каталог/карточка) не дают nm-разбивку через API. В таком случае расход распределяется поровну между товарами (приближение). Указывай "≈" перед суммой расхода WB по товарам если данные приблизительные"""


PETER_ABC_PROMPT = """Ты проводишь ABC-анализ товарного ассортимента магазина.

Форматируй ответ в Rich Markdown для Telegram: **жирный**, *курсив*, `код`, > цитата, # Заголовок, --- разделитель.
Спецсимволы . ! ( ) - = писать как есть. Никаких HTML-тегов.

ФОРМАТ ОТВЕТА:

# 🔤 ABC-анализ за N дней
**Оборот:** X ₽ | **Товаров:** N

---

## 🟢 Группа A — 80% выручки

| Товар | Выручка | Доля |
|---|---|---|
| `КБ50` | X ₽ | X% |
| `ТГ100` | X ₽ | X% |

*A-товары — приоритет. Держи остатки, масштабируй рекламу ROAS > 3.*

---

## 🟡 Группа B — следующие 15%

| Товар | Выручка | Доля |
|---|---|---|
| `ДС200` | X ₽ | X% |

*Есть потенциал перехода в A — проверь ценообразование и контент.*

---

## 🔴 Группа C — последние 5%
[кратко, если > 5 товаров — общим числом и суммарной долей]

---

## 🎯 Рекомендации
- **A:** [конкретное действие с товаром и суммой]
- **B:** [конкретное действие]
- **C:** [вывести или оставить — обоснование]

> **Вывод:** [X товаров дают Y% выручки — сфокусируй ресурсы на них]

ПРАВИЛА:
- Используй поле "name" (display_name товаров: КБ50, ТГ100 и т.д.)
- A = накопительная доля 0-80%, B = 80-95%, C = 95-100%
- Для C не перечисляй все если > 5 — сгруппируй
- Если 1-2 товара занимают > 70% — это концентрационный риск, предупреди отдельно
- Конкретные суммы и % в каждой рекомендации"""


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
        from db import get_pool, get_marketplace_shops
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        # Определяем магазины: если несколько Ozon — нужна разбивка по shop_id
        all_shops = await get_marketplace_shops(chat_id)
        ozon_shops = [s for s in all_shops if s["marketplace"] == "ozon"]
        multi_ozon = len(ozon_shops) > 1

        async def _q(conn, name: str, sql: str, *args):
            try:
                return await conn.fetch(sql, *args)
            except Exception as exc:
                logger.error(f"[Питер/_collect_data] {name}: {exc}")
                return []

        async with pool.acquire() as conn:

            # 1. Оборот по площадкам (с разбивкой по магазину при мульти-Ozon)
            revenue = await _q(conn, "revenue", """
                SELECT o.marketplace,
                       o.shop_id,
                       COALESCE(ms.shop_name, o.marketplace) AS shop_name,
                       SUM(o.seller_price * o.quantity)::numeric(12,2) AS revenue,
                       COUNT(*)                              AS orders,
                       COUNT(DISTINCT o.product_id)         AS skus
                FROM marketplace_orders o
                LEFT JOIN marketplace_shops ms ON ms.id = o.shop_id
                WHERE o.chat_id = $1 AND o.order_date >= $2
                GROUP BY o.marketplace, o.shop_id, ms.shop_name
            """, chat_id, date_from)

            # 2. Топ-10 товаров по обороту — с display_name и category из реестра
            top_products = await _q(conn, "top_products", """
                SELECT o.marketplace,
                       o.shop_id,
                       COALESCE(ms.shop_name, o.marketplace) AS shop_name,
                       o.product_id,
                       COALESCE(m.display_name, MAX(o.product_name)) AS product_name,
                       MAX(m.category)                               AS category,
                       SUM(o.seller_price * o.quantity)::numeric(12,2)      AS revenue,
                       SUM(o.quantity)                                AS qty
                FROM marketplace_orders o
                LEFT JOIN marketplace_shops ms ON ms.id = o.shop_id
                LEFT JOIN product_mapping m
                       ON m.wb_article = o.product_id
                       OR m.ozon_sku   = o.product_id
                WHERE o.chat_id = $1 AND o.order_date >= $2
                GROUP BY o.marketplace, o.shop_id, ms.shop_name, o.product_id, m.display_name
                ORDER BY revenue DESC
                LIMIT 10
            """, chat_id, date_from)

            # 3. Рентабельность WB (комиссия ~20%, логистика ~100₽/заказ)
            margin_wb = await _q(conn, "margin_wb", """
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
                JOIN product_costs c   ON c.mapping_id = m.id AND c.marketplace = 'wb'
                WHERE o.chat_id = $1 AND o.marketplace = 'wb' AND o.order_date >= $2
                GROUP BY o.product_id, m.display_name
                ORDER BY op_profit DESC
            """, chat_id, date_from)

            # 4. Рентабельность Ozon — с разбивкой по магазину при мульти-Ozon
            margin_ozon = await _q(conn, "margin_ozon", """
                SELECT
                    o.product_id,
                    o.shop_id,
                    COALESCE(ms.shop_name, 'Ozon') AS shop_name,
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
                LEFT JOIN marketplace_shops ms ON ms.id = o.shop_id
                JOIN product_mapping m ON m.ozon_sku = o.product_id
                JOIN product_costs c   ON c.mapping_id = m.id AND c.marketplace = 'ozon'
                WHERE o.chat_id = $1 AND o.marketplace = 'ozon' AND o.order_date >= $2
                GROUP BY o.product_id, o.shop_id, ms.shop_name, m.display_name
                ORDER BY op_profit DESC
            """, chat_id, date_from)

            # 5. NET-маржа из финансовых отчётов (выплата минус себестоимость минус налог от выплаты)
            # Одна строка на товар (по display_name), WB и Ozon — отдельные колонки, не отдельные
            # строки: иначе при сбое join (например "гб2.5" вместо "гб2,5" в сыром WB-артикуле)
            # товар тихо дублируется в выводе под сырым кодом вместо отображаемого имени.
            # REPLACE(...,',','.') в условии join — WB иногда отдаёт десятичный артикул через точку,
            # а в product_mapping он записан через запятую (опечатка на стороне WB, не у нас).
            TAX_RATE = config.NET_MARGIN_TAX_RATE
            # Ozon: последний полный месяц (текущий месяц даёт qty=0 — нет строк реализации)
            from datetime import date as _date
            _today = _date.today()
            _oz_month_end   = _today.replace(day=1) - timedelta(days=1)
            _oz_month_start = _oz_month_end.replace(day=1)
            net_margin_raw = await _q(conn, "net_margin", """
                SELECT
                    COALESCE(m.display_name, f.product_id) AS product_name,
                    SUM(f.quantity) FILTER (WHERE f.marketplace = 'wb')::int            AS qty_wb,
                    SUM(f.payout)   FILTER (WHERE f.marketplace = 'wb')::numeric(12,2)  AS payout_wb,
                    SUM(f.quantity) FILTER (WHERE f.marketplace = 'ozon')::int          AS qty_ozon,
                    SUM(f.payout)   FILTER (WHERE f.marketplace = 'ozon')::numeric(12,2) AS payout_ozon,
                    COALESCE(MAX(c.cost) FILTER (WHERE c.marketplace = 'wb'), 0)::numeric(12,2)   AS cost_wb,
                    COALESCE(MAX(c.cost) FILTER (WHERE c.marketplace = 'ozon'), 0)::numeric(12,2) AS cost_ozon
                FROM marketplace_financial_report f
                LEFT JOIN product_mapping m
                       -- WB: sa_name (wb_article) или nm_id (wb_nm_id) — зависит от того, что пришло в API
                       ON (f.marketplace = 'wb' AND (
                               LOWER(REPLACE(m.wb_article, ',', '.')) = LOWER(REPLACE(f.product_id, ',', '.'))
                               OR m.wb_nm_id::text = f.product_id
                           ))
                       -- Ozon: /v3/finance/transaction/list отдаёт items[].sku, не offer_id
                       OR (f.marketplace = 'ozon' AND m.ozon_sku = f.product_id)
                LEFT JOIN product_costs c ON c.mapping_id = m.id AND c.marketplace = f.marketplace
                WHERE f.chat_id = $1 AND (
                    (f.marketplace = 'wb'   AND f.report_date >= $2)
                    OR
                    (f.marketplace = 'ozon' AND f.report_date >= $3 AND f.report_date <= $4)
                )
                GROUP BY COALESCE(m.display_name, f.product_id)
                HAVING COALESCE(SUM(f.payout), 0) != 0
            """, chat_id, date_from, _oz_month_start, _oz_month_end)

            # Средняя цена продажи (с учётом скидки селлера) за тот же период — нужна, чтобы
            # перевести "требуемый payout на штуку" в рекомендованную розничную цену. payout уже
            # очищен от комиссии/логистики/хранения МП, а seller_price — это то, что видит
            # покупатель/продавец на площадке (та же цена, на которую раньше сверялись с
            # пользователем: 541,44₽ WB / 797₽ Ozon для КБ50).
            avg_price_raw = await _q(conn, "avg_price", """
                SELECT COALESCE(m.display_name, o.product_id) AS product_name, o.marketplace,
                       (SUM(o.seller_price * o.quantity) / SUM(o.quantity))::numeric(12,2) AS avg_price
                FROM marketplace_orders o
                LEFT JOIN product_mapping m
                       ON (o.marketplace = 'wb'   AND LOWER(REPLACE(m.wb_article, ',', '.')) = LOWER(REPLACE(o.product_id, ',', '.')))
                       OR (o.marketplace = 'ozon' AND m.ozon_sku = o.product_id)
                WHERE o.chat_id = $1 AND o.order_date >= $2
                  AND o.seller_price IS NOT NULL AND o.seller_price > 0
                GROUP BY o.marketplace, COALESCE(m.display_name, o.product_id)
                HAVING SUM(o.quantity) > 0
            """, chat_id, date_from)
            avg_price = {(r["product_name"], r["marketplace"]): float(r["avg_price"]) for r in avg_price_raw}

            TARGET = config.TARGET_NET_MARGIN_PCT / 100.0
            denom = (1 - TAX_RATE) - TARGET  # required_payout_per_unit = cost / denom

            def _recommend(name: str, mp: str, qty: int, payout: float, cost: float, margin_pct: float | None):
                """Целевая цена площадки для выхода на TARGET NET-маржу, или None если уже выше цели/нет данных."""
                if margin_pct is None or qty <= 0 or cost <= 0 or denom <= 0:
                    return None, margin_pct is not None and margin_pct >= TARGET * 100
                at_target = margin_pct >= TARGET * 100
                if at_target:
                    return None, True
                price = avg_price.get((name, mp))
                payout_per_unit = payout / qty
                if not price or payout_per_unit <= 0:
                    return None, False
                required_payout_per_unit = cost / denom
                take_home_ratio = payout_per_unit / price
                if take_home_ratio <= 0:
                    return None, False
                return round(required_payout_per_unit / take_home_ratio), False

            net_margin = []
            for r in net_margin_raw:
                qty_wb, payout_wb = r["qty_wb"] or 0, r["payout_wb"] or 0
                qty_ozon, payout_ozon = r["qty_ozon"] or 0, r["payout_ozon"] or 0
                cost_wb, cost_ozon = r["cost_wb"] or 0, r["cost_ozon"] or 0

                profit_wb = float(payout_wb) * (1 - TAX_RATE) - qty_wb * float(cost_wb)
                profit_ozon = float(payout_ozon) * (1 - TAX_RATE) - qty_ozon * float(cost_ozon)
                payout_total = float(payout_wb) + float(payout_ozon)
                profit_total = profit_wb + profit_ozon

                margin_pct_wb = round(profit_wb / float(payout_wb) * 100, 1) if payout_wb else None
                margin_pct_ozon = round(profit_ozon / float(payout_ozon) * 100, 1) if payout_ozon else None

                recommended_price_wb, at_target_wb = _recommend(
                    r["product_name"], "wb", qty_wb, float(payout_wb), float(cost_wb), margin_pct_wb)
                recommended_price_ozon, at_target_ozon = _recommend(
                    r["product_name"], "ozon", qty_ozon, float(payout_ozon), float(cost_ozon), margin_pct_ozon)

                net_margin.append({
                    "product_name": r["product_name"],
                    "qty_wb": qty_wb, "payout_wb": float(payout_wb),
                    "net_profit_wb": round(profit_wb, 2),
                    "net_margin_pct_wb": margin_pct_wb,
                    "recommended_price_wb": recommended_price_wb,
                    "at_target_wb": at_target_wb,
                    "qty_ozon": qty_ozon, "payout_ozon": float(payout_ozon),
                    "net_profit_ozon": round(profit_ozon, 2),
                    "net_margin_pct_ozon": margin_pct_ozon,
                    "recommended_price_ozon": recommended_price_ozon,
                    "at_target_ozon": at_target_ozon,
                    "net_profit_total": round(profit_total, 2),
                    "net_margin_pct_total": round(profit_total / payout_total * 100, 1) if payout_total else None,
                })
            net_margin.sort(key=lambda x: x["net_profit_total"], reverse=True)

            # 6. Рекламные расходы
            # Для Ozon берём реальный расход из финотчёта (marketplace_fin_adv),
            # т.к. Performance API даёт только клики (~55% от фактических расходов).
            # Для WB — Performance API покрывает все типы, финотчёт не нужен.
            # Fallback: если fin_adv пуст (до первого синка), используем perf_spend.
            adv = await _q(conn, "adv", """
                SELECT
                    a.marketplace,
                    CASE
                        WHEN a.marketplace = 'ozon' AND fa.fin_spend IS NOT NULL
                        THEN fa.fin_spend
                        ELSE a.perf_spend
                    END AS spend,
                    a.views,
                    a.clicks
                FROM (
                    SELECT marketplace,
                           SUM(spend)::numeric(12,2) AS perf_spend,
                           SUM(views)::bigint        AS views,
                           SUM(clicks)::bigint       AS clicks
                    FROM marketplace_adv_stats
                    WHERE chat_id = $1 AND stat_date >= $2
                    GROUP BY marketplace
                ) a
                LEFT JOIN (
                    SELECT marketplace,
                           SUM(adv_spend)::numeric(12,2) AS fin_spend
                    FROM marketplace_fin_adv
                    WHERE chat_id = $1 AND stat_date >= $2
                    GROUP BY marketplace
                ) fa USING (marketplace)
            """, chat_id, date_from)

            # 6. Остатки — товары с низким стоком
            low_stocks = await _q(conn, "low_stocks", """
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

            # 7. MoM тренды из ночных снимков (последние 2 месяца)
            mom = await _q(conn, "mom", """
                SELECT DATE_TRUNC('month', snapshot_date) AS month,
                       SUM(revenue)::numeric(12,2) AS revenue,
                       SUM(orders_count)::int      AS orders
                FROM daily_revenue_snapshot
                WHERE chat_id = $1 AND snapshot_date >= NOW() - INTERVAL '60 days'
                GROUP BY 1 ORDER BY 1
            """, chat_id)

            # 8. Топ возвратов за 30 дней
            returns_top = await _q(conn, "returns_top", """
                SELECT product_id, product_name,
                       SUM(returns_count)::int          AS returns_count,
                       SUM(return_amount)::numeric(12,2) AS return_amount,
                       AVG(return_rate)::numeric(6,4)    AS return_rate
                FROM product_returns_analytics
                WHERE chat_id = $1 AND stat_date >= NOW() - INTERVAL '30 days'
                GROUP BY product_id, product_name
                ORDER BY return_amount DESC
                LIMIT 10
            """, chat_id)

            # 9. Топ ключевых слов WB (для SEO-контекста)
            kw_top = await _q(conn, "kw_top", """
                SELECT DISTINCT ON (keyword) keyword, position, search_count, ctr
                FROM product_search_keywords
                WHERE chat_id = $1 AND marketplace = 'wb'
                ORDER BY keyword, search_count DESC NULLS LAST
                LIMIT 15
            """, chat_id)

            # 10. Региональная аналитика WB (откуда заказывают)
            regions_wb = await _q(conn, "regions_wb", """
                SELECT region,
                       COUNT(*)::int                                  AS orders_cnt,
                       SUM(seller_price * quantity)::numeric(12,2)    AS revenue
                FROM marketplace_orders
                WHERE chat_id = $1 AND marketplace = 'wb' AND order_date >= $2
                  AND region IS NOT NULL AND region != ''
                GROUP BY region
                ORDER BY revenue DESC
                LIMIT 10
            """, chat_id, date_from)

            # 11. Эффект инфографики — CTR до/после обновления
            infographic_ctr = await _q(conn, "infographic_ctr", """
                SELECT
                    COALESCE(m.display_name, m.wb_article) AS name,
                    m.infographic_updated_at::date          AS updated_at,
                    ROUND(AVG(CASE WHEN p.stat_date <  m.infographic_updated_at::date
                                        AND p.stat_date >= (m.infographic_updated_at - INTERVAL '14 days')::date
                                   THEN p.ctr END)::numeric, 2) AS ctr_before,
                    ROUND(AVG(CASE WHEN p.stat_date >= m.infographic_updated_at::date
                                   THEN p.ctr END)::numeric, 2) AS ctr_after,
                    COUNT(CASE WHEN p.stat_date >= m.infographic_updated_at::date THEN 1 END)::int AS days_after
                FROM product_mapping m
                LEFT JOIN product_adv_stats p
                       ON (p.product_id = m.wb_nm_id::text OR p.product_id = m.ozon_sku)
                      AND p.chat_id = $1
                      AND p.stat_date >= (m.infographic_updated_at - INTERVAL '14 days')::date
                WHERE m.chat_id = $1 AND m.infographic_updated_at IS NOT NULL
                GROUP BY m.display_name, m.wb_article, m.infographic_updated_at
                ORDER BY m.infographic_updated_at DESC
                LIMIT 10
            """, chat_id)

        return {
            "period_days":      days,
            "date_from":        date_from,
            "multi_ozon":       multi_ozon,
            "ozon_shops":       [{"id": s["id"], "name": s.get("shop_name") or "Ozon"} for s in ozon_shops],
            "revenue":          [dict(r) for r in revenue],
            "top_products":     [dict(r) for r in top_products],
            "margin_wb":        [dict(r) for r in margin_wb],
            "margin_ozon":      [dict(r) for r in margin_ozon],
            "net_margin":       [dict(r) for r in net_margin],
            "adv":              [dict(r) for r in adv],
            "low_stocks":       [dict(r) for r in low_stocks],
            "mom_trends":       [dict(r) for r in mom],
            "returns_top":      [dict(r) for r in returns_top],
            "kw_top":           [dict(r) for r in kw_top],
            "regions_wb":       [dict(r) for r in regions_wb],
            "infographic_ctr":  [dict(r) for r in infographic_ctr],
        }

    async def _collect_advanced_data(self, chat_id: int, days: int = 14) -> dict:
        """Расширенные метрики: тренд, CTR/ROAS по товарам, stock velocity."""
        from db import get_pool
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        async def _q(conn, name: str, sql: str, *args):
            try:
                return await conn.fetch(sql, *args)
            except Exception as exc:
                logger.error(f"[Питер/_collect_advanced_data] {name}: {exc}")
                return []

        async with pool.acquire() as conn:

            # 1. Тренд: текущие 7 дней vs предыдущие 7 дней
            trend = await _q(conn, "trend", """
                SELECT marketplace,
                       SUM(CASE WHEN order_date >= NOW() - INTERVAL '7 days'
                                THEN seller_price * quantity ELSE 0 END)::numeric(12,2) AS week_current,
                       SUM(CASE WHEN order_date <  NOW() - INTERVAL '7 days'
                                THEN seller_price * quantity ELSE 0 END)::numeric(12,2) AS week_prev
                FROM marketplace_orders
                WHERE chat_id = $1 AND order_date >= NOW() - INTERVAL '14 days'
                GROUP BY marketplace
            """, chat_id)

            # 2. CTR, ROAS, расход по товарам (из product_adv_stats + выкупы)
            # ROAS = выкупы (продажи, без возвратов) / расход на рекламу
            product_metrics = await _q(conn, "product_metrics", """
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
                    COALESCE(s.buyouts, 0)::numeric(12,2)           AS buyouts,
                    CASE WHEN SUM(p.spend) > 0
                         THEN ROUND(COALESCE(s.buyouts, 0) / SUM(p.spend), 2)
                         ELSE 0 END                                 AS roas,
                    CASE WHEN COALESCE(s.buyouts, 0) > 0
                         THEN ROUND(SUM(p.spend) / COALESCE(s.buyouts, 0) * 100, 2)
                         ELSE NULL END                              AS drr
                FROM product_adv_stats p
                LEFT JOIN product_mapping m
                       ON m.wb_nm_id  = p.product_id
                       OR m.ozon_sku  = p.product_id
                LEFT JOIN (
                    SELECT
                        sl.marketplace,
                        -- marketplace_sales хранит ozon_offer_id, а не ozon_sku — транслируем
                        CASE WHEN sl.marketplace = 'ozon'
                             THEN COALESCE(mm.ozon_sku, sl.product_id)
                             ELSE sl.product_id END AS key,
                        SUM(sl.price * sl.quantity)::numeric(12,2) AS buyouts
                    FROM marketplace_sales sl
                    LEFT JOIN product_mapping mm
                           ON mm.ozon_offer_id = sl.product_id
                    WHERE sl.chat_id = $1 AND sl.sale_date >= $2 AND sl.is_return = FALSE
                    GROUP BY sl.marketplace,
                             CASE WHEN sl.marketplace = 'ozon'
                                  THEN COALESCE(mm.ozon_sku, sl.product_id)
                                  ELSE sl.product_id END
                ) s ON s.marketplace = p.marketplace AND s.key = p.product_id
                WHERE p.chat_id = $1 AND p.stat_date >= $2
                GROUP BY p.product_id, m.display_name, p.marketplace, s.buyouts
                ORDER BY adv_spend DESC
                LIMIT 20
            """, chat_id, date_from)

            # 3. Stock velocity — дней осталось при текущих продажах
            stock_velocity = await _q(conn, "stock_velocity", """
                SELECT
                    s.marketplace,
                    s.product_id,
                    COALESCE(m.display_name, MAX(s.product_name)) AS name,
                    MAX(m.category)                               AS category,
                    SUM(s.stock)::integer                          AS stock,
                    COALESCE(v.daily_orders, 0)                    AS daily_orders,
                    CASE WHEN COALESCE(v.daily_orders, 0) > 0
                         THEN ROUND(SUM(s.stock) / v.daily_orders)
                         ELSE 999 END                              AS days_left
                FROM marketplace_stocks s
                LEFT JOIN product_mapping m
                       ON (s.marketplace = 'wb'   AND m.wb_article    = s.product_id)
                       OR (s.marketplace = 'ozon' AND m.ozon_offer_id = s.product_id)
                LEFT JOIN (
                    SELECT
                        o.marketplace,
                        -- marketplace_orders хранит ozon_sku, а не ozon_offer_id — транслируем
                        CASE WHEN o.marketplace = 'ozon'
                             THEN COALESCE(mm.ozon_offer_id, o.product_id)
                             ELSE o.product_id END AS key,
                        ROUND(SUM(o.quantity)::numeric / $3, 2) AS daily_orders
                    FROM marketplace_orders o
                    LEFT JOIN product_mapping mm
                           ON mm.ozon_sku = o.product_id
                    WHERE o.chat_id = $1 AND o.order_date >= $2
                    GROUP BY o.marketplace,
                             CASE WHEN o.marketplace = 'ozon'
                                  THEN COALESCE(mm.ozon_offer_id, o.product_id)
                                  ELSE o.product_id END
                ) v ON v.marketplace = s.marketplace AND v.key = s.product_id
                WHERE s.chat_id = $1
                GROUP BY s.marketplace, s.product_id, m.display_name, v.daily_orders
                ORDER BY days_left ASC
            """, chat_id, date_from, days)
            # LIMIT не ставим здесь: товар может продаваться на WB и Ozon с разной
            # скоростью, и обрезка по строкам до группировки на фронте выкидывала
            # одну из площадок (см. retrospectives/2026-06-16_dashboard-sync-roas-ozon-id-mismatch.md).
            # Товаров мало (десятки), фронт сам группирует по display_name и берёт топ-15.

            # 4. Воронка конверсии по товарам
            funnel = await _q(conn, "funnel", """
                SELECT
                    f.product_id,
                    COALESCE(m.display_name, f.product_id) AS name,
                    f.marketplace,
                    SUM(f.views)::bigint        AS views,
                    SUM(f.add_to_cart)::bigint  AS add_to_cart,
                    SUM(f.orders_count)::bigint AS orders_count,
                    SUM(f.buyouts)::bigint      AS buyouts,
                    CASE WHEN SUM(f.views) > 0
                         THEN ROUND(SUM(f.add_to_cart)::numeric / SUM(f.views) * 100, 2)
                         ELSE 0 END AS view_to_cart_pct,
                    CASE WHEN SUM(f.add_to_cart) > 0
                         THEN ROUND(SUM(f.orders_count)::numeric / SUM(f.add_to_cart) * 100, 2)
                         ELSE 0 END AS cart_to_order_pct
                FROM product_funnel_stats f
                LEFT JOIN product_mapping m
                       ON m.wb_article = f.product_id OR m.ozon_sku = f.product_id
                WHERE f.chat_id = $1 AND f.stat_date >= $2
                GROUP BY f.product_id, m.display_name, f.marketplace
                ORDER BY views DESC
                LIMIT 15
            """, chat_id, date_from)

            abc_rows = await _q(conn, "abc_rows", """
                SELECT
                    o.product_id,
                    COALESCE(m.display_name, MAX(o.product_name)) AS name,
                    SUM(o.seller_price * o.quantity)::numeric(12,2) AS revenue,
                    SUM(o.quantity)::int AS qty
                FROM marketplace_orders o
                LEFT JOIN product_mapping m ON (
                    m.wb_article = o.product_id OR m.ozon_sku = o.product_id
                )
                WHERE o.chat_id = $1 AND o.order_date >= $2
                GROUP BY o.product_id, m.display_name
                ORDER BY revenue DESC
            """, chat_id, date_from)

        total_rev = sum(float(r["revenue"] or 0) for r in abc_rows)
        abc_data: list[dict] = []
        if total_rev > 0:
            cumulative = 0.0
            for r in abc_rows:
                rev = float(r["revenue"] or 0)
                cumulative += rev
                cum_pct = cumulative / total_rev * 100
                abc_data.append({
                    "name":           r["name"],
                    "product_id":     r["product_id"],
                    "revenue":        rev,
                    "qty":            int(r["qty"] or 0),
                    "share_pct":      round(rev / total_rev * 100, 1),
                    "cumulative_pct": round(cum_pct, 1),
                    "group":          "A" if cum_pct <= 80 else ("B" if cum_pct <= 95 else "C"),
                })

        return {
            "period_days":     days,
            "date_from":       date_from,
            "trend":           [dict(r) for r in trend],
            "product_metrics": [dict(r) for r in product_metrics],
            "stock_velocity":  [dict(r) for r in stock_velocity],
            "funnel":          [dict(r) for r in funnel],
            "abc_data":        abc_data,
        }

    _PETER_NEXT_REPORT = InlineKeyboardMarkup([[
        InlineKeyboardButton("💰 ДРР",     callback_data="pnext:drr"),
        InlineKeyboardButton("🔻 Воронка", callback_data="pnext:funnel"),
        InlineKeyboardButton("🔤 ABC",     callback_data="pnext:abc"),
        InlineKeyboardButton("📋 Аудит",  callback_data="pnext:audit"),
    ]])

    _PETER_NEXT_DRR = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔻 Воронка",  callback_data="pnext:funnel"),
        InlineKeyboardButton("📊 Отчёт",   callback_data="pnext:report"),
    ]])

    _PETER_NEXT_HINTS: dict[str, str] = {
        "drr":    "💰 Запусти <code>/drr</code> — ДРР и ROAS по каждому товару",
        "funnel": "🔻 Запусти <code>/funnel</code> — воронка конверсии карточек",
        "abc":    "🔤 Запусти <code>/abc</code> — ABC-анализ: какие товары дают 80% выручки",
        "audit":  "📋 Запусти <code>/audit</code> — полный аудит магазина",
        "report": "📊 Запусти <code>/report</code> — отчёт о продажах",
    }

    async def _send_answer(
        self,
        answer: str,
        *,
        update: Update | None = None,
        chat_id: int | None = None,
        bot=None,
        after_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        _cid = chat_id or (update.effective_chat.id if update else None)
        if _cid:
            await _send_rich(self.bot_token, _cid, answer)
            if after_markup:
                await self.bot.send_message(
                    chat_id=_cid,
                    text="Что дальше?",
                    reply_markup=after_markup,
                )

    async def handle_message(
        self,
        update: "Update",
        context: "ContextTypes.DEFAULT_TYPE",
        *,
        override_text: str | None = None,
    ) -> None:
        """Прямые сообщения → handle_task (данные из БД, без симуляции tool_use)."""
        if not update.message:
            return
        user_text = override_text or (update.message.text or "")
        if not user_text:
            return
        chat_id = update.effective_chat.id
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        await update.message.reply_text("📊 Анализирую…")
        self._current_chat_id = chat_id
        try:
            result = await self.handle_task(user_text, from_agent="user")
        finally:
            self._current_chat_id = None
        await _send_rich(
            self.bot_token, chat_id, result,
            reply_to_message_id=update.message.message_id,
        )

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        logger.info(f"[Питер] Задача от {from_agent}: {task!r}")

        chat_id = getattr(self, "_current_chat_id", None) or 0

        # Dispatch коротких команд из proxy Марты (вида "__order__ [args]")
        if task.startswith("__order__"):
            import re as _re_ord
            _m = _re_ord.search(r"(\d+)", task)
            _days_ord = int(_m.group(1)) if _m else 30
            return await self._order_analysis(chat_id, _days_ord)

        import re as _re

        # Установка срока поставки: «срок поставки 14 дней», «lead_time=14», «поставка идёт 7 дней»
        _lead_match = _re.search(
            r"(?:срок\s+(?:поставки?|доставки?)|время\s+(?:поставки?|доставки?)|"
            r"поставка\s+(?:идёт|длится|занимает)|lead[_\s]?time\s*[=:]?)\s*(\d+)",
            task.lower(),
        )
        if chat_id and _lead_match:
            days_val = int(_lead_match.group(1))
            from db import set_user_setting
            await set_user_setting(chat_id, "supply_lead_days", str(days_val))
            return (
                f"✅ Срок поставки обновлён: *{days_val} дней* от заказа до склада МП.\n"
                f"_Применяется в /supply и /order._"
            )

        # Установка страхового буфера: «буфер 7 дней», «safety=7», «страховой запас 10»
        _safety_match = _re.search(
            r"(?:буфер|страховой\s+запас|safety)\s*[=:]?\s*(\d+)",
            task.lower(),
        )
        if chat_id and _safety_match:
            days_val = int(_safety_match.group(1))
            from db import set_user_setting
            await set_user_setting(chat_id, "supply_safety_days", str(days_val))
            return (
                f"✅ Страховой буфер обновлён: *{days_val} дней*.\n"
                f"_Применяется в /supply и /order._"
            )

        _days_match = _re.search(r"за\s+(\d+)\s+дн", task.lower())
        _days = int(_days_match.group(1)) if _days_match else 14

        _seo_audit_kw = (
            "seo аудит", "аудит seo", "seo анализ", "анализ seo",
            "слабые карточки", "карточки переделать", "переделать карточки",
            "какие карточки", "приоритет seo", "seo приоритет",
        )
        if chat_id and any(kw in task.lower() for kw in _seo_audit_kw):
            try:
                seo_data = await self._collect_seo_audit_data(chat_id, days=30)
                prompt = (
                    f"Аналитическая задача от {from_agent}: {task}\n\n"
                    f"SEO-ДАННЫЕ ПО ТОВАРАМ (30 дней, urgency по убыванию):\n"
                    f"{json.dumps(seo_data[:20], ensure_ascii=False, default=str, indent=2)}"
                )
                # Cache problematic articles so /seo_audit buttons work immediately after
                problematic = [p for p in seo_data if p.get("issues")]
                if problematic:
                    articles_payload = json.dumps([
                        {"article": p["article"], "marketplace": p["marketplace"], "name": p["name"]}
                        for p in problematic
                    ])
                    await self._redis_set(f"seo_audit:{chat_id}", articles_payload, ttl=3600)
            except Exception as e:
                logger.warning(f"[Питер] handle_task seo_audit: ошибка данных: {e}")
                prompt = ""
        else:
            prompt = ""

        _supply_kw = ("поставк", "поставить", "регион", "склад", "кластер", "везти", "отгрузк")
        if not prompt and chat_id and any(kw in task.lower() for kw in _supply_kw):
            try:
                supply_data = await self._collect_supply_data(chat_id, days=14)
                prompt = (
                    f"Аналитическая задача от {from_agent}: {task}\n\n"
                    f"ДАННЫЕ ПО ОСТАТКАМ, В ПУТИ И ПРОДАЖАМ (14 дней):\n"
                    f"stock = на складе МП, in_transit = в пути (аналитика остатков).\n"
                    f"supply_committed = qty уже оформленных активных поставок (не суммировать с in_transit).\n"
                    f"to_order = заказать у поставщика СВЕРХ (in_transit + supply_committed) — уже рассчитано.\n"
                    f"mkt_supplies[].warehouse_name — склад назначения поставки (WB: officeName, Ozon: из заявки).\n"
                    f"ozon_warehouses = [{{'warehouse_name': ..., 'stock': ...}}] — текущий FBO сток по складам Ozon.\n"
                    f"Если ozon_warehouses НЕ пуст — показывай реальные склады.\n"
                    f"Если ozon_warehouses ПУСТ — пиши 'данных по складам Ozon нет' и не используй WB как замену.\n"
                    f"lead_days (в данных каждого товара) — дней от заказа поставщику до поставки на склад МП.\n"
                    f"total_days_left < lead_days → 🔴 КРИТИЧНО (кончится до прихода партии).\n"
                    f"wb_open_warehouses — склады WB, принимающие сейчас (коэф. > 0 из WB API); рекомендуй только их.\n"
                    f"clusters[].cluster_dr — точный per-cluster темп продаж (шт/день): WB — из регионов заказов, Ozon — из аналитики складов.\n"
                    f"clusters[].days_left — запас кластера в днях по cluster_dr.\n"
                    f"clusters[].need — нужно отправить в кластер для TARGET_DAYS запаса.\n"
                    f"category — категория товара (из product_mapping).\n"
                    f"{json.dumps(supply_data, ensure_ascii=False, default=str, indent=2)}"
                )
            except Exception as e:
                logger.warning(f"[Питер] handle_task supply: ошибка данных: {e}")
                prompt = ""

        data_str = ""
        if chat_id and not prompt:
            try:
                data = await self._collect_data(chat_id, days=_days)
                adv_data = await self._collect_advanced_data(chat_id, days=_days)

                funnel_note = ""
                if not adv_data.get("funnel"):
                    funnel_note = (
                        "\n\nВОРОНКА: данных в product_funnel_stats нет — НЕ строй воронку "
                        "из рекламных данных и НЕ придумывай цифры. Скажи пользователю "
                        "запустить /sync_funnel у Макса, чтобы загрузить реальные данные воронки."
                    )

                data_str = (
                    f"\n\nБАЗОВЫЕ ДАННЫЕ ({_days} дней):\n"
                    f"{json.dumps(data, ensure_ascii=False, default=str, indent=2)}\n\n"
                    f"РАСШИРЕННЫЕ ДАННЫЕ (тренд, CTR/ROAS, остатки):\n"
                    f"{json.dumps(adv_data, ensure_ascii=False, default=str, indent=2)}"
                    f"{funnel_note}"
                )
            except Exception as e:
                logger.warning(f"[Питер] handle_task: ошибка сбора данных: {e}")
                data_str = f"\n\n⚠️ Ошибка загрузки данных из БД: {e}\nСообщи пользователю что данные временно недоступны и предложи попробовать позже."

        if not prompt:
            prompt = f"Аналитическая задача от {from_agent}: {task}{data_str}"
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.MAX_TOKENS,
                system=PETER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.warning(f"[Питер] handle_task: ошибка Claude: {e}")
            answer = f"⚠️ Ошибка анализа: {e}"
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

        comp_data = []
        try:
            from db import get_competitor_snapshots
            comp_data = await get_competitor_snapshots(weeks=4)
        except Exception:
            pass

        # Сохраняем рекомендованные цены в БД для Макса (/apply_prices)
        recs = [
            {
                "display_name":          r["product_name"],
                "recommended_price_wb":  r.get("recommended_price_wb"),
                "recommended_price_ozon": r.get("recommended_price_ozon"),
            }
            for r in data.get("net_margin", [])
            if r.get("recommended_price_wb") or r.get("recommended_price_ozon")
        ]
        if recs:
            try:
                from db import save_price_recommendations
                await save_price_recommendations(chat_id, recs)
            except Exception as e:
                logger.warning(f"[Питер/report] save_price_recommendations: {e}")

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

        mom_str = ""
        if data.get("mom_trends"):
            mom_str = (
                f"\n\nMoM ТРЕНДЫ (помесячно из ночных снимков):\n"
                f"{json.dumps(data['mom_trends'], ensure_ascii=False, default=str, indent=2)}"
            )

        returns_str = ""
        if data.get("returns_top"):
            returns_str = (
                f"\n\nВОЗВРАТЫ (топ за 30 дней):\n"
                f"{json.dumps(data['returns_top'], ensure_ascii=False, default=str, indent=2)}"
            )

        kw_str = ""
        if data.get("kw_top"):
            kw_str = (
                f"\n\nКЛЮЧЕВЫЕ СЛОВА WB (топ по охвату):\n"
                f"{json.dumps(data['kw_top'], ensure_ascii=False, default=str, indent=2)}"
            )

        comp_str = ""
        if comp_data:
            comp_str = (
                f"\n\nЦЕНЫ КОНКУРЕНТОВ WB (снапшот, медиана топ-100 по нише, последние 4 нед.):\n"
                f"{json.dumps(comp_data, ensure_ascii=False, default=str, indent=2)}"
            )

        infographic_str = ""
        if data.get("infographic_ctr"):
            infographic_str = (
                f"\n\nЭФФЕКТ ИНФОГРАФИКИ (CTR до/после обновления):\n"
                f"{json.dumps(data['infographic_ctr'], ensure_ascii=False, default=str, indent=2)}"
            )

        prompt = f"""Проанализируй данные магазинов за последние {days} дней.
{goal_str}

БАЗОВЫЕ ДАННЫЕ:
{json.dumps(data, ensure_ascii=False, default=str, indent=2)}

РАСШИРЕННЫЕ ДАННЫЕ (тренд, CTR/ROAS по товарам, остатки):
{json.dumps(adv_data, ensure_ascii=False, default=str, indent=2)}{mom_str}{returns_str}{kw_str}{comp_str}{infographic_str}

ВАЖНО:
- Данные по заказам, не по выкупам. Реальная выручка ниже на % возвратов.
- net_margin — ОСНОВНОЙ показатель рентабельности: payout − себестоимость − налог {int(config.NET_MARGIN_TAX_RATE*100)}% от payout. Используй его, не margin_wb/margin_ozon.
- net_margin: одна строка = один товар (product_name), без дублей. qty_wb/payout_wb/net_profit_wb/net_margin_pct_wb — показатели по WB, аналогично _ozon — по Ozon, _total — сумма по обеим площадкам. Если qty_wb=0 — товара нет на WB (аналогично для Ozon), не показывай нулевую колонку как площадку с продажами.
- Целевая NET-маржа — {config.TARGET_NET_MARGIN_PCT:.0f}% (config.TARGET_NET_MARGIN_PCT). at_target_wb/at_target_ozon=true → площадка уже на цели или выше, отмечай "✅ норма", не выводи лишних чисел. Если false и recommended_price_wb/ozon не null — это целевая розничная цена площадки, при которой маржа выйдет на {config.TARGET_NET_MARGIN_PCT:.0f}%; явно называй её "рекомендованная цена" и говори, на сколько ₽/% поднять текущую цену. Если recommended_price_* = null при at_target=false — не хватает данных (нет себестоимости через /cost или нет заказов за период) — скажи это прямо, не придумывай число.
- Формируй net_margin как одну таблицу: товар | WB (шт/прибыль/%/рекоменд. цена) | Ozon (шт/прибыль/%/рекоменд. цена) | Итого (прибыль/%). Список короткий (товаров немного) — выводи ВСЕ строки, не выбирай топ-N.
- Если net_margin пустой — запусти /sync_fin у Макса. Только тогда временно используй margin_wb/margin_ozon (GROSS, без комиссий МП и без налога — переоценивает прибыль) и явно предупреди, что это грубая оценка.
- Комиссия WB ~15-25%, логистика ~50-150₽/заказ; Ozon ~5-15%.
- product_metrics.avg_ctr — CTR из рекламы (если 0 — данные ещё не накоплены после /sync_adv).
- product_metrics.roas — ROAS = выкупы (продажи без возвратов)/расход на рекламу. Если 0 — данные не синхронизированы.
- КРИТИЧНО: data["adv"] — это суммарный рекламный расход по площадке (ВСЕ товары вместе). При анализе конкретного товара (КБ50, ТГ100 и т.д.) строку "Реклама" и ДРР считай ТОЛЬКО из product_metrics[товар].adv_spend. Никогда не подставляй data["adv"].spend как расход отдельного товара.
- stock_velocity.days_left — дней осталось стока при текущем темпе продаж. 999 = нет продаж.
- Если margin_ozon пустой — Ozon-заказы есть, но маппинг SKU не позволил посчитать маржу.
- multi_ozon=True означает несколько магазинов Ozon. В revenue и margin_ozon есть поле shop_name — используй его вместо «Ozon» при мульти-Ozon, показывай каждый магазин отдельной строкой. В net_margin данные Ozon суммированы по всем магазинам — укажи это в отчёте.
- mom_trends — помесячная выручка и заказы за последние 60 дней. Если 2 месяца — посчитай MoM рост: (текущий месяц / предыдущий − 1) × 100%. Выведи одной строкой в блоке отчёта.
- returns_top — товары с наибольшей суммой возвратов за 30 дней (если есть данные после /sync_returns). Укажи топ-3 по return_amount и возможные причины. Если пусто — данные не синхронизированы (/sync_returns у Макса).
- kw_top — топ ключевых слов WB по охвату (если есть данные после /sync_keywords). Укажи ключи с лучшей позицией (чем меньше число, тем выше в поиске) и наибольшим search_count. Если пусто — данные не синхронизированы (/sync_keywords у Макса).
- infographic_ctr — эффект замены инфографики: ctr_before/ctr_after в %. days_after = сколько дней прошло после загрузки. Если ctr_after IS NULL или days_after < 7 — данных ещё недостаточно (накапливается), напиши "CTR ещё накапливается (X дн. из 14)". Если есть оба значения — покажи дельту: было → стало (+X% или −X%). Блок выводить только если список непустой.
{"- Цель: " + str(goal) + " ₽/день суммарно WB+Ozon." if goal else ""}
{"- ЦЕНЫ КОНКУРЕНТОВ: median_price — медиана топ-100 товаров WB по ключевому запросу ниши. Сравни свои цены (из product_mapping через adv_data) с медианой. Если цена выше медианы >15% — укажи это как риск; если ниже — возможность поднять." if comp_data else ""}

Дай конкретный анализ по формату из system prompt с 5 практическими действиями."""

        await update.message.reply_text("🤔 Анализирую…")
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=4096,
                system=PETER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/report] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            update=update,
            after_markup=self._PETER_NEXT_REPORT,
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
- Каждое действие в топ-5 — конкретный товар `код` + цифры
- CTR данные в product_metrics.avg_ctr (0 = нет данных из рекламы)
- ROAS в product_metrics.roas (0 = нет данных)
- days_left в stock_velocity (999 = нет продаж по этому товару)
- trend показывает неделя к неделе по каждой площадке
- Используй net_margin (выплата − себестоимость − налог {int(config.NET_MARGIN_TAX_RATE*100)}%) как маржу. margin_wb/margin_ozon — только запасной грубый ориентир, если net_margin пуст (без комиссий МП и налога, переоценивает прибыль)

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
            update=update,
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

РЕКЛАМНЫЕ РАСХОДЫ ИТОГО ПО ПЛОЩАДКЕ (все товары вместе, только для строки "По площадкам"):
{json.dumps(data["adv"], ensure_ascii=False, default=str, indent=2)}

ОБОРОТ ИТОГО ПО ПЛОЩАДКЕ (все товары вместе):
{json.dumps(data["revenue"], ensure_ascii=False, default=str, indent=2)}

МЕТРИКИ ПО КАЖДОМУ ТОВАРУ (adv_spend здесь — расход конкретного товара, используй для per-product анализа):
{json.dumps(adv_data["product_metrics"], ensure_ascii=False, default=str, indent=2)}

ВАЖНО:
- ДРР = adv_spend / buyouts × 100%
- ROAS = buyouts / adv_spend (выкупы, без возвратов)
- КРИТИЧНО: для строк по конкретному товару (КБ50 и т.д.) используй adv_spend из МЕТРИКИ ПО КАЖДОМУ ТОВАРУ, а НЕ итоговый расход площадки из РЕКЛАМНЫЕ РАСХОДЫ ИТОГО. Это разные числа.
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
            update=update,
            after_markup=self._PETER_NEXT_DRR,
        )

    async def run_drr_for_chat(self, chat_id: int, days: int = 30) -> None:
        """Запустить ДРР-отчёт по chat_id без Update (вызов из другого агента)."""
        await self.bot.send_message(chat_id, f"💰 Считаю ДРР за {days} дней…")
        try:
            data = await self._collect_data(chat_id, days=days)
            adv_data = await self._collect_advanced_data(chat_id, days=days)
        except Exception as e:
            logger.error(f"[Питер/drr] ошибка сбора данных: {e}", exc_info=True)
            await self.bot.send_message(chat_id, f"❌ Ошибка сбора данных: {e}")
            return

        prompt = f"""Выдай ДРР-отчёт по товарам и площадкам. Используй формат PETER_DRR_PROMPT.

ПЕРИОД: {days} дней

РЕКЛАМНЫЕ РАСХОДЫ ПО ПЛОЩАДКАМ:
{json.dumps(data["adv"], ensure_ascii=False, default=str, indent=2)}

ОБОРОТ ПО ПЛОЩАДКАМ:
{json.dumps(data["revenue"], ensure_ascii=False, default=str, indent=2)}

МЕТРИКИ ПО ТОВАРАМ (CTR, ROAS, расход, выкупы):
{json.dumps(adv_data["product_metrics"], ensure_ascii=False, default=str, indent=2)}

ВАЖНО:
- ДРР = adv_spend / buyouts × 100%
- ROAS = buyouts / adv_spend (выкупы, без возвратов)
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
            await self.bot.send_message(chat_id, f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            chat_id=chat_id,
            after_markup=self._PETER_NEXT_DRR,
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

Формат ответа (Rich Markdown, не длиннее 35 строк):

📈 **Воронка конверсии за N дней**

**Топ по показам:**
`Название` — показов: N | в корзину: N% | заказов: N% | выкуп: N%
[ещё 4-6 товаров]

⚠️ **Слабые карточки (мало показов):**
[Товары с показами < медианы — проблема с SEO/позицией]

🛒 **Плохая конверсия в корзину (<3%):**
[Товары — проблема с карточкой/фото/ценой]

✅ **Лидеры конверсии:**
[Товары с view→cart > 10% и cart→order > 50%]

> Главный вывод: почему какой-то товар не продаётся — мало показов или плохая карточка?

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
            update=update,
        )

    async def _get_wb_open_warehouses(self, chat_id: int) -> dict[str, int] | None:
        """Склады WB, принимающие поставки короба сейчас (из Redis-кеша или WB API).

        Возвращает {warehouseName: coefficient} или None если API недоступен.
        Кеш: 6 часов в Redis (данные меняются раз в сутки).
        """
        cache_key = f"wb_acceptance:{chat_id}"
        cached = await self._redis_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        try:
            from db import get_marketplace_shops
            from tools.marketplace import WBClient
            shops = await get_marketplace_shops(chat_id)
            wb_shop = next((s for s in shops if s["marketplace"] == "wb"), None)
            if not wb_shop:
                return None

            wb = WBClient(wb_shop["api_token"])
            coefficients = await wb.get_acceptance_coefficients(box_type="Короб")
            if not coefficients:
                return None

            # Берём уникальные склады, принимающие сейчас (coeff > 0); если склад встречается
            # несколько раз (разные даты) — берём максимальный коэффициент текущего дня.
            from datetime import date as _date
            today = str(_date.today())
            today_entries = [c for c in coefficients if c.get("date", "").startswith(today)]
            source = today_entries if today_entries else coefficients

            result: dict[str, int] = {}
            for c in source:
                name = c["warehouseName"]
                coeff = int(c.get("coefficient", 0))
                if coeff > 0:
                    result[name] = max(result.get(name, 0), coeff)

            if result:
                await self._redis_set(cache_key, json.dumps(result, ensure_ascii=False), ttl=6 * 3600)
            return result or None
        except Exception as e:
            logger.warning(f"[Питер] _get_wb_open_warehouses: {e}")
            return None

    async def _get_lead_days(self, chat_id: int) -> int:
        """Срок поставки от поставщика до МП: из настроек пользователя или config."""
        from db import get_user_setting
        raw = await get_user_setting(chat_id, "supply_lead_days")
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
        return config.SUPPLY_LEAD_TIME_DAYS

    async def _get_safety_days(self, chat_id: int) -> int:
        """Страховой буфер запаса: из настроек пользователя или config."""
        from db import get_user_setting
        raw = await get_user_setting(chat_id, "supply_safety_days")
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
        return config.SUPPLY_SAFETY_STOCK_DAYS

    async def _get_ozon_warehouse_demand(self, chat_id: int, days: int) -> dict[tuple, float]:
        """Per-(offer_id, cluster) спрос Ozon из аналитики по складам (кеш 6ч).

        Ключ: (offer_id, cluster) → daily_rate (шт/день).
        Данные из /v1/analytics/data с dimension ['sku', 'warehouse'],
        SKU транслируется в offer_id через product_mapping.
        """
        import json as _json
        from db import get_pool, get_marketplace_shops
        from tools.marketplace import make_client
        from agents.max import _get_ozon_cluster

        cache_key = f"ozon_wh_demand:{chat_id}:{days}"
        cached = await self._redis_get(cache_key)
        if cached:
            try:
                return {tuple(k.split("|", 1)): v for k, v in _json.loads(cached).items()}
            except Exception:
                pass

        shops = await get_marketplace_shops(chat_id)
        ozon_shops = [s for s in shops if s["marketplace"] == "ozon"]
        if not ozon_shops:
            return {}

        pool = await get_pool()
        async with pool.acquire() as conn:
            sku_rows = await conn.fetch(
                "SELECT ozon_sku::text AS sku, ozon_offer_id FROM product_mapping "
                "WHERE chat_id = $1 AND ozon_sku IS NOT NULL AND ozon_offer_id IS NOT NULL",
                chat_id,
            )
        sku_to_offer: dict[str, str] = {r["sku"]: r["ozon_offer_id"] for r in sku_rows}

        date_from_str = (datetime.now(_UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        date_to_str   = datetime.now(_UTC).strftime("%Y-%m-%d")

        result: dict[tuple, float] = {}
        for shop in ozon_shops:
            try:
                client = make_client(shop)
                wh_demand = await client.get_warehouse_demand(date_from_str, date_to_str)
                for row in wh_demand:
                    offer_id = sku_to_offer.get(row["sku"])
                    if not offer_id:
                        continue
                    cluster = _get_ozon_cluster(row["warehouse_name"])
                    key = (offer_id, cluster)
                    result[key] = result.get(key, 0.0) + row["qty"] / days
            except Exception as e:
                logger.warning(f"[Питер/_get_ozon_warehouse_demand] shop {shop.get('id')}: {e}")

        if result:
            serialized = {f"{k[0]}|{k[1]}": v for k, v in result.items()}
            await self._redis_set(cache_key, _json.dumps(serialized), ttl=6 * 3600)

        return result

    async def _collect_supply_data(self, chat_id: int, days: int = 14) -> dict:
        """Остатки по складам/кластерам + темп продаж + товары в пути для плана поставки."""
        from db import get_pool
        from agents.max import _get_cluster, _get_ozon_cluster, _get_cluster_from_region
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        # Живые данные о доступности складов WB (коэффициенты приёмки, кеш 6ч)
        wb_open_warehouses = await self._get_wb_open_warehouses(chat_id)

        # Точный per-cluster спрос Ozon из аналитики по складам (кеш 6ч)
        ozon_cluster_dr = await self._get_ozon_warehouse_demand(chat_id, days)

        async with pool.acquire() as conn:
            raw_stocks = await conn.fetch("""
                SELECT s.marketplace, s.product_id, s.warehouse_name,
                       SUM(s.stock)::int AS stock,
                       COALESCE(m.display_name, MAX(s.product_name)) AS name,
                       MAX(m.category) AS category
                FROM marketplace_stocks s
                LEFT JOIN product_mapping m
                       ON (s.marketplace = 'wb'   AND m.wb_article    = s.product_id)
                       OR (s.marketplace = 'ozon' AND m.ozon_offer_id = s.product_id)
                WHERE s.chat_id = $1
                GROUP BY s.marketplace, s.product_id, s.warehouse_name, m.display_name
            """, chat_id)

            velocity_raw = await conn.fetch("""
                SELECT o.marketplace,
                       CASE WHEN o.marketplace = 'ozon'
                            THEN COALESCE(moz.ozon_offer_id, o.product_id)
                            ELSE o.product_id END AS key,
                       COALESCE(mwb.display_name, moz.display_name, MAX(o.product_name)) AS name,
                       ROUND(SUM(o.quantity)::numeric / $2, 2) AS daily_rate
                FROM marketplace_orders o
                LEFT JOIN LATERAL (
                    SELECT ozon_offer_id, display_name FROM product_mapping
                    WHERE ozon_sku = o.product_id LIMIT 1
                ) moz ON o.marketplace = 'ozon'
                LEFT JOIN LATERAL (
                    SELECT display_name FROM product_mapping
                    WHERE wb_article = o.product_id LIMIT 1
                ) mwb ON o.marketplace = 'wb'
                WHERE o.chat_id = $1 AND o.order_date >= $3
                GROUP BY o.marketplace,
                         CASE WHEN o.marketplace = 'ozon'
                              THEN COALESCE(moz.ozon_offer_id, o.product_id)
                              ELSE o.product_id END,
                         COALESCE(mwb.display_name, moz.display_name)
                HAVING SUM(o.quantity) > 0
            """, chat_id, days, date_from)

            # Товары в пути на склад (от поставщика к МП)
            in_transit_raw = await conn.fetch("""
                SELECT marketplace, product_id, qty
                FROM marketplace_in_transit
                WHERE chat_id = $1
            """, chat_id)

            # Статусы поставок на МП с разбивкой по товарам и складам
            supply_orders_raw = await conn.fetch("""
                SELECT marketplace, supply_id, status_id, status_name,
                       product_id, qty, warehouse_name
                FROM marketplace_supply_orders
                WHERE chat_id = $1
                  AND product_id != ''
                ORDER BY marketplace, product_id, status_name
            """, chat_id)

            # Per-region спрос WB для точного расчёта per-cluster daily_rate
            wb_region_demand_raw = await conn.fetch("""
                SELECT product_id, region,
                       ROUND(SUM(quantity)::numeric / $2, 3) AS daily_rate
                FROM marketplace_orders
                WHERE chat_id = $1 AND marketplace = 'wb'
                  AND order_date >= $3
                  AND region IS NOT NULL AND region != ''
                GROUP BY product_id, region
                HAVING SUM(quantity) > 0
            """, chat_id, days, date_from)


        velocity: dict[tuple, float] = {
            (r["marketplace"], r["key"]): float(r["daily_rate"]) for r in velocity_raw
        }
        velocity_names: dict[tuple, str] = {
            (r["marketplace"], r["key"]): r["name"] or r["key"] for r in velocity_raw
        }
        # in_transit по (marketplace, product_id)
        in_transit_map: dict[tuple, int] = {
            (r["marketplace"], r["product_id"]): int(r["qty"]) for r in in_transit_raw
        }
        # supply_orders_map: (marketplace, product_id) → [{status_name, qty, warehouse_name}]
        supply_orders_map: dict[tuple, list[dict]] = {}
        for r in supply_orders_raw:
            key = (r["marketplace"], r["product_id"])
            if key not in supply_orders_map:
                supply_orders_map[key] = []
            supply_orders_map[key].append({
                "status":         r["status_name"],
                "qty":            int(r["qty"]),
                "warehouse_name": r.get("warehouse_name") or "",
            })

        # WB per-cluster daily_rate из регионов покупателей.
        # Ключ: (product_id, cluster) → daily_rate.
        # Позволяет точно вычислить days_left и need для каждого WB-кластера
        # вместо использования суммарного темпа по всем кластерам.
        wb_cluster_dr: dict[tuple, float] = {}
        for r in wb_region_demand_raw:
            cluster = _get_cluster_from_region(r["region"])
            key = (r["product_id"], cluster)
            wb_cluster_dr[key] = wb_cluster_dr.get(key, 0.0) + float(r["daily_rate"])


        ozon_rows = [(row["product_id"], row["warehouse_name"] or "", row["stock"] or 0)
                     for row in raw_stocks if row["marketplace"] == "ozon"]
        logger.info(f"[Питер/supply] Ozon строки из БД: {ozon_rows}")

        cluster_stocks: dict[str, dict] = {}
        for row in raw_stocks:
            mp = row["marketplace"]
            pid = row["product_id"]
            wh = row["warehouse_name"] or ""
            cluster = _get_cluster(wh) if mp == "wb" else _get_ozon_cluster(wh)
            name = row["name"] or pid
            stock = row["stock"] or 0

            key = (mp, pid)
            daily_rate = velocity.get(key, 0.0)
            name = velocity_names.get(key, name)

            prod_key = f"{mp}:{name}"
            if prod_key not in cluster_stocks:
                cluster_stocks[prod_key] = {
                    "name": name, "marketplace": mp, "daily_rate": daily_rate,
                    "product_id": pid,
                    "category": row["category"] or "",
                    "clusters": {},
                    "ozon_warehouses": {},  # реальные имена складов Ozon из БД
                }
            entry = cluster_stocks[prod_key]
            entry["clusters"][cluster] = entry["clusters"].get(cluster, 0) + stock
            if mp == "ozon" and wh:
                entry["ozon_warehouses"][wh] = entry["ozon_warehouses"].get(wh, 0) + stock
            if daily_rate and entry["daily_rate"] == 0:
                entry["daily_rate"] = daily_rate

        TARGET_DAYS = 45
        lead_days = await self._get_lead_days(chat_id)
        safety_days = await self._get_safety_days(chat_id)
        result = []
        for prod_data in sorted(cluster_stocks.values(), key=lambda x: -x["daily_rate"]):
            dr = prod_data["daily_rate"]
            mp = prod_data["marketplace"]
            pid = prod_data["product_id"]
            in_transit = in_transit_map.get((mp, pid), 0)

            total_stock = sum(prod_data["clusters"].values())
            total_need = max(0, round(TARGET_DAYS * dr - total_stock)) if dr > 0 else 0
            mkt_supplies = supply_orders_map.get((mp, pid), [])

            # Статусы поставок, уже находящихся в процессе (вычитаем из to_order)
            _WB_COMMITTED  = {"Запланировано", "Отгрузка разрешена", "В пути", "Транзит", "Идёт приёмка"}
            _OZON_COMMITTED = {"Новая", "Готова к отгрузке", "В пути", "Принята на склад"}
            committed_statuses = _WB_COMMITTED if mp == "wb" else _OZON_COMMITTED
            supply_committed = sum(
                s["qty"] for s in mkt_supplies
                if s["status"] in committed_statuses
            )
            # Сколько ещё заказывать у поставщика (сверх in_transit + уже оформленных поставок)
            to_order = max(0, total_need - in_transit - supply_committed)

            clusters_out = []
            for cl, stock in sorted(prod_data["clusters"].items()):
                # Per-cluster daily_rate:
                # WB — из регионов заказов (точно); fallback на пропорцию стока
                # Ozon — из аналитики по складам (/v1/analytics/data, точно);
                #        fallback на пропорцию стока если API не вернул данных
                if mp == "wb":
                    cl_dr = wb_cluster_dr.get((pid, cl))
                    if cl_dr is None and total_stock > 0:
                        cl_dr = dr * stock / total_stock
                    cl_dr = cl_dr or 0.0
                else:
                    cl_dr = ozon_cluster_dr.get((pid, cl))
                    if cl_dr is None and total_stock > 0:
                        cl_dr = dr * stock / total_stock
                    cl_dr = cl_dr or 0.0

                days_left_wh = round(stock / cl_dr, 1) if cl_dr > 0 else 999
                need_cluster = max(0, round(TARGET_DAYS * cl_dr - stock)) if cl_dr > 0 else 0
                clusters_out.append({
                    "cluster": cl,
                    "stock": stock,
                    "cluster_dr": round(cl_dr, 2),  # per-cluster темп (шт/день)
                    "days_left": days_left_wh,
                    "need": need_cluster,
                })

            total_days_left = round(total_stock / dr, 1) if dr > 0 else 999

            # Реальные склады Ozon из marketplace_stocks: [{warehouse_name, stock}]
            raw_ozon_wh = prod_data.get("ozon_warehouses", {})
            ozon_warehouses_out = [
                {"warehouse_name": wh_name, "stock": wh_stock}
                for wh_name, wh_stock in sorted(raw_ozon_wh.items(), key=lambda x: -x[1])
                if wh_name
            ]

            result.append({
                "name": prod_data["name"],
                "marketplace": prod_data["marketplace"],
                "category": prod_data.get("category", ""),
                "daily_rate": float(dr),
                "target_days": TARGET_DAYS,
                "lead_days": lead_days,
                "safety_days": safety_days,
                "in_transit": in_transit,              # в пути к складу МП (из аналитики остатков)
                "supply_committed": supply_committed,  # уже оформлено в поставках МП
                "mkt_supplies": mkt_supplies,          # [{status, qty, warehouse_name}]
                "total_stock": total_stock,
                "total_days_left": total_days_left,
                "total_need": total_need,
                "to_order": to_order,                  # заказать у поставщика сверх in_transit + supply_committed
                "clusters": clusters_out,
                "ozon_warehouses": ozon_warehouses_out,  # [{warehouse_name, stock}] — реальные склады
            })

        return {
            "products": result,
            "days_analyzed": days,
            "wb_open_warehouses": wb_open_warehouses,  # {name: coeff} или None если API недоступен
        }

    async def cmd_supply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/supply [период=14] — план поставок по регионам/кластерам."""
        chat_id = update.effective_user.id
        days = 14
        for tok in (context.args or []):
            if tok.startswith("период="):
                try:
                    days = int(tok.split("=", 1)[1])
                except ValueError:
                    pass

        await update.message.reply_text(f"📦 Анализирую остатки по складам за {days} дней…")
        try:
            supply_data = await self._collect_supply_data(chat_id, days=days)
        except Exception as e:
            logger.error(f"[Питер/supply] ошибка данных: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")
            return

        lead = await self._get_lead_days(chat_id)
        safety = await self._get_safety_days(chat_id)

        # Классифицируем срочность в Python — не доверяем это Claude
        products = supply_data["products"]
        for p in products:
            tl = p["total_days_left"]
            if tl < lead:
                p["urgency"] = "КРИТИЧНО"   # кончится до прихода партии
            elif tl < lead + safety:
                p["urgency"] = "СРОЧНО"     # пора заказывать
            else:
                p["urgency"] = "НОРМА"

        wb_products  = [p for p in products if p["marketplace"] == "wb"]
        ozon_products = [p for p in products if p["marketplace"] == "ozon"]

        wb_open = supply_data.get("wb_open_warehouses")
        if wb_open:
            wb_wh_block = (
                "СКЛАДЫ WB ОТКРЫТЫ СЕЙЧАС (WB API, коэф. приёмки короба > 0):\n"
                + "\n".join(f"  • {name} (коэф. {coeff})" for name, coeff in sorted(wb_open.items()))
                + "\n⚠️ Используй ТОЛЬКО эти точные названия складов из списка выше. "
                "Не пиши 'Коледино', 'Подольск', 'Шушары' и др. — только имена из API."
            )
        else:
            wb_wh_block = (
                "СКЛАДЫ WB: API недоступен. Используй кластеры из поля clusters."
            )

        threshold_note = (
            f"Срок поставки = {lead} дн, буфер = {safety} дн → "
            f"КРИТИЧНО если < {lead} дн, СРОЧНО если < {lead + safety} дн."
        )

        n_critical = sum(1 for p in products if p["urgency"] == "КРИТИЧНО")
        n_urgent   = sum(1 for p in products if p["urgency"] == "СРОЧНО")
        n_ok       = sum(1 for p in products if p["urgency"] == "НОРМА")

        prompt = f"""Составь план поставок. Срочность уже рассчитана в поле urgency.
{threshold_note}
Всего позиций: {len(products)} ({n_critical} КРИТИЧНО, {n_urgent} СРОЧНО, {n_ok} НОРМА).
НЕ добавляй пагинацию. Показывай ВСЕ {len(products)} позиций полностью.

══════════════════════════
ДАННЫЕ WB ({len(wb_products)} товаров, период {days} дней):
{json.dumps(wb_products, ensure_ascii=False, indent=2)}

{wb_wh_block}

══════════════════════════
ДАННЫЕ OZON ({len(ozon_products)} товаров, период {days} дней):
{json.dumps(ozon_products, ensure_ascii=False, indent=2)}

OZON FBO: поставка на конкретный склад Ozon. Рекомендуй по кластерам — куда
отправить и сколько, основываясь на days_left и need каждого кластера.
cluster_dr для Ozon — точный, из /v1/analytics/data (заказы по складу за период).

ПОЛЯ:
- stock — на складе МП сейчас
- in_transit — товары в пути к складу МП (из аналитики остатков WB/Ozon, агрегат)
- supply_committed — сумма qty из mkt_supplies с активными статусами (уже оформлено в поставках)
- mkt_supplies — поставки из ЛК МП: [{status, qty, warehouse_name}]
  warehouse_name — склад куда идёт поставка (WB: officeName, Ozon: warehouse.name если есть)
  Возможные статусы WB: "Запланировано", "Отгрузка разрешена", "В пути", "Транзит", "Идёт приёмка", "Принято"
  Ozon: "Черновик", "Новая", "Готова к отгрузке", "В пути", "Принята на склад"
  Показывай КАЖДЫЙ статус отдельно с эмодзи-иконкой и складом (если есть)
- to_order — заказать у поставщика СВЕРХ (in_transit + supply_committed), уже рассчитано
- total_days_left — суммарный запас в днях продаж
- clusters[].cluster_dr — точный темп продаж кластера (шт/день):
    WB — из регионов заказов; Ozon — из аналитики по складам API (/v1/analytics/data)
    Если данных по кластеру нет, cluster_dr рассчитан пропорционально стоку (≈приближение)
- clusters[].days_left — запас кластера в днях (по cluster_dr)
- clusters[].need — нужно отправить в кластер для цели TARGET_DAYS (по cluster_dr)
- ozon_warehouses — текущий FBO сток по складам Ozon: [{warehouse_name, stock}]
  Если НЕ пуст — используй эти данные для рекомендаций.
  Если ПУСТ — FBO данных нет (товар FBS или ещё не поставлен на Ozon FBO).
- urgency — КРИТИЧНО / СРОЧНО / НОРМА (уже рассчитано, не пересчитывай)

Эмодзи для mkt_supplies.status:
  📝 Запланировано / Черновик
  ✅ Отгрузка разрешена / Готова к отгрузке
  🚛 В пути
  🔄 Транзит
  📥 Идёт приёмка / Принята на склад
  ✔️ Принято / Новая
  ❓ прочие

ФОРМАТ (Rich Markdown, мобильно, ВСЕ позиции):

## 📦 WB

🔴 **КРИТИЧНО** — *кончится до прихода партии*

**[Название]**
`[X] шт/день суммарно` · запас [N] дн
*Кластеры WB (только принимающие склады):*
  📦 [Кластер/Склад]: [N] шт · [X] шт/день · [N] дн → нужно [N] шт
  📦 [Кластер/Склад]: [N] шт · [X] шт/день · [N] дн → нужно [N] шт
🚚 В пути (аналитика): [N] шт *(если есть)*
[эмодзи] [Статус] → [склад если есть]: [N] шт *(для каждого mkt_supplies, если есть)*
**→ заказать [N] шт (распред.: [Кластер1] [N] шт, [Кластер2] [N] шт)**

---

🟡 **СРОЧНО** — *пора размещать заказ*

**[Название]**
`[X] шт/день суммарно` · запас [N] дн
*Кластеры:*
  📦 [Кластер]: [N] шт · [X] шт/день · [N] дн → нужно [N] шт
🚚 В пути (аналитика): [N] шт *(если есть)*
[эмодзи] [Статус] → [склад]: [N] шт *(если есть)*
**→ заказать [N] шт**

---

🟢 **НОРМА**
**[Название]** — [N] дн суммарно *(слабый кластер: [Кластер] [N] дн)* · заказ не нужен

---

## 🔵 Ozon

ПРАВИЛО по ozon_warehouses:
• Если ozon_warehouses НЕ пуст → показывай реальные склады из этого поля.
• Если ozon_warehouses ПУСТ → пиши "данных по складам Ozon нет". Не используй WB-данные как замену.
• Никогда не сравнивай WB и Ozon данные, не используй один МП как прокси для другого.
  Сравнение между МП — только если пользователь явно об этом попросил.

Формат для каждого Ozon-товара КРИТИЧНО/СРОЧНО:

**[Название]**
`[X] шт/день суммарно` · запас [N] дн
*Склады Ozon (из ozon_warehouses):*
  🏭 [warehouse_name]: [N] шт
Для mkt_supplies Ozon показывай склад назначения (warehouse_name) если он есть:
  ✅ Готова к отгрузке → [склад]: [N] шт
  🚛 В пути → [склад]: [N] шт
**→ заказать [N] шт** *(на склад с наибольшим спросом/наименьшим запасом)*

🟢 **НОРМА**
**[Название]** — [N] дн суммарно · склады: [wh1] [N] шт, [wh2] [N] шт

---

## 📋 Итого к заказу

*(только позиции с to_order > 0)*
**[Название]** (WB/Ozon) · 🔴/🟡 · **[N] шт** → куда: [склад/кластер] [N]"""

        await update.message.reply_text("🤔 Составляю план поставки…")
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=8000,
                system=PETER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/supply] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            update=update,
        )

    async def _collect_order_advice_data(self, chat_id: int, days: int = 30) -> list[dict]:
        """Суммарный сток по товару (все склады) + темп продаж → совет заказывать или нет."""
        from db import get_pool
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        async with pool.acquire() as conn:
            # Суммарный сток по каждому товару (все склады суммируем)
            stock_rows = await conn.fetch("""
                SELECT s.marketplace, s.product_id,
                       COALESCE(m.display_name, MAX(s.product_name)) AS name,
                       MAX(m.category)                                AS category,
                       MAX(c.cost)::numeric(10,2)                     AS unit_cost,
                       SUM(s.stock)::int                              AS total_stock
                FROM marketplace_stocks s
                LEFT JOIN product_mapping m
                       ON (s.marketplace = 'wb'   AND m.wb_article    = s.product_id)
                       OR (s.marketplace = 'ozon' AND m.ozon_offer_id = s.product_id)
                LEFT JOIN product_costs c
                       ON c.mapping_id = m.id AND c.marketplace = s.marketplace
                WHERE s.chat_id = $1
                GROUP BY s.marketplace, s.product_id, m.display_name
            """, chat_id)

            # Темп продаж за период
            velocity_rows = await conn.fetch("""
                SELECT o.marketplace,
                       CASE WHEN o.marketplace = 'ozon'
                            THEN COALESCE(mm.ozon_offer_id, o.product_id)
                            ELSE o.product_id END                         AS key,
                       COALESCE(mwb.display_name, moz.display_name,
                                MAX(o.product_name))                      AS name,
                       ROUND(SUM(o.quantity)::numeric / $2, 2)            AS daily_rate,
                       SUM(o.quantity)::int                               AS total_qty
                FROM marketplace_orders o
                LEFT JOIN LATERAL (
                    SELECT ozon_offer_id, display_name FROM product_mapping
                    WHERE ozon_sku = o.product_id LIMIT 1
                ) moz ON o.marketplace = 'ozon'
                LEFT JOIN LATERAL (
                    SELECT display_name FROM product_mapping
                    WHERE wb_article = o.product_id LIMIT 1
                ) mwb ON o.marketplace = 'wb'
                WHERE o.chat_id = $1 AND o.order_date >= $3
                GROUP BY o.marketplace,
                         CASE WHEN o.marketplace = 'ozon'
                              THEN COALESCE(moz.ozon_offer_id, o.product_id)
                              ELSE o.product_id END,
                         COALESCE(mwb.display_name, moz.display_name)
                HAVING SUM(o.quantity) > 0
            """, chat_id, days, date_from)

        velocity: dict[tuple, float] = {
            (r["marketplace"], r["key"]): float(r["daily_rate"]) for r in velocity_rows
        }
        vel_names: dict[tuple, str] = {
            (r["marketplace"], r["key"]): r["name"] or r["key"] for r in velocity_rows
        }

        lead = await self._get_lead_days(chat_id)
        safety = await self._get_safety_days(chat_id)
        order_point = lead + safety               # порог "заказывать"

        result = []
        for row in stock_rows:
            mp = row["marketplace"]
            pid = row["product_id"]
            key = (mp, pid)
            name = vel_names.get(key) or row["name"] or pid
            dr = velocity.get(key, 0.0)
            stock = row["total_stock"] or 0
            unit_cost = float(row["unit_cost"] or 0)

            days_left = round(stock / dr, 1) if dr > 0 else 999

            # Статус: насколько срочно заказывать
            if dr == 0:
                status = "no_sales"       # нет продаж — не анализируем
            elif days_left < lead:
                status = "critical"       # 🔴 уже опаздываем — товар кончится до прихода партии
            elif days_left < order_point:
                status = "order_now"      # 🟡 пора заказывать
            else:
                status = "ok"             # 🟢 запас есть

            # Сколько заказать для 3 горизонтов (с учётом текущего стока)
            def qty_for(target_days: int) -> int:
                return max(0, round(target_days * dr - stock))

            result.append({
                "name":       name,
                "marketplace": mp,
                "total_stock": stock,
                "daily_rate":  round(dr, 2),
                "days_left":   days_left,
                "lead_days":   lead,
                "safety_days": safety,
                "order_point": order_point,
                "status":      status,
                "unit_cost":   unit_cost,
                "qty_30d":  qty_for(30),
                "qty_60d":  qty_for(60),
                "qty_90d":  qty_for(90),
                "cost_30d": round(qty_for(30) * unit_cost) if unit_cost else None,
                "cost_60d": round(qty_for(60) * unit_cost) if unit_cost else None,
                "cost_90d": round(qty_for(90) * unit_cost) if unit_cost else None,
            })

        # Сортируем: сначала критичные, потом по days_left
        _order = {"critical": 0, "order_now": 1, "ok": 2, "no_sales": 3}
        result.sort(key=lambda x: (_order.get(x["status"], 9), x["days_left"]))
        return result

    async def cmd_order(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/order [период=30] — совет заказывать ли у поставщика, три горизонта (30/60/90 дней)."""
        chat_id = update.effective_user.id
        days = 30
        for tok in (context.args or []):
            if tok.startswith("период="):
                try:
                    days = int(tok.split("=", 1)[1])
                except ValueError:
                    pass
            elif tok.isdigit():
                days = int(tok)

        lead_preview = await self._get_lead_days(chat_id)
        safety_preview = await self._get_safety_days(chat_id)
        await update.message.reply_text(
            f"📬 Анализирую остатки и темп продаж за {days} дней…\n"
            f"Срок поставки: {lead_preview} дн + {safety_preview} дн буфер"
        )

        try:
            data = await self._collect_order_advice_data(chat_id, days=days)
        except Exception as e:
            logger.error(f"[Питер/order] ошибка данных: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")
            return

        if not data:
            await update.message.reply_text(
                "❌ Нет данных по остаткам. Запусти /sync у Макса для синхронизации."
            )
            return

        answer = await self._order_analysis(chat_id, days, data)
        await self._send_answer(answer, update=update)

    async def _order_analysis(self, chat_id: int, days: int, data: list[dict] | None = None) -> str:
        """Сформировать рекомендацию по заказу у поставщика. Возвращает текст ответа."""
        if data is None:
            data = await self._collect_order_advice_data(chat_id, days=days)
        if not data:
            return "❌ Нет данных по остаткам. Запусти /sync у Макса для синхронизации."

        lead = await self._get_lead_days(chat_id)
        safety = await self._get_safety_days(chat_id)

        prompt = f"""Дай рекомендацию: заказывать ли товар у поставщика.

ПЕРИОД АНАЛИЗА: {days} дней продаж
СРОК ПОСТАВКИ: {lead} дней от заказа до склада МП + {safety} дней страховой буфер
ПОРОГ ЗАКАЗА: если осталось < {lead + safety} дней — заказывать

ДАННЫЕ ПО ТОВАРАМ:
{json.dumps(data, ensure_ascii=False, default=str, indent=2)}

ФОРМАТ (мобильный, Rich Markdown):

# 📬 Заказ у поставщика

## 🔴 Заказать немедленно
*(status=critical: товар закончится раньше чем придёт партия)*
**`Название`** (WB/Ozon) — осталось X дн, X шт/день
→ 30 дней: **X шт** (~X₽) | 60 дней: **X шт** | 90 дней: **X шт**

## 🟡 Заказать сейчас
*(status=order_now: запас < {lead + safety} дней)*
**`Название`** — осталось X дн, X шт/день
→ 30 дней: **X шт** (~X₽) | 60 дней: **X шт** | 90 дней: **X шт**

## 🟢 Пока не нужно
*(status=ok: запас > {lead + safety} дней)*
`Название` — осталось X дн *(следующий заказ через ~X дн)*

---

> **Итого к заказу прямо сейчас:** X позиций, ~X шт, ~X₽ (если есть себестоимость)

ПРАВИЛА:
- Если cost_30d/60d/90d = null — себестоимость не задана, показывай только штуки
- Если daily_rate = 0 (status=no_sales) — пропусти товар, упомяни одной строкой в конце
- Если qty_30d = 0 — текущего стока хватает на 30 дней без дозаказа, можно указать это
- Три горизонта: 30/60/90 дней — это сколько дней продаж надо обеспечить (не срок поставки)
- Если нет данных по стокам — предупреди что нужен /sync у Макса"""

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=3000,
                system=PETER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/order] ошибка Claude: {e}", exc_info=True)
            return f"❌ Ошибка анализа: {e}"

    async def _collect_seo_audit_data(self, chat_id: int, days: int = 30) -> list[dict]:
        """Воронка + контент карточек + SEO-проблемы для каждого товара."""
        from db import get_pool
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                WITH funnel AS (
                    SELECT marketplace, product_id,
                           SUM(views)::bigint             AS views,
                           SUM(add_to_cart)::bigint       AS add_to_cart,
                           SUM(orders_count)::bigint      AS orders,
                           AVG(avg_position)::numeric(5,1) AS avg_position
                    FROM product_funnel_stats
                    WHERE chat_id = $1 AND stat_date >= $2
                    GROUP BY marketplace, product_id
                )
                SELECT
                    f.marketplace,
                    f.product_id,
                    COALESCE(m.display_name, f.product_id)                           AS name,
                    CASE WHEN f.marketplace = 'wb'
                         THEN COALESCE(m.wb_article,    f.product_id)
                         ELSE COALESCE(m.ozon_offer_id, f.product_id) END            AS article,
                    f.views,
                    f.add_to_cart,
                    f.orders,
                    CASE WHEN f.views > 0
                         THEN ROUND(f.add_to_cart::numeric / f.views * 100, 2)
                         ELSE 0 END                                                   AS ctr,
                    f.avg_position,
                    pc.title,
                    LENGTH(COALESCE(pc.title, ''))                                   AS title_len,
                    LENGTH(COALESCE(pc.description, ''))                             AS desc_len,
                    COALESCE(JSONB_ARRAY_LENGTH(pc.characteristics), 0)              AS chars_count
                FROM funnel f
                LEFT JOIN product_mapping m ON
                    (f.marketplace = 'wb'   AND m.wb_nm_id        = f.product_id) OR
                    (f.marketplace = 'ozon' AND m.ozon_sku::text  = f.product_id)
                LEFT JOIN product_cards pc ON pc.chat_id = $1
                    AND pc.marketplace = f.marketplace
                    AND (
                        (f.marketplace = 'wb'   AND pc.product_id = f.product_id) OR
                        (f.marketplace = 'ozon' AND pc.product_id = m.ozon_offer_id)
                    )
                WHERE f.views > 0
                ORDER BY f.views DESC
                LIMIT 40
            """, chat_id, date_from)

        if not rows:
            return []

        results = []
        for row in rows:
            r = dict(row)
            ctr         = float(r["ctr"] or 0)
            title_len   = int(r["title_len"] or 0)
            desc_len    = int(r["desc_len"] or 0)
            chars_count = int(r["chars_count"] or 0)
            views       = int(r["views"] or 0)
            avg_pos     = r["avg_position"]

            issues: list[str] = []
            if r["title"] is None:
                issues.append("нет данных карточки — нужен /sync_cards")
            else:
                if title_len < 40:
                    issues.append(f"заголовок {title_len}/60 симв.")
                if desc_len < 150:
                    issues.append(f"описание {desc_len} симв.")
                if chars_count < 5:
                    issues.append(f"характеристик {chars_count}/7")

            if ctr < 2.0 and views >= 100:
                issues.append(f"CTR {ctr}% (норма 2–3%)")
            if avg_pos and float(avg_pos) > 50:
                issues.append(f"позиция в поиске {avg_pos}")

            # Urgency: много показов + плохой CTR = максимальный приоритет переделки
            urgency = int(views * (1.0 / (ctr + 0.5))) if views > 0 else 0

            results.append({
                "marketplace":  r["marketplace"],
                "name":         r["name"],
                "article":      r["article"],
                "views":        views,
                "ctr":          ctr,
                "avg_position": float(avg_pos) if avg_pos else None,
                "orders":       int(r["orders"] or 0),
                "title_len":    title_len,
                "desc_len":     desc_len,
                "chars_count":  chars_count,
                "issues":       issues,
                "urgency":      urgency,
            })

        results.sort(key=lambda x: x["urgency"], reverse=True)
        return results

    async def cmd_seo_audit(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/seo_audit [период=30] — аудит SEO карточек, приоритизация для переделки."""
        chat_id = update.effective_user.id
        days = 30
        if context.args:
            try:
                days = int(context.args[0])
            except ValueError:
                pass

        await update.message.reply_text(f"🔍 Анализирую SEO карточек за {days} дней…")

        data = await self._collect_seo_audit_data(chat_id, days)
        if not data:
            await update.message.reply_text(
                "❌ Данных нет. Запусти /sync_funnel и /sync_cards у Макса."
            )
            return

        avg_ctr           = sum(p["ctr"] for p in data) / len(data)
        products_w_issues = sum(1 for p in data if p["issues"])
        no_card_data      = sum(1 for p in data if p["title_len"] == 0 and "нет данных" in " ".join(p["issues"]))

        prompt = f"""Период: {days} дней. Всего товаров: {len(data)}.
Средний CTR: {avg_ctr:.1f}%. Товаров с SEO-проблемами: {products_w_issues}.
{f'Без данных карточки (нужен /sync_cards): {no_card_data}.' if no_card_data else ''}

SEO-ДАННЫЕ ПО ТОВАРАМ (urgency = показы × 1/CTR, сортировка по убыванию):
{json.dumps(data[:25], ensure_ascii=False, indent=2)}

Составь чёткий список товаров для переделки SEO.
Для каждого: что конкретно слабо + одно действие.
В рекомендациях пиши артикул для команды /seo у Элины.

Формат ответа (Rich Markdown, до 35 строк):

🔍 **SEO-аудит за {days} дней** — X товаров нужно переделать

**🔴 Срочно (высокие показы, плохой CTR):**
`АРТИКУЛ` [МП] — CTR X%, показов N: [что слабо]
→ `/seo АРТИКУЛ` у Элины

**🟡 Улучшить (контент неполный):**
`АРТИКУЛ` — заголовок Xсимв., [что добавить]

**🟢 Низкая видимость (мало показов):**
`АРТИКУЛ` — позиция X, нужны ключевые слова

> Главный вывод одной строкой."""

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
            logger.error(f"[Питер/seo_audit] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            update=update,
        )

        # Offer to dispatch SEO tasks to Elina for problematic products
        problematic = [p for p in data if p["issues"]]
        if problematic:
            articles_payload = json.dumps([
                {"article": p["article"], "marketplace": p["marketplace"], "name": p["name"]}
                for p in problematic
            ])
            await self._redis_set(f"seo_audit:{chat_id}", articles_payload, ttl=3600)
            n = len(problematic)
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🚀 SEO топ-3", callback_data=f"pseo:top3:{chat_id}"),
                InlineKeyboardButton(f"🚀 SEO все ({n})", callback_data=f"pseo:all:{chat_id}"),
            ]])
            await update.message.reply_text(
                f"Запустить SEO-задачи у Элины для {n} товаров?",
                reply_markup=keyboard,
            )

    async def _handle_seo_audit_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработчик кнопок запуска SEO у Элины после аудита (pseo:*)."""
        query = update.callback_query
        await query.answer()
        parts = (query.data or "").split(":")
        # parts: ["pseo", "top3"|"all", chat_id]
        if len(parts) < 3:
            return
        mode = parts[1]
        try:
            target_chat_id = int(parts[2])
        except ValueError:
            return

        raw = await self._redis_get(f"seo_audit:{target_chat_id}")
        if not raw:
            await query.edit_message_text("⏰ Результаты аудита устарели — запусти /seo_audit снова.")
            return

        try:
            articles_list: list[dict] = json.loads(raw)
        except Exception:
            await query.edit_message_text("❌ Ошибка чтения данных аудита.")
            return

        subset = articles_list[:3] if mode == "top3" else articles_list
        if not subset:
            await query.edit_message_text("✅ Нет товаров для SEO-оптимизации.")
            return

        enqueued = 0
        for item in subset:
            article = item.get("article", "")
            mp = item.get("marketplace", "")
            name = item.get("name", article)
            if not article:
                continue
            try:
                await enqueue_task(
                    assigned_agent="elina",
                    payload=f"напиши seo для товара {article}",
                    from_agent="peter",
                    chat_id=target_chat_id,
                )
                enqueued += 1
                logger.info(f"[Питер/seo_audit] enqueued SEO task for {mp} {article} ({name})")
            except Exception as e:
                logger.error(f"[Питер/seo_audit] ошибка enqueue {article}: {e}")

        names = ", ".join(f"`{it['article']}`" for it in subset)
        await query.edit_message_text(
            f"✅ Отправила {enqueued} SEO-задач Элине: {names}\n"
            f"_Результаты появятся в Notion Content DB._",
            parse_mode="Markdown",
        )

    async def cmd_set(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/set ключ=значение — изменить настройку аккаунта.

        Поддерживаемые ключи:
          lead_time=N   — срок поставки от поставщика до склада МП (дней)
          safety=N      — страховой буфер запаса (дней)
        """
        chat_id = update.effective_user.id
        args_raw = " ".join(context.args or []).strip()

        _SET_ALIASES = {
            "lead_time":  "supply_lead_days",
            "lead":       "supply_lead_days",
            "срок":       "supply_lead_days",
            "поставка":   "supply_lead_days",
            "safety":     "supply_safety_days",
            "буфер":      "supply_safety_days",
        }

        import re as _re_set
        m = _re_set.match(r"([a-zа-яё_]+)\s*[=:]\s*(\d+)", args_raw.lower())
        if not m:
            current_lead = await self._get_lead_days(chat_id)
            current_safety = await self._get_safety_days(chat_id)
            await update.message.reply_text(
                f"⚙️ <b>Настройки поставок</b>\n\n"
                f"Срок поставки: <b>{current_lead} дн</b> (до склада МП)\n"
                f"Страховой буфер: <b>{current_safety} дн</b>\n\n"
                f"<b>Изменить:</b>\n"
                f"/set lead_time=14 — срок поставки\n"
                f"/set safety=7 — страховой буфер",
                parse_mode="HTML",
            )
            return

        alias, val_str = m.group(1), m.group(2)
        db_key = _SET_ALIASES.get(alias)
        if not db_key:
            await update.message.reply_text(
                f"❓ Неизвестный ключ «{alias}».\n"
                f"Доступные: lead_time, safety"
            )
            return

        val = int(val_str)
        from db import set_user_setting
        await set_user_setting(chat_id, db_key, str(val))

        labels = {
            "supply_lead_days":   f"Срок поставки: *{val} дн* (от заказа до склада МП)",
            "supply_safety_days": f"Страховой буфер: *{val} дн*",
        }
        await update.message.reply_text(
            f"✅ {labels[db_key]}\n_Применяется в /supply и /order._",
            parse_mode="Markdown",
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
                chat_id=chat_id,
            )
            logger.info(f"[Питер/weekly_audit] отправлен в chat_id={chat_id}")
        except Exception as e:
            logger.error(f"[Питер/weekly_audit] ошибка отправки: {e}")

    async def run_daily_digest(self, chat_id: int) -> None:
        """Ежедневный вечерний дайджест — вызывается планировщиком в 21:00 МСК."""
        logger.info(f"[Питер/daily_digest] Запуск для chat_id={chat_id}")
        try:
            data     = await self._collect_data(chat_id, days=7)
            adv_data = await self._collect_advanced_data(chat_id, days=7)
        except Exception as e:
            logger.error(f"[Питер/daily_digest] ошибка данных: {e}")
            return

        total_revenue = sum(float(r["revenue"] or 0) for r in data["revenue"])
        total_orders  = sum(int(r["orders"]  or 0)   for r in data["revenue"])
        total_adv     = sum(float(r["spend"] or 0)   for r in data["adv"])
        drr = round(total_adv / total_revenue * 100, 1) if total_revenue else 0

        # Авто-триггер Peter→Elina: CTR < 1% → ставим задачу Элине
        low_ctr_names: list[str] = []
        low_ctr_items = [
            m for m in adv_data.get("product_metrics", [])
            if 0 < float(m.get("avg_ctr") or 0) < 1.0
        ]
        for m in low_ctr_items[:3]:
            article = str(m.get("product_id") or m.get("name", "")).strip()
            if article:
                try:
                    await enqueue_task(
                        assigned_agent="elina",
                        payload=f"напиши seo для товара {article}",
                        from_agent="peter",
                        chat_id=chat_id,
                    )
                    low_ctr_names.append(article)
                except Exception as _e:
                    logger.warning(f"[Питер/daily_digest] enqueue elina ошибка: {_e}")

        elina_note = (
            f"\n\n📸 Элина работает над карточками: {', '.join(low_ctr_names)} (низкий CTR)"
            if low_ctr_names else ""
        )

        # Алерт для дизайнера: инфографика нужна
        if low_ctr_items and chat_id:
            infographic_items = []
            for m in low_ctr_items[:3]:
                article = str(m.get("product_id") or m.get("name", "")).strip()
                name    = str(m.get("name", article)).strip()
                mp      = str(m.get("marketplace", "wb")).lower()
                ctr_val = round(float(m.get("avg_ctr") or 0), 2)
                if article:
                    infographic_items.append({
                        "article": article, "name": name,
                        "marketplace": mp, "ctr_before": ctr_val,
                    })
            if infographic_items:
                await self._redis_set(
                    f"pending_infographic:{chat_id}",
                    json.dumps(infographic_items, ensure_ascii=False),
                    ttl=7 * 86_400,
                )
                lines = "\n".join(
                    f"📦 {it['name']} ({it['marketplace'].upper()}) — CTR {it['ctr_before']}%"
                    for it in infographic_items
                )
                await self._notify_user(
                    chat_id,
                    f"📸 *Нужна новая инфографика*\n\n"
                    f"Обнаружены товары с низким CTR:\n\n{lines}\n\n"
                    f"Подготовьте новые карточки и пришлите фото в бот — "
                    f"Макс загрузит на WB автоматически.",
                )

        prompt = f"""Ежедневный вечерний дайджест магазина. Не более 15 строк.

Период: 7 дней | Выручка: {total_revenue:,.0f} ₽ | Заказов: {total_orders} | ДРР: {drr}%

ДАННЫЕ:
{json.dumps({**data, **adv_data}, ensure_ascii=False, default=str, indent=2)}

ФОРМАТ:
📊 **Итоги дня**
**Выручка:** X ₽ (WB X + Ozon X) | **Заказов:** N
**ДРР:** X% | **Тренд:** ↑/↓X% к прошлой неделе

⚠️ Срочно (если дефицит стока или критический ДРР — укажи конкретно)
💡 1-2 главных действия на завтра

Только факты и цифры. Если нет проблем — «Всё в норме».{elina_note}"""

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=1000,
                system=PETER_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/daily_digest] ошибка Claude: {e}")
            return

        try:
            await self._send_answer(
                answer,
                chat_id=chat_id,
            )
            logger.info(f"[Питер/daily_digest] отправлен в chat_id={chat_id}")
        except Exception as e:
            logger.error(f"[Питер/daily_digest] ошибка отправки: {e}")

    async def cmd_abc(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/abc [период=30] — ABC-анализ товаров по вкладу в выручку."""
        chat_id = update.effective_user.id
        days = 30
        if context.args:
            try:
                days = int(context.args[0])
            except ValueError:
                pass

        await update.message.reply_text(f"🔤 ABC-анализ за {days} дней…")

        adv = await self._collect_advanced_data(chat_id, days=days)
        abc_data = adv.get("abc_data", [])

        if not abc_data:
            await update.message.reply_text("❌ Нет данных о заказах. Запусти /sync у Макса.")
            return

        total_revenue = sum(r["revenue"] for r in abc_data)

        prompt = (
            f"ABC-анализ товаров за {days} дней. Общая выручка: {total_revenue:,.0f} ₽.\n\n"
            f"ДАННЫЕ (отсортированы по выручке, с накопительной долей):\n"
            f"{json.dumps(abc_data, ensure_ascii=False, indent=2)}\n\n"
            f"Используй формат PETER_ABC_PROMPT."
        )

        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
            resp = await client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=2048,
                system=PETER_ABC_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text
        except Exception as e:
            logger.error(f"[Питер/abc] ошибка Claude: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка анализа: {e}")
            return

        await self._send_answer(
            answer,
            update=update,
            after_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Отчёт", callback_data="pnext:report"),
                InlineKeyboardButton("💰 ДРР",   callback_data="pnext:drr"),
            ]]),
        )

    async def cmd_returns(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/returns [период=30] — анализ возвратов по товарам (топ по ставке)."""
        chat_id = update.effective_user.id
        days = 30
        if context.args:
            try:
                days = int(context.args[0])
            except ValueError:
                pass

        await update.message.reply_text(f"↩️ Анализирую возвраты за {days} дней…")

        from db import get_pool
        pool = await get_pool()
        date_from = (datetime.now(_UTC) - timedelta(days=days)).date()

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT ra.product_id, ra.marketplace,
                       COALESCE(m.display_name, ra.product_name, ra.product_id) AS name,
                       SUM(ra.returns_count)::int            AS total_returns,
                       SUM(ra.return_amount)::numeric(12,2)  AS total_amount,
                       AVG(ra.return_rate)::numeric(6,4)     AS avg_rate
                FROM product_returns_analytics ra
                LEFT JOIN product_mapping m ON (
                    m.wb_article = ra.product_id OR
                    m.ozon_offer_id = ra.product_id
                )
                WHERE ra.chat_id = $1 AND ra.stat_date >= $2
                GROUP BY ra.product_id, ra.marketplace, ra.product_name, m.display_name
                ORDER BY avg_rate DESC NULLS LAST, total_returns DESC
                LIMIT 15
            """, chat_id, date_from)

        if not rows:
            await update.message.reply_text(
                "❌ Нет данных о возвратах. Запусти <code>/sync_returns</code> у Макса.",
                parse_mode="HTML",
            )
            return

        lines = [f"↩️ <b>Возвраты за {days} дней:</b>\n"]
        high_return = []

        for r in rows:
            rate_pct  = float(r["avg_rate"] or 0) * 100
            mp_label  = "🟣" if r["marketplace"] == "wb" else "🔵"
            amount_k  = float(r["total_amount"] or 0) / 1000
            flag      = " ⚠️" if rate_pct > 5 else ""
            lines.append(
                f"{mp_label} <b>{r['name']}</b> — {rate_pct:.1f}%"
                f" ({r['total_returns']} шт · {amount_k:.0f}к ₽){flag}"
            )
            if rate_pct > 5:
                high_return.append({"name": r["name"], "product_id": r["product_id"]})

        if high_return:
            lines.append("")
            lines.append("⚠️ <i>Возврат >5% — описание может не совпадать с ожиданиями.</i>")

        buttons = [
            [InlineKeyboardButton(f"📝 Улучшить: {p['name']}", callback_data=f"returns_elina:{p['product_id']}")]
            for p in high_return[:3]
        ]
        markup = InlineKeyboardMarkup(buttons) if buttons else None

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=markup,
        )

    async def _handle_returns_elina_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Callback returns_elina:{product_id} → ставит задачу Элине на улучшение карточки."""
        query = update.callback_query
        await query.answer()
        chat_id = query.from_user.id
        product_id = query.data.split(":", 1)[1]

        payload = json.dumps({
            "action": "improve_card",
            "product_id": product_id,
            "reason": "high_return_rate",
            "context": "Товар имеет высокий % возвратов (>5%). Улучши заголовок и описание, чтобы ожидания покупателей точнее совпадали с товаром.",
        })

        try:
            await enqueue_task(
                assigned_agent="elina",
                task_type="improve_card",
                payload=payload,
                chat_id=chat_id,
            )
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(
                f"✅ Элина получила задание: улучшить описание для <code>{product_id}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"[Питер/returns_elina] {e}", exc_info=True)
            await query.message.reply_text(f"❌ Ошибка: {e}")

    def _help_text(self) -> str:
        return (
            "📊 **Питер** — бизнес-аналитик\n\n"
            "Анализирую продажи WB и Ozon, считаю NET-маржу, ДРР, рентабельность,\n"
            "даю конкретные рекомендации по росту.\n\n"
            "📌 **Команды:**\n"
            "/report [цель=X] [период=14] — отчёт о продажах, NET-маржа, "
            "CTR до/после инфографики, рекомендации по ценам\n"
            "/audit — полная оценка магазина (SWOT, KPI, топ-5 действий)\n"
            "/drr [период=30] — ДРР и ROAS по товарам с вердиктами\n"
            "/funnel [период=30] — воронка конверсии (показы→корзина→заказ)\n"
            "/abc [период=30] — ABC-анализ товаров по вкладу в выручку\n"
            "/returns [период=30] — анализ возвратов: причины, топ-товары, тренд\n"
            "/seo_audit [период=30] — аудит SEO карточек, приоритеты для переделки\n"
            "/supply [период=14] — план поставок по регионам и кластерам\n"
            "/order [период=30] — заказать ли у поставщика (горизонт 30/60/90 дн)\n"
            "/analyze <вопрос> — произвольный бизнес-анализ\n"
            "/set [срок=N] [буфер=N] — настройки поставок (срок доставки, страховой буфер)\n"
            "/reset — очистить историю\n\n"
            "💡 После /report рекомендации по ценам сохраняются — применить через /apply_prices у Макса\n"
            "💡 Пример: /report цель=100000 период=14"
        )

    _PETER_MENU_KEYBOARD = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Отчёт",   callback_data="pmenu:report"),
            InlineKeyboardButton("🔍 Аудит",   callback_data="pmenu:audit"),
        ],
        [
            InlineKeyboardButton("📣 ДРР",     callback_data="pmenu:drr"),
            InlineKeyboardButton("🔻 Воронка", callback_data="pmenu:funnel"),
        ],
        [
            InlineKeyboardButton("📦 Поставки", callback_data="pmenu:supply"),
            InlineKeyboardButton("📬 Заказ",    callback_data="pmenu:order"),
        ],
        [
            InlineKeyboardButton("🔤 ABC",      callback_data="pmenu:abc"),
        ],
        [
            InlineKeyboardButton("💬 Вопрос",  callback_data="pmenu:analyze"),
        ],
    ])

    _PETER_MENU_HINTS: dict[str, str] = {
        "report": (
            "📊 <b>Отчёт о продажах</b>\n\n"
            "Полный анализ за 14 дней: выручка, топ-товары, план роста.\n\n"
            "/report — запустить\n"
            "/report цель=300000 период=30 — с параметрами"
        ),
        "audit": (
            "🔍 <b>Аудит магазина</b>\n\n"
            "SWOT-анализ, KPI, сильные и слабые стороны.\n\n"
            "/audit — запустить"
        ),
        "drr": (
            "📣 <b>ДРР и ROAS</b>\n\n"
            "Эффективность рекламы по каждому товару за 30 дней.\n\n"
            "/drr — запустить"
        ),
        "funnel": (
            "🔻 <b>Воронка конверсии</b>\n\n"
            "Просмотры → корзина → заказ по карточкам.\n"
            "Требует предварительного /sync_funnel у Макса.\n\n"
            "/funnel — запустить"
        ),
        "abc": (
            "🔤 <b>ABC-анализ</b>\n\n"
            "Ранжирование товаров по вкладу в выручку:\n"
            "A — 80%, B — следующие 15%, C — хвост.\n\n"
            "/abc — запустить (период 30 дней)\n"
            "/abc 14 — за 14 дней"
        ),
        "supply": (
            "📦 <b>План поставок</b>\n\n"
            "Какие товары, в какие регионы и сколько штук нужно везти.\n"
            "Расчёт: текущие остатки по складам ÷ темп продаж = дней осталось.\n\n"
            "/supply — запустить (период 14 дней)\n"
            "/supply период=30 — за 30 дней"
        ),
        "order": (
            "📬 <b>Заказ у поставщика</b>\n\n"
            "Стоит ли сейчас заказывать товар у поставщика?\n"
            "Три горизонта: на 30 / 60 / 90 дней продаж.\n"
            "Срок и буфер — по вашим настройкам /set.\n\n"
            "/order — запустить (анализ за 30 дней)\n"
            "/order период=14 — за 14 дней"
        ),
        "analyze": (
            "💬 <b>Произвольный анализ</b>\n\n"
            "Напиши вопрос напрямую, например:\n"
            "<i>Питер, какие товары стоит вывести из оборота?</i>\n\n"
            "/analyze [вопрос] — или просто напиши мне"
        ),
    }

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/menu — главное меню Питера."""
        await update.message.reply_text(
            "📋 <b>Меню Питера</b>\nВыбери аналитический инструмент:",
            parse_mode="HTML",
            reply_markup=self._PETER_MENU_KEYBOARD,
        )

    async def _handle_peter_menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик inline-кнопок меню Питера (pmenu:*)."""
        query = update.callback_query
        await query.answer()
        cmd = query.data.split(":", 1)[1] if ":" in query.data else ""
        text = self._PETER_MENU_HINTS.get(cmd, "❓ Неизвестный раздел")
        await query.message.reply_text(text, parse_mode="HTML")

    async def _handle_peter_next_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик кнопок 'Что дальше?' у Питера (pnext:*)."""
        query = update.callback_query
        await query.answer()
        cmd = query.data.split(":", 1)[1] if ":" in query.data else ""
        text = self._PETER_NEXT_HINTS.get(cmd, "❓ Неизвестная команда")
        await query.message.reply_text(text, parse_mode="HTML")

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start",   "Запуск и помощь"),
            BotCommand("menu",    "Меню аналитических команд"),
            BotCommand("report",  "Отчёт о продажах и план роста"),
            BotCommand("audit",   "Полная оценка магазина (SWOT, KPI)"),
            BotCommand("drr",     "ДРР и ROAS по товарам"),
            BotCommand("funnel",    "Воронка конверсии карточек"),
            BotCommand("seo_audit", "SEO-аудит: какие карточки нужно переделать"),
            BotCommand("abc",       "ABC-анализ: какие товары дают 80% выручки"),
            BotCommand("returns",   "Анализ возвратов: причины, топ-товары, тренд"),
            BotCommand("supply",  "План поставок по регионам и кластерам"),
            BotCommand("order",   "Заказать ли у поставщика — 30/60/90 дней"),
            BotCommand("analyze", "Произвольный бизнес-анализ"),
            BotCommand("set",     "Настройки поставок (срок, буфер)"),
            BotCommand("reset",   "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("menu",    self.cmd_menu))
        self.app.add_handler(CommandHandler("report",  self.cmd_report))
        self.app.add_handler(CommandHandler("analyze", self.cmd_analyze))
        self.app.add_handler(CommandHandler("audit",   self.cmd_audit))
        self.app.add_handler(CommandHandler("drr",     self.cmd_drr))
        self.app.add_handler(CommandHandler("funnel",    self.cmd_funnel))
        self.app.add_handler(CommandHandler("seo_audit", self.cmd_seo_audit))
        self.app.add_handler(CommandHandler("abc",        self.cmd_abc))
        self.app.add_handler(CommandHandler("returns",    self.cmd_returns))
        self.app.add_handler(CommandHandler("supply",  self.cmd_supply))
        self.app.add_handler(CommandHandler("order",   self.cmd_order))
        self.app.add_handler(CommandHandler("set",     self.cmd_set))
        self.app.add_handler(
            CallbackQueryHandler(self._handle_returns_elina_callback, pattern=r"^returns_elina:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_peter_menu_callback, pattern=r"^pmenu:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_peter_next_callback, pattern=r"^pnext:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_seo_audit_callback, pattern=r"^pseo:")
        )
