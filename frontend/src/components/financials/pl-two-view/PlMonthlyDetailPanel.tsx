import { useEffect } from 'react'
import { X } from 'lucide-react'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import PlLineDetailContent from './PlLineDetailContent'

export type MonthlyCellSelection = {
  rowId: string
  lineCode: string
  label: string
  year: number
  month: number
  periodLabel: string
  amountKeur?: number
  lineMomKeur?: number
  /** Report period the monthly table was built for (row-id anchor). */
  anchorYear?: number
  anchorMonth?: number
  /** Set when opened from a custom aggregate column */
  columnId?: string
}

type Props = {
  selection: MonthlyCellSelection
  entity?: string
  statement?: FinStatementKind
  onClose: () => void
  /** Fills the report split grid column (no fixed sidebar width). */
  columnLayout?: boolean
}

export default function PlMonthlyDetailPanel({
  selection,
  entity,
  statement = 'pl',
  onClose,
  columnLayout,
}: Props) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside
      className={`flex flex-col bg-white overflow-hidden ${
        columnLayout ? 'min-w-0 w-full rounded-xl border border-slate-200' : 'shrink-0 border-l border-slate-200'
      }`}
      style={columnLayout ? { minHeight: 360 } : { width: 'min(420px, 38vw)', minWidth: 320 }}
      aria-label="Line detail"
    >
      <header className="shrink-0 px-3 py-3 border-b border-slate-100 bg-gradient-to-r from-slate-50 to-white">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-slate-900 truncate">{selection.label}</h3>
            <p className="text-xs text-slate-500 mt-0.5">{selection.periodLabel}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-500 shrink-0"
            aria-label="Close detail"
          >
            <X size={18} />
          </button>
        </div>
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-3">
        <PlLineDetailContent
          statement={statement}
          lineCode={selection.lineCode}
          label={selection.label}
          year={selection.year}
          month={selection.month}
          anchorYear={selection.anchorYear}
          anchorMonth={selection.anchorMonth}
          entity={entity}
          lineMomKeur={selection.lineMomKeur}
          cellAmountKeur={selection.amountKeur}
        />
      </div>
    </aside>
  )
}
