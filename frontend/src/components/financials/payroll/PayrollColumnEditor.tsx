import { Pencil } from 'lucide-react'
import { useMemo } from 'react'
import type { PersonnelDimension, PersonnelLayout, PersonnelMetricDef } from '../../../lib/api'
import {
  PL_TOOLBAR_BTN,
  PL_TOOLBAR_BTN_STYLE,
} from '../../financials/pl-two-view/plToolbarButton'
import SalesSideDrawer from '../../sales/SalesSideDrawer'
import type { PayrollColumnDef } from './payrollColumnRegistry'
import {
  breakdownFromLayout,
  DEFAULT_METRIC_IDS,
  layoutFromBreakdown,
  mergeMetricColumnsFromApi,
  saveColumnDimension,
  saveLayout,
  savePayrollColumns,
  saveRowDimensions,
  setDeltaColumnVisible,
  toggleColumnVisibility,
  type PayrollBreakdownPreset,
} from './payrollColumnRegistry'

type Props = {
  columns: PayrollColumnDef[]
  layout: PersonnelLayout
  columnDimension: PersonnelDimension
  rowDimensions: PersonnelDimension[]
  availableMetrics?: PersonnelMetricDef[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onColumnsChange: (cols: PayrollColumnDef[]) => void
  onLayoutChange: (l: PersonnelLayout) => void
  onColumnDimensionChange: (d: PersonnelDimension) => void
  onRowDimensionsChange: (d: PersonnelDimension[]) => void
  disabled?: boolean
}

const BREAKDOWN_OPTIONS: Array<{ id: PayrollBreakdownPreset; label: string }> = [
  { id: 'flat', label: 'By division (default)' },
  { id: 'rows_entity', label: 'Rows by entity' },
  { id: 'rows_org_unit', label: 'Rows by org unit' },
  { id: 'split_entity', label: 'Columns split by entity' },
]

export default function PayrollColumnEditor({
  columns,
  layout,
  columnDimension,
  rowDimensions,
  availableMetrics,
  open,
  onOpenChange,
  onColumnsChange,
  onLayoutChange,
  onColumnDimensionChange,
  onRowDimensionsChange,
  disabled,
}: Props) {
  const effectiveColumns = useMemo(
    () => (availableMetrics?.length ? mergeMetricColumnsFromApi(columns, availableMetrics) : columns),
    [columns, availableMetrics],
  )

  const snapCols = effectiveColumns.filter(c => c.kind === 'snapshot')
  const metricCols = effectiveColumns.filter(c => c.kind === 'metric')
  const deltaCol = effectiveColumns.find(c => c.kind === 'delta')

  const breakdown = breakdownFromLayout(layout, columnDimension, rowDimensions)

  function applyColumns(next: PayrollColumnDef[]) {
    onColumnsChange(next)
    savePayrollColumns(next)
  }

  function toggle(id: string) {
    applyColumns(toggleColumnVisibility(effectiveColumns, id))
  }

  function setBreakdown(preset: PayrollBreakdownPreset) {
    const next = layoutFromBreakdown(preset)
    onLayoutChange(next.layout)
    saveLayout(next.layout)
    onColumnDimensionChange(next.columnDimension)
    saveColumnDimension(next.columnDimension)
    onRowDimensionsChange(next.rowDimensions)
    saveRowDimensions(next.rowDimensions)
  }

  const metricsForList = metricCols.length
    ? metricCols
    : (availableMetrics ?? DEFAULT_METRIC_IDS.map(id => ({ id, label: id }))).map(m => ({
        id: `metric-${m.id}`,
        kind: 'metric' as const,
        label: m.label,
        metricId: m.id,
        visible: DEFAULT_METRIC_IDS.includes(m.id),
      }))

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onOpenChange(true)}
        className={`${PL_TOOLBAR_BTN} px-2`}
        style={{
          ...PL_TOOLBAR_BTN_STYLE,
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
        }}
        title="Edit table columns"
      >
        <Pencil size={12} strokeWidth={1.75} className="shrink-0" />
        <span>Columns</span>
      </button>
      <SalesSideDrawer open={open} onClose={() => onOpenChange(false)}>
        <div className="px-4 py-3 border-b border-slate-100">
          <h3 className="text-sm font-semibold text-slate-900">Table columns</h3>
          <p className="text-xs text-slate-500 mt-0.5">Years, metrics and layout — charts keep their own controls</p>
        </div>
        <div className="p-4 space-y-5 overflow-y-auto max-h-[calc(100vh-8rem)]">
          <div>
            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Fiscal years</p>
            <div className="space-y-2">
              {snapCols.map(col => (
                <label key={col.id} className="flex items-center gap-2 text-sm text-slate-700">
                  <input type="checkbox" checked={col.visible} onChange={() => toggle(col.id)} />
                  <span>{col.label}</span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Metrics</p>
            <div className="space-y-2">
              {metricsForList.map(col => (
                <label key={col.id} className="flex items-center gap-2 text-sm text-slate-700">
                  <input type="checkbox" checked={col.visible} onChange={() => toggle(col.id)} />
                  <span>{col.label}</span>
                </label>
              ))}
            </div>
          </div>

          {deltaCol && (
            <div>
              <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Delta columns</p>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={deltaCol.visible}
                  onChange={() => applyColumns(setDeltaColumnVisible(effectiveColumns, !deltaCol.visible))}
                />
                <span>{deltaCol.label}</span>
              </label>
            </div>
          )}

          <div>
            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-2">Breakdown</p>
            <select
              className="w-full text-sm border border-slate-200 rounded-md px-2 py-1.5 bg-white"
              value={breakdown}
              onChange={e => setBreakdown(e.target.value as PayrollBreakdownPreset)}
            >
              {BREAKDOWN_OPTIONS.map(o => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>
      </SalesSideDrawer>
    </>
  )
}
