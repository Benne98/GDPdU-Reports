import { useState, useCallback, useEffect } from 'react'
import { motion } from 'framer-motion'
import { Search, ChevronDown, X, ArrowDownUp } from 'lucide-react'
import { api, GlLine, GlLinesResponse } from '../../lib/api'
import { fmtAmount } from '../../lib/fmt'

interface DrillDownTableProps {
  entity?:        string
  dateFrom?:      string
  dateTo?:        string
  level2?:        string
  level3?:        string
  level4?:        string
  glAccountId?:   string
  statementType?: string
  customerName?:  string
  supplierName?:  string
  onClose:        () => void
  title:          string
}

const TOP_N = 20

export default function DrillDownTable({
  entity, dateFrom, dateTo, level2, level3, level4, glAccountId, statementType, customerName, supplierName, onClose, title,
}: DrillDownTableProps) {
  const [topRows,    setTopRows]    = useState<GlLine[]>([])
  const [allRows,    setAllRows]    = useState<GlLine[]>([])
  const [cursor,     setCursor]     = useState<string | null>(null)
  const [hasMore,    setHasMore]    = useState(false)
  const [expanded,   setExpanded]   = useState(false)
  const [loadingTop, setLoadingTop] = useState(false)
  const [loadingAll, setLoadingAll] = useState(false)
  const [search,     setSearch]     = useState('')
  const [error,      setError]      = useState<string | null>(null)

  // Load top-20 sorted by |amount|
  const loadTop = useCallback(async (q: string) => {
    setLoadingTop(true)
    setError(null)
    try {
      const res: GlLinesResponse = await api.glLines({
        entity, dateFrom, dateTo, level2, level3, level4, glAccountId, statementType, customerName, supplierName,
        search:  q || undefined,
        sortBy:  'amount_abs',
        limit:   TOP_N,
      })
      setTopRows(res.rows)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoadingTop(false)
    }
  }, [entity, dateFrom, dateTo, level2, level3, level4, glAccountId, statementType, customerName, supplierName])

  // Load all rows in date order (cursor-paginated)
  const loadAll = useCallback(async (reset: boolean, q: string) => {
    setLoadingAll(true)
    setError(null)
    try {
      const res: GlLinesResponse = await api.glLines({
        entity, dateFrom, dateTo, level2, level3, level4, glAccountId, statementType, customerName, supplierName,
        search: q || undefined,
        cursor: reset ? undefined : (cursor ?? undefined),
        limit:  100,
      })
      setAllRows(prev => reset ? res.rows : [...prev, ...res.rows])
      setCursor(res.next_cursor)
      setHasMore(res.has_more)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoadingAll(false)
    }
  }, [entity, dateFrom, dateTo, level2, level3, level4, glAccountId, statementType, customerName, supplierName, cursor])

  // Initial load on mount / filter change
  useEffect(() => {
    setExpanded(false)
    setAllRows([])
    setCursor(null)
    loadTop(search)
  }, [entity, dateFrom, dateTo, level2, level3, level4, glAccountId, statementType, customerName, supplierName]) // eslint-disable-line

  const handleSearch = () => {
    setExpanded(false)
    setAllRows([])
    setCursor(null)
    loadTop(search)
  }

  const handleExpand = () => {
    setExpanded(true)
    loadAll(true, search)
  }

  const displayRows  = expanded ? allRows  : topRows
  const isLoading    = expanded ? loadingAll : loadingTop
  const totalLoaded  = expanded ? allRows.length : topRows.length

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 12 }}
      transition={{ duration: 0.25 }}
      className="rounded-xl overflow-hidden mb-6"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-5 py-4"
        style={{ borderBottom: '1px solid #E2E8F0' }}
      >
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#111827' }}>{title}</h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            {expanded
              ? `${totalLoaded} bookings loaded — sorted by date`
              : `Top ${TOP_N} bookings by absolute amount`}
          </p>
        </div>
        <button
          onClick={onClose}
          className="w-7 h-7 rounded-lg flex items-center justify-center transition-colors"
          style={{ background: '#F4F6F9', color: '#475569', border: '1px solid #E2E8F0' }}
        >
          <X size={14} />
        </button>
      </div>

      {/* Search bar */}
      <div className="px-5 py-3 flex gap-2" style={{ borderBottom: '1px solid #E2E8F0' }}>
        <div className="relative flex-1">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: '#94A3B8' }} />
          <input
            type="text"
            placeholder="Search booking text…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            className="w-full pl-8 pr-3 py-2 rounded-lg text-xs outline-none"
            style={{ background: '#F4F6F9', border: '1px solid #CBD5E1', color: '#111827' }}
          />
        </div>
        <button onClick={handleSearch} className="btn-gold text-xs px-3 py-2">
          Search
        </button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto" style={{ maxHeight: 440 }}>
        {error ? (
          <div className="p-6 text-center text-sm" style={{ color: '#DC2626' }}>{error}</div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: '1px solid #E2E8F0' }}>
                {['Date', 'Entity', 'Account', 'P&L Line', 'Amount', 'Booking Text', 'Doc. No.'].map(h => (
                  <th
                    key={h}
                    className="px-4 py-2.5 text-left font-semibold tracking-wide uppercase whitespace-nowrap"
                    style={{ color: '#94A3B8', background: '#F8FAFC', position: 'sticky', top: 0, zIndex: 1, fontSize: '0.6rem' }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayRows.map((row, i) => (
                <tr
                  key={row.booking_line_id}
                  style={{
                    borderBottom: '1px solid #F1F5F9',
                    background:   i % 2 === 0 ? '#FFFFFF' : '#FAFBFC',
                  }}
                >
                  <td className="px-4 py-2 whitespace-nowrap" style={{ color: '#475569' }}>
                    {row.posting_date}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap" style={{ color: '#475569' }}>
                    {row.legal_entity_code}
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap font-mono" style={{ color: '#1E3A5F' }}>
                    {row.gl_account_id}
                  </td>
                  <td className="px-4 py-2" style={{ color: '#475569', maxWidth: 160 }}>
                    <span title={row.level_3 ?? ''} className="truncate block max-w-[160px]">
                      {row.level_3 ?? row.level_2 ?? '—'}
                    </span>
                  </td>
                  <td
                    className="px-4 py-2 text-right whitespace-nowrap font-semibold tabular-nums"
                    style={{ color: row.amount_signed >= 0 ? '#10B981' : '#DC2626' }}
                  >
                    {fmtAmount(row.amount_signed)}
                  </td>
                  <td className="px-4 py-2" style={{ color: '#475569', maxWidth: 200 }}>
                    <span title={row.booking_text ?? ''} className="truncate block max-w-[200px]">
                      {row.booking_text ?? '—'}
                    </span>
                  </td>
                  <td className="px-4 py-2 whitespace-nowrap font-mono" style={{ color: '#94A3B8', fontSize: '0.6rem' }}>
                    {row.reference_document_number ?? row.journal_entry_number}
                  </td>
                </tr>
              ))}

              {isLoading && (
                <tr>
                  <td colSpan={7} className="px-4 py-4 text-center text-xs animate-pulse" style={{ color: '#94A3B8' }}>
                    Loading…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Footer actions */}
      <div
        className="px-5 py-3 flex items-center justify-between gap-3"
        style={{ borderTop: '1px solid #E2E8F0' }}
      >
        {/* Expand to all */}
        {!expanded && !isLoading && (
          <button
            onClick={handleExpand}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors"
            style={{ color: '#1E3A5F', background: 'rgba(30,58,95,0.06)', border: '1px solid rgba(30,58,95,0.12)' }}
          >
            <ArrowDownUp size={12} />
            Show all bookings (sorted by date)
          </button>
        )}

        {/* Load next page when expanded */}
        {expanded && hasMore && !isLoading && (
          <button
            onClick={() => loadAll(false, search)}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors btn-outline"
          >
            <ChevronDown size={13} />
            Load next 100 rows
          </button>
        )}

        {expanded && !hasMore && !isLoading && (
          <span className="text-xs" style={{ color: '#94A3B8' }}>
            All {totalLoaded} bookings loaded
          </span>
        )}

        {/* Row count badge */}
        <span className="ml-auto text-xs tabular-nums" style={{ color: '#CBD5E1' }}>
          {totalLoaded} rows
        </span>
      </div>
    </motion.div>
  )
}
