import { createContext, useContext, useRef, ReactNode } from 'react'

// Единственный физический Telegram.WebApp.MainButton общий на всё приложение, но
// несколько виджетов (NetMarginTable, BidSuggestions) независимо регистрируют на него
// своё действие через useMainButtonAction. Без общего "владельца" второй виджет
// перезаписывает текст/показ кнопки, но не снимает обработчик первого — по нажатию
// срабатывают оба onClick. Этот контекст держит текущего владельца (id хука + его
// onClick) и гарантирует, что при захвате кнопки новым виджетом обработчик предыдущего
// владельца всегда снимается первым.
interface Owner {
  id: symbol
  onClick: () => void
}

interface MainButtonContextValue {
  claim: (ownerId: symbol, onClick: () => void) => void
  release: (ownerId: symbol) => void
}

const MainButtonContext = createContext<MainButtonContextValue | null>(null)

export function MainButtonProvider({ children }: { children: ReactNode }) {
  const ownerRef = useRef<Owner | null>(null)

  const claim = (ownerId: symbol, onClick: () => void) => {
    const mainButton = (window as any).Telegram?.WebApp?.MainButton
    if (mainButton && ownerRef.current && ownerRef.current.id !== ownerId) {
      mainButton.offClick(ownerRef.current.onClick)
    }
    ownerRef.current = { id: ownerId, onClick }
  }

  // Снимает обработчик и прячет кнопку, только если ownerId — текущий владелец.
  // Если владелец уже сменился (другой виджет успел захватить кнопку раньше) — no-op,
  // чтобы не задеть чужой активный обработчик/текст.
  const release = (ownerId: symbol) => {
    if (ownerRef.current?.id !== ownerId) return
    const mainButton = (window as any).Telegram?.WebApp?.MainButton
    if (mainButton) {
      mainButton.offClick(ownerRef.current.onClick)
      mainButton.hide()
    }
    ownerRef.current = null
  }

  return (
    <MainButtonContext.Provider value={{ claim, release }}>
      {children}
    </MainButtonContext.Provider>
  )
}

export function useMainButtonContext(): MainButtonContextValue {
  const ctx = useContext(MainButtonContext)
  if (!ctx) throw new Error('useMainButtonContext must be used within MainButtonProvider')
  return ctx
}
