import { useEffect, useState } from 'react'
import { NetMarginRow, applyPrice } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import { MARGIN_TARGET_PCT, marginColorClass as marginColor } from '../theme'

const fmt = (v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}к` : v.toLocaleString()

type Pending = { key: string; marketplace: 'wb' | 'ozon'; productId: string; price: number }
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
            {recPrice.toLocaleString()}₽
          </button>
        ) : (
          <span className="text-blue-600 dark:text-blue-400 font-medium">{recPrice.toLocaleString()}₽</span>
        )}
        {failed && <span className="text-red-500 ml-1" title="Не удалось применить, попробуй ещё раз">⚠</span>}
      </td>
    )
  }
  return <td className={`text-right py-1.5 ${marginColor(pct)}`}>{pct}%</td>
}

export default function NetMarginTable({ data }: { data: NetMarginRow[] }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const [status, setStatus] = useState<Record<string, RowStatus>>({})

  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp
    const mainButton = tg?.MainButton
    if (!mainButton) return
    if (!pending) {
      mainButton.hide()
      return
    }
    const label = pending.marketplace === 'wb' ? 'WB' : 'Ozon'
    mainButton.setText(`Применить цену ${label}: ${pending.price.toLocaleString()} ₽`)
    mainButton.show()

    const onClick = async () => {
      mainButton.showProgress()
      try {
        const res = await applyPrice(pending.marketplace, pending.productId, pending.price)
        setStatus(s => ({ ...s, [pending.key]: res.ok ? 'applied' : 'error' }))
      } catch {
        setStatus(s => ({ ...s, [pending.key]: 'error' }))
      } finally {
        mainButton.hideProgress()
        setPending(null)
      }
    }
    mainButton.onClick(onClick)
    return () => {
      mainButton.offClick(onClick)
    }
  }, [pending])

  // Скрыть MainButton при уходе со страницы/размонтировании карточки
  useEffect(() => () => {
    const mainButton = (window as any).Telegram?.WebApp?.MainButton
    mainButton?.hide()
  }, [])

  if (!data.length) {
    return (
      <Card title="NET маржа (реальные выплаты)" subtitle={`Цель: ${MARGIN_TARGET_PCT}% · ✓ норма · % → цена₽ = рекомендация`}>
        <EmptyState />
      </Card>
    )
  }
  const rows = [...data].sort((a, b) => b.net_profit_total - a.net_profit_total)
  return (
    <Card title="NET маржа (реальные выплаты)" subtitle={`Цель: ${MARGIN_TARGET_PCT}% · ✓ норма · % → цена₽ = рекомендация · нажми на цену чтобы применить`}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-700">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">WB шт</th>
              <th className="text-right pb-2 font-medium">WB маржа</th>
              <th className="text-right pb-2 font-medium">Ozon шт</th>
              <th className="text-right pb-2 font-medium">Ozon маржа</th>
              <th className="text-right pb-2 font-medium">Прибыль</th>
              <th className="text-right pb-2 font-medium">Итого %</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const keyWb = `${i}-wb`
              const keyOzon = `${i}-ozon`
              return (
                <tr key={i} className="border-b border-gray-100 dark:border-gray-700">
                  <td className="py-1.5 pr-2 font-medium">{r.product_name}</td>
                  <td className="text-right py-1.5">{r.qty_wb || '—'}</td>
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
                    })}
                  />
                  <td className="text-right py-1.5">{r.qty_ozon || '—'}</td>
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
                    })}
                  />
                  <td className="text-right py-1.5 font-medium">{fmt(r.net_profit_total)} ₽</td>
                  <td className={`text-right py-1.5 font-bold ${marginColor(r.net_margin_pct_total)}`}>
                    {r.net_margin_pct_total !== null ? `${r.net_margin_pct_total}%` : '—'}
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
