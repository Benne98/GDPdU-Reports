import { useState } from 'react'
import { Pencil, X } from 'lucide-react'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../financials/pl-two-view/plToolbarButton'
import SalesChartEditorPopover, { useChartEditorAnchor } from '../analytics/SalesChartEditorPopover'
import {
  AGING_DIMENSION_BAR_OPTIONS,
  type AgingDimensionBarConfig,
} from './shared/agingDimensionBarConfig'

export default function ReceivablesDimensionBarEditor({
  config,
  onChange,
  disabled,
}: {
  config: AgingDimensionBarConfig
  onChange: (cfg: AgingDimensionBarConfig) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const anchorRef = useChartEditorAnchor()

  return (
    <div className="shrink-0">
      <button
        ref={anchorRef}
        type="button"
        title="Edit chart settings"
        disabled={disabled}
        onClick={() => setOpen(v => !v)}
        className={PL_TOOLBAR_ICON_BTN}
        style={{ ...PL_TOOLBAR_BTN_STYLE, opacity: disabled ? 0.5 : 1 }}
      >
        <Pencil size={14} strokeWidth={1.75} />
      </button>
      <SalesChartEditorPopover open={open} onClose={() => setOpen(false)} anchorRef={anchorRef} width={280}>
        <div className="flex items-center justify-between mb-2 shrink-0">
          <span className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>Chart settings</span>
          <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100">
            <X size={14} />
          </button>
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Dimension</p>
        <div className="flex flex-col gap-0.5 mb-3 shrink-0 max-h-40 overflow-y-auto">
          {AGING_DIMENSION_BAR_OPTIONS.map(opt => {
            const active = config.dimension === opt.id
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => onChange({ ...config, dimension: opt.id })}
                className={`${PL_TOOLBAR_BTN} w-full text-left px-2.5 py-1.5 text-xs rounded-lg`}
                style={{
                  ...PL_TOOLBAR_BTN_STYLE,
                  background: active ? 'rgba(30,58,95,0.1)' : 'transparent',
                  color: active ? '#1E3A5F' : '#475569',
                  fontWeight: active ? 600 : 400,
                  border: `1px solid ${active ? 'rgba(30,58,95,0.2)' : '#E2E8F0'}`,
                }}
              >
                {opt.label}
              </button>
            )
          })}
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>
          Comparison (background bar)
        </p>
        <div className="flex flex-col gap-1 shrink-0">
          <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: '#475569' }}>
            <input
              type="checkbox"
              checked={config.comparePm}
              onChange={e => onChange({ ...config, comparePm: e.target.checked })}
            />
            Prior month (Vormonat)
          </label>
          <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: '#475569' }}>
            <input
              type="checkbox"
              checked={config.comparePy}
              onChange={e => onChange({ ...config, comparePy: e.target.checked })}
            />
            Same month prior year (Vorjahr)
          </label>
        </div>
      </SalesChartEditorPopover>
    </div>
  )
}
