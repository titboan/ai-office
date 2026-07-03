import { ReactNode } from 'react'

interface CardProps {
  title?: ReactNode
  subtitle?: ReactNode
  children: ReactNode
  headerExtra?: ReactNode
}

// Общая карточка-виджет дашборда — раньше "bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm"
// было продублировано в каждом chart-компоненте по отдельности.
export default function Card({ title, subtitle, children, headerExtra }: CardProps) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm">
      {(title || headerExtra) && (
        <div className={headerExtra ? 'flex items-center justify-between mb-3' : undefined}>
          {title && <h2 className={`text-sm font-semibold ${subtitle ? 'mb-1' : headerExtra ? '' : 'mb-3'}`}>{title}</h2>}
          {headerExtra}
        </div>
      )}
      {subtitle && <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">{subtitle}</p>}
      {children}
    </div>
  )
}
