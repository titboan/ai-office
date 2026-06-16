import { Component, ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: string | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(e: Error): State {
    return { error: e.message }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center p-6">
          <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-xl p-4 text-sm max-w-sm">
            <div className="font-semibold mb-1">Ошибка загрузки</div>
            <div className="text-xs opacity-75">{this.state.error}</div>
            <button
              className="mt-3 text-xs underline"
              onClick={() => this.setState({ error: null })}
            >
              Попробовать снова
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
