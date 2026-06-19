import { useCallback, useEffect, useMemo, useState } from 'react'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import PlViewToggleButton from '../../financials/pl-two-view/PlViewToggleButton'
import { FIN_REPORT_SPLIT_GRID } from '../../financials/statement-two-view/finReportLayout'
import { exportFlatTablePptx } from '../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr } from '../../../lib/exportXlsx'
import { fmtChartKpi } from '../../../lib/fmt'
import { stripLegalForm } from '../../../lib/stripLegalForm'
import type { SalesTopEntitiesColLabels, SalesTopEntity } from '../../../lib/api'
import SalesColumnEditor from './SalesColumnEditor'
import {
  ENTITY_REPORT_FIELDS,
  ENTITY_TABLE_GROUPS_MONTH,
  ENTITY_TABLE_GROUPS_WEEK,
  entityColumns,
  entityReportColumnsMonth,
  entityReportColumnsWeek,
  entityTableColumnsMonth,
  entityTableColumnsWeek,
  loadSalesColumns,
  normalizeEntityColumns,
  reconcileSalesColumns,
  saveSalesColumns,
} from './salesColumnRegistry'
import { numericFieldValue, salesDataColumns } from './salesFinTableRender'
import type { SalesColumnDef, SalesViewMode } from './salesTableTypes'
import {
  buildSegmentedCustomerRows,
  filterNonZeroEntities,
  type CustomerSortKey,
  type TopCustomerDisplayRow,
} from './topCustomersSegment'
import { renderFinStyleTable } from './salesFinTableRender'
import { SALES_TABLE_HEADER_BG, SALES_TABLE_HEADER_HIGHLIGHT_BG } from './salesFinTableCells'
import { isSalesHighlightColumn } from './salesColumnKinds'

type Props = {
  title: string
  subtitle: string
  tableId: string
  rows: SalesTopEntity[]
  colLabels: SalesTopEntitiesColLabels
  loading: boolean
  bullets: string[]
  rankBy?: CustomerSortKey
  onRankByChange?: (r: CustomerSortKey) => void
  exportName: string
  extended?: boolean
  finTableStyle?: boolean
  periodGrain?: 'month' | 'week'
  /** Column header for name (Customer / Supplier). */
  partnerLabel?: string
  /** Strip legal forms from names (customers). */
  stripLegalNames?: boolean
  /** Shown in subtitle when extended (e.g. customers / suppliers). */
  entityKindLabel?: string
  /** Optional intro paragraph shown in report view before bullets. */
  reportIntro?: string
}

function viewStorageKey(tableId: string): string {
  return `finssentials.sales.${tableId}.viewMode.v1`
}

function loadViewMode(tableId: string): SalesViewMode {
  try {
    const v = localStorage.getItem(viewStorageKey(tableId))
    if (v === 'table' || v === 'report') return v
  } catch { /* ignore */ }
  return 'report'
}

const NUMERIC_FIELDS = new Set([
  'cm', 'pm', 'py_cm', 'ytd', 'ytd_py', 'mtd', 'mtd_py', 'mtd_pm',
  'delta_cm_pm', 'delta_cm_py', 'delta_ytd', 'delta_mtd', 'ytd_plan', 'plan_cm', 'coverage',
])

function cellValue(row: SalesTopEntity | Record<string, number | null>, field: string): string {
  if (field === 'name') return '—'
  if (field === 'coverage') {
    const v = (row as SalesTopEntity).coverage ?? (row as Record<string, number | null>).coverage
    return v != null ? `${Number(v).toFixed(1)}%` : '—'
  }
  const n = numericFieldValue(row, field)
  if (n != null) return fmtChartKpi(n)
  return '—'
}

function displayName(name: string, strip: boolean): string {
  return strip ? stripLegalForm(name) : name
}

