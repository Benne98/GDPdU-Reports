import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { ChevronRight, Filter } from 'lucide-react'
import type { ReceivablesHierarchyBreakdownRow } from '../../../lib/api'
import { FIN_TABLE_CELL_CLASS } from '../../financials/statement-two-view/finReportLayout'
import { L1_ROW_STYLE, L2_ROW_STYLE } from '../analytics/salesBreakdownRender'
import { SalesDeltaCell, SalesFinHeader, SalesValCell, SALES_TABLE_HEADER_BG } from '../analytics/salesFinTableCells'
import type { AgingBreakdownColumnDef, ReceivablesHierarchyDim } from './shared/receivablesDimensionBreakdownConfig'
import { receivablesBreakdownLabelHeader } from './shared/receivablesDimensionBreakdownConfig'
import {
  buildReceivablesHierarchy,
  defaultExpandedHierarchyKeys,
  flattenVisibleHierarchyNodes,
  type ReceivablesHierarchyNode,
} from './shared/receivablesDimensionHierarchy'

type SortState = { id: string; dir: 'asc' | 'desc' }

type ColumnFilters = {
  text: Record<string, string>
  select: Record<string, string[] | undefined>
}

type Props = {
  rows: ReceivablesHierarchyBreakdownRow[]
  hierarchy: ReceivablesHierarchyDim[]
  view: 'buckets' | 'due_overdue'
  columns: AgingBreakdownColumnDef[]
  filtersEnabled: boolean
  loading?: boolean
  maxHeight?: number
}

function compareSortValues(a: unknown, b: unknown, dir: 'asc' | 'desc'): number {
  const av = a == null ? '' : a
  const bv = b == null ? '' : b
  if (typeof av === 'number' && typeof bv === 'number') {
    return dir === 'asc' ? av - bv : bv - av
  }
  const cmp = String(av).localeCompare(String(bv), 'de', { numeric: true })
  return dir === 'asc' ? cmp : -cmp
}

function ColumnFilterMenu({
  colId,
  label,
  options,
  filters,
  optionSearch,
  onOptionSearch,
  onToggle,
  onSelectAll,
  onClear,
  onClose,
}: {
  colId: string
  label: string
  options: string[]
  filters: ColumnFilters
  optionSearch: string
  onOptionSearch: (v: string) => void
  onToggle: (value: string) => void
  onSelectAll: () => void
  onClear: () => void
  onClose: () => void
}) {
  const selected = filters.select[colId]
  const hasFilter = selected !== undefined
  const filteredOpts = options.filter(o => o.toLowerCase().includes(optionSearch.trim().toLowerCase()))

  return (
    <div
      className="absolute left-0 top-full mt-1 z-50 w-56 rounded-lg border border-slate-200 bg-white shadow-lg p-2 text-left"
      onClick={e => e.stopPropagation()}
    >
      <p className="text-[0.65rem] font-semibold text-slate-700 mb-1.5 px-1">{label}</p>
      <input
        type="search"
        placeholder="Search values…"
        value={optionSearch}
        onChange={e => onOptionSearch(e.target.value)}
        className="w-full text-[0.65rem] border border-slate-200 rounded px-2 py-1 mb-2"
      />
      <div className="max-h-40 overflow-y-auto space-y-0.5 mb-2">
        {filteredOpts.map(opt => {
          const checked = !hasFilter || selected!.includes(opt)
          return (
            <label key={opt} className="flex items-center gap-2 text-[0.65rem] text-slate-700 px-1 py-0.5 cursor-pointer hover:bg-slate-50 rounded">
              <input type="checkbox" checked={checked} onChange={() => onToggle(opt)} />
              <span className="truncate">{opt}</span>
            </label>
          )
        })}
      </div>
      <div className="flex gap-1 border-t border-slate-100 pt-2">
        <button type="button" className="text-[0.65rem] px-2 py-1 rounded hover:bg-slate-50 text-slate-600" onClick={onSelectAll}>
          Select all
        </button>
        <button type="button" className="text-[0.65rem] px-2 py-1 rounded hover:bg-slate-50 text-slate-600" onClick={onClear}>
          Clear
        </button>
        <button type="button" className="text-[0.65rem] px-2 py-1 rounded ml-auto hover:bg-slate-50 text-slate-600" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  )
}

