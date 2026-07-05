import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { DayRevenue } from '../api'
import Card from '../components/Card'
import { useIsDarkMode } from '../hooks/useIsDarkMode'
import { marketplaceChartColor, TOOLTIP_STYLE } from '../theme'

export default function OrdersChart({ data }: { data: DayRevenue[] }) {
  const isDark = useIsDarkMode()
  return (
    <Card title="Заказы по дням">
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'currentColor' }} tickFormatter={d => d.slice(5)} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} width={30} allowDecimals={false} />
          <Tooltip formatter={(v: number) => [`${v} шт`]} contentStyle={TOOLTIP_STYLE} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="wb" name="WB" fill={marketplaceChartColor('wb', isDark)} stackId="a" />
          <Bar dataKey="ozon" name="Ozon" fill={marketplaceChartColor('ozon', isDark)} stackId="a" />
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}
