import { Circle } from 'lucide-react'
import { MARKETPLACE, MarketplaceKey } from '../theme'

// Цветной кружок + название площадки — раньше эмодзи "🟣 WB"/"🔵 Ozon" (рендерится
// по-разному в разных Telegram-клиентах), теперь настоящая брендовая иконка.
export default function MarketplaceBadge({ marketplace, className = '' }: { marketplace: string; className?: string }) {
  const mp = MARKETPLACE[marketplace as MarketplaceKey]
  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <Circle size={8} fill={mp?.color ?? '#6b7280'} stroke="none" />
      {mp?.label ?? marketplace}
    </span>
  )
}
