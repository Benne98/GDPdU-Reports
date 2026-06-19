import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Filter,
} from 'lucide-react'

export type FilterableColumn<T> = {
  id: string
  label: string
  align?: 'left' | 'right'
  getValue: (row: T) => string
  /** Value used in filter checklist (defaults to getValue). */
  getSelectValue?: (row: T) => string
  /** Value used for sorting — number for numeric columns. */
  sortValue?: (row: T) => string | number
  render?: (row: T) => React.ReactNode
  textPlaceholder?: string
  /** Show value checklist in column filter menu. Default true. */
  enableSelect?: boolean
  /** Allow sort + filter menu on header. Default true. */
  sortable?: boolean
}

type ColumnFilters = {
  text: Record<string, string>
  select: Record<string, string[]>
}

type SortState = {
  id: string
  dir: 'asc' | 'desc'
}

export type FilterableTableExpandable<T> = {
  getRowId: (row: T) => string
  canExpand: (row: T) => boolean
  renderDetail: (row: T) => React.ReactNode
}

function compareSortValues(a: string | number, b: string | number, dir: 'asc' | 'desc'): number {
  const av = a ?? ''
  const bv = b ?? ''
  if (typeof av === 'number' && typeof bv === 'number') {
    return dir === 'asc' ? av - bv : bv - av
  }
  const as = String(av)
  const bs = String(bv)
  const cmp = as.localeCompare(bs, 'de', { numeric: true, sensitivity: 'base' })
  return dir === 'asc' ? cmp : -cmp
}

function ColumnFilterMenu<T>({
  col,
  options,
  filters,
  sort,
  optionSearch,
  onOptionSearchChange,
  onSort,
  onToggleValue,
  onSelectAll,
  onClearColumn,
  onClose,
}: {
  col: FilterableColumn<T>
  options: string[]
  filters: ColumnFilters
  sort: SortState | null
  optionSearch: string
  onOptionSearchChange: (value: string) => void
  onSort: (dir: 'asc' | 'desc') => void
  onToggleValue: (value: string) => void
  onSelectAll: (checked: boolean) => void
  onClearColumn: () => void
  onClose: () => void
}) {
  const selected = new Set(filters.select[col.id] ?? [])
  const hasValueFilter = filters.select[col.id] !== undefined
  const allSelected = !hasValueFilter
  const search = optionSearch.trim().toLowerCase()
  const visibleOptions = search
    ? options.filter(o => o.toLowerCase().includes(search))
    : options

  const visibleAllChecked = visibleOptions.length > 0
    && visibleOptions.every(o => allSelected || selected.has(o))

  return (
    <div
      className="absolute top-full mt-1 z-30 min-w-[200px] max-w-[260px] rounded-lg border shadow-lg text-left font-normal"
      style={{
        background: '#FFFFFF',
        borderColor: '#E2E8F0',
        boxShadow: '0 8px 24px rgba(15,23,42,0.12)',
        [col.align === 'right' ? 'right' : 'left']: 0,
      }}
      onClick={e => e.stopPropagation()}
    >
      {col.sortable !== false && (
        <div className="py-1 border-b" style={{ borderColor: '#F1F5F9' }}>
          <button
            type="button"
            className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] hover:bg-slate-50"
            style={{ color: sort?.id === col.id && sort.dir === 'asc' ? '#1E3A5F' : '#334155' }}
            onClick={() => { onSort('asc'); onClose() }}
          >
            <ArrowUp size={13} />
            Sort A to Z
          </button>
          <button
            type="button"
            className="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] hover:bg-slate-50"
            style={{ color: sort?.id === col.id && sort.dir === 'desc' ? '#1E3A5F' : '#334155' }}
            onClick={() => { onSort('desc'); onClose() }}
          >
            <ArrowDown size={13} />
            Sort Z to A
          </button>
        </div>
      )}

      {col.enableSelect !== false && (
        <>
          <div className="px-2 py-2 border-b" style={{ borderColor: '#F1F5F9' }}>
            <input
              type="search"
              value={optionSearch}
              onChange={e => onOptionSearchChange(e.target.value)}
              placeholder={col.textPlaceholder ?? 'Search values…'}
              className="w-full rounded border px-2 py-1 text-[11px]"
              style={{ borderColor: '#E2E8F0', color: '#334155' }}
              autoFocus
            />
          </div>
          <div className="max-h-[180px] overflow-y-auto py-1">
            <label className="flex items-center gap-2 px-3 py-1 cursor-pointer hover:bg-slate-50">
              <input
                type="checkbox"
                checked={visibleAllChecked}
                onChange={e => onSelectAll(e.target.checked)}
                className="shrink-0"
              />
              <span className="text-[11px] font-medium" style={{ color: '#64748B' }}>
                (Select all)
              </span>
            </label>
            {visibleOptions.length === 0 ? (
              <p className="px-3 py-2 text-[11px]" style={{ color: '#94A3B8' }}>No values</p>
            ) : (
              visibleOptions.map(opt => (
                <label
                  key={opt}
                  className="flex items-center gap-2 px-3 py-1 cursor-pointer hover:bg-slate-50"
                >
                  <input
                    type="checkbox"
                    checked={allSelected || selected.has(opt)}
                    onChange={() => onToggleValue(opt)}
                    className="shrink-0"
                  />
                  <span className="text-[11px] truncate" title={opt} style={{ color: '#334155' }}>
                    {opt || '(Blank)'}
                  </span>
                </label>
              ))
            )}
          </div>
        </>
      )}

      <div className="py-1 border-t" style={{ borderColor: '#F1F5F9' }}>
        <button
          type="button"
          className="w-full px-3 py-1.5 text-[11px] text-left hover:bg-slate-50 disabled:opacity-40"
          style={{ color: '#64748B' }}
          disabled={filters.select[col.id] === undefined && !filters.text[col.id]?.trim()}
          onClick={() => { onClearColumn(); onClose() }}
        >
          Clear filter
        </button>
      </div>
    </div>
  )
}

