import { Filter } from 'lucide-react'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from '../../financials/pl-two-view/plToolbarButton'

type Props = {
  active: boolean
  onClick: () => void
  title?: string
  disabled?: boolean
}

/** Excel-style funnel filter toggle for table toolbars. */
export default function TableFilterToolbarButton({ active, onClick, title, disabled }: Props) {
  return (
    <button
      type="button"
      title={title ?? (active ? 'Hide column filters' : 'Show column filters')}
      disabled={disabled}
      onClick={onClick}
      className={PL_TOOLBAR_ICON_BTN}
      style={{
        ...PL_TOOLBAR_BTN_STYLE,
        color: active ? '#1E3A5F' : PL_TOOLBAR_BTN_STYLE.color,
        background: active ? '#EFF6FF' : PL_TOOLBAR_BTN_STYLE.background,
        borderColor: active ? '#BFDBFE' : PL_TOOLBAR_BTN_STYLE.borderColor,
        opacity: disabled ? 0.5 : 1,
      }}
      aria-pressed={active}
    >
      <Filter size={14} strokeWidth={1.75} />
    </button>
  )
}
