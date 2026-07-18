import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { MomRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import { useIsDarkMode } from '../hooks/useIsDarkMode'
import { marketplaceChartColor, TOOLTIP_STYLE } from '../theme'

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)

export default function MomChart({ data }: { data: MomRow[] }) {
  const isDark = useIsDarkMode()

  if (data.length < 2) {
    return <Card title="Динамика по месяцам"><EmptyState message="Нужно минимум 2 месяца данных для динамики" /></Card>
  }
  const rows = data.map(r => ({
    ...r,
    label: r.month ? r.month.slice(0, 7) : '',
  }))
  return (
    <Card title="Динамика по месяцам">
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={rows} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: 'currentColor' }} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} tickFormatter={fmt} width={36} />
          <Tooltip formatter={(v: number) => [`${v.toLocaleString('ru-RU')} ₽`]} contentStyle={TOOLTIP_STYLE} />
          <Bar dataKey="revenue" name="Выручка" fill={marketplaceChartColor('wb', isDark)} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}
