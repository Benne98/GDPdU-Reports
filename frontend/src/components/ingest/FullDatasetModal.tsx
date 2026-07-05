/**
 * FullDatasetModal.tsx — Server-paginated full-dataset viewer for a GL format group.
 *
 * Opened from GlGroupConfigPanel in wizard mode only (gated on onMemberValidated
 * being present). Fetches pages via POST /api/v1/ingest/dataset/rows.
 *
 * Features:
 *   - Sticky header with per-column sort toggle (asc/desc)
 *   - Filter row: text input (contains) for string/date; min/max for numbers
 *   - 300ms debounce before a filter change triggers a re-fetch
 *   - Pagination: prev/next, page-size selector (50/100/200/500)
 *   - Loading, empty, and error states (including 413 "dataset too large")
 *   - Escape key closes the modal
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import {
  fetchDatasetRows,
  type DatasetColumn,
  type DatasetFilter,
  type DatasetRowsResponse,
  type Profile,
} from '../../lib/gdpduApi'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FullDatasetModalProps {
  /** Shown in the modal header (e.g. "Full dataset — Group A"). */
  title: string
  /** One entry per member entity in the format group. */
  members: Array<{ file_id: string; entity: string; sheet?: string | null }>
  /** Shared group profile (column mapping, sign config, date parsing). */
  profile: Profile
  onClose: () => void
}

/** Raw filter input pair: v1 = min or contains text; v2 = max (numbers only). */
type FilterInputs = Record<string, { v1: string; v2: string }>

