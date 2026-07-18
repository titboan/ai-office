import { ABC_GROUP_STYLE } from '../theme'

// Общий бейдж ABC-группы — раньше был только внутри AbcTable, теперь нужен и в
// NetMarginTable (чтобы видеть "товар из группы A даёт минус", не переключаясь
// между двумя карточками дашборда).
export default function AbcBadge({ group }: { group: 'A' | 'B' | 'C' }) {
  return (
    <span className={`px-1 py-0.5 rounded text-[10px] font-bold ${ABC_GROUP_STYLE[group]}`}>
      {group}
    </span>
  )
}
