import { TrendRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import MarketplaceBadge from '../components/MarketplaceBadge'
import { trendColorClass } from '../theme'

// prev === 0 && current === 0 → действительно нет данных за обе недели.
// prev === 0 && current > 0 → рост с нуля, а не "нет данных" — Infinity, не null.
function pct(current: number, prev: number): number | null {
  if (prev === 0) return current > 0 ? Infinity : null
  return ((current - prev) / prev) * 100
}

function arrow(v: number | null) {
  if (v === null) return '—'
  if (v === Infinity) return '🆕 новое'
  return v >= 0 ? `↑${v.toFixed(1)}%` : `↓${Math.abs(v).toFixed(1)}%`
}

export default function WowTrend({ data }: { data: TrendRow[] }) {
  if (!data.length) {
    return <Card title="Тренд (неделя к неделе)"><EmptyState message="Нет данных за прошлую неделю" /></Card>
  }
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
