import { useState } from 'react'
import { Pencil, X } from 'lucide-react'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../financials/pl-two-view/plToolbarButton'
import { SALES_ANALYTICS_DIMS } from './salesChartRegistry'
import SalesChartEditorPopover, { useChartEditorAnchor } from './SalesChartEditorPopover'

type Props = {
  value: string
  onChange: (key: string) => void
  disabled?: boolean
}

export default function SalesAnalyticsDimEditor({ value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const anchorRef = useChartEditorAnchor()

  return (
    <div className="shrink-0">
      <button
        ref={anchorRef}
        type="button"
        title="Choose breakdown dimension"
        disabled={disabled}
        onClick={() => setOpen(v => !v)}
        className={PL_TOOLBAR_ICON_BTN}
        style={{ ...PL_TOOLBAR_BTN_STYLE, opacity: disabled ? 0.5 : 1 }}
      >
        <Pencil size={14} strokeWidth={1.75} />
      </button>
      <SalesChartEditorPopover open={open} onClose={() => setOpen(false)} anchorRef={anchorRef} width={256}>
        <div className="flex items-center justify-between mb-2 shrink-0">
          <span className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>Dimension</span>
          <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100">
            <X size={14} />
          </button>
        </div>
        <p className="text-[10px] mb-2 shrink-0" style={{ color: '#94A3B8' }}>
          Break down chart by
        </p>
        <div className="flex flex-col gap-0.5 flex-1 min-h-0 overflow-y-auto">
          {SALES_ANALYTICS_DIMS.map(d => {
            const active = value === d.key
            return (
              <button
                key={d.key}
                type="button"
                onClick={() => {
                  onChange(d.key)
                  setOpen(false)
                }}
                className={`${PL_TOOLBAR_BTN} w-full text-left px-2.5 py-1.5 text-xs shrink-0`}
                style={{
                  ...PL_TOOLBAR_BTN_STYLE,
                  background: active ? 'rgba(30,58,95,0.1)' : 'transparent',
                  color: active ? '#1E3A5F' : '#475569',
                  fontWeight: active ? 600 : 400,
                }}
              >
                {d.label}
              </button>
            )
          })}
        </div>
      </SalesChartEditorPopover>
    </div>
  )
}
