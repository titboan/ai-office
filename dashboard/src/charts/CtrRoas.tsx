import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts'
import { ProductMetric } from '../api'

function ctrColor(ctr: number) {
  if (ctr < 1) return '#dc2626'
  if (ctr < 3) return '#d97706'
  return '#059669'
}

function roasColor(roas: number) {
  if (roas < 2) return '#dc2626'
  if (roas < 5) return '#d97706'
  return '#059669'
}

export default function CtrRoas({ data }: { data: ProductMetric[] }) {
  const withSpend = data.filter(d => d.adv_spend > 0).slice(0, 12)

  return (
    <div className="bg-white rounded-xl p-4 shadow-sm space-y-4">
      <div>
        <h2 className="text-sm font-semibold mb-2">CTR по товарам (%)</h2>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={withSpend} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
            <XAxis dataKey="name" tick={{ fontSize: 9 }} />
            <YAxis tick={{ fontSize: 10 }} unit="%" width={32} />
            <Tooltip formatter={(v: number) => [`${v.toFixed(2)}%`]} contentStyle={{ color: '#1f2937' }} />
            <ReferenceLine y={1} stroke="#dc2626" strokeDasharray="3 3" />
            <ReferenceLine y={3} stroke="#059669" strokeDasharray="3 3" />
            <Bar dataKey="avg_ctr" name="CTR">
              {withSpend.map((d, i) => <Cell key={i} fill={ctrColor(d.avg_ctr)} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-2">ROAS по товарам</h2>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={withSpend} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
            <XAxis dataKey="name" tick={{ fontSize: 9 }} />
            <YAxis tick={{ fontSize: 10 }} width={32} />
            <Tooltip formatter={(v: number) => [`${v.toFixed(2)}x`]} contentStyle={{ color: '#1f2937' }} />
            <ReferenceLine y={2} stroke="#dc2626" strokeDasharray="3 3" />
            <ReferenceLine y={5} stroke="#059669" strokeDasharray="3 3" />
            <Bar dataKey="roas" name="ROAS">
              {withSpend.map((d, i) => <Cell key={i} fill={roasColor(d.roas)} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
