import { NetMarginRow } from '../api'

function marginColor(pct: number | null) {
  if (pct === null) return 'text-gray-400 dark:text-gray-500'
  if (pct < 10) return 'text-red-600'
  if (pct < 30) return 'text-yellow-600'
  return 'text-green-600'
}

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : v.toLocaleString()
const fmtPct = (pct: number | null) => pct === null ? '—' : `${pct}%`

export default function NetMarginTable({ data }: { data: NetMarginRow[] }) {
  if (!data.length) return null
  const rows = [...data].sort((a, b) => b.net_profit_total - a.net_profit_total)
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-1">NET маржа (реальные выплаты)</h2>
      <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">Выплата − себестоимость − налог, после всех вычетов маркетплейса</p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-700">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">WB шт</th>
              <th className="text-right pb-2 font-medium">WB %</th>
              <th className="text-right pb-2 font-medium">Ozon шт</th>
              <th className="text-right pb-2 font-medium">Ozon %</th>
              <th className="text-right pb-2 font-medium">Прибыль</th>
              <th className="text-right pb-2 font-medium">Итого %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-gray-100 dark:border-gray-700">
                <td className="py-1.5 pr-2 font-medium">{r.product_name}</td>
                <td className="text-right py-1.5">{r.qty_wb || '—'}</td>
                <td className={`text-right py-1.5 ${marginColor(r.net_margin_pct_wb)}`}>{fmtPct(r.net_margin_pct_wb)}</td>
                <td className="text-right py-1.5">{r.qty_ozon || '—'}</td>
                <td className={`text-right py-1.5 ${marginColor(r.net_margin_pct_ozon)}`}>{fmtPct(r.net_margin_pct_ozon)}</td>
                <td className="text-right py-1.5 font-medium">{fmt(r.net_profit_total)} ₽</td>
                <td className={`text-right py-1.5 font-bold ${marginColor(r.net_margin_pct_total)}`}>
                  {fmtPct(r.net_margin_pct_total)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
