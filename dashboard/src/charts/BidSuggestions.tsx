import { useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { BidSuggestionRow, applyBid } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import MarketplaceBadge from '../components/MarketplaceBadge'
import { drrColorClass } from '../theme'
import { useMainButtonAction } from '../hooks/useMainButtonAction'

type Pending = {
  key: string; marketplace: 'wb' | 'ozon'; campaignId: string; shopId: string | null
  direction: 'up' | 'down'; deltaPct: number
}
type RowStatus = 'applied' | 'error'

export default function BidSuggestions({ data }: { data: BidSuggestionRow[] }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const [status, setStatus] = useState<Record<string, RowStatus>>({})

  useMainButtonAction(
    pending,
    p => `${p.direction === 'down' ? 'Снизить' : 'Поднять'} ставку на ${p.deltaPct}%`,
    async p => {
      try {
        const res = await applyBid(p.marketplace, p.campaignId, p.direction, p.deltaPct, p.shopId)
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
      <Card title="Ставки рекламы" subtitle="Предложения по ДРР за 7 дней">
        <EmptyState />
      </Card>
    )
  }

  return (
    <Card title="Ставки рекламы" subtitle="Предложения по ДРР за 7 дней · нажми на предложение чтобы применить">
      <div className="space-y-3">
        {data.map((r, i) => {
          const key = `${i}`
          const applied = status[key] === 'applied'
          const failed = status[key] === 'error'
          const selected = pending?.key === key
          const Arrow = r.direction === 'down' ? ArrowDown : ArrowUp
          const canApply = !!r.current_value && !!r.new_value && !applied

          return (
            <div key={key} className="space-y-1 pb-2 border-b border-gray-100 dark:border-gray-700 last:border-0 last:pb-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium truncate flex items-center gap-1">
                  <MarketplaceBadge marketplace={r.marketplace} /> {r.name}
                </span>
                <span className={`text-xs font-semibold whitespace-nowrap ${drrColorClass(r.drr)}`}>
                  ДРР {r.drr.toFixed(0)}%
                </span>
              </div>
              <div className="text-[11px] text-gray-400 dark:text-gray-500">{r.reason}</div>
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-0.5 text-xs font-medium ${r.direction === 'down' ? 'text-red-500' : 'text-green-600'}`}>
                  <Arrow size={12} /> {r.delta_pct}%
                </span>
                {r.current_value != null && r.new_value != null && (
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    {r.current_value.toLocaleString()} ₽ → {r.new_value.toLocaleString()} ₽
                  </span>
                )}
                {applied && <span className="text-xs text-green-600 font-medium">✓ применено</span>}
                {failed && <span className="text-red-500 text-xs" title="Не удалось применить, попробуй ещё раз">⚠</span>}
                {r.market_recommended_cpm != null && (
                  <span
                    className={`text-[11px] whitespace-nowrap ${
                      r.market_flag ? 'text-amber-600 dark:text-amber-400 font-medium' : 'text-gray-400 dark:text-gray-500'
                    }`}
                    title={
                      r.market_flag === 'overspend'
                        ? 'Выше рыночной ставки WB — риск перерасхода бюджета'
                        : r.market_flag === 'underspend'
                        ? 'Ниже рыночной ставки WB — риск проигрыша аукциона'
                        : undefined
                    }
                  >
                    {r.market_flag ? '⚠️ ' : ''}рынок ~{r.market_recommended_cpm.toLocaleString()} ₽
                  </span>
                )}
                {canApply && (
                  <button
                    onClick={() => setPending({
                      key, marketplace: r.marketplace, campaignId: r.campaign_id, shopId: r.shop_id,
                      direction: r.direction, deltaPct: r.delta_pct,
                    })}
                    className={`ml-auto text-xs font-medium underline decoration-dotted ${
                      selected ? 'text-blue-800 dark:text-blue-300' : 'text-blue-600 dark:text-blue-400'
                    }`}
                  >
                    Применить
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}
