import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts'
import { ProductRow } from '../api'

const COLORS = ['#7c3aed', '#2563eb', '#059669', '#d97706', '#dc2626',
                 '#7c3aed99', '#2563eb99', '#05966999', '#d9770699', '#dc262699']

const tooltipStyle = { backgroundColor: 'var(--tooltip-bg)', color: 'var(--tooltip-text)', border: '1px solid var(--tooltip-border)' }

export default function TopProducts({ data }: { data: ProductRow[] }) {
  const top10 = [...data].sort((a, b) => b.revenue - a.revenue).slice(0, 10)
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Топ товаров по выручке</h2>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart layout="vertical" data={top10} margin={{ left: 8, right: 16, top: 4, bottom: 0 }}>
          <XAxis type="number" tick={{ fontSize: 10, fill: 'currentColor' }}
            tickFormatter={v => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)} />
          <YAxis type="category" dataKey="product_name" tick={{ fontSize: 10, fill: 'currentColor' }} width={72}
            tickFormatter={(v: string) => v.length > 8 ? v.slice(0, 7) + '…' : v} />
          <Tooltip formatter={(v: number) => [`${v.toLocaleString()} ₽`]} contentStyle={tooltipStyle} />
          <Bar dataKey="revenue" name="Выручка">
            {top10.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
