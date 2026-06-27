import { NetMarginRow } from '../api'

const TARGET_PCT = 50

function marginColor(pct: number | null) {
  if (pct === null) return 'text-gray-400 dark:text-gray-500'
  if (pct < 10) return 'text-red-600'
  if (pct < 30) return 'text-yellow-600'
  if (pct < TARGET_PCT) return 'text-orange-500'
  return 'text-green-600'
}

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : v.toLocaleString()

function MarginCell({ pct, atTarget, recPrice, estimated }: {
  pct: number | null; atTarget?: boolean; recPrice?: number | null; estimated?: boolean
}) {
  if (pct === null) return <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
  const prefix = estimated ? <span className="text-gray-400 mr-0.5" title="Приблизительно: из заказов, без учёта комиссии МП">~</span> : null
  if (atTarget) {
    return (
      <td className="text-right py-1.5 text-green-600 font-medium">{prefix}✓ {pct}%</td>
    )
  }
  if (recPrice != null) {
    return (
      <td className="text-right py-1.5">
        {prefix}<span className={marginColor(pct)}>{pct}%</span>
        <span className="text-gray-400 dark:text-gray-500"> → </span>
        <span className="text-blue-600 dark:text-blue-400 font-medium">{recPrice.toLocaleString()}₽</span>
      </td>
    )
  }
  return <td className={`text-right py-1.5 ${marginColor(pct)}`}>{prefix}{pct}%</td>
}

export default function NetMarginTable({ data }: { data: NetMarginRow[] }) {
  if (!data.length) return null
  const rows = [...data].sort((a, b) => b.net_profit_total - a.net_profit_total)
  const anyEstimated = rows.some(r => r._wb_estimated)
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-1">NET маржа (реальные выплаты)</h2>
      <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">
        Цель: {TARGET_PCT}% · ✓ норма · % → цена₽ = рекомендация
        {anyEstimated && <span className="ml-1 text-gray-400"> · ~ WB из заказов (нет финотчёта)</span>}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-700">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">WB шт</th>
              <th className="text-right pb-2 font-medium">WB маржа</th>
              <th className="text-right pb-2 font-medium">Ozon шт</th>
              <th className="text-right pb-2 font-medium">Ozon маржа</th>
              <th className="text-right pb-2 font-medium">Прибыль</th>
              <th className="text-right pb-2 font-medium">Итого %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-gray-100 dark:border-gray-700">
                <td className="py-1.5 pr-2 font-medium">{r.product_name}</td>
                <td className="text-right py-1.5">{r.qty_wb || '—'}</td>
                <MarginCell
                  pct={r.qty_wb ? r.net_margin_pct_wb : null}
                  atTarget={r.at_target_wb}
                  recPrice={r.recommended_price_wb}
                  estimated={r._wb_estimated}
                />
                <td className="text-right py-1.5">{r.qty_ozon || '—'}</td>
                <MarginCell pct={r.qty_ozon ? r.net_margin_pct_ozon : null} atTarget={r.at_target_ozon} recPrice={r.recommended_price_ozon} />
                <td className="text-right py-1.5 font-medium">{fmt(r.net_profit_total)} ₽</td>
                <td className={`text-right py-1.5 font-bold ${marginColor(r.net_margin_pct_total)}`}>
                  {r.net_margin_pct_total !== null ? `${r.net_margin_pct_total}%` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
