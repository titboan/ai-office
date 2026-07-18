export interface RevenueRow { marketplace: string; revenue: number; orders: number }
export interface ProductRow { marketplace: string; product_id: string; product_name: string; revenue: number; qty: number }
export interface AdvRow { marketplace: string; spend: number }
export interface TrendRow { marketplace: string; week_current: number; week_prev: number }
export interface ProductMetric {
  product_id: string; name: string; marketplace: string
  views: number; clicks: number; avg_ctr: number
  adv_spend: number; adv_orders: number; buyouts: number; roas: number
}
export interface StockVelocity {
  marketplace: string; product_id: string; name: string
  stock: number; daily_orders: number; days_left: number
}
export interface DayRevenue { date: string; wb: number; ozon: number }

export interface NetMarginRow {
  product_name: string
  wb_article: string | null; ozon_offer_id: string | null
  qty_wb: number; payout_wb: number; net_profit_wb: number; net_margin_pct_wb: number | null
  recommended_price_wb: number | null; at_target_wb: boolean
  qty_ozon: number; payout_ozon: number; net_profit_ozon: number; net_margin_pct_ozon: number | null
  recommended_price_ozon: number | null; at_target_ozon: boolean
  net_profit_total: number; net_margin_pct_total: number | null
}
export interface MomRow { month: string; revenue: number; orders: number }
export interface ReturnRow {
  product_id: string; product_name: string; marketplace: string
  returns_count: number; return_amount: number; return_rate: number | null
}
export interface KwRow {
  keyword: string; position: number | null; search_count: number | null; ctr: number | null
  position_drop: number | null   // (позиция сейчас − позиция на прошлом снапшоте), null если истории нет
  priority: boolean              // просадка >= config.SEO_POSITION_DROP_THRESHOLD (10 мест)
}
export interface AbcRow {
  product_id: string
  name: string
  marketplace: string
  revenue: number
  qty: number
  share_pct: number
  cumulative_pct: number
  group: 'A' | 'B' | 'C'
}

// GROSS-маржа (комиссия/логистика МП грубо оценены, без налога) — запасной фоллбэк для
// MarginChart, если net_margin пуст (см. agents/peter.py::_collect_data п.3-4).
export interface GrossMarginRow {
  product_id: string
  product_name: string
  revenue: number
  qty: number
  cost: number
  op_profit: number
  profitability: number   // проценты, уже округлено до 1 знака
}

export interface FunnelRow {
  product_id: string; name: string; marketplace: string
  views: number; add_to_cart: number; orders_count: number; buyouts: number
  view_to_cart_pct: number; cart_to_order_pct: number
}

export interface SupplyCluster {
  cluster: string
  stock: number
  cluster_dr: number   // темп продаж кластера, шт/день
  days_left: number
  need: number          // нужно отправить в кластер, чтобы выйти на целевой запас
}
export interface SupplyRow {
  name: string
  marketplace: 'wb' | 'ozon'
  category: string
  daily_rate: number
  total_stock: number
  total_days_left: number
  to_order: number       // заказать у поставщика сверх того, что в пути/оформлено
  urgency: 'КРИТИЧНО' | 'СРОЧНО' | 'НОРМА'
  clusters: SupplyCluster[]
}
export interface SupplyPlan {
  products: SupplyRow[]
  lead_days: number
  safety_days: number
}

export interface CatalogProductRow {
  name: string
  wb_article: string | null
  ozon_offer_id: string | null
  wb_price: number | null
  ozon_price: number | null
  has_cost_wb: boolean
  has_cost_ozon: boolean
}
export interface ShopKpiRow {
  rating: number | null
  return_pct: number | null
  cancellation_pct: number | null
  penalty_count: number
  is_proxy: boolean   // WB-фолбэк "по данным за 30 дн", когда прямого API рейтинга нет
}
export interface CatalogData {
  products: CatalogProductRow[]
  shop_kpi: Record<string, ShopKpiRow>   // ключ — 'wb' | 'ozon'
}

export interface BidSuggestionRow {
  marketplace: 'wb' | 'ozon'
  campaign_id: string
  shop_id: string | null   // нужен только для Ozon (per-SKU ставки конкретного магазина)
  name: string
  spend_7d: number; revenue_7d: number; drr: number
  direction: 'up' | 'down'; delta_pct: number; reason: string
  current_value: number | null; new_value: number | null
  market_recommended_cpm: number | null   // только WB — рыночная рекомендация WB
  market_flag: 'overspend' | 'underspend' | null
}

export interface DashboardData {
  period_days: number
  date_from: string
  revenue: RevenueRow[]
  top_products: ProductRow[]
  adv: AdvRow[]
  trend: TrendRow[]
  product_metrics: ProductMetric[]
  stock_velocity: StockVelocity[]
  revenue_by_day: DayRevenue[]
  orders_by_day: DayRevenue[]
  sales_by_day: DayRevenue[]
  net_margin: NetMarginRow[]
  margin_wb: GrossMarginRow[]
  margin_ozon: GrossMarginRow[]
  mom_trends: MomRow[]
  returns_top: ReturnRow[]
  kw_top: KwRow[]
  funnel: FunnelRow[]
  abc_data: AbcRow[]
  bid_suggestions: BidSuggestionRow[]
  supply_plan: SupplyPlan
  catalog: CatalogData
}

export interface TimelineEvent {
  agent_key: string
  event_type: string
  created_at: string
}

export interface ChainRun {
  chain_id: string
  started_at: string
  duration_sec: number | null
  status: 'completed' | 'failed' | 'running'
  events: TimelineEvent[]
}

export interface TimelineData {
  chains: ChainRun[]
}

const API_URL = import.meta.env.VITE_API_URL ?? ''
const DEFAULT_TIMEOUT_MS = 20000

