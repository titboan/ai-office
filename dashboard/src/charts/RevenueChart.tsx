import { ComposedChart, Area, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { DayRevenue } from '../api'
import Card from '../components/Card'
import { MARKETPLACE, TOOLTIP_STYLE } from '../theme'

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
    <Card title="Выручка по дням">
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={merged} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'currentColor' }} tickFormatter={fmtDate} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} tickFormatter={fmt} width={36} />
          <Tooltip
            formatter={(v: number) => `${v.toLocaleString()} ₽`}
            labelFormatter={fmtDate}
            contentStyle={TOOLTIP_STYLE}
          />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          <Line type="monotone" dataKey="wb" name="WB заказы" stroke={MARKETPLACE.wb.color} dot={false} strokeWidth={2} />
          <Area type="monotone" dataKey="wb_s" name="WB выкупы" fill={MARKETPLACE.wb.color} stroke="none" fillOpacity={0.18} legendType="none" />
          <Line type="monotone" dataKey="ozon" name="Ozon заказы" stroke={MARKETPLACE.ozon.color} dot={false} strokeWidth={2} />
          <Area type="monotone" dataKey="ozon_s" name="Ozon выкупы" fill={MARKETPLACE.ozon.color} stroke="none" fillOpacity={0.18} legendType="none" />
        </ComposedChart>
      </ResponsiveContainer>
    </Card>
  )
}
