import { useState } from 'react'
import { Pencil, X } from 'lucide-react'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
  PL_TOOLBAR_ICON_BTN,
} from '../../financials/pl-two-view/plToolbarButton'
import { SALES_ANALYTICS_DIMS } from './salesChartRegistry'
import SalesChartEditorPopover, { useChartEditorAnchor } from './SalesChartEditorPopover'

export type GeoTrendGrain = 'year' | 'month' | 'week'

const GRAINS: { key: GeoTrendGrain; label: string }[] = [
  { key: 'year', label: 'Year' },
  { key: 'month', label: 'Month' },
  { key: 'week', label: 'Week' },
]

type Props = {
  grain: GeoTrendGrain
  dim: string
  onGrainChange: (grain: GeoTrendGrain) => void
  onDimChange: (dim: string) => void
  disabled?: boolean
}

export function geoTrendGrainLabel(grain: GeoTrendGrain): string {
  return GRAINS.find(g => g.key === grain)?.label ?? grain
}

export default function SalesGeoTrendEditor({
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
        title="Edit time range and dimension"
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
        width={288}
      >
        <div className="flex items-center justify-between mb-2 shrink-0">
          <span className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>Chart settings</span>
          <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100">
            <X size={14} />
          </button>
        </div>

        <p className="text-[10px] mb-1.5 font-medium shrink-0" style={{ color: '#64748B' }}>Time range</p>
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
