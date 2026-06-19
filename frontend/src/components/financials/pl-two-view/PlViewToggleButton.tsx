import { LayoutList, Table2 } from 'lucide-react'
import { PL_TOOLBAR_BTN, PL_TOOLBAR_BTN_STYLE } from './plToolbarButton'

export type PlViewMode = 'report' | 'table'

type Props = {
  mode: PlViewMode
  onChange: (mode: PlViewMode) => void
  disabled?: boolean
}

export default function PlViewToggleButton({ mode, onChange, disabled }: Props) {
  const isReport = mode === 'report'
  return (
    <button
      type="button"
      onClick={() => onChange(isReport ? 'table' : 'report')}
      disabled={disabled}
      title={isReport ? 'Switch to table view' : 'Switch to report view'}
      className={`${PL_TOOLBAR_BTN} px-2`}
      style={{
        ...PL_TOOLBAR_BTN_STYLE,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {isReport ? <Table2 size={12} strokeWidth={1.75} /> : <LayoutList size={12} strokeWidth={1.75} />}
      {isReport ? 'Table View' : 'Report View'}
    </button>
  )
}
