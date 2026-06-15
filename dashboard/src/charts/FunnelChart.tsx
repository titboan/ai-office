import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Legend } from 'recharts'
import { FunnelRow } from '../api'

export default function FunnelChart({ data }: { data: FunnelRow[] }) {
  if (!data.length) return null
  const top = data.slice(0, 8)
  return (
    <div className="bg-white rounded-xl p-4 shadow-sm">
      <h2 className="text-sm font-semibold mb-1">Воронка конверсии</h2>
      <p className="text-xs text-gray-400 mb-3">Просмотры → Корзина → Заказ</p>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={top} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
          <XAxis dataKey="name" tick={{ fontSize: 9 }}
            tickFormatter={(v: string) => v.length > 7 ? v.slice(0, 6) + '…' : v} />
          <YAxis tick={{ fontSize: 10 }} unit="%" width={32} />
          <Tooltip formatter={(v: number) => [`${v}%`]} contentStyle={{ color: '#1f2937' }} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 10 }} />
          <ReferenceLine y={2} stroke="#7c3aed" strokeDasharray="3 3" label={{ value: '2%', fontSize: 9, fill: '#7c3aed' }} />
          <ReferenceLine y={10} stroke="#2563eb" strokeDasharray="3 3" label={{ value: '10%', fontSize: 9, fill: '#2563eb' }} />
          <Bar dataKey="view_to_cart_pct" name="Просм.→Корзина" fill="#7c3aed" />
          <Bar dataKey="cart_to_order_pct" name="Корзина→Заказ" fill="#2563eb" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
