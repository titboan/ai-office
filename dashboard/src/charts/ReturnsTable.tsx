import { ReturnRow } from '../api'

function rateColor(rate: number) {
  const pct = rate * 100
  if (pct > 20) return 'text-red-600'
  if (pct > 10) return 'text-yellow-600'
  return 'text-green-600'
}

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : v.toLocaleString()

export default function ReturnsTable({ data }: { data: ReturnRow[] }) {
  if (!data.length) return null
  const rows = data.slice(0, 8)
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Топ возвратов</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 border-b">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">Кол-во</th>
              <th className="text-right pb-2 font-medium">Сумма</th>
              <th className="text-right pb-2 font-medium">% возврата</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-gray-50">
                <td className="py-1.5 pr-2 font-medium">{r.product_name || r.product_id}</td>
                <td className="text-right py-1.5">{r.returns_count}</td>
                <td className="text-right py-1.5">{fmt(r.return_amount)} ₽</td>
                <td className={`text-right py-1.5 font-bold ${rateColor(r.return_rate)}`}>
                  {(r.return_rate * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
