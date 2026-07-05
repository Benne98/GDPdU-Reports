import { Component, type ErrorInfo } from 'react'
import GlCashDebtTab from './GlCashDebtTab'
import type { PeriodSelection } from '../../../lib/periodSelection'

type Props = {
  period: PeriodSelection
  entity?: string
}

type State = { error: Error | null }

/** Catches render errors so Cash & debt never whitescreens the Cash flow page. */
class CashDebtErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Cash & debt render error', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mt-2 rounded-xl border border-red-200 bg-red-50 px-4 py-6 text-sm text-red-800">
          Cash &amp; debt could not be displayed. Please reload the page or contact support if this
          persists.
        </div>
      )
    }
    return <GlCashDebtTab {...this.props} />
  }
}

export default CashDebtErrorBoundary
