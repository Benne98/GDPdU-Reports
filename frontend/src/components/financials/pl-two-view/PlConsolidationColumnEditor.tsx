import { useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp, GripVertical, Pencil, X } from 'lucide-react'
import type { ConsolidationResponse, MonthlyResponse } from '../../../lib/api'
import type { FinancialStatementColLabels } from '../../../lib/api'
import { periodKey as pk } from './plPeriodLabels'
import {
  CONSOL_STANDARD_GROUPS,
  buildConsolidationColumnCatalog,
  consolidationMonthDelta,
  makeConsolidationColumn,
  reconcileConsolidationColumns,
  saveConsolidationColumns,
  type PlConsolidationColumnDef,
  type PlConsolidationTargetScope,
} from './plConsolidationColumnRegistry'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from './plToolbarButton'

const PLAN_COLUMN_KINDS = new Set([
  'plan_cm',
  'plan_vs_actual',
  'ytd_plan',
  'ytd_vs_plan',
  'ytg',
  'coverage',
])

function consolStandardGroups(statement: string) {
  if (statement === 'pl') return CONSOL_STANDARD_GROUPS
  return CONSOL_STANDARD_GROUPS.map(g => ({
    ...g,
    kinds: g.kinds.filter(k => !PLAN_COLUMN_KINDS.has(k)),
  })).filter(g => g.kinds.length > 0)
}

type Props = {
  consol: ConsolidationResponse
  colLabels: FinancialStatementColLabels | undefined
  monthly: MonthlyResponse | null
  columns: PlConsolidationColumnDef[]
  onChange: (cols: PlConsolidationColumnDef[]) => void
  carouselEntityCode?: string
  /** pl | bs | wc | cf — separate localStorage keys per statement */
  statement?: string
}

const TARGET_OPTIONS: Array<{ id: PlConsolidationTargetScope; label: string }> = [
  { id: 'single_entity', label: 'This entity' },
  { id: 'all_entities', label: 'All entities' },
  { id: 'aggregated', label: 'Aggregated' },
  { id: 'consolidation', label: 'Consolidation' },
]

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

function targetLabel(c: PlConsolidationColumnDef, entities: ConsolidationResponse['entities']): string {
  switch (c.target) {
    case 'aggregated':
      return 'Aggregated'
    case 'consolidation':
      return 'Consolidation'
    case 'all_entities':
      return 'All entities'
    case 'single_entity': {
      const ent = entities.find(e => e.code === c.entityCode)
      return ent?.label ?? c.entityCode ?? 'Entity'
    }
    default:
      return c.target
  }
}

