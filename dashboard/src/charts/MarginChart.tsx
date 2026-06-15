import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts'
import { MarginRow } from '../api'

function barColor(pct: number) {
  if (pct < 10) return '#dc2626'
  if (pct < 30) return '#d97706'
  return '#059669'
}

const tooltipStyle = { backgroundColor: 'var(--tooltip-bg)', color: 'var(--tooltip-text)', border: '1px solid var(--tooltip-border)' }

export default function MarginChart({ wb, ozon }: { wb: MarginRow[]; ozon: MarginRow[] }) {
  const combined = [...wb.map(r => ({ ...r, mp: 'WB' })), ...ozon.map(r => ({ ...r, mp: 'Ozon' }))]
    .sort((a, b) => b.profitability - a.profitability)
    .slice(0, 12)

  if (!combined.length) return null

  const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Рентабельность по товарам (%)</h2>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={combined} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
          <XAxis dataKey="product_name" tick={{ fontSize: 9, fill: 'currentColor' }}
            tickFormatter={(v: string) => v.length > 7 ? v.slice(0, 6) + '…' : v} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} unit="%" width={32} />
          <Tooltip
            formatter={(v: number, _: string, props: any) => [
              `${v}% | прибыль ${fmt(props.payload.op_profit)} ₽`
            ]}
            labelFormatter={(label: string) => label}
            contentStyle={tooltipStyle}
          />
          <ReferenceLine y={20} stroke="#d97706" strokeDasharray="3 3" label={{ value: '20%', fontSize: 9, fill: '#d97706' }} />
          <Bar dataKey="profitability" name="Маржа">
            {combined.map((d, i) => <Cell key={i} fill={barColor(d.profitability)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="flex gap-2 mt-2 text-xs text-gray-400 dark:text-gray-500 justify-center">
        <span className="text-green-600">●</span> &gt;30% хорошо
        <span className="text-yellow-500 ml-2">●</span> 10-30%
        <span className="text-red-500 ml-2">●</span> &lt;10% убыток
      </div>
    </div>
  )
}
