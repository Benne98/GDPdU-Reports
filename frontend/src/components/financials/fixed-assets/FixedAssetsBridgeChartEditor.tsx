import { useMemo } from 'react'
import type { FixedAssetSnapshotInfo } from '../../../lib/api'
import SalesSideDrawer from '../../sales/SalesSideDrawer'
import type { BridgeChartSettings } from './fixedAssetsChartRegistry'
import { saveBridgeSettings } from './fixedAssetsChartRegistry'

const BRIDGE_DIMENSIONS = [
  { id: 'entity' as const, label: 'Entity' },
  { id: 'segment' as const, label: 'Business segment' },
]

type Props = {
  open?: boolean
  onClose?: () => void
  embedded?: boolean
  settings: BridgeChartSettings
  snapshots: FixedAssetSnapshotInfo[]
  onChange: (s: BridgeChartSettings) => void
}

export default function FixedAssetsBridgeChartEditor({
  open = false,
  onClose,
  embedded,
  settings,
  snapshots,
  onChange,
}: Props) {
  const snapshotDates = useMemo(
    () => [...snapshots].sort((a, b) => a.as_of_date.localeCompare(b.as_of_date)),
    [snapshots],
  )

  function patch(partial: Partial<BridgeChartSettings>) {
    const next = { ...settings, ...partial }
    onChange(next)
    saveBridgeSettings(next)
  }

  const body = (
    <>
      <div className="px-4 py-3 border-b border-slate-100">
        <h3 className="text-sm font-semibold text-slate-900">NBV bridge settings</h3>
        <p className="text-xs text-slate-500 mt-0.5">Opening/closing year-ends and optional dimension split</p>
      </div>
      <div className="p-4 space-y-5 overflow-y-auto max-h-[calc(100vh-8rem)]">
        <div>
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Bridge period</p>
          <label className="flex items-center gap-2 text-sm text-slate-700 mb-2">
            <span className="w-20 text-xs text-slate-500">Closing</span>
            <select
              className="flex-1 text-sm border border-slate-200 rounded-md px-2 py-1.5 bg-white"
              value={settings.closingDate}
              onChange={e => patch({ closingDate: e.target.value })}
            >
              {snapshotDates.map(s => (
                <option key={s.as_of_date} value={s.as_of_date}>{s.col_label}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <span className="w-20 text-xs text-slate-500">Opening</span>
            <select
              className="flex-1 text-sm border border-slate-200 rounded-md px-2 py-1.5 bg-white"
              value={settings.openingDate}
              onChange={e => patch({ openingDate: e.target.value })}
            >
              {snapshotDates.map(s => (
                <option key={s.as_of_date} value={s.as_of_date}>{s.col_label}</option>
              ))}
            </select>
          </label>
        </div>

        <div>
          <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Breakdown</p>
          <div className="flex flex-wrap gap-1.5 mb-3">
            {([
              ['consolidated', 'Consolidated'],
              ['dimension', 'By dimension'],
            ] as const).map(([id, lbl]) => (
              <button
                key={id}
                type="button"
                onClick={() => patch({ scopeMode: id })}
                className="h-7 px-2.5 rounded-lg text-xs font-medium border"
                style={{
                  background: settings.scopeMode === id ? '#1E3A5F' : '#F4F6F9',
                  color: settings.scopeMode === id ? '#fff' : '#1E3A5F',
                  borderColor: '#E2E8F0',
                }}
              >
                {lbl}
              </button>
            ))}
          </div>
          {settings.scopeMode === 'dimension' && (
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <span className="w-20 text-xs text-slate-500">Dimension</span>
              <select
                className="flex-1 text-sm border border-slate-200 rounded-md px-2 py-1.5 bg-white"
                value={settings.dimension}
                onChange={e => patch({ dimension: e.target.value as BridgeChartSettings['dimension'] })}
              >
                {BRIDGE_DIMENSIONS.map(o => (
                  <option key={o.id} value={o.id}>{o.label}</option>
                ))}
              </select>
            </label>
          )}
        </div>
      </div>
    </>
  )

  if (embedded) return body
  return (
    <SalesSideDrawer open={open} onClose={onClose ?? (() => {})}>
      {body}
    </SalesSideDrawer>
  )
}
