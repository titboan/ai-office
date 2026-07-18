import { useEffect, useState, useMemo, useRef } from 'react'
import { LayoutDashboard, FileBarChart, Package, Settings, AlertCircle, Sun, Moon, RefreshCw } from 'lucide-react'
import { fetchDashboard, fetchTimeline, DashboardData, DayRevenue, TimelineData } from './api'
import RevenueChart from './charts/RevenueChart'
import TopProducts from './charts/TopProducts'
import DrrGauge from './charts/DrrGauge'
import CtrRoas from './charts/CtrRoas'
import StockTable from './charts/StockTable'
import WowTrend from './charts/WowTrend'
import MarginChart from './charts/MarginChart'
import NetMarginTable from './charts/NetMarginTable'
import CostEditor from './charts/CostEditor'
import BidSuggestions from './charts/BidSuggestions'
import FunnelChart from './charts/FunnelChart'
import ReturnsTable from './charts/ReturnsTable'
import MomChart from './charts/MomChart'
import AbcTable from './charts/AbcTable'
import ChainTimeline from './charts/ChainTimeline'
import KwTable from './charts/KwTable'
import SupplyPlan from './charts/SupplyPlan'
import ProductsTable from './charts/ProductsTable'
import ShopKpiCard from './charts/ShopKpiCard'
import ProductForm from './charts/ProductForm'
import MergeProductForm from './charts/MergeProductForm'
import AddShopForm from './charts/AddShopForm'
import CardSkeleton from './components/CardSkeleton'
import AlertBanner, { collectAlerts } from './components/AlertBanner'

type Days = 7 | 14 | 30
type Theme = 'light' | 'dark'
type Tab = 'dashboard' | 'reports' | 'catalog' | 'settings'
type MpFilter = 'all' | 'wb' | 'ozon'

interface KpiCardData {
  label: string
  value: string
  color: string
  delta?: string | null
  deltaPositive?: boolean
}

const TABS: { key: Tab; label: string; icon: typeof LayoutDashboard }[] = [
  { key: 'dashboard', label: 'Дашборд', icon: LayoutDashboard },
  { key: 'reports', label: 'Отчёты', icon: FileBarChart },
  { key: 'catalog', label: 'Каталог', icon: Package },
  { key: 'settings', label: 'Настройки', icon: Settings },
]

const THEME_STORAGE_KEY = 'dashboard-theme'
const CACHE_PREFIX = 'dashboard-v1-'
const AUTO_REFRESH_MS = 10 * 60 * 1000   // 10 минут
const VISIBILITY_THROTTLE_MS = 5 * 60 * 1000  // минимум 5 мин между авто-обновлениями

// Порядок: сохранённый вручную выбор → тема Telegram → системная тема браузера.
function getInitialTheme(): Theme {
  const saved = localStorage.getItem(THEME_STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') return saved
  const tg = (window as any).Telegram?.WebApp
  if (tg?.colorScheme === 'dark') return 'dark'
  if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) return 'dark'
  return 'light'
}

// Позволяет кнопкам в чате открывать мини-апп сразу на нужной вкладке (?tab=reports и т.п.)
function getInitialTab(): Tab {
  const t = new URLSearchParams(window.location.search).get('tab')
  return t === 'reports' || t === 'catalog' || t === 'settings' ? t : 'dashboard'
}

function loadCachedData(days: Days): DashboardData | null {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + days)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function saveCachedData(days: Days, d: DashboardData) {
  try {
    localStorage.setItem(CACHE_PREFIX + days, JSON.stringify(d))
  } catch {}
}

function zeroDayRevenue(rows: DayRevenue[], mp: 'wb' | 'ozon'): DayRevenue[] {
  return rows.map(r => ({ ...r, wb: mp === 'wb' ? r.wb : 0, ozon: mp === 'ozon' ? r.ozon : 0 }))
}

