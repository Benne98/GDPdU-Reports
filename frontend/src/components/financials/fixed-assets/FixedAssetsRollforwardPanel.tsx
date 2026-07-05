import { useCallback } from 'react'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import PlViewToggleButton from '../../financials/pl-two-view/PlViewToggleButton'
import type {
  FixedAssetDimension,
  FixedAssetReportDetailResponse,
  FixedAssetRollforwardResponse,
  FixedAssetSnapshotInfo,
} from '../../../lib/api'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../lib/exportXlsx'
import FixedAssetsColumnEditor from './FixedAssetsColumnEditor'
import FixedAssetsReportRollforward from './FixedAssetsReportRollforward'
import FixedAssetsRollforwardTable from './FixedAssetsRollforwardTable'
import type { FixedAssetsColumnDef } from './fixedAssetsColumnRegistry'
import { DIMENSION_OPTIONS } from './fixedAssetsColumnRegistry'
import { saveViewMode } from './fixedAssetsColumnRegistry'

type Props = {
  rollforward: FixedAssetRollforwardResponse | null
  reportDetail: FixedAssetReportDetailResponse | null
  loading: boolean
  error: string | null
  columns: FixedAssetsColumnDef[]
  dimensions: FixedAssetDimension[]
  snapshots: FixedAssetSnapshotInfo[]
  onColumnsChange: (cols: FixedAssetsColumnDef[]) => void
  onDimensionsChange: (dims: FixedAssetDimension[]) => void
  viewMode: 'report' | 'table'
  onViewModeChange: (mode: 'report' | 'table') => void
  exportName: string
}

export function visibleColKeys(
  data: FixedAssetRollforwardResponse,
  visibleYears: Set<number>,
): string[] {
  return data.col_keys.filter(k => {
    const y = parseInt(k.split('-')[0], 10)
    return visibleYears.has(y)
  })
}

export default function FixedAssetsRollforwardPanel({
  rollforward,
  reportDetail,
  loading,
  error,
  columns,
  dimensions,
  snapshots,
  onColumnsChange,
  onDimensionsChange,
  viewMode,
  onViewModeChange,
  exportName,
}: Props) {
  const visibleYears = new Set(columns.filter(c => c.visible && c.year).map(c => c.year as number))
  const colKeys = rollforward ? visibleColKeys(rollforward, visibleYears) : []

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (kind !== 'xlsx' || !rollforward) return
      const headers = ['kEUR', ...colKeys.map(k => rollforward.col_labels[k] ?? k)]
      const rows: XlsxRow[] = rollforward.rows
        .filter(r => r.row_kind !== 'subtotal')
        .map(r => ({
          label: r.label,
          values: colKeys.map(k => {
            const v = r.amounts?.[k]
            return v == null || Number.isNaN(v) ? '' : v
          }),
          kind: r.row_kind === 'total' || r.row_kind === 'section_header' ? 'subtotal' : 'data',
        }))
      await exportToXlsx({
        title: 'Fixed assets rollforward',
        subtitle: exportName,
        headers,
        rows,
        filename: `fixed-assets-rollforward-${todayStr()}.xlsx`,
      })
    },
    [rollforward, colKeys, exportName],
  )

  function toggleView() {
    const next = viewMode === 'report' ? 'table' : 'report'
    onViewModeChange(next)
    saveViewMode(next)
  }

  const dimLabel = dimensions
    .map(d => DIMENSION_OPTIONS.find(o => o.id === d)?.label ?? d)
    .join(' → ')

  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-slate-100">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Fixed assets rollforward</h3>
          <p className="text-xs text-slate-500">kEUR · {viewMode === 'table' ? dimLabel : 'Balance sheet line with asset detail'}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <PlViewToggleButton mode={viewMode} onChange={toggleView} disabled={loading} />
          {viewMode === 'table' && (
            <FixedAssetsColumnEditor
              columns={columns}
              dimensions={dimensions}
              snapshots={snapshots}
              onColumnsChange={onColumnsChange}
              onDimensionsChange={onDimensionsChange}
              disabled={loading}
            />
          )}
          <PlExportMenu disabled={!rollforward || loading} onExport={handleExport} formats={['xlsx']} />
        </div>
      </div>
      {error && <p className="text-sm text-red-600 px-4 pt-3">{error}</p>}
      {viewMode === 'report' && reportDetail && !error && (
        <FixedAssetsReportRollforward data={reportDetail} visibleColKeys={colKeys} loading={loading} />
      )}
      {viewMode === 'table' && (
        <div className="p-4">
          {loading && <p className="text-sm text-slate-500">Loading rollforward…</p>}
          {!loading && !error && rollforward && (
            <FixedAssetsRollforwardTable data={rollforward} visibleColKeys={colKeys} />
          )}
          {!loading && !error && !rollforward && (
            <p className="text-sm text-slate-500">No fixed-asset snapshots for this period.</p>
          )}
        </div>
      )}
      {viewMode === 'report' && !reportDetail && !loading && !error && (
        <p className="text-sm text-slate-500 px-4 py-6">No fixed-asset snapshots for this period.</p>
      )}
    </section>
  )
}
