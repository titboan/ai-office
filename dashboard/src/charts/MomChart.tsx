import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { MomRow } from '../api'

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)

const tooltipStyle = { backgroundColor: 'var(--tooltip-bg)', color: 'var(--tooltip-text)', border: '1px solid var(--tooltip-border)' }

export default function MomChart({ data }: { data: MomRow[] }) {
  if (data.length < 2) return null
  const rows = data.map(r => ({
    ...r,
    label: r.month ? r.month.slice(0, 7) : '',
  }))
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Динамика по месяцам</h2>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={rows} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: 'currentColor' }} />
          <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} tickFormatter={fmt} width={36} />
          <Tooltip formatter={(v: number) => [`${v.toLocaleString()} ₽`]} contentStyle={tooltipStyle} />
          <Bar dataKey="revenue" name="Выручка" fill="#7c3aed" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
