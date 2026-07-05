import { useCallback, useState } from 'react'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import PlViewToggleButton from '../../financials/pl-two-view/PlViewToggleButton'
import type { PersonnelAccountingResponse, PersonnelDimension, PersonnelLayout, PersonnelMovementsResponse } from '../../../lib/api'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../lib/exportXlsx'
import { fmtPct } from '../../../lib/fmt'
import PayrollAccountingTable, { buildDisplayCols } from './PayrollAccountingTable'
import PayrollAccountingReport from './PayrollAccountingReport'
import PayrollColumnEditor from './PayrollColumnEditor'
import type { PayrollColumnDef } from './payrollColumnRegistry'
import { DIMENSION_OPTIONS, saveViewMode } from './payrollColumnRegistry'

type Props = {
  data: PersonnelAccountingResponse | null
  movements?: PersonnelMovementsResponse | null
  loading: boolean
  error: string | null
  columns: PayrollColumnDef[]
  layout: PersonnelLayout
  columnDimension: PersonnelDimension
  rowDimensions: PersonnelDimension[]
  viewMode: 'report' | 'table'
  onColumnsChange: (cols: PayrollColumnDef[]) => void
  onLayoutChange: (l: PersonnelLayout) => void
  onColumnDimensionChange: (d: PersonnelDimension) => void
  onRowDimensionsChange: (d: PersonnelDimension[]) => void
  onViewModeChange: (mode: 'report' | 'table') => void
  exportName: string
}

function formatExportCell(value: number | null | undefined, unit: string): string | number {
  if (value == null || Number.isNaN(value)) return ''
  if (unit === 'pct') return fmtPct(value)
  if (unit === 'count') return Math.round(value)
  return value
}

export function visibleColKeys(data: PersonnelAccountingResponse, visibleDates: Set<string>): string[] {
  const keys = data.col_keys ?? data.col_dates ?? []
  return keys.filter(k => {
    const datePart = k.split('|')[0]
    return visibleDates.has(datePart)
  })
}

export default function PayrollAccountingPanel({
  data,
  movements: _movements,
  loading,
  error,
  columns,
  layout,
  columnDimension,
  rowDimensions,
  viewMode,
  onColumnsChange,
  onLayoutChange,
  onColumnDimensionChange,
  onRowDimensionsChange,
  onViewModeChange,
  exportName,
}: Props) {
  const [editorOpen, setEditorOpen] = useState(false)
  const visibleDates = new Set(
    columns.filter(c => c.kind === 'snapshot' && c.visible && c.snapshotDate).map(c => c.snapshotDate as string),
  )
  if (data?.anchor_date) visibleDates.add(data.anchor_date)
  const displayCols = data ? buildDisplayCols(data, columns, visibleDates) : []
  const colKeys = displayCols.filter(c => c.kind === 'snapshot').map(c => c.key)

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (kind !== 'xlsx' || !data) return
      const headers = ['EURk', ...colKeys.map(k => data.col_labels[k] ?? k)]
      const rows: XlsxRow[] = data.rows
        .filter(r => r.row_kind !== 'section_header' && r.row_kind !== 'kpi_header')
        .map(r => ({
          label: r.label,
          values: colKeys.map(k => formatExportCell(r.amounts?.[k], r.unit)),
          kind: r.row_kind === 'total' || r.row_kind === 'kpi' || r.row_kind === 'subtotal' ? 'subtotal' : 'data',
        }))
      await exportToXlsx({
        title: 'Payroll accounting',
        subtitle: exportName,
        headers,
        rows,
        filename: `payroll-accounting-${todayStr()}.xlsx`,
      })
    },
    [data, colKeys, exportName],
  )

  function toggleView() {
    const next = viewMode === 'report' ? 'table' : 'report'
    onViewModeChange(next)
    saveViewMode(next)
  }

  const divisionLabel = DIMENSION_OPTIONS.find(d => d.id === 'bereich')?.label ?? 'Division'

  const subtitle = viewMode === 'report'
    ? `EURk · by ${divisionLabel} · FY comparison`
    : layout === 'column_split'
      ? `EURk · column split by ${DIMENSION_OPTIONS.find(d => d.id === columnDimension)?.label ?? columnDimension}`
      : layout === 'row_hierarchy'
        ? `EURk · ${rowDimensions.map(d => DIMENSION_OPTIONS.find(o => o.id === d)?.label ?? d).join(' → ')}`
        : `EURk · by ${divisionLabel}`

  return (
    <section className={`rounded-xl border border-slate-200 bg-white shadow-sm ${viewMode === 'report' ? '' : 'overflow-hidden'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-slate-100">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Payroll accounting</h3>
          <p className="text-xs text-slate-500">{subtitle}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <PlViewToggleButton mode={viewMode} onChange={toggleView} disabled={loading} />
          {viewMode === 'table' && (
            <PayrollColumnEditor
              columns={columns}
              layout={layout}
              columnDimension={columnDimension}
              rowDimensions={rowDimensions}
              availableMetrics={data?.available_metrics}
              open={editorOpen}
              onOpenChange={setEditorOpen}
              onColumnsChange={onColumnsChange}
              onLayoutChange={onLayoutChange}
              onColumnDimensionChange={onColumnDimensionChange}
              onRowDimensionsChange={onRowDimensionsChange}
              disabled={loading}
            />
          )}
          <PlExportMenu disabled={!data || loading} onExport={handleExport} formats={['xlsx']} />
        </div>
      </div>
      <div className={viewMode === 'report' ? 'p-0' : 'p-4'}>
        {loading && <p className="text-sm text-slate-500 px-4 py-4">Loading payroll table…</p>}
        {error && <p className="text-sm text-red-600 px-4 py-4">{error}</p>}
        {!loading && !error && data && viewMode === 'report' && (
          <PayrollAccountingReport
            data={data}
            visibleColKeys={colKeys}
            loading={loading}
          />
        )}
        {!loading && !error && data && viewMode === 'table' && (
          <PayrollAccountingTable data={data} displayCols={displayCols} columns={columns} />
        )}
        {!loading && !error && !data && (
          <p className="text-sm text-slate-500">No personnel snapshots for this period.</p>
        )}
      </div>
    </section>
  )
}

export { saveViewMode as savePayrollViewMode }
