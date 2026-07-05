// Плейсхолдер карточки на время загрузки — раньше вместо этого был один
// блокирующий текст "Загружаю данные…" на весь экран. Формой повторяет Card,
// чтобы макет не "прыгал", когда реальные данные приходят.
export default function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-xl p-4 shadow-sm animate-pulse">
      <div className="h-3 w-1/2 bg-gray-200 dark:bg-gray-700 rounded mb-4" />
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <div key={i} className="h-3 bg-gray-100 dark:bg-gray-700/60 rounded" style={{ width: `${85 - i * 15}%` }} />
        ))}
      </div>
    </div>
  )
}
