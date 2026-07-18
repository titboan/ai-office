import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { ProductRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import { useIsDarkMode } from '../hooks/useIsDarkMode'
import { TOOLTIP_STYLE } from '../theme'

const COLORS_LIGHT = ['#7c3aed', '#2563eb', '#059669', '#d97706', '#dc2626',
                       '#7c3aed99', '#2563eb99', '#05966999', '#d9770699', '#dc262699']
// Светлее/контрастнее на тёмном фоне (те же оттенки, что colorDark у WB/Ozon и marginColorHex-палитры).
const COLORS_DARK = ['#a78bfa', '#60a5fa', '#34d399', '#fbbf24', '#f87171',
                      '#a78bfa99', '#60a5fa99', '#34d39999', '#fbbf2499', '#f8717199']

export default function TopProducts({ data }: { data: ProductRow[] }) {
  const isDark = useIsDarkMode()
  const COLORS = isDark ? COLORS_DARK : COLORS_LIGHT
  const top10 = [...data].sort((a, b) => b.revenue - a.revenue).slice(0, 10)
  if (!top10.length) {
    return <Card title="Топ товаров по выручке"><EmptyState message="Нет продаж за период" /></Card>
  }
  return (
    <Card title="Топ товаров по выручке">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart layout="vertical" data={top10} margin={{ left: 8, right: 16, top: 4, bottom: 0 }}>
          <XAxis type="number" tick={{ fontSize: 10, fill: 'currentColor' }}
            tickFormatter={v => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)} />
          <YAxis type="category" dataKey="product_name" tick={{ fontSize: 10, fill: 'currentColor' }} width={72}
            tickFormatter={(v: string) => v.length > 8 ? v.slice(0, 7) + '…' : v} />
          <Tooltip formatter={(v: number) => [`${v.toLocaleString('ru-RU')} ₽`]} contentStyle={TOOLTIP_STYLE} />
          <Bar dataKey="revenue" name="Выручка">
            {top10.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  )
}
