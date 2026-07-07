import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2, RefreshCw, Search } from 'lucide-react'
import {
  api,
  type Entity,
  type GlLine,
  type GlLinesFiltersResponse,
} from '../lib/api'
import AnalyticsPageShell from '../components/ui/AnalyticsPageShell'
import PlExportMenu, { type PlExportKind } from '../components/financials/pl-two-view/PlExportMenu'
import PlDetailBookingJournalModal from '../components/financials/pl-two-view/PlDetailBookingJournalModal'
import { stripLegalForm } from '../lib/stripLegalForm'
import {
  displayBookingAmount,
  exportAccountStatementRows,
} from '../components/account-statement/exportAccountStatement'
import TrialBalanceTab from '../components/account-statement/TrialBalanceTab'
import ReportsTab from '../components/account-statement/ReportsTab'

const PAGE_SIZE = 100
const ALL = '__all__'

type PageTab = 'bookings' | 'trial-balance' | 'reports'

type Filters = {
  entity: string
  glAccountId: string
  statementType: string
  plBsItem: string
  journalEntryNumber: string
  dateFrom: string
  dateTo: string
  search: string
}

function fmtIsoToDe(iso: string): string {
  const [y, m, d] = iso.split('-')
  if (!y || !m || !d) return iso
  return `${d}.${m}.${y}`
}

function defaultFilters(meta: GlLinesFiltersResponse | null): Filters {
  return {
    entity: ALL,
    glAccountId: ALL,
    statementType: ALL,
    plBsItem: ALL,
    journalEntryNumber: '',
    dateFrom: meta?.date_min?.slice(0, 10) ?? '',
    dateTo: meta?.date_max?.slice(0, 10) ?? '',
    search: '',
  }
}

function plBsKey(item: { statement_type: string; level_2: string; level_3: string | null }): string {
  return `${item.statement_type}::${item.level_2}::${item.level_3 ?? ''}`
}

