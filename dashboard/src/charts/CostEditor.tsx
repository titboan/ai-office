import { useEffect, useState } from 'react'
import { CostRow, getCosts, setCost } from '../api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'

type Field = { purchaseLogistics: string; packagingMarking: string }
type RowStatus = 'applied' | 'error'

const numOrEmpty = (v: number | null) => (v === null ? '' : String(v))

// Если обе статьи пустые/невалидные — показываем "—", а не NaN.
function sumLabel(a: string, b: string): string {
  const pa = parseFloat(a)
  const pb = parseFloat(b)
  if (isNaN(pa) && isNaN(pb)) return '—'
  const total = (isNaN(pa) ? 0 : pa) + (isNaN(pb) ? 0 : pb)
  return `${total.toLocaleString()} ₽`
}

const CARD_TITLE = 'Себестоимость'
const CARD_SUBTITLE = 'Закупка+логистика и упаковка+маркировка по каждой площадке · Итого = сумма статей'

export default function CostEditor() {
  const [rows, setRows] = useState<CostRow[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [fields, setFields] = useState<Record<string, Field>>({})
  const [status, setStatus] = useState<Record<string, RowStatus>>({})

  useEffect(() => {
    getCosts()
      .then(data => {
        setRows(data)
        const init: Record<string, Field> = {}
        for (const r of data) {
          if (r.wb_article) {
            init[`${r.mapping_id}-wb`] = {
              purchaseLogistics: numOrEmpty(r.purchase_logistics_wb),
              packagingMarking: numOrEmpty(r.packaging_marking_wb),
            }
          }
          if (r.ozon_offer_id) {
            init[`${r.mapping_id}-ozon`] = {
              purchaseLogistics: numOrEmpty(r.purchase_logistics_ozon),
              packagingMarking: numOrEmpty(r.packaging_marking_ozon),
            }
          }
        }
        setFields(init)
      })
      .catch(() => setLoadError(true))
  }, [])

  const updateField = (key: string, part: Partial<Field>) => {
    setFields(f => ({ ...f, [key]: { ...f[key], ...part } }))
  }

  // Пустая статья расходов = 0, не блокирует сохранение. Невалидное число (NaN) — игнорируем запрос.
  const save = async (key: string, marketplace: 'wb' | 'ozon', productId: string) => {
    const field = fields[key]
    if (!field) return
    const pl = field.purchaseLogistics.trim() === '' ? 0 : parseFloat(field.purchaseLogistics)
    const pm = field.packagingMarking.trim() === '' ? 0 : parseFloat(field.packagingMarking)
    if (isNaN(pl) || isNaN(pm)) return
    try {
      const res = await setCost(marketplace, productId, pl, pm)
      setStatus(s => ({ ...s, [key]: res.ok ? 'applied' : 'error' }))
    } catch {
      setStatus(s => ({ ...s, [key]: 'error' }))
    }
  }

  const inputClass =
    'w-16 text-right bg-transparent border-b border-dashed border-gray-300 dark:border-gray-600 focus:outline-none focus:border-purple-500'

  if (rows === null) {
    return (
      <Card title={CARD_TITLE} subtitle={CARD_SUBTITLE}>
        <EmptyState message={loadError ? 'Не удалось загрузить' : 'Загрузка…'} />
      </Card>
    )
  }

  if (!rows.length) {
    return (
      <Card title={CARD_TITLE} subtitle={CARD_SUBTITLE}>
        <EmptyState />
      </Card>
    )
  }

  return (
    <Card title={CARD_TITLE} subtitle={CARD_SUBTITLE}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400 dark:text-gray-500 border-b dark:border-gray-700">
              <th className="text-left pb-2 font-medium">Товар</th>
              <th className="text-right pb-2 font-medium">WB закупка+лог</th>
              <th className="text-right pb-2 font-medium">WB упак+марк</th>
              <th className="text-right pb-2 font-medium">WB итого</th>
              <th className="text-right pb-2 font-medium">Ozon закупка+лог</th>
              <th className="text-right pb-2 font-medium">Ozon упак+марк</th>
              <th className="text-right pb-2 font-medium">Ozon итого</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => {
              const keyWb = `${r.mapping_id}-wb`
              const keyOzon = `${r.mapping_id}-ozon`
              const fWb = fields[keyWb]
              const fOzon = fields[keyOzon]
              return (
                <tr key={r.mapping_id} className="border-b border-gray-100 dark:border-gray-700">
                  <td className="py-1.5 pr-2 font-medium">{r.display_name}</td>
                  {r.wb_article ? (
                    <>
                      <td className="text-right py-1.5">
                        <input
                          type="number" min="0" className={inputClass}
                          value={fWb?.purchaseLogistics ?? ''}
                          onChange={e => updateField(keyWb, { purchaseLogistics: e.target.value })}
                          onBlur={() => save(keyWb, 'wb', r.wb_article!)}
                        />
                      </td>
                      <td className="text-right py-1.5">
                        <input
                          type="number" min="0" className={inputClass}
                          value={fWb?.packagingMarking ?? ''}
                          onChange={e => updateField(keyWb, { packagingMarking: e.target.value })}
                          onBlur={() => save(keyWb, 'wb', r.wb_article!)}
                        />
                      </td>
                      <td className="text-right py-1.5 font-medium">
                        {sumLabel(fWb?.purchaseLogistics ?? '', fWb?.packagingMarking ?? '')}
                        {status[keyWb] === 'applied' && <span className="text-green-600 ml-1">✓</span>}
                        {status[keyWb] === 'error' && (
                          <span className="text-red-500 ml-1" title="Не удалось сохранить, попробуй ещё раз">⚠</span>
                        )}
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                    </>
                  )}
                  {r.ozon_offer_id ? (
                    <>
                      <td className="text-right py-1.5">
                        <input
                          type="number" min="0" className={inputClass}
                          value={fOzon?.purchaseLogistics ?? ''}
                          onChange={e => updateField(keyOzon, { purchaseLogistics: e.target.value })}
                          onBlur={() => save(keyOzon, 'ozon', r.ozon_offer_id!)}
                        />
                      </td>
                      <td className="text-right py-1.5">
                        <input
                          type="number" min="0" className={inputClass}
                          value={fOzon?.packagingMarking ?? ''}
                          onChange={e => updateField(keyOzon, { packagingMarking: e.target.value })}
                          onBlur={() => save(keyOzon, 'ozon', r.ozon_offer_id!)}
                        />
                      </td>
                      <td className="text-right py-1.5 font-medium">
                        {sumLabel(fOzon?.purchaseLogistics ?? '', fOzon?.packagingMarking ?? '')}
                        {status[keyOzon] === 'applied' && <span className="text-green-600 ml-1">✓</span>}
                        {status[keyOzon] === 'error' && (
                          <span className="text-red-500 ml-1" title="Не удалось сохранить, попробуй ещё раз">⚠</span>
                        )}
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                    </>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
