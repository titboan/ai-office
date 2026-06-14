import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from 'recharts'
import { DayRevenue } from '../api'

export default function OrdersChart({ data }: { data: DayRevenue[] }) {
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-3">Заказы по дням</h2>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={d => d.slice(5)} />
          <YAxis tick={{ fontSize: 10 }} width={30} allowDecimals={false} />
          <Tooltip formatter={(v: number) => [`${v} шт`]} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="wb" name="WB" fill="#7c3aed" stackId="a" />
          <Bar dataKey="ozon" name="Ozon" fill="#2563eb" stackId="a" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
