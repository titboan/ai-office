import { Circle } from 'lucide-react'
import { MARKETPLACE, MarketplaceKey, marketplaceChartColor } from '../theme'
import { useIsDarkMode } from '../hooks/useIsDarkMode'

// Цветной кружок + название площадки — раньше эмодзи "🟣 WB"/"🔵 Ozon" (рендерится
// по-разному в разных Telegram-клиентах), теперь настоящая брендовая иконка.
// В тёмной теме используем colorDark (светлее/контрастнее), иначе кружок тускнеет.
export default function MarketplaceBadge({ marketplace, className = '' }: { marketplace: string; className?: string }) {
  const isDark = useIsDarkMode()
  const mp = MARKETPLACE[marketplace as MarketplaceKey]
  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <Circle size={8} fill={marketplaceChartColor(marketplace, isDark)} stroke="none" />
      {mp?.label ?? marketplace}
    </span>
  )
}
