import { TrendRow } from '../api'
import Card from '../components/Card'
import MarketplaceBadge from '../components/MarketplaceBadge'
import { trendColorClass } from '../theme'

function pct(current: number, prev: number) {
  if (!prev) return null
  return ((current - prev) / prev) * 100
}

function arrow(v: number | null) {
  if (v === null) return '—'
  return v >= 0 ? `↑${v.toFixed(1)}%` : `↓${Math.abs(v).toFixed(1)}%`
}

export default function WowTrend({ data }: { data: TrendRow[] }) {
  if (!data.length) return null
  return (
    <Card title="Тренд (неделя к неделе)">
      <div className="flex gap-4">
        {data.map(row => {
          const v = pct(row.week_current, row.week_prev)
          return (
            <div key={row.marketplace} className="flex-1 text-center">
              <div className="text-xs text-gray-500 dark:text-gray-400 mb-1 justify-center flex"><MarketplaceBadge marketplace={row.marketplace} /></div>
              <div className={`text-xl font-bold ${trendColorClass(v)}`}>{arrow(v)}</div>
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                {(row.week_current / 1000).toFixed(0)}к / {(row.week_prev / 1000).toFixed(0)}к ₽
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
