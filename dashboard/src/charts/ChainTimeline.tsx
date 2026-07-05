import {
  Workflow, Users, Code2, Search, BarChart3, PenLine, Calendar,
  ShoppingCart, Palette, Newspaper, Landmark, Bot, LucideIcon,
} from 'lucide-react'
import { ChainRun, TimelineEvent } from '../api'
import Card from '../components/Card'

const AGENT_ICON: Record<string, LucideIcon> = {
  marta: Users, kevin: Code2, kasper: Search, peter: BarChart3,
  elina: PenLine, alex: Calendar, max: ShoppingCart, dan: Palette,
  eva: Newspaper, tina: Landmark,
}

const STATUS_BADGE: Record<string, string> = {
  completed: 'text-green-600 bg-green-50 dark:text-green-400 dark:bg-green-900/30',
  failed:    'text-red-600 bg-red-50 dark:text-red-400 dark:bg-red-900/30',
  running:   'text-blue-600 bg-blue-50 dark:text-blue-400 dark:bg-blue-900/30',
}

const STATUS_LABEL: Record<string, string> = {
  completed: '✓ готово',
  failed:    '✗ ошибка',
  running:   '⟳ работает',
}

function fmtDuration(sec: number | null): string {
  if (!sec || sec < 1) return ''
  if (sec < 60) return `${sec}с`
  return `${Math.floor(sec / 60)}м${sec % 60 > 0 ? ` ${sec % 60}с` : ''}`
}

function fmtTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function buildAgentSteps(events: TimelineEvent[]): Array<{ key: string; status: 'completed' | 'failed' | 'running' }> {
  const order: string[] = []
  const status = new Map<string, 'completed' | 'failed' | 'running'>()
  for (const e of events) {
    const k = e.agent_key || 'unknown'
    if (k === 'unknown') continue
    if (!status.has(k)) {
      order.push(k)
      status.set(k, 'running')
    }
    if (e.event_type === 'TASK_COMPLETED') status.set(k, 'completed')
    if (e.event_type === 'TASK_FAILED')    status.set(k, 'failed')
  }
  return order.map(k => ({ key: k, status: status.get(k)! }))
}

export default function ChainTimeline({ chains }: { chains: ChainRun[] }) {
  return (
    <Card title={<span className="text-gray-700 dark:text-gray-200 flex items-center gap-1.5"><Workflow size={15} /> Последние цепочки</span>}>
      <div className="space-y-3">
      {chains.length === 0 && (
        <div className="text-sm text-gray-400 dark:text-gray-500">Нет данных за последние 7 дней</div>
      )}

      {chains.map(chain => {
        const agents = buildAgentSteps(chain.events)
        return (
          <div
            key={chain.chain_id}
            className="border border-gray-100 dark:border-gray-700 rounded-lg p-3 space-y-2"
          >
            {/* Header row */}
            <div className="flex items-center justify-between gap-2 flex-wrap">
              <div className="flex items-center gap-2 text-xs text-gray-400 dark:text-gray-500">
                <span className="font-mono">{chain.chain_id}</span>
                <span>{fmtTime(chain.started_at)}</span>
                {chain.duration_sec ? <span>{fmtDuration(chain.duration_sec)}</span> : null}
              </div>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_BADGE[chain.status]}`}>
                {STATUS_LABEL[chain.status]}
              </span>
            </div>

            {/* Agent pipeline */}
            {agents.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap">
                {agents.map(({ key, status }, i) => {
                  const AgentIcon = AGENT_ICON[key] ?? Bot
                  return (
                    <div key={`${key}-${i}`} className="flex items-center gap-1">
                      <span
                        className={`text-xs px-2 py-0.5 rounded-full inline-flex items-center gap-1 ${
                          status === 'completed'
                            ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                            : status === 'failed'
                            ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                            : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                        }`}
                      >
                        <AgentIcon size={11} /> {key}
                      </span>
                      {i < agents.length - 1 && (
                        <span className="text-gray-300 dark:text-gray-600 text-xs">→</span>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
      </div>
    </Card>
  )
}
