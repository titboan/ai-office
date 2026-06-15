import { useEffect, useState } from 'react'
import { fetchDashboard, DashboardData } from './api'
import RevenueChart from './charts/RevenueChart'
import TopProducts from './charts/TopProducts'
import DrrGauge from './charts/DrrGauge'
import CtrRoas from './charts/CtrRoas'
import StockTable from './charts/StockTable'
import WowTrend from './charts/WowTrend'
import MarginChart from './charts/MarginChart'
import NetMarginTable from './charts/NetMarginTable'
import FunnelChart from './charts/FunnelChart'
import ReturnsTable from './charts/ReturnsTable'
import MomChart from './charts/MomChart'

type Days = 7 | 14 | 30

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState<Days>(14)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp
    tg?.ready()
    tg?.expand()
  }, [])

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchDashboard(days)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [days])

  const totalRevenue = data?.revenue.reduce((s, r) => s + r.revenue, 0) ?? 0
  const totalOrders = data?.revenue.reduce((s, r) => s + r.orders, 0) ?? 0
  const avgCheck = totalOrders > 0 ? Math.round(totalRevenue / totalOrders) : 0
  const totalSpend = data?.adv.reduce((s, r) => s + r.spend, 0) ?? 0
  const drr = totalRevenue > 0 ? (totalSpend / totalRevenue * 100).toFixed(1) : '—'

  const wowTotal = data?.trend.reduce(
    (acc, r) => ({ cur: acc.cur + r.week_current, prev: acc.prev + r.week_prev }),
    { cur: 0, prev: 0 }
  )
  const wowPct = wowTotal && wowTotal.prev > 0
    ? ((wowTotal.cur - wowTotal.prev) / wowTotal.prev * 100)
    : null

  const kpiCards = data ? [
    { label: 'Выручка', value: `${(totalRevenue / 1000).toFixed(0)}к ₽`, color: '' },
    { label: 'Заказов', value: totalOrders.toLocaleString(), color: '' },
    { label: 'Ср. чек', value: `${(avgCheck / 1000).toFixed(1)}к ₽`, color: '' },
    { label: 'Реклама', value: `${(totalSpend / 1000).toFixed(0)}к ₽`, color: '' },
    { label: 'ДРР', value: `${drr}%`, color: '' },
    {
      label: 'WoW',
      value: wowPct !== null
        ? `${wowPct >= 0 ? '↑' : '↓'}${Math.abs(wowPct).toFixed(1)}%`
        : '—',
      color: wowPct === null ? '' : wowPct >= 0 ? 'text-green-600' : 'text-red-500',
    },
  ] : []

  return (
    <div className="min-h-screen p-3 space-y-3" style={{ background: 'var(--tg-theme-bg-color, #f5f5f5)' }}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-base font-bold">📊 Дашборд</h1>
        <div className="flex gap-1">
          {([7, 14, 30] as Days[]).map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                days === d ? 'bg-purple-600 text-white' : 'bg-white text-gray-600'
              }`}
            >
              {d}д
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="text-center py-12 text-gray-400 text-sm">Загружаю данные…</div>
      )}

      {error && (
        <div className="bg-red-50 text-red-600 rounded-xl p-4 text-sm">
          ❌ {error}
        </div>
      )}

      {data && !loading && (
        <>
          {/* KPI cards — 6 штук, 2 строки по 3 */}
          <div className="grid grid-cols-3 gap-2">
            {kpiCards.map(({ label, value, color }) => (
              <div key={label} className="bg-white rounded-xl p-3 shadow-sm text-center">
                <div className="text-xs text-gray-500">{label}</div>
                <div className={`text-lg font-bold mt-0.5 ${color || 'text-gray-800'}`}>{value}</div>
              </div>
            ))}
          </div>

          {/* WoW тренд по маркетплейсам */}
          <WowTrend data={data.trend} />

          {/* Выручка по дням (линия заказов + область выкупов) */}
          <RevenueChart data={data.revenue_by_day} sales={data.sales_by_day ?? []} />

          {/* Топ товаров */}
          <TopProducts data={data.top_products} />

          {/* ДРР по площадкам */}
          <DrrGauge revenue={data.revenue} adv={data.adv} />

          {/* Рентабельность (gross margin) */}
          <MarginChart wb={data.margin_wb ?? []} ozon={data.margin_ozon ?? []} />

          {/* NET маржа из реальных выплат */}
          <NetMarginTable data={data.net_margin ?? []} />

          {/* Воронка конверсии */}
          <FunnelChart data={data.funnel ?? []} />

          {/* CTR и ROAS по товарам */}
          <CtrRoas data={data.product_metrics} />

          {/* Возвраты */}
          <ReturnsTable data={data.returns_top ?? []} />

          {/* Остатки */}
          <StockTable data={data.stock_velocity} />

          {/* MoM динамика */}
          <MomChart data={data.mom_trends ?? []} />

          <div className="text-center text-xs text-gray-400 pb-2">
            За {data.period_days} дней с {data.date_from}
          </div>
        </>
      )}
    </div>
  )
}
