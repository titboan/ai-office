import { ChangeEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'
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

const tableInputClass =
  'w-16 text-right bg-transparent border-b border-dashed border-gray-300 dark:border-gray-600 focus:outline-none focus:border-purple-500'
const cardInputClass =
  'w-full text-base bg-transparent border-b border-dashed border-gray-300 dark:border-gray-600 focus:outline-none focus:border-purple-500 py-1'

type CostFieldsProps = {
  field: Field | undefined
  keyName: string
  marketplace: 'wb' | 'ozon'
  productId: string
  save: (key: string, marketplace: 'wb' | 'ozon', productId: string) => void
  updateField: (key: string, part: Partial<Field>) => void
  status: RowStatus | undefined
  compact: boolean
}

// Рендер пары полей (закупка+логистика, упаковка+маркировка) + итого для одной площадки.
// compact=true — три <td> для десктопной таблицы, compact=false — подписанные поля для карточки.
function CostFields({ field, keyName, marketplace, productId, save, updateField, status, compact }: CostFieldsProps) {
  const commonProps = (part: keyof Field) => ({
    type: 'number' as const,
    min: '0',
    value: field?.[part] ?? '',
    onChange: (e: ChangeEvent<HTMLInputElement>) => updateField(keyName, { [part]: e.target.value }),
    onBlur: () => save(keyName, marketplace, productId),
    onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur() },
  })

  const totalNode = (
    <>
      {sumLabel(field?.purchaseLogistics ?? '', field?.packagingMarking ?? '')}
      {status === 'applied' && <span className="text-green-600 ml-1">✓</span>}
      {status === 'error' && (
        <span className="text-red-500 ml-1" title="Не удалось сохранить, попробуй ещё раз">⚠</span>
      )}
    </>
  )

  if (compact) {
    return (
      <>
        <td className="text-right py-1.5">
          <input className={tableInputClass} {...commonProps('purchaseLogistics')} />
        </td>
        <td className="text-right py-1.5">
          <input className={tableInputClass} {...commonProps('packagingMarking')} />
        </td>
        <td className="text-right py-1.5 font-medium">{totalNode}</td>
      </>
    )
  }

  return (
    <div>
      <label className="text-xs text-gray-500 dark:text-gray-400">Закупка+логистика</label>
      <input className={cardInputClass} {...commonProps('purchaseLogistics')} />
      <label className="text-xs text-gray-500 dark:text-gray-400 mt-2 block">Упаковка+маркировка</label>
      <input className={cardInputClass} {...commonProps('packagingMarking')} />
      <div className="text-sm font-medium mt-2">Итого: {totalNode}</div>
    </div>
  )
}

export default function CostEditor() {
  const [rows, setRows] = useState<CostRow[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [fields, setFields] = useState<Record<string, Field>>({})
  const [status, setStatus] = useState<Record<string, RowStatus>>({})
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const statusTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  // Мобильная клавиатура (Telegram Mini App) не закрывается сама при скролле —
  // снимаем фокус вручную, это триггерит существующий onBlur/save.
  useEffect(() => {
    const handleScroll = () => {
      (document.activeElement as HTMLElement)?.blur()
    }
    window.addEventListener('scroll', handleScroll, { capture: true, passive: true })
    return () => window.removeEventListener('scroll', handleScroll, true)
  }, [])

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
      scheduleStatusClear(key, res.ok)
    } catch {
      setStatus(s => ({ ...s, [key]: 'error' }))
      scheduleStatusClear(key, false)
    }
  }

  // 'applied' исчезает через ~2с, чтобы не залипал навсегда; 'error' остаётся до следующей попытки.
  const scheduleStatusClear = (key: string, applied: boolean) => {
    if (statusTimers.current[key]) clearTimeout(statusTimers.current[key])
    if (!applied) return
    statusTimers.current[key] = setTimeout(() => {
      setStatus(s => {
        if (s[key] !== 'applied') return s
        const next = { ...s }
        delete next[key]
        return next
      })
    }, 2000)
  }

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
      <div className="hidden sm:block overflow-x-auto">
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
              return (
                <tr key={r.mapping_id} className="border-b border-gray-100 dark:border-gray-700">
                  <td className="py-1.5 pr-2 font-medium">{r.display_name}</td>
                  {r.wb_article ? (
                    <CostFields
                      field={fields[keyWb]} keyName={keyWb} marketplace="wb" productId={r.wb_article}
                      save={save} updateField={updateField} status={status[keyWb]} compact
                    />
                  ) : (
                    <>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                      <td className="text-right py-1.5 text-gray-400 dark:text-gray-500">—</td>
                    </>
                  )}
                  {r.ozon_offer_id ? (
                    <CostFields
                      field={fields[keyOzon]} keyName={keyOzon} marketplace="ozon" productId={r.ozon_offer_id}
                      save={save} updateField={updateField} status={status[keyOzon]} compact
                    />
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
      <div className="sm:hidden divide-y divide-gray-100 dark:divide-gray-700">
        {rows.map(r => {
          const keyWb = `${r.mapping_id}-wb`
          const keyOzon = `${r.mapping_id}-ozon`
          const isOpen = expanded.has(r.mapping_id)
          const toggle = () => setExpanded(s => {
            const next = new Set(s)
            if (next.has(r.mapping_id)) next.delete(r.mapping_id)
            else next.add(r.mapping_id)
            return next
          })
          const wbTotal = sumLabel(fields[keyWb]?.purchaseLogistics ?? '', fields[keyWb]?.packagingMarking ?? '')
          const ozTotal = sumLabel(fields[keyOzon]?.purchaseLogistics ?? '', fields[keyOzon]?.packagingMarking ?? '')
          return (
            <div key={r.mapping_id}>
              <button
                type="button"
                onClick={toggle}
                className="w-full flex items-center justify-between py-2.5 text-left"
              >
                <span className="font-medium text-sm truncate pr-2">{r.display_name}</span>
                <span className="flex items-center gap-2 shrink-0">
                  {r.wb_article && (
                    <span className="text-xs text-gray-500 dark:text-gray-400">WB {wbTotal}</span>
                  )}
                  {r.ozon_offer_id && (
                    <span className="text-xs text-gray-500 dark:text-gray-400">Oz {ozTotal}</span>
                  )}
                  <span className="text-gray-400 dark:text-gray-500 text-xs">{isOpen ? '▲' : '▼'}</span>
                </span>
              </button>
              {isOpen && (
                <div className="pb-3 space-y-3">
                  {r.wb_article && (
                    <div>
                      <div className="text-xs text-gray-400 uppercase mb-1">WB</div>
                      <CostFields
                        field={fields[keyWb]} keyName={keyWb} marketplace="wb" productId={r.wb_article}
                        save={save} updateField={updateField} status={status[keyWb]} compact={false}
                      />
                    </div>
                  )}
                  {r.ozon_offer_id && (
                    <div>
                      <div className="text-xs text-gray-400 uppercase mb-1">Ozon</div>
                      <CostFields
                        field={fields[keyOzon]} keyName={keyOzon} marketplace="ozon" productId={r.ozon_offer_id}
                        save={save} updateField={updateField} status={status[keyOzon]} compact={false}
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}
