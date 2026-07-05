import { useEffect } from 'react'

// Общий паттерн "выбрал действие в карточке → нативная Telegram MainButton внизу
// экрана подтверждает и выполняет" — используется NetMarginTable (цены) и
// BidSuggestions (ставки рекламы), чтобы не дублировать show/hide/onClick/cleanup.
export function useMainButtonAction<T>(
  pending: T | null,
  getText: (p: T) => string,
  onConfirm: (p: T) => Promise<void>,
) {
  useEffect(() => {
    const mainButton = (window as any).Telegram?.WebApp?.MainButton
    if (!mainButton) return
    if (!pending) {
      mainButton.hide()
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
    mainButton.onClick(onClick)
    return () => mainButton.offClick(onClick)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending])

  // Скрыть MainButton при уходе со страницы/размонтировании карточки
  useEffect(() => () => {
    (window as any).Telegram?.WebApp?.MainButton?.hide()
  }, [])
}
