import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine } from 'recharts'
import { ProductMetric } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import { useIsDarkMode } from '../hooks/useIsDarkMode'
import { TOOLTIP_STYLE } from '../theme'

// Светлее/контрастнее на тёмном фоне — та же логика, что marginColorHex/stockColorClass.
function ctrColor(ctr: number, isDark: boolean) {
  if (ctr < 1) return isDark ? '#f87171' : '#dc2626'
  if (ctr < 3) return isDark ? '#fbbf24' : '#d97706'
  return isDark ? '#34d399' : '#059669'
}

function roasColor(roas: number, isDark: boolean) {
  if (roas < 2) return isDark ? '#f87171' : '#dc2626'
  if (roas < 5) return isDark ? '#fbbf24' : '#d97706'
  return isDark ? '#34d399' : '#059669'
}

export default function CtrRoas({ data }: { data: ProductMetric[] }) {
  const isDark = useIsDarkMode()
  const redLine = isDark ? '#f87171' : '#dc2626'
  const greenLine = isDark ? '#34d399' : '#059669'
  const withSpend = data.filter(d => d.adv_spend > 0).slice(0, 12)

  if (!withSpend.length) {
    return <Card><EmptyState message="Нет данных по рекламе за период — запусти /sync_adv у Макса" /></Card>
  }

  return (
    <Card>
      <div className="space-y-4">
        <div>
          <h2 className="text-sm font-semibold mb-2">CTR по товарам (%)</h2>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={withSpend} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
              <XAxis dataKey="name" tick={{ fontSize: 9, fill: 'currentColor' }} />
              <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} unit="%" width={32} />
              <Tooltip formatter={(v: number) => [`${v.toFixed(2)}%`]} contentStyle={TOOLTIP_STYLE} />
              <ReferenceLine y={1} stroke={redLine} strokeDasharray="3 3" />
              <ReferenceLine y={3} stroke={greenLine} strokeDasharray="3 3" />
              <Bar dataKey="avg_ctr" name="CTR">
                {withSpend.map((d, i) => <Cell key={i} fill={ctrColor(d.avg_ctr, isDark)} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div>
          <h2 className="text-sm font-semibold mb-2">ROAS по товарам</h2>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={withSpend} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
              <XAxis dataKey="name" tick={{ fontSize: 9, fill: 'currentColor' }} />
              <YAxis tick={{ fontSize: 10, fill: 'currentColor' }} width={32} />
              <Tooltip formatter={(v: number) => [`${v.toFixed(2)}x`]} contentStyle={TOOLTIP_STYLE} />
              <ReferenceLine y={2} stroke={redLine} strokeDasharray="3 3" />
              <ReferenceLine y={5} stroke={greenLine} strokeDasharray="3 3" />
              <Bar dataKey="roas" name="ROAS">
                {withSpend.map((d, i) => <Cell key={i} fill={roasColor(d.roas, isDark)} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </Card>
  )
}
