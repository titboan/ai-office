import { ComposedChart, Area, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { DayRevenue } from '../api'

interface Props {
  data: DayRevenue[]
  sales: DayRevenue[]
}

const fmt = (v: number) =>
  v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)

const fmtDate = (iso: string) => {
  const [, m, d] = iso.split('-')
  return `${d}.${m}`
}

const tooltipStyle = { backgroundColor: 'var(--tooltip-bg)', color: 'var(--tooltip-text)', border: '1px solid var(--tooltip-border)' }

export default function RevenueChart({ data, sales }: Props) {
  const salesMap = new Map(sales.map(s => [s.date, s]))
  const merged = data.map(r => ({
    date: r.date,
    wb: r.wb,
    ozon: r.ozon,
    wb_s: salesMap.get(r.date)?.wb ?? 0,
    ozon_s: salesMap.get(r.date)?.ozon ?? 0,
  }))

  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Выручка по дням</h2>
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={merged} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'currentColor' }} tickFormatter={fmtDate} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} tickFormatter={fmt} width={36} />
          <Tooltip
            formatter={(v: number) => [`${v.toLocaleString()} ₽`]}
            labelFormatter={fmtDate}
            contentStyle={tooltipStyle}
          />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          <Area type="monotone" dataKey="wb_s" name="WB выкупы" fill="#7c3aed" stroke="none" fillOpacity={0.18} legendType="none" />
          <Area type="monotone" dataKey="ozon_s" name="Ozon выкупы" fill="#2563eb" stroke="none" fillOpacity={0.18} legendType="none" />
          <Line type="monotone" dataKey="wb" name="WB" stroke="#7c3aed" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="ozon" name="Ozon" stroke="#2563eb" dot={false} strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
