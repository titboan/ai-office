// Общие дизайн-токены дашборда: цвета площадок, пороги маржи/ДРР/остатков, стиль тултипов.
// Раньше эти значения были продублированы в каждом chart-компоненте по отдельности —
// при следующем изменении порога/цвета правим один раз здесь.

export const MARKETPLACE = {
  // color — для светлой темы; colorDark — светлее и контрастнее на тёмном фоне
  // (иначе линии/области графиков остаются на тех же hex и выглядят тускло в dark mode)
  wb:   { label: 'WB',   color: '#7c3aed', colorDark: '#a78bfa' },
  ozon: { label: 'Ozon', color: '#2563eb', colorDark: '#60a5fa' },
} as const

export type MarketplaceKey = keyof typeof MARKETPLACE

export const marketplaceLabel = (mp: string) => MARKETPLACE[mp as MarketplaceKey]?.label ?? mp
export const marketplaceColor = (mp: string) => MARKETPLACE[mp as MarketplaceKey]?.color ?? '#6b7280'

// Цвет площадки для графиков (Recharts не умеет читать Tailwind dark: классы) —
// используется вместе с useIsDarkMode().
export function marketplaceChartColor(mp: string, isDark: boolean): string {
  const m = MARKETPLACE[mp as MarketplaceKey]
  if (!m) return '#6b7280'
  return isDark ? m.colorDark : m.color
}

// Recharts contentStyle — привязан к CSS-переменным тултипа из index.css
export const TOOLTIP_STYLE = {
  backgroundColor: 'var(--tooltip-bg)',
  color: 'var(--tooltip-text)',
  border: '1px solid var(--tooltip-border)',
}

// ── NET-маржа: цель 50% ────────────────────────────────────────────────────
export const MARGIN_TARGET_PCT = 50

export function marginColorClass(pct: number | null): string {
  if (pct === null) return 'text-gray-400 dark:text-gray-500'
  if (pct < 10) return 'text-red-600'
  if (pct < 30) return 'text-yellow-600'
  if (pct < MARGIN_TARGET_PCT) return 'text-orange-500'
  return 'text-green-600'
}

export function marginColorHex(pct: number): string {
  if (pct < 10) return '#dc2626'
  if (pct < 30) return '#d97706'
  if (pct < MARGIN_TARGET_PCT) return '#f59e0b'
  return '#059669'
}

// ── Остатки: дней продаж ────────────────────────────────────────────────────
export function stockColorClass(days: number): string {
  if (days < 7) return 'text-red-600 dark:text-red-400'
  if (days < 14) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-green-600 dark:text-green-400'
}

export function stockBarClass(days: number): string {
  if (days < 7) return 'bg-red-500'
  if (days < 14) return 'bg-yellow-500'
  return 'bg-green-500'
}

// ── ДРР (доля рекламных расходов) ───────────────────────────────────────────
export function drrColorClass(pct: number | null): string {
  if (pct === null) return 'text-gray-400'
  if (pct > 30) return 'text-red-500'
  if (pct > 20) return 'text-yellow-500'
  return 'text-green-600'
}

// ── Знак роста/падения (WoW-тренд и т.п.) ───────────────────────────────────
export function trendColorClass(v: number | null): string {
  if (v === null) return 'text-gray-400'
  return v >= 0 ? 'text-green-600' : 'text-red-500'
}