export default function AccountStatementPage() {
  const [pageTab, setPageTab] = useState<PageTab>('bookings')
  const [entities, setEntities] = useState<Entity[]>([])
  const [meta, setMeta] = useState<GlLinesFiltersResponse | null>(null)
  const [draft, setDraft] = useState<Filters>(() => defaultFilters(null))
  const [applied, setApplied] = useState<Filters>(() => defaultFilters(null))
  const [rows, setRows] = useState<GlLine[]>([])
  const [totalCount, setTotalCount] = useState<number | null>(null)
  const [offset, setOffset] = useState(0)
  const [loadingMeta, setLoadingMeta] = useState(true)
  const [loadingRows, setLoadingRows] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedBookingId, setSelectedBookingId] = useState<number | null>(null)

  const entityNameByCode = useMemo(() => {
    const map = new Map<string, string>()
    for (const e of entities) {
      map.set(e.legal_entity_code, stripLegalForm(e.entity_name))
    }
    return map
  }, [entities])

  const entityDisplayName = useCallback(
    (code: string) => entityNameByCode.get(code) ?? code,
    [entityNameByCode],
  )

  const entityLabel = useMemo(() => {
    if (applied.entity === ALL) return 'All entities'
    return entityDisplayName(applied.entity)
  }, [applied.entity, entityDisplayName])

  const loadMeta = useCallback(async (entityCode: string) => {
    setLoadingMeta(true)
    try {
      const ent = entityCode === ALL ? undefined : entityCode
      const [entList, filters] = await Promise.all([
        entities.length ? Promise.resolve(entities) : api.entities(),
        api.glLinesFilters(ent),
      ])
      if (!entities.length) setEntities(entList)
      setMeta(filters)
      setDraft(prev => ({
        ...prev,
        dateFrom: prev.dateFrom || filters.date_min?.slice(0, 10) || '',
        dateTo: prev.dateTo || filters.date_max?.slice(0, 10) || '',
      }))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not load filter options')
    } finally {
      setLoadingMeta(false)
    }
  }, [entities])

  const queryParams = useCallback(
    (f: Filters, rowOffset: number, withTotal: boolean) => {
      const plParts = f.plBsItem !== ALL ? f.plBsItem.split('::') : []
      return {
        entity: f.entity !== ALL ? f.entity : undefined,
        dateFrom: f.dateFrom || undefined,
        dateTo: f.dateTo || undefined,
        glAccountId: f.glAccountId !== ALL ? f.glAccountId : undefined,
        journalEntryNumber: f.journalEntryNumber.trim() || undefined,
        statementType: f.statementType !== ALL ? f.statementType : undefined,
        level2: plParts[1] || undefined,
        level3: plParts[2] || undefined,
        search: f.search.trim() || undefined,
        sortBy: 'date_desc' as const,
        offset: rowOffset,
        limit: PAGE_SIZE,
        includeTotal: withTotal,
      }
    },
    [],
  )

  const fetchPage = useCallback(
    async (f: Filters, rowOffset: number, append: boolean) => {
      setLoadingRows(true)
      setError(null)
      try {
        const res = await api.glLines(queryParams(f, rowOffset, !append))
        setRows(prev => (append ? [...prev, ...res.rows] : res.rows))
        setOffset(rowOffset)
        if (res.total_count != null) setTotalCount(res.total_count)
        else if (!append) setTotalCount(null)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Could not load bookings')
      } finally {
        setLoadingRows(false)
      }
    },
    [queryParams],
  )

  useEffect(() => {
    void loadMeta(draft.entity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.entity])

  useEffect(() => {
    if (!meta?.date_min || !meta?.date_max) return
    const next = defaultFilters(meta)
    setDraft(next)
    setApplied(next)
    void fetchPage(next, 0, false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta?.date_min, meta?.date_max])

  const applyFilters = useCallback(() => {
    setApplied(draft)
    void fetchPage(draft, 0, false)
  }, [draft, fetchPage])

  const plBsOptions = useMemo(() => {
    if (!meta) return []
    let items = meta.pl_bs_items
    if (draft.statementType !== ALL) {
      items = items.filter(i => i.statement_type === draft.statementType)
    }
    return items
  }, [meta, draft.statementType])

  const accountOptions = useMemo(() => {
    if (!meta) return []
    let accs = meta.accounts
    if (draft.statementType !== ALL) {
      accs = accs.filter(a => a.statement_type === draft.statementType)
    }
    if (draft.plBsItem !== ALL) {
      const [, l2, l3] = draft.plBsItem.split('::')
      accs = accs.filter(a => a.level_2 === l2 && (l3 ? a.level_3 === l3 : true))
    }
    return accs
  }, [meta, draft.statementType, draft.plBsItem])

  const fetchAllForExport = useCallback(async (): Promise<GlLine[]> => {
    const out: GlLine[] = []
    let off = 0
    const limit = 500
    for (;;) {
      const res = await api.glLines({ ...queryParams(applied, off, false), limit })
      out.push(...res.rows)
      if (!res.has_more || res.rows.length === 0) break
      off += res.rows.length
      if (off > 50_000) break
    }
    return out
  }, [applied, queryParams])

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (kind !== 'xlsx') return
      setExporting(true)
      try {
        const all = await fetchAllForExport()
        await exportAccountStatementRows(all, {
          entityLabel,
          dateFrom: applied.dateFrom,
          dateTo: applied.dateTo,
          entityDisplayName,
        })
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Export failed')
      } finally {
        setExporting(false)
      }
    },
    [applied, entityLabel, entityDisplayName, fetchAllForExport],
  )

  const hasMore = totalCount != null
    ? rows.length < totalCount
    : rows.length > 0 && rows.length % PAGE_SIZE === 0

  const selectClass =
    'w-full rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-xs text-slate-800 outline-none focus:border-[#1E3A5F]/40 focus:ring-2 focus:ring-[#1E3A5F]/10'

  return (
    <AnalyticsPageShell bootReady={!loadingMeta} bootLoading={loadingMeta}>
      <div className="mx-auto w-full max-w-[1920px] px-6 py-8 space-y-6">
        {/* Header */}
        <div>
          <p className="text-[12px] font-semibold uppercase tracking-wider text-slate-400">
            Reporting
          </p>
          <h1 className="text-2xl font-semibold text-slate-900 mt-1">Export</h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            GL bookings, trial balance, and monthly / annual report packages for download.
          </p>
        </div>

        {/* Sub-pages */}
        <div className="inline-flex rounded-xl border border-slate-200 p-1 bg-slate-50/80">
          <button
            type="button"
            onClick={() => setPageTab('bookings')}
            className={`rounded-lg px-4 py-2 text-xs font-medium transition-colors ${
              pageTab === 'bookings'
                ? 'bg-white text-[#1E3A5F] shadow-sm'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Bookings
          </button>
          <button
            type="button"
            onClick={() => setPageTab('trial-balance')}
            className={`rounded-lg px-4 py-2 text-xs font-medium transition-colors ${
              pageTab === 'trial-balance'
                ? 'bg-white text-[#1E3A5F] shadow-sm'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Trial balance
          </button>
          <button
            type="button"
            onClick={() => setPageTab('reports')}
            className={`rounded-lg px-4 py-2 text-xs font-medium transition-colors ${
              pageTab === 'reports'
                ? 'bg-white text-[#1E3A5F] shadow-sm'
                : 'text-slate-500 hover:text-slate-700'
            }`}
          >
            Reports
          </button>
        </div>

        {pageTab === 'reports' ? (
          <ReportsTab />
        ) : pageTab === 'trial-balance' ? (
          <TrialBalanceTab />
        ) : (
          <>
        <section
          className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm"
          aria-label="Filter panel"
        >
          <div className="flex items-center justify-between gap-4 mb-4">
            <h2 className="text-sm font-semibold text-slate-800">Filter panel</h2>
            <button
              type="button"
              onClick={() => void fetchPage(applied, 0, false)}
              disabled={loadingRows}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw size={14} className={loadingRows ? 'animate-spin' : ''} />
              Refresh
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <label className="block space-y-1">
              <span className="text-[12px] font-medium text-slate-500">Entity</span>
              <select
                className={selectClass}
                value={draft.entity}
                onChange={e => setDraft(f => ({ ...f, entity: e.target.value }))}
              >
                <option value={ALL}>All</option>
                {entities.map(e => (
                  <option key={e.legal_entity_code} value={e.legal_entity_code}>
                    {stripLegalForm(e.entity_name)}
                  </option>
                ))}
              </select>
            </label>

            <label className="block space-y-1">
              <span className="text-[12px] font-medium text-slate-500">Account type</span>
              <select
                className={selectClass}
                value={draft.statementType}
                onChange={e =>
                  setDraft(f => ({ ...f, statementType: e.target.value, plBsItem: ALL, glAccountId: ALL }))
                }
              >
                <option value={ALL}>All</option>
                <option value="PL">P&amp;L</option>
                <option value="BS">Balance sheet</option>
              </select>
            </label>

            <label className="block space-y-1">
              <span className="text-[12px] font-medium text-slate-500">P&amp;L / BS item</span>
              <select
                className={selectClass}
                value={draft.plBsItem}
                onChange={e =>
                  setDraft(f => ({ ...f, plBsItem: e.target.value, glAccountId: ALL }))
                }
              >
                <option value={ALL}>All</option>
                {plBsOptions.map(item => {
                  const key = plBsKey(item)
                  const label = item.level_3
                    ? `${item.level_2} / ${item.level_3}`
                    : item.level_2
                  return (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  )
                })}
              </select>
            </label>

            <label className="block space-y-1 md:col-span-2">
              <span className="text-[12px] font-medium text-slate-500">Account</span>
              <select
                className={selectClass}
                value={draft.glAccountId}
                onChange={e => setDraft(f => ({ ...f, glAccountId: e.target.value }))}
              >
                <option value={ALL}>All</option>
                {accountOptions.map(a => (
                  <option key={a.gl_account_id} value={a.gl_account_id}>
                    {a.gl_account_id} — {a.account_name}
                  </option>
                ))}
              </select>
            </label>

            <label className="block space-y-1">
              <span className="text-[12px] font-medium text-slate-500">Booking number</span>
              <input
                type="text"
                className={selectClass}
                placeholder="e.g. 1446566"
                value={draft.journalEntryNumber}
                onChange={e => setDraft(f => ({ ...f, journalEntryNumber: e.target.value }))}
              />
            </label>

            <label className="block space-y-1">
              <span className="text-[12px] font-medium text-slate-500">Period from</span>
              <input
                type="date"
                className={selectClass}
                value={draft.dateFrom}
                min={meta?.date_min?.slice(0, 10)}
                max={draft.dateTo || meta?.date_max?.slice(0, 10)}
                onChange={e => setDraft(f => ({ ...f, dateFrom: e.target.value }))}
              />
            </label>

            <label className="block space-y-1">
              <span className="text-[12px] font-medium text-slate-500">Period to</span>
              <input
                type="date"
                className={selectClass}
                value={draft.dateTo}
                min={draft.dateFrom || meta?.date_min?.slice(0, 10)}
                max={meta?.date_max?.slice(0, 10)}
                onChange={e => setDraft(f => ({ ...f, dateTo: e.target.value }))}
              />
            </label>

            <label className="block space-y-1 md:col-span-2">
              <span className="text-[12px] font-medium text-slate-500">Booking text</span>
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="text"
                  className={`${selectClass} pl-8`}
                  placeholder="Search in booking text…"
                  value={draft.search}
                  onChange={e => setDraft(f => ({ ...f, search: e.target.value }))}
                  onKeyDown={e => e.key === 'Enter' && applyFilters()}
                />
              </div>
            </label>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button type="button" onClick={applyFilters} className="btn-gold text-xs px-4 py-2">
              Apply filters
            </button>
            <button
              type="button"
              className="text-xs text-slate-500 hover:text-slate-700"
              onClick={() => {
                const reset = defaultFilters(meta)
                setDraft(reset)
                setApplied(reset)
                void fetchPage(reset, 0, false)
              }}
            >
              Reset
            </button>
            {meta?.date_min && meta?.date_max && (
              <span className="text-[12px] text-slate-400">
                Data range {fmtIsoToDe(meta.date_min.slice(0, 10))} – {fmtIsoToDe(meta.date_max.slice(0, 10))}
              </span>
            )}
          </div>
        </section>

        {/* Bookings table */}
        <section
          className="rounded-2xl border border-slate-200/80 bg-white shadow-sm overflow-hidden"
          aria-label="Bookings overview"
        >
          <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
            <div>
              <h2 className="text-sm font-semibold text-slate-800">Bookings overview</h2>
              <p className="text-xs text-slate-400 mt-0.5">
                {totalCount != null
                  ? `${rows.length.toLocaleString('en-US')} of ${totalCount.toLocaleString('en-US')} lines`
                  : `${rows.length.toLocaleString('en-US')} lines loaded`}
                {applied.glAccountId !== ALL && (
                  <> · Account {applied.glAccountId}</>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {exporting && (
                <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
                  <Loader2 size={14} className="animate-spin" />
                  Preparing export…
                </span>
              )}
              <PlExportMenu
                formats={['xlsx']}
                onExport={handleExport}
                disabled={exporting || loadingRows}
              />
            </div>
          </div>

          {error && (
            <div className="mx-5 mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
              {error}
            </div>
          )}

          <div className="overflow-x-auto max-h-[min(70vh,720px)]">
            <table className="w-full border-collapse text-xs">
              <thead className="sticky top-0 z-[1] bg-slate-50/95 backdrop-blur-sm">
                <tr className="border-b border-slate-200">
                  {['Entity', 'Booking number', 'Amount', 'Account number', 'Account', 'Posting date', 'Booking text'].map(
                    h => (
                      <th
                        key={h}
                        className="px-4 py-2.5 text-left font-semibold text-slate-500 whitespace-nowrap"
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {loadingRows && rows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                      <Loader2 size={20} className="animate-spin inline-block mr-2" />
                      Loading bookings…
                    </td>
                  </tr>
                ) : rows.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-slate-400">
                      No bookings match the current filters.
                    </td>
                  </tr>
                ) : (
                  rows.map((r, idx) => (
                    <tr
                      key={r.booking_line_id}
                      className={`border-b border-slate-100 hover:bg-slate-50/70 ${
                        idx % 2 === 1 ? 'bg-slate-50/30' : ''
                      }`}
                    >
                      <td className="px-4 py-2 whitespace-nowrap text-slate-700">
                        {entityDisplayName(r.legal_entity_code)}
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap">
                        <button
                          type="button"
                          onClick={() => setSelectedBookingId(r.booking_line_id)}
                          className="font-medium text-[#1E3A5F] hover:underline tabular-nums"
                        >
                          {r.journal_entry_number}
                        </button>
                      </td>
                      <td className="px-4 py-2 text-right whitespace-nowrap tabular-nums font-medium text-slate-800">
                        {displayBookingAmount(r.amount_signed)}
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap tabular-nums text-slate-600">
                        {r.gl_account_id}
                      </td>
                      <td className="px-4 py-2 max-w-[200px] truncate text-slate-700" title={r.account_name}>
                        {r.account_name}
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap text-slate-600">
                        {fmtIsoToDe(r.posting_date)}
                      </td>
                      <td className="px-4 py-2 max-w-[280px] truncate text-slate-500" title={r.booking_text ?? ''}>
                        {r.booking_text || '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {(hasMore || rows.length > PAGE_SIZE) && (
            <div className="flex items-center justify-between px-5 py-3 border-t border-slate-100 bg-slate-50/50">
              <span className="text-[12px] text-slate-400">
                Showing {rows.length.toLocaleString('en-US')}
                {totalCount != null ? ` / ${totalCount.toLocaleString('en-US')}` : ''} bookings
              </span>
              <div className="flex gap-2">
                {hasMore && (
                  <button
                    type="button"
                    disabled={loadingRows}
                    onClick={() => void fetchPage(applied, offset + PAGE_SIZE, true)}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Load more
                  </button>
                )}
              </div>
            </div>
          )}
        </section>

        <p className="text-[12px] text-slate-400 text-right pb-2">
          Export · Finssentials © {new Date().getFullYear()}
        </p>
          </>
        )}
      </div>

      <PlDetailBookingJournalModal
        bookingLineId={selectedBookingId}
        onClose={() => setSelectedBookingId(null)}
      />
    </AnalyticsPageShell>
  )
}
