export interface RevenueRow { marketplace: string; revenue: number; orders: number; skus: number }
export interface ProductRow { marketplace: string; product_id: string; product_name: string; revenue: number; qty: number }
export interface AdvRow { marketplace: string; spend: number; views: number; clicks: number }
export interface StockRow { marketplace: string; product_id: string; product_name: string; stock: number }
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
  product_id: string; product_name: string
  returns_count: number; return_amount: number; return_rate: number
}
export interface KwRow { keyword: string; position: number; search_count: number; ctr: number }
export interface AbcRow {
  product_id: string
  name: string
  revenue: number
  qty: number
  share_pct: number
  cumulative_pct: number
  group: 'A' | 'B' | 'C'
}

export interface FunnelRow {
  product_id: string; name: string; marketplace: string
  views: number; add_to_cart: number; orders_count: number; buyouts: number
  view_to_cart_pct: number; cart_to_order_pct: number
}

export interface DashboardData {
  period_days: number
  date_from: string
  revenue: RevenueRow[]
  top_products: ProductRow[]
  adv: AdvRow[]
  low_stocks: StockRow[]
  trend: TrendRow[]
  product_metrics: ProductMetric[]
  stock_velocity: StockVelocity[]
  revenue_by_day: DayRevenue[]
  orders_by_day: DayRevenue[]
  sales_by_day: DayRevenue[]
  net_margin: NetMarginRow[]
  mom_trends: MomRow[]
  returns_top: ReturnRow[]
  kw_top: KwRow[]
  funnel: FunnelRow[]
  abc_data: AbcRow[]
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

export async function fetchDashboard(days = 14): Promise<DashboardData> {
  const urlToken = new URLSearchParams(window.location.search).get('token') ?? ''
  const headers: Record<string, string> = {}
  if (!urlToken) {
    const tg = (window as any).Telegram?.WebApp
    headers['X-Telegram-Init-Data'] = tg?.initData ?? ''
  }

  const tokenParam = urlToken ? `&token=${encodeURIComponent(urlToken)}` : ''
  const res = await fetch(`${API_URL}/api/dashboard?days=${days}${tokenParam}`, { headers })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

// Пишущее действие — только настоящий Telegram initData, без ?token= (та ссылка read-only
// для коллег, не должна давать право менять цену на маркетплейсе от имени владельца).
export async function applyPrice(
  marketplace: 'wb' | 'ozon', productId: string, newPrice: number
): Promise<{ ok: boolean }> {
  const tg = (window as any).Telegram?.WebApp
  const res = await fetch(`${API_URL}/api/apply_price`, {
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

export async function fetchTimeline(): Promise<TimelineData> {
  const urlToken = new URLSearchParams(window.location.search).get('token') ?? ''
  const headers: Record<string, string> = {}
  if (!urlToken) {
    const tg = (window as any).Telegram?.WebApp
    headers['X-Telegram-Init-Data'] = tg?.initData ?? ''
  }
  const tokenParam = urlToken ? `?token=${encodeURIComponent(urlToken)}` : ''
  const res = await fetch(`${API_URL}/api/timeline${tokenParam}`, { headers })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}
