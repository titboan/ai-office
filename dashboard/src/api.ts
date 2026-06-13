export interface RevenueRow { marketplace: string; revenue: number; orders: number; skus: number }
export interface ProductRow { marketplace: string; product_id: string; product_name: string; revenue: number; qty: number }
export interface AdvRow { marketplace: string; spend: number; views: number; clicks: number }
export interface StockRow { marketplace: string; product_id: string; product_name: string; stock: number }
export interface TrendRow { marketplace: string; week_current: number; week_prev: number }
export interface ProductMetric {
  product_id: string; name: string; marketplace: string
  views: number; clicks: number; avg_ctr: number
  adv_spend: number; adv_orders: number; revenue: number; roas: number
}
export interface StockVelocity {
  marketplace: string; product_id: string; name: string
  stock: number; daily_orders: number; days_left: number
}
export interface DayRevenue { date: string; wb: number; ozon: number }

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
