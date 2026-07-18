import { KwRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'

// Позиции WB: чем меньше число — тем выше в поиске.
function positionColor(priority: boolean, position: number | null) {
  if (priority) return 'text-red-600 dark:text-red-400'
  if (position != null && position > 50) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-gray-600 dark:text-gray-400'
}

export default function KwTable({ data }: { data: KwRow[] }) {
  if (!data || data.length === 0) {
    return (
      <Card title="Ключевые слова WB">
        <EmptyState message="Недоступно — WB закрыл API ключевых слов (404), синк не поможет" />
      </Card>
    )
  }

  const priorityCount = data.filter((r) => r.priority).length

  return (
    <Card
      title="Ключевые слова WB"
      headerExtra={
        priorityCount > 0 ? (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
            📉 просели: {priorityCount}
          </span>
        ) : undefined
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700">
              <th className="text-left py-1 pr-2 font-medium">Ключевое слово</th>
              <th className="text-right py-1 pr-2 font-medium">Позиция</th>
              <th className="text-right py-1 pr-2 font-medium">Частотность</th>
              <th className="text-right py-1 font-medium">CTR</th>
            </tr>
          </thead>
          <tbody>
            {data.map((r, i) => (
              <tr
                key={`${r.keyword}-${i}`}
                className={`border-b border-gray-50 dark:border-gray-700/50 ${
                  r.priority ? 'bg-red-50/40 dark:bg-red-900/10' : ''
                }`}
              >
                <td className="py-1.5 pr-2 max-w-[160px] truncate text-gray-700 dark:text-gray-300">
                  {r.priority && <span className="mr-1" title={`просела на ${r.position_drop} мест`}>📉</span>}
                  {r.keyword}
                </td>
                <td className={`py-1.5 pr-2 text-right font-semibold tabular-nums ${positionColor(r.priority, r.position)}`}>
                  {r.position ?? '—'}
                  {r.position_drop != null && r.position_drop > 0 && (
                    <span className="ml-1 text-[10px] font-normal text-red-500 dark:text-red-400">
                      (−{r.position_drop})
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-2 text-right text-gray-600 dark:text-gray-400 tabular-nums">
                  {r.search_count?.toLocaleString() ?? '—'}
                </td>
                <td className="py-1.5 text-right text-gray-600 dark:text-gray-400 tabular-nums">
                  {r.ctr != null ? `${r.ctr}%` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 text-xs text-gray-400 dark:text-gray-500">
        📉 — позиция просела на {'≥'}10 мест с прошлого замера
      </div>
    </Card>
  )
}