export default function PlConsolidationColumnEditor({
  consol,
  colLabels,
  monthly,
  columns,
  onChange,
  carouselEntityCode,
  statement = 'pl',
}: Props) {
  const [open, setOpen] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [target, setTarget] = useState<PlConsolidationTargetScope>('all_entities')
  const [cmpMonth, setCmpMonth] = useState('')
  const [customA, setCustomA] = useState('')
  const [customB, setCustomB] = useState('')

  const periods = monthly?.periods ?? []
  const catalog = useMemo(
    () => (colLabels ? buildConsolidationColumnCatalog(colLabels, periods) : new Map()),
    [colLabels, periods],
  )
  const activeIds = useMemo(() => new Set(columns.map(c => c.id)), [columns])

  function persist(next: PlConsolidationColumnDef[]) {
    const reconciled = reconcileConsolidationColumns(next, catalog, periods)
    onChange(reconciled)
    saveConsolidationColumns(reconciled, statement)
  }

  function addColumn(templateId: string) {
    const template = catalog.get(templateId)
    if (!template || activeIds.has(templateId)) return

    const entityCode =
      target === 'single_entity' ? carouselEntityCode ?? consol.entities[0]?.code : undefined
    if (target === 'single_entity' && !entityCode) return

    if (target === 'all_entities') {
      const additions = consol.entities
        .map(e => makeConsolidationColumn('single_entity', e.code, template))
        .filter(c => !activeIds.has(c.id))
      if (!additions.length) return
      persist([...columns, ...additions])
      return
    }

    const col = makeConsolidationColumn(target, entityCode, template)
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

  const cmpKey = cmpMonth || (periods.length ? pk(periods[periods.length - 1].year, periods[periods.length - 1].month) : '')
  const periodOptions = periods.map(p => ({ key: pk(p.year, p.month), label: p.label }))

  if (!colLabels) return null

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        title="Build table columns"
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
                <p className="text-[0.65rem] text-slate-500 mt-0.5">Entity breakdown — order and add columns</p>
              </div>
              <button type="button" onClick={() => setOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">Apply columns to</p>
                <div className="grid grid-cols-2 gap-1 p-0.5 rounded-lg bg-slate-100">
                  {TARGET_OPTIONS.map(opt => (
                    <button
                      key={opt.id}
                      type="button"
                      className={`text-[0.65rem] font-medium py-1.5 rounded-md ${
                        target === opt.id ? 'bg-white shadow text-[#1E3A5F]' : 'text-slate-600'
                      }`}
                      onClick={() => setTarget(opt.id)}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </section>

              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">
                  Column order ({columns.length})
                </p>
                {columns.length === 0 ? (
                  <p className="text-xs text-slate-500">No extra columns — showing current month only.</p>
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
                        <div className="flex-1 min-w-0">
                          <p className="text-[0.65rem] text-slate-500 truncate">{targetLabel(c, consol.entities)}</p>
                          <p className="text-xs font-medium text-slate-800 truncate">{c.labelLine1}</p>
                        </div>
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

              {consolStandardGroups(statement).map(group => (
                <Section key={group.title} title={group.title} defaultOpen={group.title.includes('Current')}>
                  <div className="flex flex-wrap gap-1.5">
                    {group.kinds.map(kind => {
                      const def = catalog.get(kind)
                      if (!def) return null
                      return (
                        <button
                          key={kind}
                          type="button"
                          disabled={target === 'single_entity' && !carouselEntityCode && !consol.entities[0]}
                          onClick={() => addColumn(kind)}
                          className="px-2 py-1.5 rounded-md text-[0.7rem] text-left hover:bg-slate-50 disabled:opacity-40"
                          style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                        >
                          {def.labelLine1}
                        </button>
                      )
                    })}
                  </div>
                </Section>
              ))}

              {periods.length > 0 && (
                <Section title="Monthly amounts (12M history)" defaultOpen={false}>
                  <div className="flex flex-wrap gap-1.5">
                    {periods.map(p => {
                      const key = pk(p.year, p.month)
                      const def = catalog.get(`month:${key}`)
                      if (!def) return null
                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => addColumn(def.id)}
                          className="px-2 py-1 rounded-md text-[0.7rem]"
                          style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                        >
                          {p.label}
                        </button>
                      )
                    })}
                  </div>
                </Section>
              )}

              {periods.length > 0 && (
                <Section title="Monthly variances" defaultOpen={false}>
                  <label className="block text-[0.65rem] font-medium text-slate-600 mb-1">Reference month</label>
                  <select
                    value={cmpKey}
                    onChange={e => setCmpMonth(e.target.value)}
                    className="w-full mb-2 text-xs border border-slate-200 rounded-lg px-2 py-1.5"
                  >
                    {periodOptions.map(o => (
                      <option key={o.key} value={o.key}>{o.label}</option>
                    ))}
                  </select>
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    <button
                      type="button"
                      onClick={() => addColumn(`month_mom:${cmpKey}`)}
                      className="px-2 py-1.5 rounded-md text-[0.7rem]"
                      style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                    >
                      {catalog.get(`month_mom:${cmpKey}`)?.labelLine1 ?? '+ MoM'}
                    </button>
                    <button
                      type="button"
                      onClick={() => addColumn(`month_yoy:${cmpKey}`)}
                      className="px-2 py-1.5 rounded-md text-[0.7rem]"
                      style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                    >
                      {catalog.get(`month_yoy:${cmpKey}`)?.labelLine1 ?? '+ YoY'}
                    </button>
                  </div>
                  <p className="text-[0.65rem] font-medium text-slate-600 mb-1">Custom month delta</p>
                  <div className="flex gap-1.5 items-center mb-2">
                    <select value={customA || cmpKey} onChange={e => setCustomA(e.target.value)} className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5">
                      {periodOptions.map(o => <option key={`a-${o.key}`} value={o.key}>{o.label}</option>)}
                    </select>
                    <span className="text-xs text-slate-400">−</span>
                    <select value={customB} onChange={e => setCustomB(e.target.value)} className="flex-1 text-xs border border-slate-200 rounded-lg px-2 py-1.5">
                      <option value="">Month B…</option>
                      {periodOptions.map(o => <option key={`b-${o.key}`} value={o.key}>{o.label}</option>)}
                    </select>
                  </div>
                  <button
                    type="button"
                    disabled={!customA || !customB || customA === customB}
                    onClick={() => {
                      const col = consolidationMonthDelta(customA || cmpKey, customB, periods)
                      addColumn(col.id)
                    }}
                    className="text-xs font-medium px-2 py-1.5 rounded-lg"
                    style={{ color: '#1E3A5F', background: 'rgba(30,58,95,0.06)', border: '1px solid rgba(30,58,95,0.15)' }}
                  >
                    Add custom delta
                  </button>
                </Section>
              )}

              <button
                type="button"
                onClick={() => persist([])}
                className="text-xs font-medium w-full py-2 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
              >
                Reset — current month only
              </button>
            </div>
          </div>
        </>
      )}
    </>
  )
}
