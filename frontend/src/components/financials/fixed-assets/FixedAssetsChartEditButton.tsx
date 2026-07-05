import { Pencil } from 'lucide-react'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from '../../financials/pl-two-view/plToolbarButton'

type Props = {
  title: string
  onClick: () => void
  disabled?: boolean
}

export default function FixedAssetsChartEditButton({ title, onClick, disabled }: Props) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title}
      className={PL_TOOLBAR_ICON_BTN}
      style={{
        ...PL_TOOLBAR_BTN_STYLE,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <Pencil size={14} strokeWidth={1.75} />
    </button>
  )
}
