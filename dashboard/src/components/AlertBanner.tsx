import { CheckCircle2, XCircle, AlertTriangle } from 'lucide-react'
import { DashboardData } from '../api'

export interface Alert {
  level: 'critical' | 'warning'
  text: string
  scrollTo?: string  // id элемента для скролла при нажатии
}

export function collectAlerts(data: DashboardData): Alert[] {
  const alerts: Alert[] = []

  const criticalStock = data.stock_velocity.filter(r => r.days_left < 7 && r.days_left !== 999)
  if (criticalStock.length === 1) {
    alerts.push({ level: 'critical', text: `Сток кончается: ${criticalStock[0].name} (< 7 дней)`, scrollTo: 'section-stock' })
  } else if (criticalStock.length > 1) {
    alerts.push({ level: 'critical', text: `Сток кончается: ${criticalStock.length} позиций < 7 дней`, scrollTo: 'section-stock' })
  }

  const warnStock = data.stock_velocity.filter(r => r.days_left >= 7 && r.days_left < 14 && r.days_left !== 999)
  if (warnStock.length > 0) {
    alerts.push({ level: 'warning', text: `Сток на исходе: ${warnStock.length} позиций (7–14 дней)`, scrollTo: 'section-stock' })
  }

  const totalRevenue = data.revenue.reduce((s, r) => s + r.revenue, 0)
  const totalSpend = data.adv.reduce((s, r) => s + r.spend, 0)
  if (totalRevenue > 0) {
    const drr = (totalSpend / totalRevenue) * 100
    if (drr > 30) {
      alerts.push({ level: 'critical', text: `ДРР ${drr.toFixed(1)}% — выше нормы (цель < 20%)`, scrollTo: 'section-drr' })
    } else if (drr > 20) {
      alerts.push({ level: 'warning', text: `ДРР ${drr.toFixed(1)}% — повышенный`, scrollTo: 'section-drr' })
    }
  }

  // Проверка свежести данных: дата конца периода должна быть близко к сегодня
  try {
    const dateFrom = new Date(data.date_from)
    const dataEnd = new Date(dateFrom.getTime() + data.period_days * 24 * 60 * 60 * 1000)
    const daysStale = Math.floor((Date.now() - dataEnd.getTime()) / (1000 * 60 * 60 * 24))
    if (daysStale > 1) {
      alerts.push({ level: 'warning', text: `Данные не обновлялись ${daysStale} дн — запусти синк у Макса` })
    }
  } catch {}

  return alerts
}

export default function AlertBanner({ data }: { data: DashboardData }) {
  const alerts = collectAlerts(data)

  if (alerts.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 text-xs font-medium">
        <CheckCircle2 size={14} className="shrink-0" />
        Всё в норме — сток и ДРР в пределах цели
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      {alerts.map((a, i) => {
        const isCritical = a.level === 'critical'
        return (
          <div
            key={i}
            className={`flex items-start gap-2 px-3 py-2 rounded-xl text-xs ${
              isCritical
                ? 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'
                : 'bg-yellow-50 dark:bg-yellow-900/20 text-yellow-700 dark:text-yellow-400'
            } ${a.scrollTo ? 'cursor-pointer active:opacity-70' : ''}`}
            onClick={a.scrollTo
              ? () => document.getElementById(a.scrollTo!)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              : undefined
            }
          >
            {isCritical
              ? <XCircle size={14} className="shrink-0 mt-0.5" />
              : <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            }
            <span className="flex-1">{a.text}</span>
            {a.scrollTo && <span className="shrink-0 opacity-50 self-center">↓</span>}
          </div>
        )
      })}
    </div>
  )
}
