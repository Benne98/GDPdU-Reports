import { useState } from 'react'
import { Pencil, X } from 'lucide-react'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../financials/pl-two-view/plToolbarButton'
import SalesChartEditorPopover, { useChartEditorAnchor } from './SalesChartEditorPopover'
import { CHURN_DIM_OPTIONS, type ChurnGrain } from './churnBridgeLayout'

const GRAINS: { key: ChurnGrain; label: string }[] = [
  { key: 'year', label: 'Year' },
  { key: 'quarter', label: 'Quarter' },
  { key: 'month', label: 'Month' },
]

type Props = {
  grain: ChurnGrain
  dim: string
  onGrainChange: (grain: ChurnGrain) => void
  onDimChange: (dim: string) => void
  disabled?: boolean
}

export function churnGrainLabel(grain: ChurnGrain): string {
  return GRAINS.find(g => g.key === grain)?.label ?? grain
}

export default function SalesChurnEditor({
  grain,
  dim,
  onGrainChange,
  onDimChange,
  disabled,
}: Props) {
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
      <SalesChartEditorPopover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={anchorRef}
        width={300}
      >
        <div className="flex items-center justify-between mb-2 shrink-0">
          <span className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>Chart settings</span>
          <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100">
            <X size={14} />
          </button>
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Period grain</p>
        <div className="flex rounded-lg overflow-hidden mb-3 shrink-0" style={{ border: '1px solid #E2E8F0' }}>
          {GRAINS.map(g => {
            const active = grain === g.key
            return (
              <button
                key={g.key}
                type="button"
                onClick={() => onGrainChange(g.key)}
                className="flex-1 px-2 py-1.5 text-xs font-medium transition-colors"
                style={{
                  background: active ? 'rgba(30,58,95,0.1)' : '#FFFFFF',
                  color: active ? '#1E3A5F' : '#64748B',
                  fontWeight: active ? 600 : 400,
                }}
              >
                {g.label}
              </button>
            )
          })}
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Break down by</p>
        <div className="flex flex-col gap-0.5 max-h-52 overflow-y-auto">
          {CHURN_DIM_OPTIONS.map(d => {
            const active = dim === d.key
            return (
              <button
                key={d.key}
                type="button"
                onClick={() => {
                  onDimChange(d.key)
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