export default function ReceivablesDimensionHierarchyTable({
  rows,
  hierarchy,
  view,
  columns,
  filtersEnabled,
  loading,
  maxHeight = 480,
}: Props) {
  const labelHeader = useMemo(() => receivablesBreakdownLabelHeader(hierarchy), [hierarchy])
  const tree = useMemo(() => buildReceivablesHierarchy(rows, hierarchy, view), [rows, hierarchy, view])
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [filters, setFilters] = useState<ColumnFilters>({ text: {}, select: {} })
  const [sort, setSort] = useState<SortState | null>(null)
  const [openFilterCol, setOpenFilterCol] = useState<string | null>(null)
  const [optionSearchByCol, setOptionSearchByCol] = useState<Record<string, string>>({})
  const tableRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!filtersEnabled) setOpenFilterCol(null)
  }, [filtersEnabled])

  useEffect(() => {
    setExpanded(defaultExpandedHierarchyKeys(tree))
  }, [tree])

  useEffect(() => {
    if (!openFilterCol) return
    const onDoc = (e: MouseEvent) => {
      if (!tableRef.current?.contains(e.target as Node)) setOpenFilterCol(null)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [openFilterCol])

  const flatNodes = useMemo(() => flattenVisibleHierarchyNodes(tree, expanded), [tree, expanded])
  const tableRows = useMemo(() => {
    return flatNodes.map(node => ({
      node,
      label: node.label,
      ...node.metrics,
    }))
  }, [flatNodes])

  const selectOptions = useMemo(() => {
    const opts: Record<string, string[]> = {}
    opts.label = [...new Set(tableRows.map(r => r.label))].sort((a, b) => a.localeCompare(b, 'de', { numeric: true }))
    for (const col of columns) {
      const uniq = new Set<string>()
      for (const r of tableRows) {
        const v = r[col.field as keyof typeof r]
        uniq.add(v == null ? '(Blank)' : String(v))
      }
      opts[col.id] = [...uniq].sort((a, b) => a.localeCompare(b, 'de', { numeric: true }))
    }
    return opts
  }, [tableRows, columns])

  const filtered = useMemo(() => {
    return tableRows.filter(row => {
      const labelQ = (filters.text.label ?? '').trim().toLowerCase()
      if (labelQ && !row.label.toLowerCase().includes(labelQ)) return false

      const labelSel = filters.select.label
      if (labelSel !== undefined) {
        if (labelSel.length === 0) return false
        if (!labelSel.includes(row.label)) return false
      }

      for (const col of columns) {
        const raw = String(row[col.field as keyof typeof row] ?? '')
        const selectVal = raw.trim() || '(Blank)'
        const selected = filters.select[col.id]
        if (selected !== undefined) {
          if (selected.length === 0) return false
          if (!selected.includes(selectVal)) return false
        }
        const q = (filters.text[col.id] ?? '').trim().toLowerCase()
        if (q && !raw.toLowerCase().includes(q)) return false
      }
      return true
    })
  }, [tableRows, filters, columns])

  const sorted = useMemo(() => {
    if (!sort) return filtered
    const dir = sort.dir
    if (sort.id === 'label') {
      return [...filtered].sort((a, b) => compareSortValues(a.label, b.label, dir))
    }
    const col = columns.find(c => c.id === sort.id)
    if (!col) return filtered
    return [...filtered].sort((a, b) =>
      compareSortValues(a[col.field as keyof typeof a], b[col.field as keyof typeof b], dir),
    )
  }, [filtered, sort, columns])

  const maxAbsByField = useMemo(() => {
    const out: Record<string, number> = {}
    for (const col of columns) {
      if (!col.field.startsWith('delta_')) continue
      let max = 1
      for (const r of tableRows) {
        const v = r[col.field as keyof typeof r]
        if (typeof v === 'number') max = Math.max(max, Math.abs(v))
      }
      out[col.field] = max
    }
    return out
  }, [tableRows, columns])

  function toggleExpand(key: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const hasActiveFilters =
    Object.values(filters.text).some(v => v.trim()) ||
    Object.values(filters.select).some(v => v !== undefined) ||
    sort != null

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-xs" style={{ color: '#94A3B8' }}>
        Loading…
      </div>
    )
  }

  if (!rows.length) {
    return (
      <div className="flex items-center justify-center py-8 text-xs" style={{ color: '#94A3B8' }}>
        No receivables for this period and filter selection.
      </div>
    )
  }

  return (
    <div ref={tableRef}>
      <div className="overflow-auto min-h-[200px]" style={{ maxHeight }}>
        <table className="w-full border-collapse">
          <thead className="sticky top-0 z-10" style={{ background: SALES_TABLE_HEADER_BG }}>
            <tr>
              <th
                className={`${FIN_TABLE_CELL_CLASS} text-left font-semibold text-xs align-bottom relative`}
                style={{ color: '#475569', background: SALES_TABLE_HEADER_BG, verticalAlign: 'bottom', minWidth: 180 }}
              >
                <div className="flex items-center gap-1">
                  <span>{labelHeader}</span>
                  {filtersEnabled && (
                    <button
                      type="button"
                      className="p-0.5 rounded hover:bg-slate-100"
                      style={{
                        color: filters.select.label !== undefined ? '#1E3A5F' : '#94A3B8',
                        background: openFilterCol === 'label' ? '#EFF6FF' : 'transparent',
                      }}
                      aria-label="Filter breakdown"
                      onClick={() => setOpenFilterCol(prev => (prev === 'label' ? null : 'label'))}
                    >
                      <Filter
                        size={13}
                        strokeWidth={filters.select.label !== undefined || openFilterCol === 'label' ? 2.25 : 1.75}
                      />
                    </button>
                  )}
                </div>
                {filtersEnabled && openFilterCol === 'label' && (
                  <ColumnFilterMenu
                    colId="label"
                    label="Breakdown"
                    options={selectOptions.label ?? []}
                    filters={filters}
                    optionSearch={optionSearchByCol.label ?? ''}
                    onOptionSearch={v => setOptionSearchByCol(s => ({ ...s, label: v }))}
                    onToggle={val => {
                      setFilters(f => {
                        const all = selectOptions.label ?? []
                        const cur = new Set(f.select.label ?? all)
                        if (f.select.label === undefined) for (const o of all) cur.add(o)
                        if (cur.has(val)) cur.delete(val)
                        else cur.add(val)
                        return { ...f, select: { ...f.select, label: [...cur] } }
                      })
                    }}
                    onSelectAll={() => setFilters(f => ({ ...f, select: { ...f.select, label: undefined } }))}
                    onClear={() => setFilters(f => ({ ...f, select: { ...f.select, label: [] } }))}
                    onClose={() => setOpenFilterCol(null)}
                  />
                )}
              </th>
              {columns.map(col => (
                <th key={col.id} className="relative">
                  <div className="flex items-center justify-end gap-1">
                    <SalesFinHeader label={col.label} highlighted={false} />
                    {filtersEnabled && (
                      <button
                        type="button"
                        className="p-0.5 rounded hover:bg-slate-100 shrink-0"
                        style={{
                          color: filters.select[col.id] !== undefined ? '#1E3A5F' : '#94A3B8',
                          background: openFilterCol === col.id ? '#EFF6FF' : 'transparent',
                        }}
                        aria-label={`Filter ${col.label}`}
                        onClick={() => setOpenFilterCol(prev => (prev === col.id ? null : col.id))}
                      >
                        <Filter
                          size={13}
                          strokeWidth={filters.select[col.id] !== undefined || openFilterCol === col.id ? 2.25 : 1.75}
                        />
                      </button>
                    )}
                  </div>
                  {filtersEnabled && openFilterCol === col.id && (
                    <ColumnFilterMenu
                      colId={col.id}
                      label={col.label}
                      options={selectOptions[col.id] ?? []}
                      filters={filters}
                      optionSearch={optionSearchByCol[col.id] ?? ''}
                      onOptionSearch={v => setOptionSearchByCol(s => ({ ...s, [col.id]: v }))}
                      onToggle={val => {
                        setFilters(f => {
                          const all = selectOptions[col.id] ?? []
                          const cur = new Set(f.select[col.id] ?? all)
                          if (f.select[col.id] === undefined) for (const o of all) cur.add(o)
                          if (cur.has(val)) cur.delete(val)
                          else cur.add(val)
                          return { ...f, select: { ...f.select, [col.id]: [...cur] } }
                        })
                      }}
                      onSelectAll={() => setFilters(f => ({ ...f, select: { ...f.select, [col.id]: undefined } }))}
                      onClear={() => setFilters(f => ({ ...f, select: { ...f.select, [col.id]: [] } }))}
                      onClose={() => setOpenFilterCol(null)}
                    />
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(row => (
              <HierarchyDataRow
                key={row.node.key}
                node={row.node}
                columns={columns}
                metrics={row.node.metrics}
                maxAbsByField={maxAbsByField}
                expanded={expanded.has(row.node.key)}
                onToggle={toggleExpand}
              />
            ))}
          </tbody>
        </table>
      </div>
      {hasActiveFilters && (
        <div className="mt-2 flex justify-end">
          <button
            type="button"
            className="text-[0.65rem] text-slate-500 hover:text-slate-700 underline"
            onClick={() => {
              setFilters({ text: {}, select: {} })
              setSort(null)
            }}
          >
            Clear all filters & sort
          </button>
        </div>
      )}
    </div>
  )
}

function rowStyleForLevel(level: number): CSSProperties {
  if (level === 0) return L1_ROW_STYLE
  if (level === 1) return L2_ROW_STYLE
  return { background: '#FFFFFF', borderBottom: '1px solid #F8FAFC' }
}

function isSummaryRow(node: ReceivablesHierarchyNode): boolean {
  return node.children.length > 0
}

function labelCellClass(level: number): string {
  const pads = ['pl-1.5', 'pl-4', 'pl-7', 'pl-10']
  return pads[Math.min(level, pads.length - 1)]
}

function HierarchyDataRow({
  node,
  columns,
  metrics,
  maxAbsByField,
  expanded,
  onToggle,
}: {
  node: ReceivablesHierarchyNode
  columns: AgingBreakdownColumnDef[]
  metrics: Record<string, number>
  maxAbsByField: Record<string, number>
  expanded: boolean
  onToggle: (key: string) => void
}) {
  const hasChildren = node.children.length > 0
  const bold = isSummaryRow(node)
  const rowStyle = rowStyleForLevel(node.level)

  return (
    <tr style={rowStyle}>
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-left max-w-[14rem] truncate whitespace-nowrap ${labelCellClass(node.level)}`}
        style={{
          background: (rowStyle.background as string | undefined) ?? '#FFFFFF',
          color: node.level === 0 ? '#1E3A5F' : bold ? '#1E3A5F' : '#334155',
          fontSize: '0.68rem',
          fontWeight: bold || node.level === 0 ? 600 : 500,
        }}
        title={node.label}
      >
        <div className="flex items-center gap-1 min-w-0">
          {hasChildren ? (
            <button
              type="button"
              onClick={() => onToggle(node.key)}
              className="shrink-0 p-0.5 rounded hover:bg-slate-100 -ml-0.5"
              aria-label={expanded ? 'Collapse' : 'Expand'}
            >
              <ChevronRight
                size={14}
                style={{
                  color: '#64748B',
                  transform: expanded ? 'rotate(90deg)' : 'none',
                  transition: 'transform 0.15s',
                }}
              />
            </button>
          ) : (
            <span className="w-5 shrink-0" />
          )}
          <span className="truncate">{node.label}</span>
        </div>
      </td>
      {columns.map(col => {
        const val = metrics[col.field]
        if (col.sub === 'delta_pm' || col.sub === 'delta_py') {
          return (
            <SalesDeltaCell
              key={col.id}
              value={val}
              maxAbs={maxAbsByField[col.field] ?? 1}
              bold={bold}
            />
          )
        }
        return (
          <SalesValCell
            key={col.id}
            value={val}
            highlighted={false}
            bold={bold}
            isPct={col.isPct}
          />
        )
      })}
    </tr>
  )
}