// Без клиентского таймаута зависший запрос (плохая сеть, забуксовавший бэкенд)
// оставляет UI в состоянии "загрузка"/"обновление" навсегда — Telegram WebView не
// прерывает fetch сам. Оборачиваем каждый вызов в AbortController с таймаутом,
// сигнатуры экспортируемых функций (fetchDashboard и т.д.) не меняются.
async function fetchWithTimeout(url: string, options: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

export async function fetchDashboard(days = 14): Promise<DashboardData> {
  const urlToken = new URLSearchParams(window.location.search).get('token') ?? ''
  const headers: Record<string, string> = {}
  if (!urlToken) {
    const tg = (window as any).Telegram?.WebApp
    headers['X-Telegram-Init-Data'] = tg?.initData ?? ''
  }

  const tokenParam = urlToken ? `&token=${encodeURIComponent(urlToken)}` : ''
  const res = await fetchWithTimeout(`${API_URL}/api/dashboard?days=${days}${tokenParam}`, { headers })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// Пишущее действие — только настоящий Telegram initData, без ?token= (та ссылка read-only
// для коллег, не должна давать право менять цену на маркетплейсе от имени владельца).
export async function applyPrice(
  marketplace: 'wb' | 'ozon', productId: string, newPrice: number
): Promise<{ ok: boolean }> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetchWithTimeout(`${API_URL}/api/apply_price`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData ?? '',
    },
    body: JSON.stringify({ marketplace, product_id: productId, new_price: newPrice }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// Тот же принцип, что и applyPrice — только настоящий Telegram initData.
// shopId нужен только для Ozon (per-SKU ставки конкретного магазина), для WB — null.
export async function applyBid(
  marketplace: 'wb' | 'ozon', campaignId: string, direction: 'up' | 'down', deltaPct: number,
  shopId: string | null
): Promise<{ ok: boolean; current?: number; new?: number }> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetchWithTimeout(`${API_URL}/api/apply_bid`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData ?? '',
    },
    body: JSON.stringify({
      marketplace, campaign_id: campaignId, direction, delta_pct: deltaPct, shop_id: shopId,
    }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export interface CostRow {
  mapping_id: number
  display_name: string
  wb_article: string | null
  ozon_offer_id: string | null
  cost_wb: number | null
  purchase_logistics_wb: number | null
  packaging_marking_wb: number | null
  cost_ozon: number | null
  purchase_logistics_ozon: number | null
  packaging_marking_ozon: number | null
}

// Себестоимость — чувствительные бизнес-данные, тот же принцип, что и applyPrice:
// только настоящий Telegram initData, без ?token= (та ссылка read-only для коллег).
export async function getCosts(): Promise<CostRow[]> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetchWithTimeout(`${API_URL}/api/costs`, {
    headers: { 'X-Telegram-Init-Data': tg?.initData ?? '' },
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json().then(d => d.costs)
}

export async function setCost(
  marketplace: 'wb' | 'ozon', productId: string, purchaseLogistics: number, packagingMarking: number
): Promise<{ ok: boolean }> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetchWithTimeout(`${API_URL}/api/set_cost`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData ?? '',
    },
    body: JSON.stringify({
      marketplace, product_id: productId,
      purchase_logistics: purchaseLogistics, packaging_marking: packagingMarking,
    }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// Тот же принцип, что и setCost — только настоящий Telegram initData, без ?token=.
// Заменяет /map и текстовую часть /add у Макса (agents/max.py). name — обязателен и
// является натуральным ключом (совпадение с уже существующим товаром обновляет его,
// как ON CONFLICT (display_name) в /map) — wb_article/ozon_offer_id/category опциональны.
export async function createProduct(
  name: string, wbArticle: string, ozonOfferId: string, category: string
): Promise<{ ok: boolean }> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetchWithTimeout(`${API_URL}/api/product`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData ?? '',
    },
    body: JSON.stringify({
      name, wb_article: wbArticle, ozon_offer_id: ozonOfferId, category,
    }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// Заменяет /merge_products (inline-пикер mergewiz:* у Макса) — та же db.merge_product_rows,
// но по натуральным ключам вместо внутренних id (catalog.products их и так отдаёт).
export async function mergeProduct(
  wbArticle: string, ozonOfferId: string
): Promise<{ ok: boolean; error?: string }> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetchWithTimeout(`${API_URL}/api/merge_product`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData ?? '',
    },
    body: JSON.stringify({ wb_article: wbArticle, ozon_offer_id: ozonOfferId }),
  })
  if (!res.ok && res.status !== 404) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// Заменяет /add_shop у Макса. apiToken — чувствительное поле (полный доступ к аккаунту
// продавца на маркетплейсе) — никогда не логируем в консоль, только в JSON body запроса
// (как и остальные POST здесь), поле в форме — type="password".
export async function addShop(
  marketplace: 'wb' | 'ozon', apiToken: string, clientId: string, shopName: string
): Promise<{ ok: boolean }> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetchWithTimeout(`${API_URL}/api/add_shop`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData ?? '',
    },
    body: JSON.stringify({
      marketplace, api_token: apiToken, client_id: clientId, shop_name: shopName,
    }),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export async function fetchTimeline(): Promise<TimelineData> {
  const urlToken = new URLSearchParams(window.location.search).get('token') ?? ''
  const headers: Record<string, string> = {}
  if (!urlToken) {
    const tg = (window as any).Telegram?.WebApp
    headers['X-Telegram-Init-Data'] = tg?.initData ?? ''
  }
  const tokenParam = urlToken ? `?token=${encodeURIComponent(urlToken)}` : ''
  const res = await fetchWithTimeout(`${API_URL}/api/timeline${tokenParam}`, { headers })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}
