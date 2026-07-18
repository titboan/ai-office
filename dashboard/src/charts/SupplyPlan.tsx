import { SupplyPlan as SupplyPlanData, SupplyRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import MarketplaceBadge from '../components/MarketplaceBadge'

// Та же классификация, что в agents/peter.py::_classify_supply_urgency —
// критично/срочно/норма по total_days_left относительно lead_days+safety_days.
const URGENCY_STYLE: Record<SupplyRow['urgency'], { icon: string; badge: string }> = {
  КРИТИЧНО: { icon: '🔴', badge: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  СРОЧНО:   { icon: '🟡', badge: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' },
  НОРМА:    { icon: '🟢', badge: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
}

function ProductRow({ row }: { row: SupplyRow }) {
  const clusters = [...row.clusters].sort((a, b) => b.need - a.need)
  return (
    <div className="py-1.5 border-b border-gray-50 dark:border-gray-700/50 last:border-0">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <MarketplaceBadge marketplace={row.marketplace} />
          <span className="text-xs font-medium truncate min-w-0">{row.name}</span>
        </div>
        <span className="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
          запас {row.total_days_left === 999 ? '∞' : `${row.total_days_left} дн`}
        </span>
      </div>
      {row.to_order > 0 && (
        <div className="text-xs font-semibold text-gray-700 dark:text-gray-200 mt-0.5">
          → заказать {row.to_order} шт
        </div>
      )}
      {clusters.length > 0 && (
        <div className="flex gap-1 flex-wrap mt-1">
          {clusters.map((c) => (
            <span
              key={c.cluster}
              className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] bg-gray-50 dark:bg-gray-900/40 text-gray-500 dark:text-gray-400"
              title={`${c.stock} шт на складе · темп ${c.cluster_dr} шт/день`}
            >
              {c.cluster}: {c.stock} шт · {c.days_left === 999 ? '∞' : `${c.days_left} дн`}
              {c.need > 0 && <span className="ml-1 text-orange-500 dark:text-orange-400">→{c.need}</span>}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function SupplyPlan({ data }: { data: SupplyPlanData }) {
  const products = data?.products ?? []

  if (products.length === 0) {
    return (
      <Card title="План поставок">
        <EmptyState message="Нет данных — запусти синк остатков и заказов у Макса" />
      </Card>
    )
  }

  const critical = products
    .filter((p) => p.urgency === 'КРИТИЧНО')
    .sort((a, b) => b.to_order - a.to_order)
  const urgent = products
    .filter((p) => p.urgency === 'СРОЧНО')
    .sort((a, b) => b.to_order - a.to_order)
  const normal = products.filter((p) => p.urgency === 'НОРМА')

  return (
    <Card
      title="План поставок по регионам/кластерам"
      subtitle={`Срок поставки ${data.lead_days} дн + буфер ${data.safety_days} дн → критично, если запас кончится раньше`}
      headerExtra={
        <div className="flex gap-1">
          {critical.length > 0 && (
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${URGENCY_STYLE.КРИТИЧНО.badge}`}>
              {URGENCY_STYLE.КРИТИЧНО.icon} {critical.length}
            </span>
          )}
          {urgent.length > 0 && (
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${URGENCY_STYLE.СРОЧНО.badge}`}>
              {URGENCY_STYLE.СРОЧНО.icon} {urgent.length}
            </span>
          )}
        </div>
      }
    >
      {critical.length > 0 && (
        <div className="mb-2">
          <div className="text-xs font-semibold text-red-600 dark:text-red-400 mb-1">
            {URGENCY_STYLE.КРИТИЧНО.icon} Критично — кончится до прихода партии
          </div>
          {critical.map((p) => <ProductRow key={`${p.marketplace}-${p.name}`} row={p} />)}
        </div>
      )}

      {urgent.length > 0 && (
        <div className="mb-2">
          <div className="text-xs font-semibold text-yellow-600 dark:text-yellow-400 mb-1">
            {URGENCY_STYLE.СРОЧНО.icon} Срочно — пора размещать заказ
          </div>
          {urgent.map((p) => <ProductRow key={`${p.marketplace}-${p.name}`} row={p} />)}
        </div>
      )}

      {normal.length > 0 && (
        <details className="mt-1">
          <summary className="text-xs font-medium text-gray-500 dark:text-gray-400 cursor-pointer">
            {URGENCY_STYLE.НОРМА.icon} Норма ({normal.length}) — заказ не нужен
          </summary>
          <div className="mt-1">
            {normal
              .sort((a, b) => b.total_days_left - a.total_days_left)
              .map((p) => (
                <div key={`${p.marketplace}-${p.name}`} className="flex items-center justify-between gap-2 py-1 text-xs text-gray-500 dark:text-gray-400">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <MarketplaceBadge marketplace={p.marketplace} />
                    <span className="truncate min-w-0">{p.name}</span>
                  </div>
                  <span className="whitespace-nowrap">{p.total_days_left === 999 ? '∞' : `${p.total_days_left} дн`}</span>
                </div>
              ))}
          </div>
        </details>
      )}
    </Card>
  )
}