export default function SalesTopEntitiesSection({
  title,
  subtitle,
  tableId,
  rows,
  colLabels,
  loading,
  bullets,
  rankBy = 'cm',
  onRankByChange,
  exportName,
  extended = false,
  finTableStyle = false,
  periodGrain = 'month',
  partnerLabel = 'Customer',
  stripLegalNames = false,
  entityKindLabel = 'customers',
  reportIntro,
}: Props) {
  const [viewMode, setViewMode] = useState<SalesViewMode>(() => loadViewMode(tableId))
  const [visible, setVisible] = useState(10)

  const catalog = useMemo(() => {
    if (!extended && !finTableStyle) return entityColumns(colLabels)
    return periodGrain === 'week'
      ? entityTableColumnsWeek(colLabels, partnerLabel)
      : entityTableColumnsMonth(colLabels, partnerLabel)
  }, [colLabels, extended, finTableStyle, periodGrain, partnerLabel])

  const finReportColumns = useMemo(
    () => periodGrain === 'week'
      ? entityReportColumnsWeek(colLabels, partnerLabel)
      : entityReportColumnsMonth(colLabels, partnerLabel),
    [colLabels, periodGrain, partnerLabel],
  )

  const columnGroups = periodGrain === 'week' ? ENTITY_TABLE_GROUPS_WEEK : ENTITY_TABLE_GROUPS_MONTH

  const [columns, setColumns] = useState<SalesColumnDef[]>(() =>
    normalizeEntityColumns(loadSalesColumns(tableId, catalog), catalog),
  )

  useEffect(() => {
    setColumns(prev => normalizeEntityColumns(reconcileSalesColumns(prev, catalog), catalog))
  }, [catalog])

  useEffect(() => {
    try {
      localStorage.setItem(viewStorageKey(tableId), viewMode)
    } catch { /* ignore */ }
  }, [viewMode, tableId])

  const reportColumns = useMemo(() => {
    if (finTableStyle || extended) return finReportColumns
    return catalog.filter(c => (ENTITY_REPORT_FIELDS as readonly string[]).includes(c.field))
  }, [finTableStyle, extended, finReportColumns, catalog])

  const tableColumns = columns

  const activeRows = useMemo(() => filterNonZeroEntities(rows), [rows])

  const numericFields = useMemo(() => {
    const fields = new Set<string>()
    for (const c of reportColumns) {
      if (NUMERIC_FIELDS.has(c.field)) fields.add(c.field)
    }
    if (extended || finTableStyle) {
      for (const c of tableColumns) {
        if (NUMERIC_FIELDS.has(c.field)) fields.add(c.field)
      }
    }
    return [...fields]
  }, [reportColumns, tableColumns, extended, finTableStyle])

  const segmentedRows: TopCustomerDisplayRow[] = useMemo(() => {
    if (!extended) return []
    const sortKey: CustomerSortKey = periodGrain === 'week' && rankBy === 'ytd' ? 'mtd' : rankBy
    return buildSegmentedCustomerRows(activeRows, sortKey, numericFields, periodGrain)
  }, [activeRows, extended, rankBy, numericFields, periodGrain])

  const tableData: TopCustomerDisplayRow[] = useMemo(() => {
    if (extended) return segmentedRows
    const limit = viewMode === 'report' ? 10 : visible
    return activeRows.slice(0, limit).map(row => ({ kind: 'customer' as const, row }))
  }, [extended, segmentedRows, activeRows, viewMode, visible])

  const nameForRow = useCallback(
    (name: string) => displayName(name, stripLegalNames),
    [stripLegalNames],
  )

  const handleColumnsChange = useCallback((cols: SalesColumnDef[]) => {
    const next = normalizeEntityColumns(cols, catalog)
    setColumns(next)
    saveSalesColumns(tableId, next)
  }, [tableId, catalog])

  const sortOptions = useMemo((): { key: CustomerSortKey; label: string }[] => {
    if (periodGrain === 'week') {
      return [
        { key: 'cm', label: colLabels.cm },
        { key: 'mtd', label: colLabels.mtd ?? 'MTD' },
      ]
    }
    return [
      { key: 'cm', label: colLabels.cm },
      { key: 'ytd', label: colLabels.ytd },
    ]
  }, [periodGrain, colLabels])

  async function handleExport(kind: PlExportKind) {
    const exportCols = extended ? tableColumns : columns
    const flatRows = extended
      ? buildSegmentedCustomerRows(
          activeRows,
          periodGrain === 'week' && rankBy === 'ytd' ? 'mtd' : rankBy,
          exportCols.map(c => c.field).filter(f => NUMERIC_FIELDS.has(f)),
          periodGrain,
        )
      : activeRows.map(row => ({ kind: 'customer' as const, row }))

    const headers = ['#', ...exportCols.map(c => c.label)]
    const xlsxRows = flatRows.map(dr => {
      if (dr.kind === 'subtotal' || dr.kind === 'total') {
        const label = dr.kind === 'total' ? 'Total' : dr.label
        return {
          label,
          values: [
            '',
            label,
            ...exportCols.slice(1).map(c => cellValue(dr.totals, c.field)),
          ],
          kind: 'data' as const,
        }
      }
      return {
        label: String(dr.row.rank),
        values: [
          String(dr.row.rank),
          ...exportCols.map(c =>
            c.field === 'name'
              ? displayName(dr.row.name, extended)
              : cellValue(dr.row, c.field),
          ),
        ],
        kind: 'data' as const,
      }
    })

    const base = `${exportName}_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: title,
        tableHeading: title,
        breadcrumbCurrent: 'Profitability',
        breadcrumbParent: 'Sales',
        footerRight: subtitle,
        headers,
        rows: xlsxRows,
      })
      return
    }
    await exportToXlsx({ title, subtitle, headers, rows: xlsxRows, filename: `${base}.xlsx` })
  }

  function renderExtendedTable(cols: SalesColumnDef[], data: TopCustomerDisplayRow[]) {
    const dataCols = salesDataColumns(cols)
    const nameCol = cols.find(c => c.field === 'name')
    const partnerHdr = nameCol?.label ?? partnerLabel

    return (
      <table className="w-full text-xs">
        <thead
          className="sticky top-0 z-20"
          style={{ background: SALES_TABLE_HEADER_BG, boxShadow: '0 1px 0 #E2E8F0' }}
        >
          <tr>
            <th
              className="px-2 py-1.5 text-left font-semibold w-8"
              style={{ color: '#475569', background: SALES_TABLE_HEADER_BG }}
            >
              #
            </th>
            <th
              className="px-2 py-1.5 text-left font-semibold min-w-[9rem]"
              style={{ color: '#475569', background: SALES_TABLE_HEADER_BG }}
            >
              {partnerHdr}
            </th>
            {dataCols.map(c => {
              const highlighted = isSalesHighlightColumn(c.field, periodGrain)
              return (
                <th
                  key={c.id}
                  className="px-2 py-1.5 font-semibold whitespace-nowrap text-right"
                  style={{
                    color: '#475569',
                    background: highlighted ? SALES_TABLE_HEADER_HIGHLIGHT_BG : SALES_TABLE_HEADER_BG,
                  }}
                >
                  {c.label}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {data.map((dr, i) => {
            if (dr.kind === 'subtotal' || dr.kind === 'total') {
              const label = dr.kind === 'total' ? 'Total' : dr.label
              return (
                <tr
                  key={dr.kind === 'total' ? `total-${i}` : `sub-${dr.label}-${i}`}
                  style={{
                    background: '#F4F6F9',
                    borderTop: dr.kind === 'total' ? '2px solid #E2E8F0' : '1px solid #E2E8F0',
                  }}
                >
                  <td className="px-2 py-1.5" />
                  <td className="px-2 py-1.5 font-semibold text-left" style={{ color: '#1E3A5F' }}>
                    {label}
                  </td>
                  {dataCols.map(c => (
                    <td
                      key={c.id}
                      className="px-2 py-1.5 text-right tabular-nums font-semibold"
                      style={{ color: '#1E3A5F' }}
                    >
                      {cellValue(dr.totals, c.field)}
                    </td>
                  ))}
                </tr>
              )
            }
            const r = dr.row
            return (
              <tr key={`${r.rank}-${r.name}`} style={{ borderBottom: '1px solid #F8FAFC' }}>
                <td className="px-2 py-1.5 tabular-nums" style={{ color: '#64748B' }}>{r.rank}</td>
                <td
                  className="px-2 py-1.5 max-w-[12rem] font-medium text-left truncate"
                  style={{ color: '#334155' }}
                  title={displayName(r.name, stripLegalNames)}
                >
                  {displayName(r.name, stripLegalNames)}
                </td>
                {dataCols.map(c => (
                  <td
                    key={c.id}
                    className={`px-2 py-1.5 text-right tabular-nums ${c.field === 'cm' || c.field === 'ytd' || c.field === 'mtd' ? 'font-semibold' : ''}`}
                    style={{ color: '#1E3A5F' }}
                  >
                    {cellValue(r, c.field)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    )
  }

  function renderLegacyTable(cols: SalesColumnDef[], data: SalesTopEntity[]) {
    return (
      <table className="w-full text-xs">
        <thead>
          <tr style={{ background: '#F8FAFC' }}>
            <th className="px-2 py-1.5 text-left font-semibold w-8" style={{ color: '#475569' }}>#</th>
            {cols.map(c => (
              <th
                key={c.id}
                className={`px-2 py-1.5 font-semibold ${c.field === 'name' ? 'text-left' : 'text-right'}`}
                style={{ color: '#475569' }}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map(r => (
            <tr key={r.rank} style={{ borderBottom: '1px solid #F8FAFC' }}>
              <td className="px-2 py-1.5 tabular-nums" style={{ color: '#64748B' }}>{r.rank}</td>
              {cols.map(c => (
                <td
                  key={c.id}
                  className={`px-2 py-1.5 ${c.field === 'name' ? 'font-medium text-left' : 'text-right tabular-nums font-semibold'}`}
                  style={{ color: c.field === 'name' ? '#334155' : '#1E3A5F' }}
                >
                  {c.field === 'name' ? r.name : cellValue(r, c.field)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  const scrollMaxH = extended || finTableStyle
    ? (viewMode === 'report' ? 480 : 520)
    : (viewMode === 'report' ? 360 : 420)

  function renderTable(cols: SalesColumnDef[]) {
    if (finTableStyle) {
      return renderFinStyleTable({
        cols,
        data: tableData,
        periodGrain,
        nameForRow,
      })
    }
    if (extended) {
      return renderExtendedTable(cols, segmentedRows)
    }
    const legacyData = activeRows.slice(0, viewMode === 'report' ? 10 : visible)
    return renderLegacyTable(cols, legacyData)
  }

  return (
    <div className="rounded-xl" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}>
      <div className="px-5 pt-4 pb-3 border-b flex justify-between gap-3 flex-wrap items-start" style={{ borderColor: '#F1F5F9' }}>
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>{title}</h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            {extended
              ? (viewMode === 'report'
                ? `All ${entityKindLabel} by cumulative tiers (30 / 60 / 80 %) — kEUR invoiced`
                : `Full ${entityKindLabel} detail — scroll horizontally if needed`)
              : finTableStyle
                ? (viewMode === 'report'
                  ? 'Summary — Income Statement layout'
                  : 'Full table — Income Statement layout')
                : (viewMode === 'report'
                  ? 'Summary table and commentary — kEUR invoiced'
                  : 'Full table — configure columns with the pencil (table builder)')}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          {onRankByChange && (
            <div className="flex rounded-lg overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
              {sortOptions.map(opt => (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => onRankByChange(opt.key)}
                  className="px-2 py-1 text-xs font-medium"
                  style={{
                    background: rankBy === opt.key ? 'rgba(30,58,95,0.1)' : '#FFF',
                    color: rankBy === opt.key ? '#1E3A5F' : '#64748B',
                  }}
                >
                  Sort: {opt.label}
                </button>
              ))}
            </div>
          )}
          <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
          {viewMode === 'table' && (
            <SalesColumnEditor
              tableId={tableId}
              catalog={catalog}
              columns={columns}
              groups={extended || finTableStyle ? columnGroups : undefined}
              onChange={handleColumnsChange}
              disabled={loading}
            />
          )}
          <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!activeRows.length || loading} />
        </div>
      </div>

      {loading ? (
        <div className="p-8 text-center text-xs" style={{ color: '#94A3B8' }}>Loading…</div>
      ) : viewMode === 'report' ? (
        <div className={`p-4 ${extended ? FIN_REPORT_SPLIT_GRID : FIN_REPORT_SPLIT_GRID} min-h-[320px]`}>
          <div className="overflow-auto min-h-[280px]" style={{ maxHeight: scrollMaxH }}>
            {activeRows.length ? renderTable(reportColumns) : (
              <p className="text-xs py-8 text-center" style={{ color: '#94A3B8' }}>No data for this period</p>
            )}
          </div>
          <div className="pt-1 lg:pt-0">
            {reportIntro && (
              <p className="text-xs leading-relaxed mb-3" style={{ color: '#475569' }}>
                {reportIntro}
              </p>
            )}
            <ul className="space-y-2 text-xs" style={{ color: '#334155' }}>
            {bullets.map((b, i) => (
              <li key={i} className="flex gap-2">
                <span className="font-semibold tabular-nums shrink-0" style={{ color: '#1E3A5F' }}>{i + 1}.</span>
                <span>{b}</span>
              </li>
            ))}
            </ul>
          </div>
        </div>
      ) : (
        <div className="p-4">
          <div className="overflow-auto min-h-[280px]" style={{ maxHeight: scrollMaxH }}>
            {activeRows.length ? renderTable(tableColumns) : (
              <p className="text-xs py-8 text-center" style={{ color: '#94A3B8' }}>No data for this period</p>
            )}
          </div>
          {!extended && activeRows.length > visible && (
            <button
              type="button"
              className="w-full mt-2 text-xs py-1.5 rounded-lg"
              style={{ background: '#F4F6F9', color: '#475569' }}
              onClick={() => setVisible(v => v + 10)}
            >
              Show 10 more
            </button>
          )}
        </div>
      )}
    </div>
  )
}