export default function FilterableDataTable<T>({
  columns,
  rows,
  maxHeight = 320,
  emptyMessage = 'No rows',
  filteredEmptyMessage = 'No rows match your filters',
  headerLeft,
  headerRight,
  className = 'mt-4',
  expandable,
  /** When set, column filter menus only appear while true (toggle via toolbar). Omit for always-on headers. */
  filtersEnabled,
  /** @deprecated Excel-style filters are always in column headers. */
  defaultFiltersOpen: _defaultFiltersOpen,
  /** @deprecated Use column header filter menus instead. */
  filtersCollapsible: _filtersCollapsible,
}: {
  columns: FilterableColumn<T>[]
  rows: T[]
  maxHeight?: number
  emptyMessage?: string
  filteredEmptyMessage?: string
  headerLeft?: ReactNode
  headerRight?: ReactNode
  className?: string
  expandable?: FilterableTableExpandable<T>
  filtersEnabled?: boolean
  defaultFiltersOpen?: boolean
  filtersCollapsible?: boolean
}) {
  const [filters, setFilters] = useState<ColumnFilters>({ text: {}, select: {} })
  const [sort, setSort] = useState<SortState | null>(null)
  const [openFilterCol, setOpenFilterCol] = useState<string | null>(null)
  const [optionSearchByCol, setOptionSearchByCol] = useState<Record<string, string>>({})
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const tableRef = useRef<HTMLDivElement>(null)

  const showColumnFilters = filtersEnabled !== false

  useEffect(() => {
    if (!showColumnFilters) setOpenFilterCol(null)
  }, [showColumnFilters])

  useEffect(() => {
    if (!openFilterCol) return
    const onDocClick = (e: MouseEvent) => {
      if (!tableRef.current?.contains(e.target as Node)) {
        setOpenFilterCol(null)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [openFilterCol])

  const selectOptions = useMemo(() => {
    const opts: Record<string, string[]> = {}
    for (const col of columns) {
      if (col.enableSelect === false) continue
      const getter = col.getSelectValue ?? col.getValue
      const uniq = new Set<string>()
      for (const row of rows) {
        const v = String(getter(row) ?? '').trim()
        uniq.add(v || '(Blank)')
      }
      opts[col.id] = [...uniq].sort((a, b) => a.localeCompare(b, 'de', { numeric: true }))
    }
    return opts
  }, [rows, columns])

  const filtered = useMemo(() => {
    return rows.filter(row =>
      columns.every(col => {
        const raw = String(col.getValue(row) ?? '')
        const selectVal = String((col.getSelectValue ?? col.getValue)(row) ?? '').trim() || '(Blank)'

        const selected = filters.select[col.id]
        if (col.enableSelect !== false && selected !== undefined) {
          if (selected.length === 0) return false
          if (!selected.includes(selectVal)) return false
        }

        const q = (filters.text[col.id] ?? '').trim().toLowerCase()
        if (q && !raw.toLowerCase().includes(q)) return false

        return true
      }),
    )
  }, [rows, filters, columns])

  const sorted = useMemo(() => {
    if (!sort) return filtered
    const col = columns.find(c => c.id === sort.id)
    if (!col || col.sortable === false) return filtered
    const getter = col.sortValue ?? col.getValue
    return [...filtered].sort((a, b) => compareSortValues(getter(a), getter(b), sort.dir))
  }, [filtered, sort, columns])

  const setSortForColumn = (colId: string, dir: 'asc' | 'desc') => {
    setSort({ id: colId, dir })
  }

  const toggleSelect = (id: string, value: string) => {
    setFilters(f => {
      const allOpts = selectOptions[id] ?? []
      const cur = new Set(f.select[id] ?? [])
      if (f.select[id] === undefined) {
        for (const o of allOpts) cur.add(o)
      }
      if (cur.has(value)) cur.delete(value)
      else cur.add(value)
      const next = { ...f.select }
      if (cur.size === 0) next[id] = []
      else if (cur.size === allOpts.length) delete next[id]
      else next[id] = [...cur]
      return { ...f, select: next }
    })
  }

  const selectAllInColumn = (colId: string, checked: boolean) => {
    setFilters(f => {
      const next = { ...f.select }
      if (checked) delete next[colId]
      else next[colId] = []
      return { ...f, select: next }
    })
  }

  const clearColumnFilter = (colId: string) => {
    setFilters(f => {
      const nextSelect = { ...f.select }
      const nextText = { ...f.text }
      delete nextSelect[colId]
      delete nextText[colId]
      return { text: nextText, select: nextSelect }
    })
    setOptionSearchByCol(prev => {
      const next = { ...prev }
      delete next[colId]
      return next
    })
  }

  const hasActiveFilters =
    Object.values(filters.text).some(v => v.trim()) ||
    Object.keys(filters.select).length > 0

  const bodyMessage = rows.length === 0 ? emptyMessage : filteredEmptyMessage
  const colCount = columns.length + (expandable ? 1 : 0)
  const showHeaderRow = Boolean(headerLeft || headerRight)

  const toggleExpanded = (id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className={`text-xs ${className}`} ref={tableRef}>
      {showHeaderRow && (
        <div className="flex flex-wrap items-start justify-between gap-3 mb-2">
          {headerLeft ? <div className="min-w-0 flex-1">{headerLeft}</div> : <div className="flex-1" />}
          {headerRight ? <div className="flex flex-wrap items-center gap-2 shrink-0">{headerRight}</div> : null}
        </div>
      )}
      <div className="overflow-auto rounded-lg border" style={{ maxHeight, borderColor: '#E2E8F0' }}>
        <table className="w-full min-w-[960px]">
          <thead className="sticky top-0 z-20" style={{ background: '#F8FAFC' }}>
            <tr style={{ borderBottom: '1px solid #E2E8F0' }}>
              {expandable && (
                <th className="w-8 px-1 py-2" aria-label="Expand" />
              )}
              {columns.map(col => {
                const isFiltered = filters.select[col.id] !== undefined
                  || Boolean(filters.text[col.id]?.trim())
                const isSorted = sort?.id === col.id
                const menuOpen = openFilterCol === col.id

                return (
                  <th
                    key={col.id}
                    className={`relative px-2 py-2 font-semibold align-middle ${
                      col.align === 'right' ? 'text-right' : 'text-left'
                    }`}
                    style={{ color: '#475569' }}
                  >
                    <div
                      className={`flex items-center gap-1 w-full ${
                        col.align === 'right' ? 'justify-end' : 'justify-start'
                      }`}
                    >
                      <span className="truncate">{col.label}</span>
                      {isSorted && (
                        sort?.dir === 'asc'
                          ? <ArrowUp size={12} style={{ color: '#1E3A5F' }} />
                          : <ArrowDown size={12} style={{ color: '#1E3A5F' }} />
                      )}
                      {col.sortable !== false && showColumnFilters && (
                        <button
                          type="button"
                          className="p-0.5 rounded shrink-0 hover:bg-slate-200/60"
                          style={{
                            color: isFiltered || menuOpen ? '#1E3A5F' : '#94A3B8',
                            background: isFiltered || menuOpen ? '#EFF6FF' : 'transparent',
                          }}
                          aria-label={`Filter ${col.label}`}
                          aria-expanded={menuOpen}
                          onClick={() => {
                            setOpenFilterCol(prev => (prev === col.id ? null : col.id))
                          }}
                        >
                          <Filter size={13} strokeWidth={isFiltered || menuOpen ? 2.25 : 1.75} />
                        </button>
                      )}
                    </div>
                    {menuOpen && (
                      <ColumnFilterMenu
                        col={col}
                        options={selectOptions[col.id] ?? []}
                        filters={filters}
                        sort={sort}
                        optionSearch={optionSearchByCol[col.id] ?? ''}
                        onOptionSearchChange={v => setOptionSearchByCol(prev => ({ ...prev, [col.id]: v }))}
                        onSort={dir => setSortForColumn(col.id, dir)}
                        onToggleValue={value => toggleSelect(col.id, value)}
                        onSelectAll={checked => selectAllInColumn(col.id, checked)}
                        onClearColumn={() => clearColumnFilter(col.id)}
                        onClose={() => setOpenFilterCol(null)}
                      />
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={colCount} className="px-3 py-8 text-center" style={{ color: '#94A3B8' }}>
                  {bodyMessage}
                </td>
              </tr>
            ) : (
              sorted.map((row, i) => {
                const rowId = expandable?.getRowId(row) ?? String(i)
                const canExpand = expandable?.canExpand(row) ?? false
                const isExpanded = expandedIds.has(rowId)
                return (
                  <Fragment key={rowId}>
                    <tr style={{ borderBottom: isExpanded ? undefined : '1px solid #F1F5F9' }}>
                      {expandable && (
                        <td className="px-1 py-1.5 align-middle">
                          {canExpand ? (
                            <button
                              type="button"
                              onClick={() => toggleExpanded(rowId)}
                              className="p-0.5 rounded hover:bg-slate-100"
                              aria-expanded={isExpanded}
                              aria-label={isExpanded ? 'Collapse row' : 'Expand row'}
                            >
                              {isExpanded ? (
                                <ChevronDown size={14} style={{ color: '#64748B' }} />
                              ) : (
                                <ChevronRight size={14} style={{ color: '#64748B' }} />
                              )}
                            </button>
                          ) : null}
                        </td>
                      )}
                      {columns.map(col => (
                        <td
                          key={col.id}
                          className={`px-2 py-1.5 ${col.align === 'right' ? 'text-right tabular-nums' : ''}`}
                          style={{ color: col.id === 'stage' ? '#64748B' : '#334155' }}
                        >
                          {col.render ? col.render(row) : col.getValue(row)}
                        </td>
                      ))}
                    </tr>
                    {expandable && canExpand && isExpanded && (
                      <tr style={{ borderBottom: '1px solid #F1F5F9' }}>
                        <td colSpan={colCount} className="px-2 py-0 pb-2" style={{ background: '#FAFBFC' }}>
                          {expandable.renderDetail(row)}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10px]" style={{ color: '#94A3B8' }}>
        <span>
          Showing {sorted.length.toLocaleString('de-DE')} of {rows.length.toLocaleString('de-DE')} rows
        </span>
        {(hasActiveFilters || sort) && (
          <button
            type="button"
            className="underline"
            onClick={() => {
              setFilters({ text: {}, select: {} })
              setSort(null)
              setOptionSearchByCol({})
            }}
          >
            Clear all filters & sort
          </button>
        )}
      </div>
    </div>
  )
}
