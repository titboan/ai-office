const GROUP_STYLE: Record<string, string> = {
  A: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  B: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  C: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
}

// Общий бейдж ABC-группы — раньше был только внутри AbcTable, теперь нужен и в
// NetMarginTable (чтобы видеть "товар из группы A даёт минус", не переключаясь
// между двумя карточками дашборда).
export default function AbcBadge({ group }: { group: 'A' | 'B' | 'C' }) {
  return (
    <span className={`px-1 py-0.5 rounded text-[10px] font-bold ${GROUP_STYLE[group]}`}>
      {group}
    </span>
  )
}
