import { useState } from 'react'
import { Pencil, X } from 'lucide-react'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../financials/pl-two-view/plToolbarButton'
import { SALES_ANALYTICS_DIMS } from './salesChartRegistry'
import SalesChartEditorPopover, { useChartEditorAnchor } from './SalesChartEditorPopover'
import type { SalesDimensionPerformanceMetric, SalesDimensionPeriodScope } from '../../../lib/api'
import {
  DIMENSION_PERF_METRICS,
  DIMENSION_PERIOD_SCOPES,
} from './salesDimensionPerformanceRegistry'

type Props = {
  dim: string
  metric: SalesDimensionPerformanceMetric
  periodScope: SalesDimensionPeriodScope
  onDimChange: (dim: string) => void
  onMetricChange: (metric: SalesDimensionPerformanceMetric) => void
  onPeriodScopeChange: (scope: SalesDimensionPeriodScope) => void
  disabled?: boolean
}

export default function SalesDimensionPerformanceEditor({
  dim,
  metric,
  periodScope,
  onDimChange,
  onMetricChange,
  onPeriodScopeChange,
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

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Metric</p>
        <div className="flex flex-col gap-0.5 mb-3 shrink-0">
          {DIMENSION_PERF_METRICS.map(m => {
            const active = metric === m.value
            return (
              <button
                key={m.value}
                type="button"
                onClick={() => onMetricChange(m.value)}
                className={`${PL_TOOLBAR_BTN} w-full text-left px-2.5 py-1.5 text-xs rounded-lg`}
                style={{
                  ...PL_TOOLBAR_BTN_STYLE,
                  background: active ? 'rgba(30,58,95,0.1)' : 'transparent',
                  color: active ? '#1E3A5F' : '#475569',
                  fontWeight: active ? 600 : 400,
                  border: `1px solid ${active ? 'rgba(30,58,95,0.2)' : '#E2E8F0'}`,
                }}
              >
                {m.label}
              </button>
            )
          })}
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Period focus</p>
        <div className="flex flex-col gap-1 mb-3 shrink-0">
          {DIMENSION_PERIOD_SCOPES.map(s => {
            const active = periodScope === s.value
            return (
              <button
                key={s.value}
                type="button"
                onClick={() => onPeriodScopeChange(s.value)}
                className={`${PL_TOOLBAR_BTN} w-full text-left px-2.5 py-1.5 text-xs rounded-lg`}
                style={{
                  ...PL_TOOLBAR_BTN_STYLE,
                  background: active ? 'rgba(30,58,95,0.1)' : 'transparent',
                  color: active ? '#1E3A5F' : '#475569',
                  fontWeight: active ? 600 : 400,
                  border: `1px solid ${active ? 'rgba(30,58,95,0.2)' : '#E2E8F0'}`,
                }}
              >
                <span className="block">{s.label}</span>
                <span className="block text-[10px] font-normal mt-0.5" style={{ color: '#94A3B8' }}>{s.hint}</span>
              </button>
            )
          })}
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Break down by</p>
        <div className="flex flex-col gap-0.5 flex-1 min-h-0 overflow-y-auto">
          {SALES_ANALYTICS_DIMS.map(d => {
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
