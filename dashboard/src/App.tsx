import { useEffect, useState } from 'react'
import { LayoutDashboard, FileBarChart, Package, Settings, AlertCircle, Sun, Moon } from 'lucide-react'
import { fetchDashboard, fetchTimeline, DashboardData, TimelineData } from './api'
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

type Days = 7 | 14 | 30
type Theme = 'light' | 'dark'
type Tab = 'dashboard' | 'reports' | 'catalog' | 'settings'

const TABS: { key: Tab; label: string; icon: typeof LayoutDashboard }[] = [
  { key: 'dashboard', label: 'Дашборд', icon: LayoutDashboard },
  { key: 'reports', label: 'Отчёты', icon: FileBarChart },
  { key: 'catalog', label: 'Каталог', icon: Package },
  { key: 'settings', label: 'Настройки', icon: Settings },
]

const THEME_STORAGE_KEY = 'dashboard-theme'

// Порядок: сохранённый вручную выбор → тема Telegram → системная тема браузера.
// Без сохранённого выбора дашборд вне Telegram (по прямой ссылке) раньше всегда
// был светлым — теперь учитывает prefers-color-scheme.
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

export default function App() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState<Days>(14)
  const [loading, setLoading] = useState(true)
  const [timeline, setTimeline] = useState<TimelineData | null>(null)
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const [tab, setTab] = useState<Tab>(getInitialTab)

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

  useEffect(() => {
    fetchTimeline().then(setTimeline).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetchDashboard(days)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [days])

  const totalRevenue = data?.revenue.reduce((s, r) => s + r.revenue, 0) ?? 0
  const totalOrders = data?.revenue.reduce((s, r) => s + r.orders, 0) ?? 0
  const avgCheck = totalOrders > 0 ? Math.round(totalRevenue / totalOrders) : 0
  const totalSpend = data?.adv.reduce((s, r) => s + r.spend, 0) ?? 0
  const drr = totalRevenue > 0 ? (totalSpend / totalRevenue * 100).toFixed(1) : '—'

  const wowTotal = data?.trend.reduce(
    (acc, r) => ({ cur: acc.cur + r.week_current, prev: acc.prev + r.week_prev }),
    { cur: 0, prev: 0 }
  )
  const wowPct = wowTotal && wowTotal.prev > 0
    ? ((wowTotal.cur - wowTotal.prev) / wowTotal.prev * 100)
    : null

  const kpiCards = data ? [
    { label: 'Выручка', value: `${(totalRevenue / 1000).toFixed(0)}к ₽`, color: '' },
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

  const activeTabInfo = TABS.find(t => t.key === tab) ?? TABS[0]
  const ActiveTabIcon = activeTabInfo.icon

  return (
    <div
      className="min-h-screen p-3 space-y-3 md:max-w-3xl lg:max-w-5xl md:mx-auto"
      style={{ background: 'var(--tg-theme-bg-color, #f5f5f5)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-base font-bold flex items-center gap-1.5">
          <ActiveTabIcon size={18} /> {activeTabInfo.label}
        </h1>
        <div className="flex gap-1 items-center">
          {tab === 'dashboard' && ([7, 14, 30] as Days[]).map(d => (
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
          <button
            onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            aria-label="Переключить тему"
            className="p-1.5 rounded bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300"
          >
            {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
          </button>
        </div>
      </div>

      {/* Таб-бар — переключение разделов мини-аппа */}
      <div className="grid grid-cols-4 gap-1">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center justify-center gap-1 px-1.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              tab === key ? 'bg-purple-600 text-white' : 'bg-white dark:bg-gray-700 text-gray-600 dark:text-gray-300'
            }`}
          >
            <Icon size={13} /> <span className="truncate">{label}</span>
          </button>
        ))}
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

      {tab === 'dashboard' && data && !loading && (
        <>
          {/* KPI cards — 6 штук: 2 строки по 3 на мобильном, 1 строка на десктопе */}
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            {kpiCards.map(({ label, value, color }) => (
              <div key={label} className="bg-white dark:bg-gray-800 rounded-xl p-3 shadow-sm text-center">
                <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
                <div className={`text-xl font-bold mt-0.5 tracking-tight ${color || 'text-gray-800 dark:text-gray-100'}`}>{value}</div>
              </div>
            ))}
          </div>

          {/* На мобильном — один столбец (как раньше); от md — сетка в 2-3 колонки,
              чтобы широкий экран не растягивал узкую телефонную вёрстку. */}
          <div className="space-y-3 md:space-y-0 md:grid md:grid-cols-2 lg:grid-cols-3 md:gap-3 md:items-start">
            {/* WoW тренд по маркетплейсам */}
            <WowTrend data={data.trend} />

            {/* Выручка по дням (линия заказов + область выкупов) */}
            <RevenueChart data={data.revenue_by_day} sales={data.sales_by_day ?? []} />

            {/* Топ товаров */}
            <TopProducts data={data.top_products} />

            {/* ДРР по площадкам */}
            <DrrGauge adv={data.adv} salesByDay={data.revenue_by_day ?? []} />

            {/* Рентабельность (NET-маржа) */}
            <MarginChart data={data.net_margin ?? []} />

            {/* NET маржа из реальных выплат */}
            <NetMarginTable data={data.net_margin ?? []} abcData={data.abc_data ?? []} />

            {/* Предложения по ставкам рекламы (ДРР) */}
            <BidSuggestions data={data.bid_suggestions ?? []} />

            {/* Воронка конверсии */}
            <FunnelChart data={data.funnel ?? []} />

            {/* CTR и ROAS по товарам */}
            <CtrRoas data={data.product_metrics} />

            {/* Возвраты */}
            <ReturnsTable data={data.returns_top ?? []} />

            {/* Остатки */}
            <StockTable data={data.stock_velocity} />

            {/* ABC-анализ */}
            <AbcTable data={data.abc_data ?? []} />

            {/* MoM динамика */}
            <MomChart data={data.mom_trends ?? []} />

            {/* Таймлайн цепочек агентов */}
            {timeline && <ChainTimeline chains={timeline.chains} />}
          </div>

          <div className="text-center text-xs text-gray-400 dark:text-gray-500 pb-2">
            За {data.period_days} дней с {data.date_from}
          </div>
        </>
      )}

      {tab === 'reports' && (
        <div className="space-y-3">
          {/* SEO: позиции ключевых слов WB, приоритет просевшим */}
          <KwTable data={data?.kw_top ?? []} />
          {/* Поставки: план по регионам/кластерам, срочность по остаткам */}
          <SupplyPlan data={data?.supply_plan ?? { products: [], lead_days: 0, safety_days: 0 }} />
        </div>
      )}

      {tab === 'catalog' && (
        <div className="space-y-3">
          {/* Товары: цены WB/Ozon, привязка артикулов, факт заданной с/с */}
          <ProductsTable data={data?.catalog?.products ?? []} />
          {/* Рейтинг и штрафы магазина на WB/Ozon */}
          <ShopKpiCard data={data?.catalog?.shop_kpi ?? {}} />
          {/* Форма вместо /map и текстовой части /add — добавить/обновить товар */}
          <ProductForm />
          {/* Форма вместо /merge_products (inline-пикер mergewiz:*) */}
          <MergeProductForm data={data?.catalog?.products ?? []} />
        </div>
      )}

      {tab === 'settings' && (
        <div className="space-y-3">
          {/* Себестоимость — редактируемая таблица (замена Excel-юнитки) */}
          <CostEditor />
          {/* Форма вместо /add_shop — подключение магазина (чувствительный токен) */}
          <AddShopForm />
        </div>
      )}
    </div>
  )
}
