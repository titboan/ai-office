import { TrendRow } from '../api'

function pct(current: number, prev: number) {
  if (!prev) return null
  return ((current - prev) / prev) * 100
}

function arrow(v: number | null) {
  if (v === null) return '—'
  return v >= 0 ? `↑${v.toFixed(1)}%` : `↓${Math.abs(v).toFixed(1)}%`
}

function color(v: number | null) {
  if (v === null) return 'text-gray-400'
  return v >= 0 ? 'text-green-600' : 'text-red-500'
}

const MP_LABEL: Record<string, string> = { wb: '🟣 WB', ozon: '🔵 Ozon' }

export default function WowTrend({ data }: { data: TrendRow[] }) {
  if (!data.length) return null
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Тренд (неделя к неделе)</h2>
      <div className="flex gap-4">
        {data.map(row => {
          const v = pct(row.week_current, row.week_prev)
          return (
            <div key={row.marketplace} className="flex-1 text-center">
              <div className="text-xs text-gray-500 mb-1">{MP_LABEL[row.marketplace] ?? row.marketplace}</div>
              <div className={`text-xl font-bold ${color(v)}`}>{arrow(v)}</div>
              <div className="text-xs text-gray-400 mt-1">
                {(row.week_current / 1000).toFixed(0)}к / {(row.week_prev / 1000).toFixed(0)}к ₽
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
