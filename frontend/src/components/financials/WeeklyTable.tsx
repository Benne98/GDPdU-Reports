import { useEffect, useMemo, useRef, useState } from 'react'
import { Pencil, X } from 'lucide-react'
import type { WeeklyBreakdownResponse, WeeklyBreakdownRow } from '../../lib/api'
import { fmtKpi, fmtPct } from '../../lib/fmt'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from './finReportLayout'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureWeeklySnapshot } from '../action-notes/captureWeeklyTable'

const STORAGE_KEY = 'finssentials.pl.weekly.columns.v1'

function loadVisibleKeys(): Set<string> | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.every(v => typeof v === 'string')) {
      return new Set(parsed as string[])
    }
  } catch {
    // ignore
  }
  return null
}

function saveVisibleKeys(keys: string[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(keys))
  } catch {
    // ignore
  }
}

interface WeeklyTableProps {
  data: WeeklyBreakdownResponse | null
  loading: boolean
  showColumnEditor?: boolean
}

type FlatCol = {
  key: string
  label: string
  isTotal: boolean
  groupMonthLabel: string
}

export default function WeeklyTable({ data, loading, showColumnEditor = false }: WeeklyTableProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(() => new Set())
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())
  const notesCtx = useOptionalActionNotesContext()

  // Build flat column list
  const allCols = useMemo((): FlatCol[] => {
    if (!data) return []
    const cols: FlatCol[] = []
    for (const g of data.groups) {
      for (const w of g.weeks) {
        cols.push({ key: w.key, label: w.label, isTotal: false, groupMonthLabel: g.month_label })
      }
      cols.push({ key: g.total.key, label: g.total.label, isTotal: true, groupMonthLabel: g.month_label })
    }
    return cols
  }, [data])

  // Initialise hidden keys from localStorage once we have the column list
  useEffect(() => {
    if (!allCols.length) return
    const stored = loadVisibleKeys()
    if (stored) {
      const hidden = new Set<string>()
      for (const c of allCols) {
        if (!stored.has(c.key)) hidden.add(c.key)
      }
      setHiddenKeys(hidden)
    }
  }, [allCols.map(c => c.key).join(',')])  // eslint-disable-line react-hooks/exhaustive-deps

  const visibleCols = useMemo(() => allCols.filter(c => !hiddenKeys.has(c.key)), [allCols, hiddenKeys])

  // ─── Pin registration ──────────────────────────────────────────────────────
  useEffect(() => {
    const stmt = data?.statement ?? 'pl'
    const pinId = `${stmt}-weekly`
    if (!notesCtx || !data?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    const visibleColumnKeys = visibleCols.map(c => c.key)
    const pinLabel = data.statement === 'cf'
      ? 'Cash flow — weekly view'
      : 'Income statement — weekly view'
    notesCtx.registerTableCandidate({
      id: pinId,
      label: pinLabel,
      description: 'Weekly breakdown by month — amounts in EURk',
      capture: () => captureWeeklySnapshot(data, 'WeeklyTable', visibleColumnKeys),
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, data, visibleCols])

  // Scroll to right on data change
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollLeft = el.scrollWidth
  }, [data])

  function toggleCol(key: string) {
    setHiddenKeys(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      const visibleNow = allCols.filter(c => !next.has(c.key)).map(c => c.key)
      saveVisibleKeys(visibleNow)
      return next
    })
  }

  function resetCols() {
    setHiddenKeys(new Set())
    saveVisibleKeys(allCols.map(c => c.key))
  }

  const checkOpen = (id: string) => userToggles.has(id)
  function toggle(id: string) {
    setUserToggles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const totalCols = 1 + visibleCols.length

  function renderRow(row: WeeklyBreakdownRow, depth: number): JSX.Element[] {
    const pad = 12 + depth * 14
    const isTitle = row.row_kind === 'title'
    const isKpiHeader = row.row_kind === 'kpi_header'
    const isKpi = row.row_kind === 'kpi'
    const isSubtotal = row.row_kind === 'subtotal'
    const isBold = row.is_bold || isSubtotal
    const isOpen = checkOpen(row.id)
    const hasChildren = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0
    const nodes: JSX.Element[] = []

    if (isTitle) {
      nodes.push(
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
          <td colSpan={totalCols} className="px-3 py-2 text-xs font-bold uppercase tracking-wide" style={{ color: '#1E3A5F' }}>
            {row.label}
          </td>
        </tr>,
      )
    } else if (isKpiHeader) {
      nodes.push(
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
          <td className="px-3 py-2 text-xs font-semibold italic" style={{ color: '#1E3A5F', paddingLeft: 12 }}>
            {row.label}
          </td>
          {visibleCols.map(c => (
            <td
              key={`${row.id}-${c.key}`}
              style={{
                background: c.isTotal ? 'rgba(30,58,95,0.06)' : '#F8FAFC',
                borderLeft: c.isTotal ? '2px solid rgba(30,58,95,0.15)' : undefined,
              }}
            />
          ))}
        </tr>,
      )
    } else {
      nodes.push(
        <tr
          key={row.id}
          style={{
            borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
            borderTop: isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
            background: isKpi ? '#F8FAFC' : isSubtotal && depth === 0 ? '#F8FAFC' : undefined,
            fontStyle: isKpi ? 'italic' : undefined,
          }}
        >
          <td
            className={`${FIN_TABLE_CELL_CLASS} text-left whitespace-nowrap`}
            style={{ minWidth: 168, paddingLeft: pad, paddingRight: 8, fontSize: FIN_TABLE_VALUE_FONT }}
          >
            <div className="flex items-center gap-0.5">
              {hasChildren ? (
                <button
                  type="button"
                  onClick={() => toggle(row.id)}
                  className="p-0.5 rounded shrink-0"
                  style={{ color: '#1E3A5F' }}
                  aria-expanded={isOpen}
                >
                  <svg
                    width={14}
                    height={14}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}
                  >
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </button>
              ) : (
                <span style={{ width: 22 }} />
              )}
              <span
                className="text-xs"
                style={{
                  fontWeight: isBold ? 600 : 400,
                  color: isKpi ? '#64748B' : '#111827',
                  fontStyle: isKpi ? 'italic' : undefined,
                }}
              >
                {row.label}
              </span>
            </div>
          </td>
          {visibleCols.map(c => {
            const v = row.amounts?.[c.key]
            const cellBg = c.isTotal ? 'rgba(30,58,95,0.06)' : undefined
            const borderL = c.isTotal ? '2px solid rgba(30,58,95,0.15)' : undefined
            if (v == null) {
              return (
                <td
                  key={c.key}
                  className={`${FIN_TABLE_CELL_CLASS} text-right text-xs`}
                  style={{ background: cellBg, borderLeft: borderL, color: '#CBD5E1', fontSize: FIN_TABLE_VALUE_FONT }}
                >
                  —
                </td>
              )
            }
            return (
              <td
                key={c.key}
                className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
                style={{
                  background: cellBg,
                  borderLeft: borderL,
                  fontWeight: isBold ? 600 : 400,
                  fontSize: FIN_TABLE_VALUE_FONT,
                  color: isKpi ? '#475569' : '#111827',
                  fontStyle: isKpi ? 'italic' : undefined,
                }}
              >
                {isKpi ? fmtPct(v) : fmtKpi(v)}
              </td>
            )
          })}
        </tr>,
      )
    }

    if (isOpen && row.children?.length) {
      for (const ch of row.children) {
        nodes.push(...renderRow(ch, depth + 1))
      }
    }
    const accounts = row.accounts
    if (isOpen && accounts?.length) {
      for (const acc of accounts) {
        nodes.push(...renderRow(acc, depth + 1))
      }
    }
    return nodes
  }

  function walkRows(rows: WeeklyBreakdownRow[]): JSX.Element[] {
    const nodes: JSX.Element[] = []
    let kpiHeaderInserted = false
    for (const row of rows) {
      if (row.row_kind === 'kpi' && !kpiHeaderInserted) {
        kpiHeaderInserted = true
        nodes.push(
          <tr key="weekly-kpi-divider" style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
            <td className="px-3 py-1.5 text-[12px] font-semibold italic" style={{ color: '#1E3A5F' }}>
              KPIs — as % of total output
            </td>
            {visibleCols.map(c => (
              <td key={`kpi-div-${c.key}`} style={{ background: c.isTotal ? 'rgba(30,58,95,0.06)' : '#F8FAFC' }} />
            ))}
          </tr>,
        )
      }
      nodes.push(...renderRow(row, 0))
    }
    return nodes
  }

  if (loading) {
    return (
      <div className="rounded-xl p-8 text-center text-sm mt-4" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        Loading weekly view…
      </div>
    )
  }
  if (!data?.rows.length) return null

  // Build group header spans for the two-row header
  const groupSpans = data.groups.map(g => {
    const count = g.weeks.filter(w => !hiddenKeys.has(w.key)).length +
      (hiddenKeys.has(g.total.key) ? 0 : 1)
    return { label: g.month_label, count }
  }).filter(s => s.count > 0)

  return (
    <div
      className="rounded-xl mt-4 overflow-hidden flex flex-col"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div
        className="px-4 pt-4 pb-3 flex items-start justify-between gap-3 shrink-0"
        style={{ borderBottom: '1px solid #F1F5F9' }}
      >
        <div>
          <span className="text-sm font-semibold" style={{ color: '#111827' }}>Income statement — weekly view</span>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            Weekly breakdown by month — amounts in EURk
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {showColumnEditor && (
            <button
              type="button"
              onClick={() => setEditorOpen(true)}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium"
              style={{ background: '#F4F6F9', border: '1px solid #E2E8F0', color: '#1E3A5F' }}
            >
              <Pencil size={12} />
              Columns
            </button>
          )}
        </div>
      </div>

      <div ref={scrollRef} className="overflow-x-auto px-4 pb-4">
        <table className="w-full border-collapse text-xs">
          <thead>
            {/* Row 1: month group labels */}
            <tr style={{ background: '#F8FAFC', borderBottom: '1px solid #E2E8F0' }}>
              <th rowSpan={2} className="px-3 py-2 text-left font-semibold align-bottom" style={{ color: '#64748B', minWidth: 168, fontSize: FIN_TABLE_VALUE_FONT }}>
                EURk
              </th>
              {groupSpans.map(gs => (
                <th
                  key={gs.label}
                  colSpan={gs.count}
                  className="px-2 py-1.5 text-center font-semibold whitespace-nowrap border-l border-slate-200"
                  style={{ color: '#1E3A5F', fontSize: FIN_TABLE_VALUE_FONT }}
                >
                  {gs.label}
                </th>
              ))}
            </tr>
            {/* Row 2: individual week / total headers */}
            <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
              {visibleCols.map(c => (
                <th
                  key={c.key}
                  className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap`}
                  style={{
                    color: c.isTotal ? '#1E3A5F' : '#64748B',
                    fontSize: FIN_TABLE_VALUE_FONT,
                    fontWeight: c.isTotal ? 700 : 600,
                    background: c.isTotal ? 'rgba(30,58,95,0.06)' : undefined,
                    borderLeft: c.isTotal ? '2px solid rgba(30,58,95,0.15)' : '1px solid rgba(30,58,95,0.06)',
                  }}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{walkRows(data.rows)}</tbody>
        </table>
      </div>

      {/* Column editor panel */}
      {editorOpen && showColumnEditor && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/20" onClick={() => setEditorOpen(false)} aria-hidden />
          <div className="fixed inset-y-0 right-0 z-50 w-full max-w-sm shadow-2xl flex flex-col" style={{ background: '#fff', borderLeft: '1px solid #E2E8F0' }}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
              <div>
                <p className="text-sm font-semibold text-slate-900">Column visibility</p>
                <p className="text-[0.65rem] text-slate-500 mt-0.5">Toggle weeks and totals</p>
              </div>
              <button type="button" onClick={() => setEditorOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
                <X size={18} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {data.groups.map(g => (
                <section key={g.month_key}>
                  <p className="text-xs font-semibold text-slate-700 mb-2">{g.month_label}</p>
                  <div className="space-y-1">
                    {[...g.weeks, { key: g.total.key, label: g.total.label }].map(c => {
                      const isTotal = c.key === g.total.key
                      const visible = !hiddenKeys.has(c.key)
                      return (
                        <label key={c.key} className="flex items-center gap-2 cursor-pointer rounded px-2 py-1.5 hover:bg-slate-50">
                          <input
                            type="checkbox"
                            checked={visible}
                            onChange={() => toggleCol(c.key)}
                            className="rounded"
                          />
                          <span className="text-xs text-slate-700" style={{ fontWeight: isTotal ? 600 : 400 }}>
                            {c.label}
                            {isTotal ? ' (Total)' : ''}
                          </span>
                        </label>
                      )
                    })}
                  </div>
                </section>
              ))}
              <button
                type="button"
                onClick={resetCols}
                className="text-xs font-medium w-full py-2 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
              >
                Reset — show all columns
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
