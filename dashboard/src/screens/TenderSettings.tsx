import { useEffect, useState } from 'react'
import { fetchTenderSettings, saveTenderSettings, TenderSettings as TenderSettingsData } from '../api'

const MAX_KEYWORDS = 10

function parseKeywords(text: string): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const line of text.split('\n')) {
    const kw = line.trim()
    if (!kw || kw.length > 60) continue
    const key = kw.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    result.push(kw)
  }
  return result
}

export default function TenderSettings() {
  const [keywordsText, setKeywordsText] = useState('')
  const [minNmck, setMinNmck] = useState('')
  const [maxNmck, setMaxNmck] = useState('')
  const [regionCode, setRegionCode] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [status, setStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    fetchTenderSettings()
      .then((s) => {
        setKeywordsText(s.keywords.join('\n'))
        setMinNmck(String(s.min_nmck))
        setMaxNmck(String(s.max_nmck))
        setRegionCode(s.region_code)
      })
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const keywords = parseKeywords(keywordsText)
  const minVal = Number(minNmck)
  const maxVal = Number(maxNmck)

  const clientErrors: string[] = []
  if (keywords.length === 0) clientErrors.push('Нужно хотя бы одно ключевое слово')
  if (keywords.length > MAX_KEYWORDS) clientErrors.push(`Максимум ${MAX_KEYWORDS} ключевых слов`)
  if (!Number.isFinite(minVal) || minVal < 0) clientErrors.push('Мин. НМЦК должен быть числом ≥ 0')
  if (!Number.isFinite(maxVal) || maxVal <= minVal) clientErrors.push('Макс. НМЦК должен быть больше минимального')
  if (!regionCode.trim()) clientErrors.push('Укажи код региона (ОКТМО)')

  async function handleSave() {
    if (clientErrors.length > 0) return
    setStatus('saving')
    setSaveError(null)
    const payload: TenderSettingsData = {
      keywords,
      min_nmck: Math.round(minVal),
      max_nmck: Math.round(maxVal),
      region_code: regionCode.trim(),
    }
    try {
      await saveTenderSettings(payload)
      setStatus('success')
      setTimeout(() => setStatus('idle'), 2000)
    } catch (e: any) {
      setStatus('error')
      setSaveError(e.message)
    }
  }

  return (
    <div className="min-h-screen p-3 space-y-3" style={{ background: 'var(--tg-theme-bg-color, #f5f5f5)' }}>
      <h1 className="text-base font-bold">⚙️ Настройки поиска тендеров</h1>

      {loading && (
        <div className="text-center py-12 text-gray-400 dark:text-gray-500 text-sm">Загружаю настройки…</div>
      )}

      {loadError && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-xl p-4 text-sm">
          ❌ {loadError}
        </div>
      )}

      {!loading && !loadError && (
        <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm space-y-4">
          <div>
            <label className="text-xs font-semibold text-gray-500 dark:text-gray-400">
              Ключевые слова (направления) — по одному на строку, до {MAX_KEYWORDS}
            </label>
            <textarea
              className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2 text-sm"
              rows={6}
              value={keywordsText}
              onChange={(e) => setKeywordsText(e.target.value)}
              placeholder={'матрасы\nпостельное белье\nмебель'}
            />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-semibold text-gray-500 dark:text-gray-400">НМЦК от, ₽</label>
              <input
                type="number"
                className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2 text-sm"
                value={minNmck}
                onChange={(e) => setMinNmck(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-gray-500 dark:text-gray-400">НМЦК до, ₽</label>
              <input
                type="number"
                className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2 text-sm"
                value={maxNmck}
                onChange={(e) => setMaxNmck(e.target.value)}
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-gray-500 dark:text-gray-400">Регион (код ОКТМО)</label>
            <input
              type="text"
              className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2 text-sm"
              value={regionCode}
              onChange={(e) => setRegionCode(e.target.value)}
              placeholder="23 — Краснодарский край"
            />
          </div>

          {clientErrors.length > 0 && (
            <div className="text-xs text-yellow-600 dark:text-yellow-400 space-y-0.5">
              {clientErrors.map((e) => <div key={e}>⚠️ {e}</div>)}
            </div>
          )}

          {status === 'error' && saveError && (
            <div className="text-xs text-red-600 dark:text-red-400">❌ {saveError}</div>
          )}

          <button
            onClick={handleSave}
            disabled={clientErrors.length > 0 || status === 'saving'}
            className={`w-full py-2 rounded-lg text-sm font-medium transition-colors ${
              status === 'success'
                ? 'bg-green-600 text-white'
                : clientErrors.length > 0 || status === 'saving'
                ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 dark:text-gray-500'
                : 'bg-purple-600 text-white'
            }`}
          >
            {status === 'saving' ? 'Сохраняю…' : status === 'success' ? '✅ Сохранено' : 'Сохранить'}
          </button>
        </div>
      )}
    </div>
  )
}
