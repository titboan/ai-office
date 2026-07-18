import { ReturnRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'

// Порог совпадает с бэкендом (agents/peter.py, /returns): >5% — высокий возврат.
function rateColor(rate: number | null) {
  if (rate == null) return 'text-gray-400 dark:text-gray-500'
  const pct = rate * 100
  if (pct > 5) return 'text-red-600 dark:text-red-400'
  if (pct > 2) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-green-600 dark:text-green-400'
}

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : v.toLocaleString('ru-RU')

export default function ReturnsTable({ data }: { data: ReturnRow[] }) {
  if (!data.length) {
    return <Card title="Топ возвратов"><EmptyState /></Card>
  }
  const rows = data.slice(0, 8)
  return (
    <Card title="Топ возвратов">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-700">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">Кол-во</th>
              <th className="text-right pb-2 font-medium">Сумма</th>
              <th className="text-right pb-2 font-medium">% возврата</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-gray-100 dark:border-gray-700">
                <td className="py-1.5 pr-2 font-medium max-w-[140px] truncate">{r.product_name || r.product_id}</td>
                <td className="text-right py-1.5">{r.returns_count}</td>
                <td className="text-right py-1.5">{fmt(r.return_amount)} ₽</td>
                <td className={`text-right py-1.5 font-bold ${rateColor(r.return_rate)}`}>
                  {r.return_rate == null ? '—' : `${(r.return_rate * 100).toFixed(1)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
