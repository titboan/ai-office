import { useEffect, useRef } from 'react'
import { useMainButtonContext } from './MainButtonContext'

// Общий паттерн "выбрал действие в карточке → нативная Telegram MainButton внизу
// экрана подтверждает и выполняет" — используется NetMarginTable (цены) и
// BidSuggestions (ставки рекламы), чтобы не дублировать show/hide/onClick/cleanup.
// MainButton физически один на всё приложение — владение им координируется через
// MainButtonContext (см. MainButtonContext.tsx), чтобы обработчик одного виджета не
// оставался висеть на кнопке после того, как другой виджет захватил её.
export function useMainButtonAction<T>(
  pending: T | null,
  getText: (p: T) => string,
  onConfirm: (p: T) => Promise<void>,
) {
  const { claim, release } = useMainButtonContext()
  // Один стабильный id на весь жизненный цикл компонента — используется как ключ
  // владения в MainButtonContext.
  const ownerIdRef = useRef<symbol>(Symbol('mainButtonOwner'))

  useEffect(() => {
    const mainButton = (window as any).Telegram?.WebApp?.MainButton
    if (!mainButton) return
    const ownerId = ownerIdRef.current
    if (!pending) {
      release(ownerId)
      return
    }
    mainButton.setText(getText(pending))
    mainButton.show()

    const onClick = async () => {
      mainButton.showProgress()
      try {
        await onConfirm(pending)
      } finally {
        mainButton.hideProgress()
      }
    }
    claim(ownerId, onClick)
    return () => release(ownerId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending])

  // Снять владение MainButton при уходе со страницы/размонтировании карточки —
  // release() сам решит, нужно ли реально прятать кнопку (только если этот компонент
  // всё ещё её владелец, а не другой виджет, успевший её перехватить).
  useEffect(() => {
    const ownerId = ownerIdRef.current
    return () => release(ownerId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
