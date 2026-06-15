import { useEffect, useState } from 'react'
import { fetchDashboard, DashboardData } from './api'
import RevenueChart from './charts/RevenueChart'
import SalesChart from './charts/SalesChart'
import TopProducts from './charts/TopProducts'
import DrrGauge from './charts/DrrGauge'
import CtrRoas from './charts/CtrRoas'
import StockTable from './charts/StockTable'

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
  const totalSpend = data?.adv.reduce((s, r) => s + r.spend, 0) ?? 0
  const drr = totalRevenue > 0 ? (totalSpend / totalRevenue * 100).toFixed(1) : '—'

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
          {/* KPI cards */}
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: 'Выручка', value: `${(totalRevenue / 1000).toFixed(0)}к ₽` },
              { label: 'Реклама', value: `${(totalSpend / 1000).toFixed(0)}к ₽` },
              { label: 'ДРР', value: `${drr}%` },
            ].map(({ label, value }) => (
              <div key={label} className="bg-white rounded-xl p-3 shadow-sm text-center">
                <div className="text-xs text-gray-500">{label}</div>
                <div className="text-lg font-bold mt-0.5">{value}</div>
              </div>
            ))}
          </div>

          <RevenueChart data={data.revenue_by_day} sales={data.sales_by_day ?? []} />
          <SalesChart data={data.sales_by_day ?? []} />
          <TopProducts data={data.top_products} />
          <DrrGauge revenue={data.revenue} adv={data.adv} />
          <CtrRoas data={data.product_metrics} />
          <StockTable data={data.stock_velocity} />

          <div className="text-center text-xs text-gray-400 pb-2">
            За {data.period_days} дней с {data.date_from}
          </div>
        </>
      )}
    </div>
  )
}
