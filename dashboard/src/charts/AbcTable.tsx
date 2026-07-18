import { Layers } from 'lucide-react'
import { AbcRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import AbcBadge from '../components/AbcBadge'
import { ABC_GROUP_STYLE } from '../theme'

const GROUP_ROW: Record<string, string> = {
  A: 'bg-green-50/40 dark:bg-green-900/10',
  B: 'bg-yellow-50/40 dark:bg-yellow-900/10',
  C: '',
}

export default function AbcTable({ data }: { data: AbcRow[] }) {
  if (!data || data.length === 0) {
    return (
      <Card title={<span className="text-gray-700 dark:text-gray-200 flex items-center gap-1.5"><Layers size={15} /> ABC-анализ</span>}>
        <EmptyState />
      </Card>
    )
  }

  const countA = data.filter(r => r.group === 'A').length
  const countB = data.filter(r => r.group === 'B').length
  const countC = data.filter(r => r.group === 'C').length

  return (
    <Card
      title={<span className="text-gray-700 dark:text-gray-200 flex items-center gap-1.5"><Layers size={15} /> ABC-анализ</span>}
      headerExtra={
        <div className="flex gap-1 text-xs">
          <span className={`px-2 py-0.5 rounded-full font-medium ${ABC_GROUP_STYLE.A}`}>A: {countA}</span>
          <span className={`px-2 py-0.5 rounded-full font-medium ${ABC_GROUP_STYLE.B}`}>B: {countB}</span>
          <span className={`px-2 py-0.5 rounded-full font-medium ${ABC_GROUP_STYLE.C}`}>C: {countC}</span>
        </div>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700">
              <th className="text-left py-1 pr-2">Товар</th>
              <th className="text-right py-1 pr-2">Выручка</th>
              <th className="text-right py-1 pr-2">Доля</th>
              <th className="text-right py-1 pr-2">Накоп.</th>
              <th className="text-center py-1">Гр.</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr
                key={`${row.product_id}-${i}`}
                className={`border-b border-gray-50 dark:border-gray-700/50 ${GROUP_ROW[row.group]}`}
              >
                <td className="py-1.5 pr-2 max-w-[140px] truncate text-gray-700 dark:text-gray-300">
                  {row.name || row.product_id}
                </td>
                <td className="py-1.5 pr-2 text-right text-gray-600 dark:text-gray-400 tabular-nums">
                  {(row.revenue / 1000).toFixed(0)}к
                </td>
                <td className="py-1.5 pr-2 text-right text-gray-600 dark:text-gray-400 tabular-nums">
                  {row.share_pct}%
                </td>
                <td className="py-1.5 pr-2 text-right text-gray-500 dark:text-gray-500 tabular-nums">
                  {row.cumulative_pct}%
                </td>
                <td className="py-1.5 text-center">
                  <AbcBadge group={row.group} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 text-xs text-gray-400 dark:text-gray-500">
        A = 80% выручки · B = 95% · C = остальные
      </div>
    </Card>
  )
}
