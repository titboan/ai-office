import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Legend } from 'recharts'
import { FunnelRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import { useIsDarkMode } from '../hooks/useIsDarkMode'
import { marketplaceChartColor, TOOLTIP_STYLE } from '../theme'

export default function FunnelChart({ data }: { data: FunnelRow[] }) {
  const isDark = useIsDarkMode()
  const wbColor = marketplaceChartColor('wb', isDark)
  const ozonColor = marketplaceChartColor('ozon', isDark)

  if (!data.length) {
    return <Card title="Воронка конверсии" subtitle="Просмотры → Корзина → Заказ"><EmptyState /></Card>
  }
  const top = data.slice(0, 8)
  return (
    <Card title="Воронка конверсии" subtitle="Просмотры → Корзина → Заказ">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={top} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
          <XAxis dataKey="name" tick={{ fontSize: 9, fill: 'currentColor' }}
            tickFormatter={(v: string) => v.length > 7 ? v.slice(0, 6) + '…' : v} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} unit="%" width={32} />
          <Tooltip formatter={(v: number) => [`${v}%`]} contentStyle={TOOLTIP_STYLE} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 10 }} />
          <ReferenceLine y={2} stroke={wbColor} strokeDasharray="3 3" label={{ value: '2%', fontSize: 9, fill: wbColor }} />
          <ReferenceLine y={10} stroke={ozonColor} strokeDasharray="3 3" label={{ value: '10%', fontSize: 9, fill: ozonColor }} />
          <Bar dataKey="view_to_cart_pct" name="Просм.→Корзина" fill={wbColor} />
          <Bar dataKey="cart_to_order_pct" name="Корзина→Заказ" fill={ozonColor} />
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}
