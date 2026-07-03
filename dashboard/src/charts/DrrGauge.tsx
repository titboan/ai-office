import { AdvRow, DayRevenue } from '../api'
import Card from '../components/Card'
import { MARKETPLACE, drrColorClass } from '../theme'

function drr(buyouts: number, spend: number) {
  if (!buyouts) return null
  return (spend / buyouts) * 100
}

export default function DrrGauge({ adv, salesByDay }: { adv: AdvRow[]; salesByDay: DayRevenue[] }) {
  const byMp = (mp: 'wb' | 'ozon') => {
    const buyouts = salesByDay.reduce((sum, row) => sum + (row[mp] ?? 0), 0)
    const s = adv.find(x => x.marketplace === mp)?.spend ?? 0
    return { buyouts, s, drr: drr(buyouts, s) }
  }

  const wb = byMp('wb')
  const ozon = byMp('ozon')

  return (
    <Card title="ДРР по площадкам">
      <div className="flex gap-4">
        {[{ label: MARKETPLACE.wb.label, ...wb }, { label: MARKETPLACE.ozon.label, ...ozon }].map(({ label, buyouts, s, drr: d }) => (
          <div key={label} className="flex-1 text-center">
            <div className="text-xs text-gray-500 dark:text-gray-400 mb-1">{label}</div>
            <div className={`text-2xl font-bold ${drrColorClass(d)}`}>
              {d !== null ? `${d.toFixed(1)}%` : '—'}
            </div>
            <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">
              продажи {buyouts.toLocaleString()} ₽ / реклама {s.toLocaleString()} ₽
            </div>
          </div>
        ))}
      </div>
      <div className="flex gap-2 mt-3 text-xs text-gray-400 dark:text-gray-500 justify-center">
        <span className="text-green-600">●</span> &lt;20% норма
        <span className="text-yellow-500 ml-2">●</span> 20-30% высокий
        <span className="text-red-500 ml-2">●</span> &gt;30% критично
      </div>
    </Card>
  )
}
