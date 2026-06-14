---
name: peter-analytics
description: >
  Используй этот skill при задачах связанных с агентом Питер:
  бизнес-анализ, отчёты по продажам WB+Ozon, расчёт рентабельности,
  ДРР, воронка конверсии, NET-маржа, финотчёты. Также при вопросах
  про финансовые отчёты маркетплейсов и ограничения текущей аналитики.
---

# Питер — Бизнес-аналитик

## Команды

- `/report [цель=200000] [период=14]` — полный анализ: выручка, NET-маржа, ДРР по товару, остатки
- `/audit` — 30-дневный аудит: оценка X/10, SWOT, KPI, топ-5 действий → Notion
- `/drr [период=14]` — ДРР и ROAS по товару (из product_adv_stats JOIN marketplace_orders)
- `/funnel` — анализ воронки: views→cart→order, где узкое место
- `/analyze <вопрос>` — произвольный бизнес-анализ

## Данные для анализа (`_collect_data`)

- `marketplace_orders` — заказы WB+Ozon, выручка (seller_price)
- `marketplace_sales` — выкупы (price = forPay WB); `is_return=TRUE` → возвраты WB
- `marketplace_adv_stats` — рекламные расходы (кампания/день)
- `product_adv_stats` — реклама на уровне товара/день (для ДРР по товару)
- `marketplace_stocks` — текущие остатки
- `product_mapping` — display_name товаров
- `product_costs` — себестоимость
- `marketplace_financial_report` → `net_margin` — реальная NET-маржа (payout − qty×с/с)
- `product_funnel_stats` → используется в `/funnel`

## Три слоя аналитики (не смешивать!)

1. **Оперативный** (marketplace_adv_stats + Performance API): CTR, клики, расход
2. **Заказы/оборот** (marketplace_orders): цена селлера, rolling window
3. **Финансовый** (marketplace_financial_report): реальный payout после комиссий/логистики/штрафов
   - WB: `ppvz_for_pay` из `/api/v5/supplier/reportDetailByPeriod` (statistics_token)
   - Ozon: `/v3/finance/transaction/list` (payout = сумма после всех удержаний)

## NET-маржа vs GROSS-маржа

- **GROSS** = выручка − себестоимость (завышена на 20–40%, не учитывает комиссии МП)
- **NET** = payout − qty × себестоимость (реальная прибыль после всех удержаний)
- Питер использует `net_margin` из `marketplace_financial_report` если данные есть, иначе GROSS
- Чтобы NET-данные появились: Макс → `/sync_fin` (за 90 дней по умолчанию)

## ДРР по товару

SQL в `_collect_advanced_data`: `product_adv_stats JOIN marketplace_orders` по product_id.
Явное поле `drr` = SUM(spend) / revenue × 100. Не вычисляется LLM-ом на лету.

## Воронка (product_funnel_stats)

Данные из WB NM Report + Ozon analytics. Заполняются через Макс `/sync_funnel`.
Питер `/funnel` — находит товары с плохой конверсией: мало показов (проблема SEO/ставки) vs плохая карточка (высокий view→cart drop).

## Ограничения

- `daily_revenue_snapshot` есть в БД, но в /report пока не используется (MoM-тренды — будущая задача)
- Ozon revenue теперь точная: берётся из `items[].price × quantity` в каждой транзакции (было: `payout + commission + logistics`)
- Ozon возвраты: агрегат есть в `marketplace_financial_report` (tx_type="returns"), точных транзакций нет без Premium Plus
- `max_tokens=4096` для /report
