import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { DayRevenue } from '../api'

const fmt = (v: number) =>
  v >= 1000 ? `${(v / 1000).toFixed(0)}к` : String(v)

export default function SalesChart({ data }: { data: DayRevenue[] }) {
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Продажи (выкупы) по дням</h2>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={d => d.slice(5)} />
          <YAxis tick={{ fontSize: 10 }} tickFormatter={fmt} width={36} />
          <Tooltip formatter={(v: number) => [`${v.toLocaleString()} ₽`]} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          <Line type="monotone" dataKey="wb" name="WB" stroke="#7c3aed" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="ozon" name="Ozon" stroke="#2563eb" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
