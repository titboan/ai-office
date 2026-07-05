// Единый вид "нет данных" — раньше часть виджетов при пустых данных молча
// исчезала (return null), только StockTable показывал текст. Теперь карточка
// с заголовком остаётся видна, просто с сообщением вместо графика/таблицы —
// понятно, что виджет проверил данные, а не сломался.
export default function EmptyState({ message = 'Нет данных за период' }: { message?: string }) {
  return (
    <div className="py-6 text-center text-xs text-gray-400 dark:text-gray-500">{message}</div>
  )
}