function filterDataByMp(data: DashboardData, mp: 'wb' | 'ozon'): DashboardData {
  return {
    ...data,
    revenue: data.revenue.filter(r => r.marketplace === mp),
    top_products: data.top_products.filter(r => r.marketplace === mp),
    adv: data.adv.filter(r => r.marketplace === mp),
    low_stocks: data.low_stocks.filter(r => r.marketplace === mp),
    trend: data.trend.filter(r => r.marketplace === mp),
    product_metrics: data.product_metrics.filter(r => r.marketplace === mp),
    stock_velocity: data.stock_velocity.filter(r => r.marketplace === mp),
    funnel: (data.funnel ?? []).filter(r => r.marketplace === mp),
    bid_suggestions: (data.bid_suggestions ?? []).filter(r => r.marketplace === mp),
    supply_plan: {
      ...data.supply_plan,
      products: (data.supply_plan?.products ?? []).filter(r => r.marketplace === mp),
    },
    revenue_by_day: zeroDayRevenue(data.revenue_by_day, mp),
    orders_by_day: zeroDayRevenue(data.orders_by_day ?? [], mp),
    sales_by_day: zeroDayRevenue(data.sales_by_day ?? [], mp),
  }
}

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState<Days>(14)
  const [loading, setLoading] = useState(true)      // true только пока нет никаких данных (нет кеша)
  const [isFetchingFresh, setIsFetchingFresh] = useState(false)  // фоновое обновление
  const [isStale, setIsStale] = useState(false)     // показаны кешированные данные
  const [timeline, setTimeline] = useState<TimelineData | null>(null)
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const [tab, setTab] = useState<Tab>(getInitialTab)
  const [mpFilter, setMpFilter] = useState<MpFilter>('all')
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const lastRefreshAt = useRef(Date.now())

  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp
    tg?.ready()
    tg?.expand()
    // Telegram по умолчанию перехватывает вертикальные свайпы у верха экрана под жест
    // «свайп вниз — закрыть мини-апп» — из-за этого шапка/вкладки ощущаются прилипшими,
    // пока скролл не наберёт momentum. Отключаем перехват, чтобы скролл был обычным на
    // всей высоте страницы (Bot API 7.7+, старые клиенты просто не имеют этого метода).
    tg?.disableVerticalSwipes?.()
  }, [])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

  // Auto-refresh: каждые 10 минут пока открыт, плюс при возврате из фона (через 5+ мин)
  useEffect(() => {
    const interval = setInterval(() => setRefreshKey(k => k + 1), AUTO_REFRESH_MS)

    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      if (Date.now() - lastRefreshAt.current > VISIBILITY_THROTTLE_MS) {
        setRefreshKey(k => k + 1)
      }
    }
    document.addEventListener('visibilitychange', onVisible)

    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  useEffect(() => {
    fetchTimeline().then(setTimeline).catch(() => {})
  }, [refreshKey])

  // Stale-while-revalidate: сначала показываем кеш (если есть), потом тихо подгружаем свежие данные
  useEffect(() => {
    lastRefreshAt.current = Date.now()

    const cached = loadCachedData(days)
    if (cached) {
      setData(cached)
      setLoading(false)
      setIsStale(true)
      setError(null)
    } else {
      setLoading(true)
      setIsStale(false)
      setData(null)
      setError(null)
    }

    setIsFetchingFresh(true)
    fetchDashboard(days)
      .then(d => {
        setData(d)
        setUpdatedAt(new Date())
        setIsStale(false)
        setLoading(false)
        saveCachedData(days, d)
      })
      .catch(e => {
        if (!cached) setError(e.message)
        setLoading(false)
      })
      .finally(() => setIsFetchingFresh(false))
  }, [days, refreshKey])

  const displayData = useMemo(
    () => data && mpFilter !== 'all' ? filterDataByMp(data, mpFilter) : data,
    [data, mpFilter]
  )

  const totalRevenue = displayData?.revenue.reduce((s, r) => s + r.revenue, 0) ?? 0
  const totalOrders = displayData?.revenue.reduce((s, r) => s + r.orders, 0) ?? 0
  const avgCheck = totalOrders > 0 ? Math.round(totalRevenue / totalOrders) : 0
  const totalSpend = displayData?.adv.reduce((s, r) => s + r.spend, 0) ?? 0
  const drr = totalRevenue > 0 ? (totalSpend / totalRevenue * 100).toFixed(1) : '—'

  const wowTotal = displayData?.trend.reduce(
    (acc, r) => ({ cur: acc.cur + r.week_current, prev: acc.prev + r.week_prev }),
    { cur: 0, prev: 0 }
  )
  const wowPct = wowTotal && wowTotal.prev > 0
    ? ((wowTotal.cur - wowTotal.prev) / wowTotal.prev * 100)
    : null

  const kpiCards: KpiCardData[] = displayData ? [
    {
      label: 'Выручка',
      value: `${(totalRevenue / 1000).toFixed(0)}к ₽`,
      color: '',
      delta: wowPct !== null ? `${wowPct >= 0 ? '+' : ''}${wowPct.toFixed(1)}%` : null,
      deltaPositive: wowPct !== null ? wowPct >= 0 : undefined,
    },
    { label: 'Заказов', value: totalOrders.toLocaleString(), color: '' },
    { label: 'Ср. чек', value: `${(avgCheck / 1000).toFixed(1)}к ₽`, color: '' },
    { label: 'Реклама', value: `${(totalSpend / 1000).toFixed(0)}к ₽`, color: '' },
    { label: 'ДРР', value: `${drr}%`, color: '' },
    {
      label: 'WoW',
      value: wowPct !== null
        ? `${wowPct >= 0 ? '↑' : '↓'}${Math.abs(wowPct).toFixed(1)}%`
        : '—',
      color: wowPct === null ? '' : wowPct >= 0 ? 'text-green-600' : 'text-red-500',
    },
  ] : []

  // Бейджи на вкладках — считаем по сырым данным, не по фильтру МП
  const criticalAlertCount = data ? collectAlerts(data).filter(a => a.level === 'critical').length : 0
  const reportsBadgeCount = data?.kw_top?.filter(r => r.priority).length ?? 0

  const activeTabInfo = TABS.find(t => t.key === tab) ?? TABS[0]
  const ActiveTabIcon = activeTabInfo.icon
  const updatedAtStr = updatedAt
    ? updatedAt.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })
    : null
  const showFilters = tab === 'dashboard' || tab === 'reports'

  return (
    <div
      className="min-h-screen px-3 pb-3 space-y-3 md:max-w-3xl lg:max-w-5xl md:mx-auto"
      style={{ background: 'var(--tg-theme-bg-color, #f5f5f5)' }}
    >
      {/* Шапка + фильтры + таб-бар — прилипают к верху при скролле (position: sticky).
          Собственный фон (не только у родителя) и -mx-3/px-3, чтобы при скролле
          под ними не просвечивал контент по бокам — родитель больше не даёт
          верхний отступ (был перенесён сюда), чтобы разница между "прилипшим" и
          обычным состоянием не создавала лишний зазор. */}
      <div
        className="sticky top-0 z-20 -mx-3 px-3 pt-3 pb-2 space-y-3 border-b border-gray-200 dark:border-gray-700"
        style={{ background: 'var(--tg-theme-bg-color, #f5f5f5)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-base font-bold flex items-center gap-1.5">
            <ActiveTabIcon size={18} /> {activeTabInfo.label}
          </h1>
          <div className="flex gap-1 items-center">
            <button
              onClick={() => setRefreshKey(k => k + 1)}
              disabled={isFetchingFresh}
              aria-label="Обновить данные"
              className="p-1.5 rounded bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-40"
            >
              <RefreshCw size={14} className={isFetchingFresh ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
              aria-label="Переключить тему"
              className="p-1.5 rounded bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300"
            >
              {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
            </button>
          </div>
        </div>

        {/* Фильтры: маркетплейс + период */}
        {showFilters && (
          <div className="flex items-center justify-between gap-2">
            <div className="flex gap-1">
              {(['all', 'wb', 'ozon'] as MpFilter[]).map(mp => (
                <button
                  key={mp}
                  onClick={() => setMpFilter(mp)}
                  className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                    mpFilter === mp ? 'bg-purple-600 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300'
                  }`}
                >
                  {mp === 'all' ? 'Все' : mp === 'wb' ? 'WB' : 'Ozon'}
                </button>
              ))}
            </div>
            <div className="flex gap-1">
              {([7, 14, 30] as Days[]).map(d => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                    days === d ? 'bg-purple-600 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300'
                  }`}
                >
                  {d}д
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Таб-бар с бейджами */}
        <div className="grid grid-cols-4 gap-1">
          {TABS.map(({ key, label, icon: Icon }) => {
            const badge = key === 'dashboard' ? criticalAlertCount : key === 'reports' ? reportsBadgeCount : 0
            return (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`flex items-center justify-center gap-1 px-1.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  tab === key ? 'bg-purple-600 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300'
                }`}
              >
                <div className="relative shrink-0">
                  <Icon size={13} />
                  {badge > 0 && (
                    <span className={`absolute -top-1 -right-1.5 flex items-center justify-center rounded-full bg-red-500 text-white font-bold leading-none ${
                      badge > 9 ? 'min-w-[14px] h-3.5 text-[8px] px-0.5' : 'w-3 h-3 text-[9px]'
                    }`}>
                      {badge > 9 ? '9+' : badge}
                    </span>
                  )}
                </div>
                <span className="truncate">{label}</span>
              </button>
            )
          })}
        </div>
      </div>

      {tab === 'dashboard' && loading && (
        <>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="bg-white dark:bg-gray-800 rounded-xl p-3 shadow-sm animate-pulse h-14" />
            ))}
          </div>
          <div className="space-y-3 md:space-y-0 md:grid md:grid-cols-2 lg:grid-cols-3 md:gap-3 md:items-start">
            {Array.from({ length: 12 }).map((_, i) => <CardSkeleton key={i} />)}
          </div>
        </>
      )}

      {tab === 'dashboard' && error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-xl p-4 text-sm flex items-center gap-1.5">
          <AlertCircle size={16} className="shrink-0" /> {error}
        </div>
      )}

      {tab === 'dashboard' && displayData && !loading && (
        <>
          {/* Статус / алерты — по сырым данным, без фильтра МП */}
          <AlertBanner data={data!} />

          {/* KPI cards — 2 строки по 3 на мобильном, 1 строка на десктопе */}
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            {kpiCards.map(({ label, value, color, delta, deltaPositive }) => (
              <div key={label} className="bg-white dark:bg-gray-800 rounded-xl p-3 shadow-sm text-center">
                <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
                <div className={`text-xl font-bold mt-0.5 tracking-tight ${color || 'text-gray-800 dark:text-gray-100'}`}>{value}</div>
                {delta && (
                  <div className={`text-[10px] font-medium mt-0.5 ${deltaPositive ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400'}`}>
                    {delta}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Чарты: на мобильном — один столбец; от md — сетка в 2-3 колонки */}
          <div className="space-y-3 md:space-y-0 md:grid md:grid-cols-2 lg:grid-cols-3 md:gap-3 md:items-start">
            <WowTrend data={displayData.trend} />
            <RevenueChart data={displayData.revenue_by_day} sales={displayData.sales_by_day ?? []} />
            <TopProducts data={displayData.top_products} />
            {/* id для скролла из AlertBanner */}
            <div id="section-drr"><DrrGauge adv={displayData.adv} salesByDay={displayData.revenue_by_day ?? []} /></div>
            <MarginChart data={displayData.net_margin ?? []} />
            <NetMarginTable data={displayData.net_margin ?? []} abcData={displayData.abc_data ?? []} />
            <BidSuggestions data={displayData.bid_suggestions ?? []} />
            <FunnelChart data={displayData.funnel ?? []} />
            <CtrRoas data={displayData.product_metrics} />
            <ReturnsTable data={displayData.returns_top ?? []} />
            <div id="section-stock"><StockTable data={displayData.stock_velocity} /></div>
            <AbcTable data={displayData.abc_data ?? []} />
            <MomChart data={displayData.mom_trends ?? []} />
            {timeline && <ChainTimeline chains={timeline.chains} />}
          </div>

          <div className="text-center text-xs text-gray-400 dark:text-gray-500 pb-2">
            За {displayData.period_days} дней с {displayData.date_from}
            {isStale
              ? ' · из кеша, обновление...'
              : updatedAtStr ? ` · обновлено в ${updatedAtStr}` : ''
            }
          </div>
        </>
      )}

      {/* Reports: скелетон если нет данных, иначе контент */}
      {tab === 'reports' && loading && (
        <div className="space-y-3">
          {Array.from({ length: 2 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
      )}
      {tab === 'reports' && !loading && (
        <div className="space-y-3">
          <div className="text-xs text-gray-400 dark:text-gray-500 px-1">
            Данные за {days} дней
            {isStale ? ' · из кеша, обновление...' : updatedAtStr ? ` · обновлено в ${updatedAtStr}` : ''}
          </div>
          <KwTable data={data?.kw_top ?? []} />
          <SupplyPlan data={displayData?.supply_plan ?? { products: [], lead_days: 0, safety_days: 0 }} />
        </div>
      )}

      {tab === 'catalog' && (
        <div className="space-y-3">
          <ProductsTable data={data?.catalog?.products ?? []} />
          <ShopKpiCard data={data?.catalog?.shop_kpi ?? {}} />
          <ProductForm />
          <MergeProductForm data={data?.catalog?.products ?? []} />
        </div>
      )}

      {tab === 'settings' && (
        <div className="space-y-3">
          <CostEditor />
          <AddShopForm />
        </div>
      )}
    </div>
  )
}
