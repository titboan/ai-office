import { useState } from 'react'
import { createProduct } from '../api'
import Card from '../components/Card'

type Status = 'idle' | 'saving' | 'ok' | 'error'

const inputClass =
  'w-full rounded-lg border border-gray-200 dark:border-gray-600 bg-transparent px-2.5 py-1.5 text-xs ' +
  'focus:outline-none focus:border-purple-500'

// Заменяет /map и текстовую часть Redis-wizard /add у Макса — одна форма со всеми полями
// сразу вместо key=value синтаксиса командной строки и пошагового мастера.
export default function ProductForm() {
  const [name, setName] = useState('')
  const [wbArticle, setWbArticle] = useState('')
  const [ozonOfferId, setOzonOfferId] = useState('')
  const [category, setCategory] = useState('')
  const [status, setStatus] = useState<Status>('idle')

  const canSubmit = name.trim().length > 0 && status !== 'saving'

  const submit = async () => {
    if (!canSubmit) return
    setStatus('saving')
    try {
      const res = await createProduct(name.trim(), wbArticle.trim(), ozonOfferId.trim(), category.trim())
      if (res.ok) {
        setStatus('ok')
        setName(''); setWbArticle(''); setOzonOfferId(''); setCategory('')
        setTimeout(() => setStatus('idle'), 2500)
      } else {
        setStatus('error')
      }
    } catch {
      setStatus('error')
    }
  }

  return (
    <Card title="Добавить / обновить товар" subtitle="Совпадение по названию обновит уже существующий товар">
      <div className="space-y-2">
        <div>
          <label className="text-xs text-gray-500 dark:text-gray-400">Название *</label>
          <input
            type="text" className={inputClass} value={name}
            onChange={e => setName(e.target.value)} placeholder="например КБ50"
          />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-xs text-gray-500 dark:text-gray-400">Артикул WB</label>
            <input
              type="text" className={inputClass} value={wbArticle}
              onChange={e => setWbArticle(e.target.value)} placeholder="необязательно"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 dark:text-gray-400">Артикул Ozon (offer_id)</label>
            <input
              type="text" className={inputClass} value={ozonOfferId}
              onChange={e => setOzonOfferId(e.target.value)} placeholder="необязательно"
            />
          </div>
        </div>
        <div>
          <label className="text-xs text-gray-500 dark:text-gray-400">Категория</label>
          <input
            type="text" className={inputClass} value={category}
            onChange={e => setCategory(e.target.value)} placeholder="необязательно"
          />
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-purple-600 text-white disabled:opacity-40"
          >
            {status === 'saving' ? 'Сохраняю…' : 'Сохранить'}
          </button>
          {status === 'ok' && <span className="text-xs text-green-600 font-medium">✓ сохранено</span>}
          {status === 'error' && (
            <span className="text-xs text-red-500" title="Не удалось сохранить, попробуй ещё раз">⚠ ошибка</span>
          )}
        </div>
        <div className="text-[11px] text-gray-400 dark:text-gray-500">
          Оставленные пустыми артикулы WB/Ozon при обновлении существующего товара — очищаются
          (как и в /map у Макса). Себестоимость задаётся отдельно на вкладке «Настройки».
        </div>
      </div>
    </Card>
  )
}
