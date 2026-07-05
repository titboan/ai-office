import { StockVelocity } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import MarketplaceBadge from '../components/MarketplaceBadge'
import { stockBarClass, stockColorClass } from '../theme'

// Шкала прогресс-бара: 30 дней = полная полоса, 999 (нет продаж) — особый случай
const SCALE_DAYS = 30

type Grouped = {
  name: string
  days_left: number
  byMp: { marketplace: string; stock: number; days_left: number }[]
}

function groupByProduct(data: StockVelocity[]): Grouped[] {
  const map = new Map<string, Grouped>()
  for (const r of data) {
    const existing = map.get(r.name)
    const entry = { marketplace: r.marketplace, stock: r.stock, days_left: r.days_left }
    if (existing) {
      existing.byMp.push(entry)
      existing.days_left = Math.min(existing.days_left, r.days_left)
    } else {
      map.set(r.name, { name: r.name, days_left: r.days_left, byMp: [entry] })
    }
  }
  return [...map.values()].sort((a, b) => a.days_left - b.days_left)
}

export default function StockTable({ data }: { data: StockVelocity[] }) {
  const rows = groupByProduct(data).slice(0, 15)

  return (
    <Card title="Остатки (дней продаж)">
      <div className="space-y-3">
        {rows.map((r) => (
          <div key={r.name} className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium truncate">{r.name}</span>
              <span className={`text-xs font-semibold whitespace-nowrap ${stockColorClass(r.days_left)}`}>
                {r.days_left === 999 ? '∞' : `${r.days_left} дн.`}
              </span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
              <div
                className={`h-full rounded-full ${stockBarClass(r.days_left)}`}
                style={{ width: `${r.days_left === 999 ? 100 : Math.min(100, (r.days_left / SCALE_DAYS) * 100)}%` }}
              />
            </div>
            <div className="flex gap-2 flex-wrap">
              {r.byMp.map((mp) => (
                <span
                  key={mp.marketplace}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-gray-50 dark:bg-gray-900/40 text-gray-500 dark:text-gray-400"
                >
                  <MarketplaceBadge marketplace={mp.marketplace} /> · {mp.stock} шт
                </span>
              ))}
            </div>
          </div>
        ))}
        {rows.length === 0 && <EmptyState />}
      </div>
    </Card>
  )
}
