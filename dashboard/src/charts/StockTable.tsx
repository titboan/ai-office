import { StockVelocity } from '../api'

function badge(days: number) {
  if (days < 7) return 'bg-red-100 text-red-700'
  if (days < 14) return 'bg-yellow-100 text-yellow-700'
  return 'bg-green-100 text-green-700'
}

function emoji(days: number) {
  if (days < 7) return '🔴'
  if (days < 14) return '🟡'
  return '🟢'
}

export default function StockTable({ data }: { data: StockVelocity[] }) {
  const rows = data.slice(0, 15)
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Остатки (дней продаж)</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 border-b">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">Склад</th>
              <th className="text-right pb-2 font-medium">Дней</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-gray-50">
                <td className="py-1.5 pr-2">
                  <span className="font-medium">{r.name}</span>
                  <span className="text-gray-400 ml-1">{r.marketplace}</span>
                </td>
                <td className="text-right py-1.5">{r.stock}</td>
                <td className="text-right py-1.5">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${badge(r.days_left)}`}>
                    {emoji(r.days_left)} {r.days_left === 999 ? '∞' : r.days_left}
                  </span>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={3} className="py-4 text-center text-gray-400">Нет данных</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
