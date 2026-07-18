import { useState } from 'react'
import { NetMarginRow, AbcRow, NetMarginPeriod, applyPrice } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import AbcBadge from '../components/AbcBadge'
import { MARGIN_TARGET_PCT, marginColorClass as marginColor } from '../theme'
import { useMainButtonAction } from '../hooks/useMainButtonAction'

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : v.toLocaleString('ru-RU')
const fmtDate = (iso: string) => {
  const [y, m, d] = iso.split('-')
  return `${d}.${m}.${y.slice(2)}`
}
// Ozon-часть net_margin всегда за последний полный календарный месяц (ограничение API Ozon),
// WB-часть — за фактически выбранный на дашборде период. Итоговая колонка «Итого» ниже
// складывает эти два разных окна — предупреждаем об этом прямо под заголовком таблицы.
function periodNote(period?: NetMarginPeriod): string | null {
  if (!period) return null
  return `NET по WB: ${fmtDate(period.wb.from)}–${fmtDate(period.wb.to)} · NET по Ozon: ${fmtDate(period.ozon.from)}–${fmtDate(period.ozon.to)} (разные периоды, «Итого» — их сумма)`
}

// ABC-строки считаются по product_id (WB/Ozon раздельно), а NetMarginTable — по общему
// названию товара. Один и тот же товар может встретиться в abc_data дважды (WB и Ozon
// строка) — берём "лучшую" из групп (A важнее B важнее C), чтобы не смотреть отдельно
// в две карточки дашборда в поисках "какой товар из группы A даёт минус".
function bestAbcGroupByName(abcData: AbcRow[]): Record<string, 'A' | 'B' | 'C'> {
  const rank = { A: 0, B: 1, C: 2 }
  const result: Record<string, 'A' | 'B' | 'C'> = {}
  for (const row of abcData) {
    const name = row.name
    if (!result[name] || rank[row.group] < rank[result[name]]) {
      result[name] = row.group
    }
  }
  return result
}

type Pending = { key: string; marketplace: 'wb' | 'ozon'; productId: string; price: number; productName: string }
type RowStatus = 'applied' | 'error'

function MarginCell({ pct, atTarget, recPrice, selectable, selected, applied, failed, onSelect }: {
  pct: number | null; atTarget?: boolean; recPrice?: number | null
  selectable?: boolean; selected?: boolean; applied?: boolean; failed?: boolean; onSelect?: () => void
}) {
  if (pct === null) return <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
  if (atTarget) {
    return (
      <td className="text-right py-1.5 text-green-600 font-medium">✓ {pct}%</td>
    )
  }
  if (applied) {
    return <td className="text-right py-1.5 text-green-600 font-medium">✓ применено</td>
  }
  if (recPrice != null) {
    return (
      <td className="text-right py-1.5">
        <span className={marginColor(pct)}>{pct}%</span>
        <span className="text-gray-400 dark:text-gray-500"> → </span>
        {selectable ? (
          <button
            onClick={onSelect}
            className={`font-medium underline decoration-dotted ${
              selected ? 'text-blue-800 dark:text-blue-300' : 'text-blue-600 dark:text-blue-400'
            }`}
          >
            {recPrice.toLocaleString('ru-RU')}₽
          </button>
        ) : (
          <span className="text-blue-600 dark:text-blue-400 font-medium">{recPrice.toLocaleString('ru-RU')}₽</span>
        )}
        {failed && <span className="text-red-500 ml-1" title="Не удалось применить, попробуй ещё раз">⚠</span>}
      </td>
    )
  }
  return <td className={`text-right py-1.5 ${marginColor(pct)}`}>{pct}%</td>
}

