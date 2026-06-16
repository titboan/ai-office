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
  qty_wb: number; payout_wb: number; net_profit_wb: number; net_margin_pct_wb: number | null
  qty_ozon: number; payout_ozon: number; net_profit_ozon: number; net_margin_pct_ozon: number | null
  net_profit_total: number; net_margin_pct_total: number | null
}
export interface MomRow { month: string; revenue: number; orders: number }
export interface ReturnRow {
  product_id: string; product_name: string
  returns_count: number; return_amount: number; return_rate: number
}
export interface KwRow { keyword: string; position: number; search_count: number; ctr: number }
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
}

const API_URL = import.meta.env.VITE_API_URL ?? ''

export async function fetchDashboard(days = 14): Promise<DashboardData> {
  const tg = (window as any).Telegram?.WebApp
  const initData: string = tg?.initData ?? ''

  const res = await fetch(`${API_URL}/api/dashboard?days=${days}`, {
    headers: { 'X-Telegram-Init-Data': initData },
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}
