import { useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import {
  api,
  type Entity,
  type TrialBalanceColumn,
  type TrialBalanceExportResponse,
  type TrialBalanceRow,
  type TrialBalanceSheetPayload,
} from '../../lib/api'
import { stripLegalForm } from '../../lib/stripLegalForm'
import PlExportMenu, { type PlExportKind } from '../financials/pl-two-view/PlExportMenu'
import { exportTrialBalanceXlsx } from './trialBalanceExport'
import { fmtTrialBalanceAmount } from './exportTrialBalance'

const ALL = '__all__'

type StmtTab = 'PL' | 'BS'

function anchorFromDateMax(iso: string | null | undefined): { year: number; month: number } | null {
  if (!iso) return null
  const [y, m] = iso.slice(0, 10).split('-').map(Number)
  if (!y || !m) return null
  return { year: y, month: m }
}

function cellValue(
  row: TrialBalanceRow,
  col: TrialBalanceColumn,
  entityDisplayName: (code: string) => string,
): string {
  if (col.group === 'spacer') return ''
  if (col.key === 'entity') return entityDisplayName(row.entity)
  if (col.key === 'level_1') return row.level_1 ?? ''
  if (col.key === 'level_2') return row.level_2
  if (col.key === 'level_3') return row.level_3
  if (col.key === 'level_4') return row.level_4 ?? ''
  if (col.key === 'account') return row.account
  const v = row.amounts[col.key]
  return v == null ? '—' : fmtTrialBalanceAmount(v)
}

export default function TrialBalanceTab() {
  const [entities, setEntities] = useState<Entity[]>([])
  const [entity, setEntity] = useState(ALL)
  const [year, setYear] = useState(2025)
  const [month, setMonth] = useState(7)
  const [stmtTab, setStmtTab] = useState<StmtTab>('PL')
  const [data, setData] = useState<TrialBalanceExportResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
    if (entity === ALL) return 'All entities'
    return entityDisplayName(entity)
  }, [entity, entityDisplayName])

  const load = useCallback(async (y: number, m: number, entCode: string) => {
    setLoading(true)
    setError(null)
    try {
      const ent = entCode === ALL ? undefined : entCode
      const payload = await api.trialBalanceExport({ year: y, month: m, entity: ent })
      setData(payload)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not load trial balance')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        const [entList, filters] = await Promise.all([api.entities(), api.glLinesFilters()])
        setEntities(entList)
        const anchor = anchorFromDateMax(filters.date_max)
        if (anchor) {
          setYear(anchor.year)
          setMonth(anchor.month)
        }
      } catch {
        /* keep defaults */
      }
    })()
  }, [])

  useEffect(() => {
    void load(year, month, entity)
  }, [entity, year, month, load])

  const activeSheet: TrialBalanceSheetPayload | null =
    data == null ? null : stmtTab === 'PL' ? data.pl : data.bs

  const displayCols = useMemo(
    () => (activeSheet ? activeSheet.columns : []),
    [activeSheet],
  )

  const monthlyRangeLabel = useMemo(() => {
    const monthly = displayCols.filter(c => c.group === 'monthly')
    if (monthly.length === 0) return null
    const first = monthly[0].label.replace(/A$/, '')
    const last = monthly[monthly.length - 1].label.replace(/A$/, '')
    return `${first} – ${last}`
  }, [displayCols])

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (kind !== 'xlsx' || !data) return
      setExporting(true)
      try {
        await exportTrialBalanceXlsx(data.pl, data.bs, { entityLabel, year, month })
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Export failed')
      } finally {
        setExporting(false)
      }
    },
    [data, entityLabel, year, month],
  )

  const selectClass =
    'w-full rounded-lg border border-slate-200 bg-slate-50/80 px-3 py-2 text-xs text-slate-800 outline-none focus:border-[#1E3A5F]/40 focus:ring-2 focus:ring-[#1E3A5F]/10'

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm">
        <div className="flex items-center justify-between gap-4 mb-4">
          <h3 className="text-sm font-semibold text-slate-800">Settings</h3>
          <button
            type="button"
            onClick={() => void load(year, month, entity)}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <label className="block space-y-1">
            <span className="text-[12px] font-medium text-slate-500">Entity</span>
            <select className={selectClass} value={entity} onChange={e => setEntity(e.target.value)}>
              <option value={ALL}>All</option>
              {entities.map(e => (
                <option key={e.legal_entity_code} value={e.legal_entity_code}>
                  {stripLegalForm(e.entity_name)}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span className="text-[12px] font-medium text-slate-500">Anchor year</span>
            <input
              type="number"
              className={selectClass}
              min={2000}
              max={2100}
              value={year}
              onChange={e => setYear(Number(e.target.value))}
            />
          </label>
          <label className="block space-y-1">
            <span className="text-[12px] font-medium text-slate-500">Anchor month</span>
            <select className={selectClass} value={month} onChange={e => setMonth(Number(e.target.value))}>
              {Array.from({ length: 12 }, (_, i) => i + 1).map(m => (
                <option key={m} value={m}>
                  {String(m).padStart(2, '0')}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white shadow-sm overflow-hidden">
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-slate-100">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-slate-800">
              {stmtTab === 'PL' ? 'P&L trial balance' : 'Balance sheet trial balance'}
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              {activeSheet
                ? `${activeSheet.row_count.toLocaleString('en-US')} accounts`
                : '—'}
              {monthlyRangeLabel ? ` · ${monthlyRangeLabel}` : ''}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <div className="inline-flex rounded-lg border border-slate-200 p-0.5 bg-slate-50">
              {(['PL', 'BS'] as const).map(tab => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setStmtTab(tab)}
                  className={`rounded-md px-4 py-2 text-xs font-medium transition-colors ${
                    stmtTab === tab
                      ? 'bg-white text-[#1E3A5F] shadow-sm'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {tab === 'PL' ? 'P&L accounts' : 'Balance sheet'}
                </button>
              ))}
            </div>
            {exporting && (
              <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
                <Loader2 size={14} className="animate-spin" />
                Preparing export…
              </span>
            )}
            <PlExportMenu
              formats={['xlsx']}
              onExport={handleExport}
              disabled={exporting || loading || !data}
            />
          </div>
        </div>

        {error && (
          <div className="mx-5 mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
            {error}
          </div>
        )}

        <div className="overflow-x-auto max-h-[min(70vh,720px)]">
          <table className="w-full border-collapse text-xs min-w-[960px]">
            <thead className="sticky top-0 z-[1] bg-slate-50/95 backdrop-blur-sm">
              <tr className="border-b border-slate-200">
                {displayCols.map(col => (
                  <th
                    key={col.key}
                    className={`px-3 py-2.5 font-semibold text-slate-500 whitespace-nowrap ${
                      col.group === 'summary' ? 'bg-slate-100/80' : ''
                    } ${col.group === 'dim' || col.group === 'spacer' ? 'text-left' : 'text-right'}`}
                  >
                    {col.label || '\u00a0'}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading && !activeSheet ? (
                <tr>
                  <td colSpan={displayCols.length || 8} className="px-4 py-12 text-center text-slate-400">
                    <Loader2 size={20} className="animate-spin inline-block mr-2" />
                    Loading trial balance…
                  </td>
                </tr>
              ) : !activeSheet || activeSheet.rows.length === 0 ? (
                <tr>
                  <td colSpan={displayCols.length || 8} className="px-4 py-12 text-center text-slate-400">
                    No mapped accounts for the selected filters.
                  </td>
                </tr>
              ) : (
                activeSheet.rows.slice(0, 500).map((row, idx) => (
                  <tr
                    key={`${row.entity}-${row.account}-${idx}`}
                    className={`border-b border-slate-100 hover:bg-slate-50/70 ${
                      idx % 2 === 1 ? 'bg-slate-50/30' : ''
                    }`}
                  >
                    {displayCols.map(col => (
                      <td
                        key={col.key}
                        className={`px-3 py-2 whitespace-nowrap ${
                          col.key === 'account' ? 'max-w-[240px] truncate text-slate-700' : 'text-slate-600'
                        } ${col.group !== 'dim' && col.group !== 'spacer' ? 'text-right tabular-nums' : ''}`}
                        title={col.key === 'account' ? row.account : undefined}
                      >
                        {cellValue(row, col, entityDisplayName)}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {activeSheet && activeSheet.rows.length > 500 && (
          <p className="px-5 py-3 text-[12px] text-slate-400 border-t border-slate-100">
            Preview limited to 500 rows. Export includes all{' '}
            {activeSheet.row_count.toLocaleString('en-US')} accounts.
          </p>
        )}
      </section>
    </div>
  )
}
