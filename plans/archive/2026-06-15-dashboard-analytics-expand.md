# План: расширение дашборда аналитикой Питера и Макса

**Статус: завершён**

---

## Контекст

Дашборд — Telegram Mini App (React + Recharts + Tailwind), бэкенд — aiohttp в `main.py`.
API `/api/dashboard` уже возвращает богатые данные из `_collect_data` и `_collect_advanced_data`,
но фронтенд показывает лишь половину из них.

### Что уже есть в дашборде
| Компонент | Данные |
|---|---|
| 3 KPI-карточки | выручка, реклама, ДРР |
| RevenueChart | выручка по дням (WB/Ozon) |
| SalesChart | продажи по дням |
| TopProducts | топ-10 по выручке (BarChart) |
| DrrGauge | ДРР WB vs Ozon |
| CtrRoas | CTR и ROAS по товарам |
| StockTable | остатки + дней продаж |

### Что API уже отдаёт, но фронтенд НЕ показывает
| Ключ в JSON | Смысл |
|---|---|
| `trend` | WoW тренд (неделя vs прошлая неделя) — TrendRow уже в api.ts |
| `margin_wb` / `margin_ozon` | Валовая рентабельность по товарам (gross margin %) |
| `net_margin` | NET маржа из реальных выплат минус себестоимость |
| `mom_trends` | MoM тренды (помесячная выручка за 2 мес.) |
| `returns_top` | Топ возвратов по сумме / rate |
| `kw_top` | Топ ключевых слов WB (позиция, охват, CTR) |
| `orders_by_day` | Заказы по дням (уже в DashboardData) |

### Что нужно добавить в бэкенд
| Данные | Источник |
|---|---|
| `funnel` (воронка) | `product_funnel_stats` — views→cart→orders→buyouts |

---

## Что делаем

### Фаза 1: Бэкенд — добавить воронку

**Файл:** `agents/peter.py` → метод `_collect_advanced_data`

Добавить запрос после query #3 (`stock_velocity`):
```sql
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
```
Добавить `"funnel": [dict(r) for r in funnel]` в возвращаемый dict.

### Фаза 2: api.ts — добавить типы

**Файл:** `dashboard/src/api.ts`

Добавить интерфейсы:
```ts
export interface MarginRow { product_id: string; product_name: string; revenue: number; qty: number; cost: number; op_profit: number; profitability: number }
export interface NetMarginRow { marketplace: string; product_id: string; product_name: string; qty: number; revenue: number; payout: number; commission: number; logistics: number; storage: number; penalty: number; cost_per_unit: number; net_profit: number; net_margin_pct: number }
export interface MomRow { month: string; revenue: number; orders: number }
export interface ReturnRow { product_id: string; product_name: string; returns_count: number; return_amount: number; return_rate: number }
export interface KwRow { keyword: string; position: number; search_count: number; ctr: number }
export interface FunnelRow { product_id: string; name: string; marketplace: string; views: number; add_to_cart: number; orders_count: number; buyouts: number; view_to_cart_pct: number; cart_to_order_pct: number }
```

Добавить в `DashboardData`:
```ts
margin_wb: MarginRow[]
margin_ozon: MarginRow[]
net_margin: NetMarginRow[]
mom_trends: MomRow[]
returns_top: ReturnRow[]
kw_top: KwRow[]
funnel: FunnelRow[]
```

### Фаза 3: KPI-карточки (App.tsx)

Расширить блок с 3 до 6 карточек в 2 строки по 3:
- Строка 1: Выручка, Заказов (sum orders), Средний чек (revenue/orders)
- Строка 2: Реклама, ДРР, WoW % (из `trend` — прирост/падение текущей недели)

WoW % считается как `(week_current - week_prev) / week_prev * 100`, суммируя по обоим маркетплейсам.
Цвет: зелёный если > 0, красный если < 0.

### Фаза 4: новые компоненты

Создать файлы в `dashboard/src/charts/`:

#### `WowTrend.tsx`
Две маленькие карточки WB и Ozon с % изменения неделя-к-неделе.
Источник: `trend` (TrendRow).

#### `MarginChart.tsx`
BarChart рентабельности по товарам (gross profitability %).
Источник: `margin_wb` + `margin_ozon` (объединить, сортировать по profitability desc).
Цвет баров: зелёный > 30%, жёлтый 10-30%, красный < 10%.
ReferenceLine на 20%.

#### `NetMarginTable.tsx`
Таблица NET маржи: товар | маркетплейс | выручка | выплата | комиссия | чистая прибыль | маржа %.
Источник: `net_margin`.
Цвет маржи: такой же как MarginChart.
Показывать только если таблица непустая (данные требуют `/sync_fin`).

#### `FunnelChart.tsx`
Горизонтальный BarChart воронки: топ-8 товаров, два бара (view→cart% и cart→order%).
ReferenceLine: view→cart норма 2%, cart→order норма 10%.
Источник: `funnel`.

#### `ReturnsTable.tsx`
Таблица: товар | возвратов | сумма возвратов | % возврата.
Источник: `returns_top`. Показывать только топ-8.
Цвет: return_rate > 20% → красный, 10-20% → жёлтый.

#### `MomChart.tsx`
BarChart помесячной выручки (2 столбца: WB и Ozon или общее).
Источник: `mom_trends`.
Показывать только если >= 2 месяцев данных.

### Фаза 5: порядок блоков в App.tsx

```
1. Header + period selector
2. KPI cards (6 штук, 2 строки по 3)
3. WowTrend (WoW индикатор WB / Ozon)
4. RevenueChart (выручка по дням)
5. TopProducts (топ-10 товаров)
6. DrrGauge (ДРР по площадкам)
7. MarginChart (рентабельность по товарам)
8. NetMarginTable (NET маржа — только если есть данные)
9. FunnelChart (воронка — только если есть данные)
10. CtrRoas (CTR + ROAS)
11. ReturnsTable (возвраты — только если есть данные)
12. StockTable (остатки)
13. MomChart (MoM — только если есть 2+ месяца)
14. Footer (период)
```

SalesChart убираем — дублирует RevenueChart и менее важен.

---

## Критические файлы

- `agents/peter.py` — добавить funnel query в `_collect_advanced_data` (~15 строк)
- `dashboard/src/api.ts` — добавить 6 новых интерфейсов + расширить DashboardData
- `dashboard/src/App.tsx` — расширить KPI, добавить новые компоненты в layout
- `dashboard/src/charts/WowTrend.tsx` — новый
- `dashboard/src/charts/MarginChart.tsx` — новый
- `dashboard/src/charts/NetMarginTable.tsx` — новый
- `dashboard/src/charts/FunnelChart.tsx` — новый
- `dashboard/src/charts/ReturnsTable.tsx` — новый
- `dashboard/src/charts/MomChart.tsx` — новый

---

## Проверка

1. `npm run build` в `dashboard/` — без ошибок TypeScript
2. `npm run dev` + открыть в браузере с mock initData — все блоки рендерятся
3. Компоненты с пустыми данными (NetMarginTable, FunnelChart) не вызывают ошибок
4. Деплой на Railway: `git push` → проверить `/api/dashboard` endpoint
