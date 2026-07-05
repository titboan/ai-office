import { useEffect, useState } from 'react'

// Графики (Recharts) не могут читать Tailwind dark: классы напрямую — им нужен
// реальный hex-цвет. Этот хук даёт текущую тему и переподписывается на переключение
// (ручное кнопкой или автоматическое через Telegram), не завязываясь на то, как
// именно был выставлен класс "dark" на <html>.
export function useIsDarkMode(): boolean {
  const [isDark, setIsDark] = useState(() => document.documentElement.classList.contains('dark'))

  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver(() => setIsDark(el.classList.contains('dark')))
    observer.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])

  return isDark
}
