import React, { useMemo, useState } from 'react'
import { Filter } from 'lucide-react'
import type { SalesBreakdownResponse } from '../../../lib/api'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import { exportFlatTablePptx } from '../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr } from '../../../lib/exportXlsx'
import { FIN_TABLE_CELL_CLASS } from '../../financials/statement-two-view/finReportLayout'
import { SALES_TABLE_HEADER_BG } from './salesFinTableCells'
import SalesBreakdownEditor from './SalesBreakdownEditor'
import type { BreakdownColumnDef, BreakdownDimConfig, BreakdownMiscConfig } from './salesBreakdownRegistry'
import {
  breakdownTitle,
  columnsForBlock,
  defaultBreakdownColumns,
  filterNonZeroBreakdownRows,
  visibleBlocks,
} from './salesBreakdownRegistry'
import {
  buildBreakdownHierarchy,
  l1GroupMetrics,
  l2GroupMetrics,
  type L1Group,
  type L2Group,
  type L3Leaf,
} from './salesBreakdownHierarchy'
import {
  BreakdownBlockHeader,
  BreakdownDataCells,
  BreakdownSubHeader,
  computeBreakdownDeltaMax,
  L1_ROW_STYLE,
  L2_ROW_STYLE,
  type BreakdownMetrics,
} from './salesBreakdownRender'

/** Same inner padding as Top Suppliers table body (`p-4`). */
const TABLE_BODY_PAD = 'p-4'

type Props = {
  data: SalesBreakdownResponse | null
  loading: boolean
  error?: string | null
  dims: BreakdownDimConfig
  misc: BreakdownMiscConfig
  columns: BreakdownColumnDef[]
  onDimsChange: (dims: BreakdownDimConfig) => void
  onMiscChange: (misc: BreakdownMiscConfig) => void
  onColumnsChange: (cols: BreakdownColumnDef[]) => void
}

type SortDirection = 'asc' | 'desc'
type SortState = { field: string; direction: SortDirection } | null

