import { RevenueRow, AdvRow } from '../api'

function drr(revenue: number, spend: number) {
  if (!revenue) return null
  return (spend / revenue) * 100
}

function color(val: number | null) {
  if (val === null) return 'text-gray-400'
  if (val > 30) return 'text-red-500'
  if (val > 20) return 'text-yellow-500'
  return 'text-green-600'
}

export default function DrrGauge({ revenue, adv }: { revenue: RevenueRow[]; adv: AdvRow[] }) {
  const byMp = (mp: string) => {
    const r = revenue.find(x => x.marketplace === mp)?.revenue ?? 0
    const s = adv.find(x => x.marketplace === mp)?.spend ?? 0
    return { r, s, drr: drr(r, s) }
  }

  const wb = byMp('wb')
  const ozon = byMp('ozon')

  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">ДРР по площадкам</h2>
      <div className="flex gap-4">
        {[{ label: '🟣 WB', ...wb }, { label: '🔵 Ozon', ...ozon }].map(({ label, r, s, drr: d }) => (
          <div key={label} className="flex-1 text-center">
            <div className="text-xs text-gray-500 mb-1">{label}</div>
            <div className={`text-2xl font-bold ${color(d)}`}>
              {d !== null ? `${d.toFixed(1)}%` : '—'}
            </div>
            <div className="text-xs text-gray-400 mt-1">
              {r.toLocaleString()} ₽ / реклама {s.toLocaleString()} ₽
            </div>
          </div>
        ))}
      </div>
      <div className="flex gap-2 mt-3 text-xs text-gray-400 justify-center">
        <span className="text-green-600">●</span> &lt;20% норма
        <span className="text-yellow-500 ml-2">●</span> 20-30% высокий
        <span className="text-red-500 ml-2">●</span> &gt;30% критично
      </div>
    </div>
  )
}
