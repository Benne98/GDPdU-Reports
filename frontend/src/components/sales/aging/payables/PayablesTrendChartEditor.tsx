import { useState } from 'react'
import { Pencil, X } from 'lucide-react'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../../financials/pl-two-view/plToolbarButton'
import SalesChartEditorPopover, { useChartEditorAnchor } from '../../analytics/SalesChartEditorPopover'
import {
  PAYABLES_TREND_GRAIN_OPTIONS,
  PAYABLES_TREND_METRICS,
  PAYABLES_TREND_PERIOD_OPTIONS,
  type PayablesTrendChartConfig,
  type PayablesTrendMetricId,
} from './shared/payablesTrendChartConfig'

export default function PayablesTrendChartEditor({
  config,
  onChange,
  disabled,
}: {
  config: PayablesTrendChartConfig
  onChange: (cfg: PayablesTrendChartConfig) => void
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const anchorRef = useChartEditorAnchor()

  function toggleMetric(id: PayablesTrendMetricId) {
    const has = config.metrics.includes(id)
    const metrics = has ? config.metrics.filter(m => m !== id) : [...config.metrics, id]
    onChange({ ...config, metrics })
  }

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
      <SalesChartEditorPopover open={open} onClose={() => setOpen(false)} anchorRef={anchorRef} width={300}>
        <div className="flex items-center justify-between mb-2 shrink-0">
          <span className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>Trend chart settings</span>
          <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100">
            <X size={14} />
          </button>
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Time depth</p>
        <div className="flex flex-wrap gap-1 mb-3 shrink-0">
          {PAYABLES_TREND_GRAIN_OPTIONS.map(opt => {
            const active = config.grain === opt.id
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => onChange({ ...config, grain: opt.id })}
                className={`${PL_TOOLBAR_BTN} px-2.5 py-1 text-[12px] rounded-lg`}
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

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Lookback</p>
        <div className="flex flex-wrap gap-1 mb-3 shrink-0">
          {PAYABLES_TREND_PERIOD_OPTIONS.map(opt => {
            const active = config.periodsBack === opt.value
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => onChange({ ...config, periodsBack: opt.value })}
                className={`${PL_TOOLBAR_BTN} px-2.5 py-1 text-[12px] rounded-lg`}
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

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Metrics</p>
        <div className="flex flex-col gap-1 max-h-48 overflow-y-auto shrink-0">
          {PAYABLES_TREND_METRICS.map(m => (
            <label
              key={m.id}
              className="flex items-center gap-2 text-xs cursor-pointer rounded-lg px-2 py-1.5 hover:bg-slate-50"
              style={{ color: '#475569' }}
            >
              <input
                type="checkbox"
                checked={config.metrics.includes(m.id)}
                onChange={() => toggleMetric(m.id)}
              />
              <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: m.color }} />
              {m.label}
            </label>
          ))}
        </div>
      </SalesChartEditorPopover>
    </div>
  )
}
