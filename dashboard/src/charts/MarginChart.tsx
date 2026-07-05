import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts'
import { NetMarginRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import { TOOLTIP_STYLE, marginColorHex } from '../theme'

export default function MarginChart({ data }: { data: NetMarginRow[] }) {
  const combined = [...data]
    .filter(d => d.net_margin_pct_total !== null)
    .sort((a, b) => (b.net_margin_pct_total ?? 0) - (a.net_margin_pct_total ?? 0))
    .slice(0, 12)

  const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)

  if (!combined.length) {
    return (
      <Card title="Рентабельность по товарам (%)" subtitle="NET-маржа: выплата МП − себестоимость − налог, по обеим площадкам">
        <EmptyState />
      </Card>
    )
  }

  return (
    <Card title="Рентабельность по товарам (%)" subtitle="NET-маржа: выплата МП − себестоимость − налог, по обеим площадкам">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={combined} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
          <XAxis dataKey="product_name" tick={{ fontSize: 9, fill: 'currentColor' }}
            tickFormatter={(v: string) => v.length > 7 ? v.slice(0, 6) + '…' : v} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} unit="%" width={32} />
          <Tooltip
            formatter={(v: number, _: string, props: any) => [
              `${v}% | прибыль ${fmt(props.payload.net_profit_total)} ₽`
            ]}
            labelFormatter={(label: string) => label}
            contentStyle={TOOLTIP_STYLE}
          />
          <ReferenceLine y={50} stroke="#059669" strokeDasharray="3 3" label={{ value: 'цель 50%', fontSize: 9, fill: '#059669' }} />
          <Bar dataKey="net_margin_pct_total" name="Маржа">
            {combined.map((d, i) => <Cell key={i} fill={marginColorHex(d.net_margin_pct_total ?? 0)} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="flex gap-2 mt-2 text-xs text-gray-400 dark:text-gray-500 justify-center">
        <span className="text-green-600">●</span> ≥50% цель
        <span className="text-yellow-500 ml-2">●</span> 30-50%
        <span className="text-orange-500 ml-2">●</span> 10-30%
        <span className="text-red-500 ml-2">●</span> &lt;10%
      </div>
    </Card>
  )
}
