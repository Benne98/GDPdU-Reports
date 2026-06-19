import { LayoutList, Table2 } from 'lucide-react'
import {
  STATEMENT_TOOLBAR_BTN,
  STATEMENT_TOOLBAR_BTN_STYLE,
} from './statementToolbarButton'
import type { StatementViewMode } from './statementTypes'

type Props = {
  mode: StatementViewMode
  onChange: (mode: StatementViewMode) => void
  disabled?: boolean
  reportDisabled?: boolean
}

export default function StatementViewToggleButton({
  mode,
  onChange,
  disabled,
  reportDisabled,
}: Props) {
  const isReport = mode === 'report'
  const blocked = disabled || (reportDisabled && !isReport)

  return (
    <button
      type="button"
      onClick={() => {
        if (disabled) return
        if (reportDisabled && isReport) return
        onChange(isReport ? 'table' : 'report')
      }}
      disabled={blocked}
      title={
        reportDisabled && !isReport
          ? 'Report view will be available in a future release'
          : isReport
            ? 'Switch to table view'
            : 'Switch to report view'
      }
      className={`${STATEMENT_TOOLBAR_BTN} px-2`}
      style={{
        ...STATEMENT_TOOLBAR_BTN_STYLE,
        cursor: blocked ? 'not-allowed' : 'pointer',
        opacity: blocked ? 0.5 : 1,
      }}
    >
      {isReport ? <Table2 size={12} strokeWidth={1.75} /> : <LayoutList size={12} strokeWidth={1.75} />}
      {isReport ? 'Table View' : 'Report View'}
    </button>
  )
}
