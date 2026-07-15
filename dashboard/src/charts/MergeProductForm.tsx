import { useState } from 'react'
import { CatalogProductRow, mergeProduct } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'

type Status = 'idle' | 'saving' | 'ok' | 'not_found' | 'error'

// Заменяет /merge_products (inline-пикер mergewiz:* у Макса) — тот же финальный шаг:
// связать WB-товар без Ozon-пары с уже существующей в реестре Ozon-only строкой.
export default function MergeProductForm({ data }: { data: CatalogProductRow[] }) {
  const candidates = data.filter(p => p.wb_article && !p.ozon_offer_id)
  const [wbArticle, setWbArticle] = useState('')
  const [ozonOfferId, setOzonOfferId] = useState('')
  const [status, setStatus] = useState<Status>('idle')

  const canSubmit = wbArticle.trim().length > 0 && ozonOfferId.trim().length > 0 && status !== 'saving'

  const submit = async () => {
    if (!canSubmit) return
    setStatus('saving')
    try {
      const res = await mergeProduct(wbArticle, ozonOfferId.trim())
      if (res.ok) {
        setStatus('ok')
        setWbArticle(''); setOzonOfferId('')
        setTimeout(() => setStatus('idle'), 2500)
      } else if (res.error === 'not_found') {
        setStatus('not_found')
      } else {
        setStatus('error')
      }
    } catch {
      setStatus('error')
    }
  }

  if (!candidates.length) {
    return (
      <Card title="Объединить товары" subtitle="WB-товар без пары с Ozon-товаром">
        <EmptyState message="Нет WB-товаров без Ozon-пары — объединять нечего" />
      </Card>
    )
  }

  return (
    <Card title="Объединить товары" subtitle="WB-товар без пары с уже существующим в каталоге Ozon-товаром">
      <div className="space-y-2">
        <div>
          <label className="text-xs text-gray-500 dark:text-gray-400">Товар WB (без Ozon-пары)</label>
          <select
            className="w-full rounded-lg border border-gray-200 dark:border-gray-600 bg-transparent px-2.5 py-1.5 text-xs focus:outline-none focus:border-purple-500"
            value={wbArticle}
            onChange={e => setWbArticle(e.target.value)}
          >
            <option value="">— выбери товар —</option>
            {candidates.map(p => (
              <option key={p.wb_article} value={p.wb_article!}>{p.name} ({p.wb_article})</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-gray-500 dark:text-gray-400">Артикул Ozon (offer_id) — уже в каталоге</label>
          <input
            type="text"
            className="w-full rounded-lg border border-gray-200 dark:border-gray-600 bg-transparent px-2.5 py-1.5 text-xs focus:outline-none focus:border-purple-500"
            value={ozonOfferId}
            onChange={e => setOzonOfferId(e.target.value)}
            placeholder="например КБ50"
          />
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-purple-600 text-white disabled:opacity-40"
          >
            {status === 'saving' ? 'Объединяю…' : 'Объединить'}
          </button>
          {status === 'ok' && <span className="text-xs text-green-600 font-medium">✓ объединено</span>}
          {status === 'not_found' && (
            <span className="text-xs text-red-500">
              Товар Ozon с таким offer_id не найден в каталоге без WB-пары
            </span>
          )}
          {status === 'error' && (
            <span className="text-xs text-red-500" title="Не удалось объединить, попробуй ещё раз">⚠ ошибка</span>
          )}
        </div>
        <div className="text-[11px] text-gray-400 dark:text-gray-500">
          Ozon-товар должен уже быть в реестре (например, попал туда синком заказов) — форма
          связывает две существующие строки, а не создаёт новую. Чтобы добавить новый
          Ozon-товар — используй форму «Добавить / обновить товар» выше.
        </div>
      </div>
    </Card>
  )
}