export default function NetMarginTable({ data, abcData = [], period }: { data: NetMarginRow[]; abcData?: AbcRow[]; period?: NetMarginPeriod }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const [status, setStatus] = useState<Record<string, RowStatus>>({})
  const abcGroupByName = bestAbcGroupByName(abcData)
  const note = periodNote(period)

  useMainButtonAction(
    pending,
    p => `${p.marketplace === 'wb' ? 'WB' : 'Ozon'} "${p.productName}" → ${p.price.toLocaleString('ru-RU')} ₽`,
    async p => {
      try {
        const res = await applyPrice(p.marketplace, p.productId, p.price)
        setStatus(s => ({ ...s, [p.key]: res.ok ? 'applied' : 'error' }))
      } catch {
        setStatus(s => ({ ...s, [p.key]: 'error' }))
      } finally {
        setPending(null)
      }
    },
  )

  if (!data.length) {
    return (
      <Card title="NET маржа (реальные выплаты)" subtitle={`Цель: ${MARGIN_TARGET_PCT}% · ✓ норма · % → цена₽ = рекомендация`}>
        <EmptyState />
      </Card>
    )
  }
  const rows = [...data].sort((a, b) => b.net_profit_total - a.net_profit_total)
  return (
    <Card title="NET маржа (реальные выплаты)" subtitle={`Цель: ${MARGIN_TARGET_PCT}% · A/B/C = вклад в выручку · ✓ норма · % → цена₽ = рекомендация · нажми на цену чтобы применить`}>
      {note && <div className="text-[10px] text-gray-400 dark:text-gray-500 mb-1.5">{note}</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-700">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium hidden md:table-cell">WB шт</th>
              <th className="text-right pb-2 font-medium">WB маржа</th>
              <th className="text-right pb-2 font-medium hidden md:table-cell">Ozon шт</th>
              <th className="text-right pb-2 font-medium">Ozon маржа</th>
              <th className="text-right pb-2 font-medium hidden md:table-cell">Прибыль</th>
              <th className="text-right pb-2 font-medium">Итого %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const keyWb = `${i}-wb`
              const keyOzon = `${i}-ozon`
              return (
                <tr key={i} className="border-b border-gray-100 dark:border-gray-700">
                  <td className="py-1.5 pr-2 font-medium max-w-[140px]">
                    <span className="flex items-center gap-1 min-w-0">
                      {abcGroupByName[r.product_name] && <AbcBadge group={abcGroupByName[r.product_name]} />}
                      <span className="truncate min-w-0">{r.product_name}</span>
                    </span>
                    {/* На мобильном — кол-во под названием вместо отдельных столбцов */}
                    {(r.qty_wb > 0 || r.qty_ozon > 0) && (
                      <div className="md:hidden text-[10px] text-gray-400 dark:text-gray-500 mt-0.5 font-normal">
                        {r.qty_wb > 0 && `WB: ${r.qty_wb} шт`}
                        {r.qty_wb > 0 && r.qty_ozon > 0 && ' · '}
                        {r.qty_ozon > 0 && `Ozon: ${r.qty_ozon} шт`}
                      </div>
                    )}
                  </td>
                  <td className="text-right py-1.5 hidden md:table-cell">{r.qty_wb || '—'}</td>
                  <MarginCell
                    pct={r.qty_wb ? r.net_margin_pct_wb : null}
                    atTarget={r.at_target_wb}
                    recPrice={r.recommended_price_wb}
                    selectable={!!r.wb_article}
                    selected={pending?.key === keyWb}
                    applied={status[keyWb] === 'applied'}
                    failed={status[keyWb] === 'error'}
                    onSelect={() => r.wb_article && setPending({
                      key: keyWb, marketplace: 'wb', productId: r.wb_article, price: r.recommended_price_wb!,
                      productName: r.product_name,
                    })}
                  />
                  <td className="text-right py-1.5 hidden md:table-cell">{r.qty_ozon || '—'}</td>
                  <MarginCell
                    pct={r.qty_ozon ? r.net_margin_pct_ozon : null}
                    atTarget={r.at_target_ozon}
                    recPrice={r.recommended_price_ozon}
                    selectable={!!r.ozon_offer_id}
                    selected={pending?.key === keyOzon}
                    applied={status[keyOzon] === 'applied'}
                    failed={status[keyOzon] === 'error'}
                    onSelect={() => r.ozon_offer_id && setPending({
                      key: keyOzon, marketplace: 'ozon', productId: r.ozon_offer_id, price: r.recommended_price_ozon!,
                      productName: r.product_name,
                    })}
                  />
                  <td className="text-right py-1.5 font-medium hidden md:table-cell">{fmt(r.net_profit_total)} ₽</td>
                  <td className={`text-right py-1.5 font-bold ${marginColor(r.net_margin_pct_total)}`}>
                    {r.net_margin_pct_total !== null ? `${r.net_margin_pct_total}%` : '—'}
                    {/* Прибыль под % на мобильном */}
                    <div className="md:hidden text-[10px] font-medium text-gray-500 dark:text-gray-400 mt-0.5">
                      {fmt(r.net_profit_total)} ₽
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