function labelCell(label: string, level: 1 | 2 | 3, bold: boolean) {
  const pad = level === 3 ? 'pl-7' : level === 2 ? 'pl-4' : 'pl-1.5'
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-left max-w-[14rem] truncate whitespace-nowrap ${pad}`}
      style={{
        color: level === 1 ? '#1E3A5F' : bold ? '#1E3A5F' : '#334155',
        fontSize: '0.68rem',
        fontWeight: bold || level === 1 ? 600 : 500,
      }}
      title={label}
    >
      {label}
    </td>
  )
}

function MetricRow({
  label,
  level,
  metrics,
  columns,
  maxAbsByField,
  style,
  bold = false,
}: {
  label: string
  level: 1 | 2 | 3
  metrics: BreakdownMetrics
  columns: BreakdownColumnDef[]
  maxAbsByField: Record<string, number>
  style?: React.CSSProperties
  bold?: boolean
}) {
  const blocks = visibleBlocks(columns)
  return (
    <tr style={style}>
      {labelCell(label, level, bold)}
      {blocks.map(block => {
        const cols = columnsForBlock(columns, block)
        return (
          <BreakdownDataCells
            key={block}
            metrics={metrics}
            columns={cols}
            maxAbsByField={maxAbsByField}
            bold={bold}
          />
        )
      })}
    </tr>
  )
}

function HierarchyBody({
  hierarchy,
  columns,
  maxAbsByField,
}: {
  hierarchy: L1Group[]
  columns: BreakdownColumnDef[]
  maxAbsByField: Record<string, number>
}) {
  return (
    <>
      {hierarchy.map(g1 => (
        <React.Fragment key={g1.label}>
          <MetricRow
            label={g1.label}
            level={1}
            metrics={l1GroupMetrics(g1)}
            columns={columns}
            maxAbsByField={maxAbsByField}
            style={L1_ROW_STYLE}
            bold
          />
          {g1.l2Groups.map(g2 => (
            <React.Fragment key={`${g1.label}-${g2.label}`}>
              <MetricRow
                label={g2.label}
                level={2}
                metrics={l2GroupMetrics(g2)}
                columns={columns}
                maxAbsByField={maxAbsByField}
                style={L2_ROW_STYLE}
                bold
              />
              {g2.leaves.map(leaf => (
                <MetricRow
                  key={`${g2.label}-${leaf.label}`}
                  label={leaf.label}
                  level={3}
                  metrics={leaf.row}
                  columns={columns}
                  maxAbsByField={maxAbsByField}
                />
              ))}
            </React.Fragment>
          ))}
          {g1.leaves.map(leaf => (
            <MetricRow
              key={`${g1.label}-${leaf.label}`}
              label={leaf.label}
              level={3}
              metrics={leaf.row}
              columns={columns}
              maxAbsByField={maxAbsByField}
            />
          ))}
        </React.Fragment>
      ))}
    </>
  )
}

export default function SalesBreakdownTable({
  data,
  loading,
  error,
  dims,
  misc,
  columns,
  onDimsChange,
  onMiscChange,
  onColumnsChange,
}: Props) {
  const [sortOpen, setSortOpen] = useState(false)
  const [sortState, setSortState] = useState<SortState>(null)
  const rows = useMemo(
    () => filterNonZeroBreakdownRows(data?.rows ?? []),
    [data?.rows],
  )
  const hasMid = data?.has_mid_level ?? false
  const colLabels = data?.col_labels ?? { pm: 'PM', cm: 'CM', plan_cm: 'Plan', delta_cm_pm: 'Δ' }
  const dimLabels = data?.dim_labels ?? { top: 'Region', bottom: 'Entity' }
  const title = breakdownTitle(dims, dimLabels)
  const effectiveColumns = columns.length > 0 ? columns : defaultBreakdownColumns(colLabels)
  const blocks = visibleBlocks(effectiveColumns)
  const hierarchy = useMemo(
    () => buildBreakdownHierarchy(rows, hasMid, misc, dims.dim_bottom),
    [rows, hasMid, misc, dims.dim_bottom],
  )
  const sortOptions = useMemo(() => {
    const opts = [
      { field: '__label__', label: 'Label' },
      ...blocks.flatMap(block =>
        columnsForBlock(effectiveColumns, block).map(col => ({
          field: col.field,
          label: `${BLOCK_SHORT[col.block] ?? col.block} ${col.label}`,
        })),
      ),
    ]
    const seen = new Set<string>()
    return opts.filter(o => {
      if (seen.has(o.field)) return false
      seen.add(o.field)
      return true
    })
  }, [blocks, effectiveColumns])

  const sortedHierarchy = useMemo(() => {
    if (!sortState) return hierarchy
    const sortByLabel = sortState.field === '__label__'
    const dir = sortState.direction === 'asc' ? 1 : -1
    const metricValue = (v: Record<string, number | null | undefined>) => Number(v[sortState.field] ?? 0)
    const cmpText = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: 'base' }) * dir
    const cmpNum = (a: number, b: number) => (a - b) * dir
    const sortLeaves = (leaves: L3Leaf[]) => {
      const copy = [...leaves]
      copy.sort((a, b) => (
        sortByLabel ? cmpText(a.label, b.label) : cmpNum(metricValue(a.row as unknown as Record<string, number | null | undefined>), metricValue(b.row as unknown as Record<string, number | null | undefined>))
      ))
      return copy
    }
    const sortL2 = (groups: L2Group[]) => {
      const copy = groups.map(g => ({ ...g, leaves: sortLeaves(g.leaves) }))
      copy.sort((a, b) => (
        sortByLabel ? cmpText(a.label, b.label) : cmpNum(metricValue(l2GroupMetrics(a)), metricValue(l2GroupMetrics(b)))
      ))
      return copy
    }
    const copy = hierarchy.map(g => ({
      ...g,
      l2Groups: sortL2(g.l2Groups),
      leaves: sortLeaves(g.leaves),
    }))
    copy.sort((a, b) => (
      sortByLabel ? cmpText(a.label, b.label) : cmpNum(metricValue(l1GroupMetrics(a)), metricValue(l1GroupMetrics(b)))
    ))
    return copy
  }, [hierarchy, sortState])

  const maxAbsByField = useMemo(() => computeBreakdownDeltaMax(rows, effectiveColumns), [rows, effectiveColumns])

  async function handleExport(kind: PlExportKind) {
    const exportRows: { label: string; values: (number | null)[]; kind: 'data'; kpiCols?: number[] }[] = []
    const flatCols = blocks.flatMap(b => columnsForBlock(effectiveColumns, b))
    const headers = [dimLabels.bottom, ...flatCols.map(c => `${BLOCK_SHORT[c.block] ?? c.block} ${c.label}`)]

    function pushRow(label: string, metrics: BreakdownMetrics) {
      exportRows.push({
        label,
        values: flatCols.map(c => {
          const v = (metrics as Record<string, number>)[c.field]
          if (c.isPct) return v != null ? +Number(v).toFixed(1) : null
          return v ?? null
        }),
        kind: 'data',
        kpiCols: flatCols.map((c, i) => (c.isPct ? i + 1 : -1)).filter(i => i >= 0),
      })
    }

    for (const g1 of sortedHierarchy) {
      pushRow(g1.label, l1GroupMetrics(g1))
      for (const g2 of g1.l2Groups) {
        pushRow(`  ${g2.label}`, l2GroupMetrics(g2))
        for (const leaf of g2.leaves) {
          pushRow(`    ${leaf.label}`, leaf.row)
        }
      }
      for (const leaf of g1.leaves) {
        pushRow(`  ${leaf.label}`, leaf.row)
      }
    }

    const base = `Sales_Breakdown_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: `Breakdown by ${title}`,
        tableHeading: `Breakdown by ${title}`,
        breadcrumbCurrent: 'Profitability',
        breadcrumbParent: 'Sales',
        footerRight: 'Gross Sales · GP · GM — kEUR',
        headers,
        columnKinds: flatCols.map(c =>
          c.sub === 'delta' ? 'delta' : c.sub === 'cm' ? 'cm' : '',
        ),
        rows: exportRows,
      })
      return
    }
    await exportToXlsx({
      title: `Breakdown by ${title}`,
      subtitle: 'Gross Sales · GP · GM — kEUR',
      headers,
      rows: exportRows,
      filename: `${base}.xlsx`,
    })
  }

  const labelHeader = hasMid
    ? `${dimLabels.top} / ${dimLabels.mid} / ${dimLabels.bottom}`
    : `${dimLabels.top} / ${dimLabels.bottom}`

  return (
    <div
      className="rounded-xl flex flex-col"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
    >
      <div className="px-5 pt-4 pb-3 border-b flex items-center gap-2 flex-wrap" style={{ borderColor: '#F1F5F9' }}>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>
            Breakdown by {title}
          </h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>Gross Sales · GP · GM — kEUR</p>
          {error && (
            <p className="text-xs mt-1" style={{ color: '#DC2626' }}>{error}</p>
          )}
        </div>
        <div className="flex items-center gap-2 ml-auto shrink-0">
          <div className="relative">
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium hover:bg-slate-50"
              style={{ borderColor: '#E2E8F0', color: '#334155' }}
              onClick={() => setSortOpen(v => !v)}
              disabled={loading || rows.length === 0}
              title="Filter and sort rows"
            >
              <Filter size={14} />
              Filter
            </button>
            {sortOpen && (
              <div
                className="absolute right-0 mt-2 w-64 rounded-lg border bg-white p-3 shadow-lg z-20"
                style={{ borderColor: '#E2E8F0' }}
              >
                <p className="text-[0.65rem] font-semibold uppercase tracking-wide mb-2" style={{ color: '#64748B' }}>
                  Sort rows
                </p>
                <label className="text-[0.68rem]" style={{ color: '#475569' }}>
                  Column
                </label>
                <select
                  className="mt-1 w-full rounded border px-2 py-1.5 text-xs"
                  style={{ borderColor: '#CBD5E1' }}
                  value={sortState?.field ?? ''}
                  onChange={e => {
                    const field = e.target.value
                    if (!field) {
                      setSortState(null)
                      return
                    }
                    setSortState(prev => ({ field, direction: prev?.direction ?? 'desc' }))
                  }}
                >
                  <option value="">Default (current grouping)</option>
                  {sortOptions.map(opt => (
                    <option key={opt.field} value={opt.field}>{opt.label}</option>
                  ))}
                </select>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    className="rounded border px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
                    style={{ borderColor: '#CBD5E1', color: '#334155' }}
                    disabled={!sortState}
                    onClick={() => setSortState(prev => (prev ? { ...prev, direction: 'asc' } : prev))}
                  >
                    Asc
                  </button>
                  <button
                    type="button"
                    className="rounded border px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50"
                    style={{ borderColor: '#CBD5E1', color: '#334155' }}
                    disabled={!sortState}
                    onClick={() => setSortState(prev => (prev ? { ...prev, direction: 'desc' } : prev))}
                  >
                    Desc
                  </button>
                </div>
                <button
                  type="button"
                  className="mt-2 w-full rounded border px-2 py-1 text-xs hover:bg-slate-50"
                  style={{ borderColor: '#CBD5E1', color: '#475569' }}
                  onClick={() => {
                    setSortState(null)
                    setSortOpen(false)
                  }}
                >
                  Reset
                </button>
              </div>
            )}
          </div>
          <SalesBreakdownEditor
            dims={dims}
            misc={misc}
            columns={effectiveColumns}
            colLabels={colLabels}
            onDimsChange={onDimsChange}
            onMiscChange={onMiscChange}
            onColumnsChange={onColumnsChange}
            disabled={loading}
          />
          <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={rows.length === 0} />
        </div>
      </div>

      <div className={TABLE_BODY_PAD}>
        <div className="overflow-auto min-h-[200px]" style={{ maxHeight: 480 }}>
          {loading ? (
            <div className="flex items-center justify-center py-8 text-xs" style={{ color: '#94A3B8' }}>Loading…</div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center gap-1">
              <span className="text-xs" style={{ color: '#94A3B8' }}>No data for this period and filter selection.</span>
              {error && (
                <span className="text-xs" style={{ color: '#DC2626' }}>{error}</span>
              )}
            </div>
          ) : (
            <table className="w-full border-collapse">
              <thead className="sticky top-0 z-10" style={{ background: SALES_TABLE_HEADER_BG }}>
                <tr>
                  <th
                    rowSpan={2}
                    className={`${FIN_TABLE_CELL_CLASS} text-left font-semibold text-xs align-bottom`}
                    style={{ color: '#475569', background: SALES_TABLE_HEADER_BG, verticalAlign: 'bottom' }}
                  >
                    {labelHeader}
                  </th>
                  {blocks.map(block => (
                    <BreakdownBlockHeader
                      key={block}
                      block={block}
                      colSpan={columnsForBlock(effectiveColumns, block).length}
                    />
                  ))}
                </tr>
                <tr>
                  {blocks.flatMap(block =>
                    columnsForBlock(effectiveColumns, block).map(col => (
                      <BreakdownSubHeader
                        key={col.id}
                        label={col.label}
                        highlighted={col.sub === 'cm'}
                      />
                    )),
                  )}
                </tr>
              </thead>
              <tbody>
                <HierarchyBody
                  hierarchy={sortedHierarchy}
                  columns={effectiveColumns}
                  maxAbsByField={maxAbsByField}
                />
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

const BLOCK_SHORT: Record<string, string> = {
  gross_sales: 'GS',
  gross_profit: 'GP',
  gross_margin: 'GM',
}