const PAGE_SIZES = [50, 100, 200, 500] as const
type PageSize = (typeof PAGE_SIZES)[number]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert raw text/number filter inputs to DatasetFilter[] for the API. */
function buildFilters(inputs: FilterInputs, columns: DatasetColumn[]): DatasetFilter[] {
  const out: DatasetFilter[] = []
  for (const col of columns) {
    const entry = inputs[col.key]
    if (!entry) continue
    const { v1, v2 } = entry
    if (col.type === 'number') {
      const hasMin = v1.trim() !== ''
      const hasMax = v2.trim() !== ''
      if (hasMin && hasMax) {
        out.push({ field: col.key, op: 'between', value: Number(v1), value2: Number(v2) })
      } else if (hasMin) {
        out.push({ field: col.key, op: 'gte', value: Number(v1) })
      } else if (hasMax) {
        out.push({ field: col.key, op: 'lte', value: Number(v2) })
      }
    } else {
      // string / date: contains
      if (v1.trim() !== '') {
        out.push({ field: col.key, op: 'contains', value: v1 })
      }
    }
  }
  return out
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function FullDatasetModal({
  title,
  members,
  profile,
  onClose,
}: FullDatasetModalProps) {
  // ── Server response ──
  const [response, setResponse] = useState<DatasetRowsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── Pagination ──
  const [offset, setOffset] = useState(0)
  const [limit, setLimit] = useState<PageSize>(50)

  // ── Sort ──
  const [sortBy, setSortBy] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  // ── Filters ──
  // filterInputs: raw typed values (updated immediately on keystroke)
  // activeFilters: debounced version sent to server
  const [filterInputs, setFilterInputs] = useState<FilterInputs>({})
  const [activeFilters, setActiveFilters] = useState<DatasetFilter[]>([])

  // Ref so the debounce callback always reads the latest columns (avoids stale closure)
  const columnsRef = useRef<DatasetColumn[]>([])
  useEffect(() => {
    if (response) columnsRef.current = response.columns
  }, [response])

  // Debounce filter inputs → activeFilters (skip first run to avoid double-fetch on mount)
  const isFirstDebounce = useRef(true)
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (isFirstDebounce.current) {
      isFirstDebounce.current = false
      return
    }
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      const filters = buildFilters(filterInputs, columnsRef.current)
      // Reset to first page and apply new filters (React 18 batches both updates)
      setOffset(0)
      setActiveFilters(filters)
    }, 300)
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [filterInputs])

  // ── Fetch ──
  // Runs on mount and whenever sort / pagination / activeFilters change.
  const fetchPage = useCallback(async () => {
    let cancelled = false
    setLoading(true)
    setError(null)
    try {
      const res = await fetchDatasetRows({
        members,
        profile,
        offset,
        limit,
        sort_by: sortBy ?? undefined,
        sort_dir: sortDir,
        filters: activeFilters,
      })
      if (!cancelled) setResponse(res)
    } catch (e) {
      if (!cancelled) setError(e instanceof Error ? e.message : 'An unexpected error occurred')
    } finally {
      if (!cancelled) setLoading(false)
    }
    return () => { cancelled = true }
  // members and profile are stable for the modal lifetime; not adding to deps
  // avoids spurious re-fetches when the parent re-renders for unrelated reasons.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, limit, sortBy, sortDir, activeFilters])

  useEffect(() => {
    void fetchPage()
  }, [fetchPage])

  // ── Sort toggle ──
  function handleSort(colKey: string) {
    if (sortBy === colKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(colKey)
      setSortDir('asc')
    }
    setOffset(0)
  }

  // ── Filter input helpers ──
  function setFilterV1(colKey: string, v1: string) {
    setFilterInputs(prev => ({ ...prev, [colKey]: { v1, v2: prev[colKey]?.v2 ?? '' } }))
  }
  function setFilterV2(colKey: string, v2: string) {
    setFilterInputs(prev => ({ ...prev, [colKey]: { v1: prev[colKey]?.v1 ?? '', v2 } }))
  }

  // ── Keyboard: Escape closes ──
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // ── Derived display values ──
  const columns = response?.columns ?? []
  const rows = response?.rows ?? []
  const total = response?.total ?? 0
  const filteredTotal = response?.filtered_total ?? 0
  const hasFilters = activeFilters.length > 0
  const displayTotal = hasFilters ? filteredTotal : total
  const currentPage = Math.floor(offset / limit) + 1
  const totalPages = Math.ceil(displayTotal / limit) || 1
  const rowStart = displayTotal === 0 ? 0 : offset + 1
  const rowEnd = Math.min(offset + limit, displayTotal)
  const canPrev = offset > 0 && !loading
  const canNext = offset + limit < displayTotal && !loading

  // ── Render ──
  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Modal panel */}
      <div
        className="relative bg-white rounded-xl shadow-2xl flex flex-col"
        style={{ width: 'min(95vw, 1400px)', height: 'min(90vh, 900px)' }}
      >

        {/* ── Header ── */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200 shrink-0">
          <div>
            <h2 className="text-base font-semibold text-slate-900">{title}</h2>
            {response && (
              <p className="text-xs text-slate-500 mt-0.5">
                {hasFilters
                  ? `${filteredTotal.toLocaleString()} matching rows of ${total.toLocaleString()} total`
                  : `${total.toLocaleString()} rows total`}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {/* Page size selector */}
            <div className="flex items-center gap-1.5">
              <label className="text-xs text-slate-500 whitespace-nowrap">Rows per page:</label>
              <select
                value={limit}
                onChange={e => {
                  setLimit(Number(e.target.value) as PageSize)
                  setOffset(0)
                }}
                className="rounded border border-slate-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
              >
                {PAGE_SIZES.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            {/* Close button */}
            <button
              onClick={onClose}
              className="rounded p-1.5 hover:bg-slate-100 text-slate-400 hover:text-slate-700 transition"
              aria-label="Close"
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M11 3L3 11M3 3l8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
            </button>
          </div>
        </div>

        {/* ── Scrollable table area ── */}
        <div className="relative flex-1 overflow-auto min-h-0">

          {/* Loading overlay */}
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-white/70 z-10 pointer-events-none">
              <span className="text-sm text-slate-500">Loading…</span>
            </div>
          )}

          {/* Error state */}
          {error && !loading && (
            <div className="flex items-center justify-center h-full p-8">
              <div className="rounded-lg border border-red-200 bg-red-50 px-6 py-5 text-sm text-red-800 max-w-lg text-center">
                <p className="font-semibold mb-1">Could not load dataset</p>
                <p className="text-red-700">{error}</p>
                <button
                  onClick={() => void fetchPage()}
                  className="mt-3 text-xs text-red-600 underline hover:no-underline"
                >
                  Retry
                </button>
              </div>
            </div>
          )}

          {/* Empty state */}
          {!loading && !error && response && rows.length === 0 && (
            <div className="flex items-center justify-center h-full">
              <p className="text-sm text-slate-400">No rows match the current filters.</p>
            </div>
          )}

          {/* Table — only rendered once columns are known */}
          {columns.length > 0 && !error && (
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 z-10 bg-white">
                {/* Sort header row */}
                <tr>
                  {columns.map(col => (
                    <th
                      key={col.key}
                      className="px-3 py-2 text-left font-semibold text-slate-600 whitespace-nowrap cursor-pointer select-none hover:bg-slate-50 border-b border-slate-200 bg-white"
                      onClick={() => handleSort(col.key)}
                    >
                      <span className="flex items-center gap-1">
                        <span>{col.label}</span>
                        {sortBy === col.key ? (
                          <span className="text-blue-600 text-[10px]">{sortDir === 'asc' ? '↑' : '↓'}</span>
                        ) : (
                          <span className="text-slate-300 text-[10px]">↕</span>
                        )}
                      </span>
                    </th>
                  ))}
                </tr>
                {/* Filter input row */}
                <tr className="bg-slate-50">
                  {columns.map(col => (
                    <th
                      key={col.key}
                      className="px-2 py-1.5 border-b border-slate-200 font-normal bg-slate-50"
                    >
                      {col.type === 'number' ? (
                        <div className="flex gap-1">
                          <input
                            type="number"
                            placeholder="min"
                            value={filterInputs[col.key]?.v1 ?? ''}
                            onChange={e => setFilterV1(col.key, e.target.value)}
                            className="w-20 rounded border border-slate-300 px-1.5 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                          />
                          <input
                            type="number"
                            placeholder="max"
                            value={filterInputs[col.key]?.v2 ?? ''}
                            onChange={e => setFilterV2(col.key, e.target.value)}
                            className="w-20 rounded border border-slate-300 px-1.5 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                          />
                        </div>
                      ) : (
                        <input
                          type="text"
                          placeholder="contains…"
                          value={filterInputs[col.key]?.v1 ?? ''}
                          onChange={e => setFilterV1(col.key, e.target.value)}
                          className="w-full min-w-[6rem] rounded border border-slate-300 px-1.5 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                        />
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => (
                  <tr key={ri} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
                    {columns.map(col => (
                      <td key={col.key} className="px-3 py-1.5 font-mono text-slate-700 whitespace-nowrap">
                        {row[col.key] == null ? (
                          <span className="text-slate-300">—</span>
                        ) : (
                          String(row[col.key])
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* ── Footer / Pagination ── */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-slate-200 shrink-0 gap-4">
          <span className="text-xs text-slate-500">
            {displayTotal === 0
              ? (loading ? '' : 'No rows')
              : `Rows ${rowStart.toLocaleString()}–${rowEnd.toLocaleString()} of ${displayTotal.toLocaleString()}${
                  hasFilters && total !== filteredTotal
                    ? ` (${total.toLocaleString()} unfiltered)`
                    : ''
                }`}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={!canPrev}
              className="rounded px-3 py-1 text-xs font-medium border border-slate-300 disabled:opacity-40 hover:bg-slate-50 transition"
            >
              Previous
            </button>
            <span className="text-xs text-slate-500 whitespace-nowrap">
              Page {currentPage} of {totalPages}
            </span>
            <button
              onClick={() => setOffset(offset + limit)}
              disabled={!canNext}
              className="rounded px-3 py-1 text-xs font-medium border border-slate-300 disabled:opacity-40 hover:bg-slate-50 transition"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
