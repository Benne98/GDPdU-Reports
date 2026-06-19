import { useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp, GripVertical, Pencil, X } from 'lucide-react'
import type { MonthlyPeriod } from '../../../lib/api'
import { periodKey as pk, periodLabel } from './plPeriodLabels'
import {
  makeAggregateColumn,
  makeAggregateVsPlanColumn,
  makeAggregateVsPyColumn,
  reconcileMonthlyColumns,
  saveMonthlyColumns,
  sortPeriodKeys,
  type MonthlyViewColumnDef,
} from './monthlyColumnRegistry'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from './plToolbarButton'

type Props = {
  periods: MonthlyPeriod[]
  columns: MonthlyViewColumnDef[]
  onChange: (cols: MonthlyViewColumnDef[]) => void
  statement?: string
  showPlanVariances?: boolean
}

function Section({ title, children, defaultOpen = true }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold text-slate-700 bg-slate-50 hover:bg-slate-100"
      >
        {title}
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="p-3 space-y-2">{children}</div>}
    </div>
  )
}

export default function MonthlyColumnEditor({
  periods,
  columns,
  onChange,
  statement = 'pl',
  showPlanVariances = true,
}: Props) {
  const [open, setOpen] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(() => new Set())

  const periodOptions = useMemo(
    () => periods.map(p => ({ key: pk(p.year, p.month), label: periodLabel(p.year, p.month) })),
    [periods],
  )

  const activeIds = useMemo(() => new Set(columns.map(c => c.id)), [columns])

  function persist(next: MonthlyViewColumnDef[]) {
    const reconciled = reconcileMonthlyColumns(next, periods)
    onChange(reconciled)
    saveMonthlyColumns(reconciled, statement)
  }

  function toggleKey(key: string) {
    setSelectedKeys(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  function addAggregate() {
    const keys = sortPeriodKeys([...selectedKeys])
    if (!keys.length) return
    const agg = makeAggregateColumn(keys, periods)
    if (activeIds.has(agg.id)) return
    persist([...columns, agg])
  }

  function addVsPy() {
    const keys = sortPeriodKeys([...selectedKeys])
    if (!keys.length) return
    const col = makeAggregateVsPyColumn(keys, periods)
    if (activeIds.has(col.id)) return
    persist([...columns, col])
  }

  function addVsPlan() {
    const keys = sortPeriodKeys([...selectedKeys])
    if (!keys.length) return
    const col = makeAggregateVsPlanColumn(keys, periods)
    if (activeIds.has(col.id)) return
    persist([...columns, col])
  }

  function removeAt(idx: number) {
    persist(columns.filter((_, i) => i !== idx))
  }

  function move(idx: number, dir: -1 | 1) {
    const j = idx + dir
    if (j < 0 || j >= columns.length) return
    const next = [...columns]
    const [item] = next.splice(idx, 1)
    next.splice(j, 0, item)
    persist(next)
  }

  function onDragDrop(toIdx: number) {
    if (dragIdx == null || dragIdx === toIdx) return
    const next = [...columns]
    const [item] = next.splice(dragIdx, 1)
    next.splice(toIdx, 0, item)
    setDragIdx(null)
    persist(next)
  }

  const selectionCount = selectedKeys.size

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        title="Build monthly columns"
        className={PL_TOOLBAR_ICON_BTN}
        style={PL_TOOLBAR_BTN_STYLE}
      >
        <Pencil size={14} strokeWidth={1.75} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/20" onClick={() => setOpen(false)} aria-hidden />
          <div
            className="fixed inset-y-0 right-0 z-50 w-full max-w-md shadow-2xl flex flex-col"
            style={{ background: '#fff', borderLeft: '1px solid #E2E8F0' }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
              <div>
                <p className="text-sm font-semibold text-slate-900">Table builder</p>
                <p className="text-[0.65rem] text-slate-500 mt-0.5">Monthly view — period totals &amp; variances</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">
                  Column order ({columns.length})
                </p>
                {columns.length === 0 ? (
                  <p className="text-xs text-slate-500">No extra columns — 12-month grid only.</p>
                ) : (
                  <ul className="space-y-1">
                    {columns.map((c, idx) => (
                      <li
                        key={c.id}
                        draggable
                        onDragStart={() => setDragIdx(idx)}
                        onDragOver={e => e.preventDefault()}
                        onDrop={() => onDragDrop(idx)}
                        onDragEnd={() => setDragIdx(null)}
                        className={`flex items-center gap-1 rounded-lg border px-2 py-1.5 bg-white ${
                          dragIdx === idx ? 'border-[#1E3A5F] ring-1 ring-[#1E3A5F]/20' : 'border-slate-200'
                        }`}
                      >
                        <GripVertical size={14} className="shrink-0 text-slate-400 cursor-grab" />
                        <p className="flex-1 text-xs font-medium text-slate-800 truncate">{c.labelLine1}</p>
                        <div className="flex shrink-0">
                          <button type="button" onClick={() => move(idx, -1)} disabled={idx === 0} className="p-1 text-slate-500 disabled:opacity-30" aria-label="Move up">
                            <ChevronUp size={14} />
                          </button>
                          <button type="button" onClick={() => move(idx, 1)} disabled={idx === columns.length - 1} className="p-1 text-slate-500 disabled:opacity-30" aria-label="Move down">
                            <ChevronDown size={14} />
                          </button>
                          <button type="button" onClick={() => removeAt(idx)} className="p-1 text-slate-500 hover:text-rose-600" aria-label="Remove">
                            <X size={14} />
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <Section title="Period total" defaultOpen>
                <p className="text-[0.65rem] text-slate-500 mb-2">
                  Select one or more months, then add a total column and optional variances vs prior year or plan.
                </p>
                <div className="flex flex-wrap gap-1.5 mb-3 max-h-40 overflow-y-auto">
                  {periodOptions.map(o => {
                    const on = selectedKeys.has(o.key)
                    return (
                      <button
                        key={o.key}
                        type="button"
                        onClick={() => toggleKey(o.key)}
                        className="px-2 py-1 rounded-md text-[0.7rem] font-medium transition-colors"
                        style={{
                          border: `1px solid ${on ? 'rgba(30,58,95,0.35)' : '#E2E8F0'}`,
                          background: on ? 'rgba(30,58,95,0.08)' : '#fff',
                          color: on ? '#1E3A5F' : '#475569',
                        }}
                      >
                        {o.label}
                      </button>
                    )
                  })}
                </div>
                <div className="flex flex-col gap-1.5">
                  <button
                    type="button"
                    disabled={selectionCount === 0}
                    onClick={addAggregate}
                    className="text-xs font-medium px-2 py-1.5 rounded-lg disabled:opacity-40"
                    style={{ color: '#1E3A5F', background: 'rgba(30,58,95,0.06)', border: '1px solid rgba(30,58,95,0.15)' }}
                  >
                    Add period total{selectionCount > 0 ? ` (${selectionCount}M)` : ''}
                  </button>
                  <button
                    type="button"
                    disabled={selectionCount === 0}
                    onClick={addVsPy}
                    className="px-2 py-1.5 rounded-md text-[0.7rem] text-left disabled:opacity-40 hover:bg-slate-50"
                    style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                  >
                    + ∆ vs prior year (same months)
                  </button>
                  {showPlanVariances ? (
                    <button
                      type="button"
                      disabled={selectionCount === 0}
                      onClick={addVsPlan}
                      className="px-2 py-1.5 rounded-md text-[0.7rem] text-left disabled:opacity-40 hover:bg-slate-50"
                      style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                    >
                      + ∆ vs plan (sum of months)
                    </button>
                  ) : null}
                </div>
              </Section>

              <button
                type="button"
                onClick={() => {
                  persist([])
                  setSelectedKeys(new Set())
                }}
                className="text-xs font-medium w-full py-2 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
              >
                Reset — 12 months only
              </button>
            </div>
          </div>
        </>
      )}
    </>
  )
}
