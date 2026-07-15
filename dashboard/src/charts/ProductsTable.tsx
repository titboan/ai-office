import { CatalogProductRow } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import MarketplaceBadge from '../components/MarketplaceBadge'

function fmtPrice(price: number | null): string {
  return price != null ? `${price.toLocaleString('ru-RU')} ₽` : '—'
}

export default function ProductsTable({ data }: { data: CatalogProductRow[] }) {
  if (!data || data.length === 0) {
    return (
      <Card title="Товары">
        <EmptyState message="Реестр пуст — добавь товар через /map или /add у Макса" />
      </Card>
    )
  }

  const noCost = data.filter((p) => !p.has_cost_wb && !p.has_cost_ozon).length

  return (
    <Card
      title="Товары"
      subtitle={`${data.length} позиций`}
      headerExtra={
        noCost > 0 ? (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
            ⚠️ нет с/с: {noCost}
          </span>
        ) : undefined
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700">
              <th className="text-left py-1 pr-2 font-medium">Товар</th>
              <th className="text-left py-1 pr-2 font-medium">WB</th>
              <th className="text-right py-1 pr-2 font-medium">Цена WB</th>
              <th className="text-left py-1 pr-2 font-medium">Ozon</th>
              <th className="text-right py-1 font-medium">Цена Ozon</th>
            </tr>
          </thead>
          <tbody>
            {data.map((p) => (
              <tr key={p.name} className="border-b border-gray-50 dark:border-gray-700/50">
                <td className="py-1.5 pr-2 max-w-[140px] truncate text-gray-700 dark:text-gray-300">{p.name}</td>
                <td className="py-1.5 pr-2 text-gray-500 dark:text-gray-400 whitespace-nowrap">
                  {p.wb_article ? (
                    <span className="inline-flex items-center gap-1">
                      <MarketplaceBadge marketplace="wb" />{p.wb_article}
                    </span>
                  ) : '—'}
                </td>
                <td className="py-1.5 pr-2 text-right tabular-nums text-gray-600 dark:text-gray-400 whitespace-nowrap">
                  {fmtPrice(p.wb_price)}
                  {p.wb_article && !p.has_cost_wb && (
                    <span className="ml-1 text-yellow-500 dark:text-yellow-400" title="Себестоимость WB не задана">⚠️</span>
                  )}
                </td>
                <td className="py-1.5 pr-2 text-gray-500 dark:text-gray-400 whitespace-nowrap">
                  {p.ozon_offer_id ? (
                    <span className="inline-flex items-center gap-1">
                      <MarketplaceBadge marketplace="ozon" />{p.ozon_offer_id}
                    </span>
                  ) : '—'}
                </td>
                <td className="py-1.5 text-right tabular-nums text-gray-600 dark:text-gray-400 whitespace-nowrap">
                  {fmtPrice(p.ozon_price)}
                  {p.ozon_offer_id && !p.has_cost_ozon && (
                    <span className="ml-1 text-yellow-500 dark:text-yellow-400" title="Себестоимость Ozon не задана">⚠️</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 text-xs text-gray-400 dark:text-gray-500">
        «—» — товара нет на площадке (артикул не привязан). ⚠️ — себестоимость не задана (/cost у Макса).
        Маржа и рекомендованные цены по каждому товару — на вкладке «Дашборд» (NET-маржа).
      </div>
    </Card>
  )
}
