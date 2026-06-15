import { NetMarginRow } from '../api'

function marginColor(pct: number) {
  if (pct < 10) return 'text-red-600'
  if (pct < 30) return 'text-yellow-600'
  return 'text-green-600'
}

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : v.toLocaleString()

export default function NetMarginTable({ data }: { data: NetMarginRow[] }) {
  if (!data.length) return null
  const rows = data.slice(0, 10)
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-1">NET маржа (реальные выплаты)</h2>
      <p className="text-xs text-gray-400 mb-3">Выплата − себестоимость после всех вычетов маркетплейса</p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 border-b">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">Выплата</th>
              <th className="text-right pb-2 font-medium">Комиссия</th>
              <th className="text-right pb-2 font-medium">Прибыль</th>
              <th className="text-right pb-2 font-medium">Маржа</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-gray-50">
                <td className="py-1.5 pr-2">
                  <div className="font-medium">{r.product_name}</div>
                  <div className="text-gray-400">{r.marketplace}</div>
                </td>
                <td className="text-right py-1.5">{fmt(r.payout)} ₽</td>
                <td className="text-right py-1.5 text-gray-500">{fmt(r.commission)} ₽</td>
                <td className="text-right py-1.5 font-medium">{fmt(r.net_profit)} ₽</td>
                <td className={`text-right py-1.5 font-bold ${marginColor(r.net_margin_pct)}`}>
                  {r.net_margin_pct}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
