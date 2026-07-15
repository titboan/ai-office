import { useState } from 'react'
import { addShop } from '../api'
import Card from '../components/Card'

type Status = 'idle' | 'saving' | 'ok' | 'error'

const inputClass =
  'w-full rounded-lg border border-gray-200 dark:border-gray-600 bg-transparent px-2.5 py-1.5 text-xs ' +
  'focus:outline-none focus:border-purple-500'

// Заменяет /add_shop у Макса. Токен продавца — чувствительное поле: type="password",
// никогда не попадает в console.log, уходит только в JSON body POST-запроса (не в URL).
export default function AddShopForm() {
  const [marketplace, setMarketplace] = useState<'wb' | 'ozon'>('wb')
  const [apiToken, setApiToken] = useState('')
  const [clientId, setClientId] = useState('')
  const [shopName, setShopName] = useState('')
  const [status, setStatus] = useState<Status>('idle')

  const canSubmit =
    apiToken.trim().length > 0 &&
    (marketplace === 'wb' || clientId.trim().length > 0) &&
    status !== 'saving'

  const submit = async () => {
    if (!canSubmit) return
    setStatus('saving')
    try {
      const res = await addShop(marketplace, apiToken.trim(), clientId.trim(), shopName.trim())
      if (res.ok) {
        setStatus('ok')
        setApiToken(''); setClientId(''); setShopName('')
        setTimeout(() => setStatus('idle'), 2500)
      } else {
        setStatus('error')
      }
    } catch {
      setStatus('error')
    }
  }

  return (
    <Card title="Подключить магазин" subtitle="API-токен продавца — храним только на сервере, не показываем на экране">
      <div className="space-y-2">
        <div>
          <label className="text-xs text-gray-500 dark:text-gray-400">Площадка</label>
          <select
            className={inputClass}
            value={marketplace}
            onChange={e => setMarketplace(e.target.value as 'wb' | 'ozon')}
          >
            <option value="wb">Wildberries</option>
            <option value="ozon">Ozon</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-gray-500 dark:text-gray-400">API-токен *</label>
          <input
            type="password" autoComplete="off" className={inputClass} value={apiToken}
            onChange={e => setApiToken(e.target.value)} placeholder="токен из личного кабинета"
          />
        </div>
        {marketplace === 'ozon' && (
          <div>
            <label className="text-xs text-gray-500 dark:text-gray-400">Client ID *</label>
            <input
              type="text" className={inputClass} value={clientId}
              onChange={e => setClientId(e.target.value)} placeholder="из личного кабинета Ozon"
            />
          </div>
        )}
        <div>
          <label className="text-xs text-gray-500 dark:text-gray-400">Название магазина</label>
          <input
            type="text" className={inputClass} value={shopName}
            onChange={e => setShopName(e.target.value)} placeholder="необязательно"
          />
        </div>

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-purple-600 text-white disabled:opacity-40"
          >
            {status === 'saving' ? 'Подключаю…' : 'Подключить'}
          </button>
          {status === 'ok' && <span className="text-xs text-green-600 font-medium">✓ подключено</span>}
          {status === 'error' && (
            <span className="text-xs text-red-500" title="Не удалось подключить, попробуй ещё раз">⚠ ошибка</span>
          )}
        </div>
      </div>
    </Card>
  )
}
