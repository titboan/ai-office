import { CatalogData, ShopKpiRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import MarketplaceBadge from '../components/MarketplaceBadge'

// Пороги как в agents/max.py::_shop_kpi_text — там просто числа без цвета, здесь
// добавляем 🟢/🟡/🔴 для быстрого визуального сканирования (рейтинг магазина, не товара —
// отдельная шкала от marginColorClass/stockColorClass в theme.ts).
function ratingColor(rating: number | null): string {
  if (rating === null) return 'text-gray-400 dark:text-gray-500'
  if (rating >= 4.5) return 'text-green-600 dark:text-green-400'
  if (rating >= 4.0) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-red-600 dark:text-red-400'
}

function MpKpi({ marketplace, kpi }: { marketplace: string; kpi: ShopKpiRow }) {
  return (
    <div className="flex-1 min-w-[120px]">
      <div className="flex items-center gap-1.5 mb-1">
        <MarketplaceBadge marketplace={marketplace} />
        {kpi.is_proxy && (
          <span className="text-[10px] text-gray-400 dark:text-gray-500">(за 30 дн)</span>
        )}
      </div>
      <div className={`text-xl font-bold ${ratingColor(kpi.rating)}`}>
        {kpi.rating != null ? `⭐ ${kpi.rating.toFixed(1)}` : '—'}
      </div>
      <div className="text-xs text-gray-500 dark:text-gray-400 mt-1 space-y-0.5">
        <div>↩️ Возвраты: {kpi.return_pct != null ? `${kpi.return_pct.toFixed(1)}%` : '—'}</div>
        <div>🚫 Отмены: {kpi.cancellation_pct != null ? `${kpi.cancellation_pct.toFixed(1)}%` : '—'}</div>
        <div>⚠️ Штрафы: {kpi.penalty_count}</div>
      </div>
    </div>
  )
}

export default function ShopKpiCard({ data }: { data: CatalogData['shop_kpi'] }) {
  const entries = Object.entries(data ?? {}) as [string, ShopKpiRow][]

  if (entries.length === 0) {
    return (
      <Card title="Рейтинг продавца">
        <EmptyState message="Данные недоступны — подключи магазин или подожди следующий синк (/shop_kpi у Макса)" />
      </Card>
    )
  }

  return (
    <Card title="Рейтинг продавца">
      <div className="flex gap-4 flex-wrap">
        {entries.map(([mp, kpi]) => <MpKpi key={mp} marketplace={mp} kpi={kpi} />)}
      </div>
    </Card>
  )
}
