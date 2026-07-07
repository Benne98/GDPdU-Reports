import { useState, useEffect, useRef, useLayoutEffect, useCallback, type Dispatch, type SetStateAction } from 'react'
import { ChevronDown, ChevronUp, Loader2, AlertCircle, FileText, Share2, X } from 'lucide-react'
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  CartesianGrid,
  XAxis,
  YAxis,
} from 'recharts'
import { api, DuPontData, TopCustomerData, AgingResponse, WcRatiosData, type Entity } from '../../lib/api'
import { fmtKpi, fmtPct } from '../../lib/fmt'
import { stripLegalForm } from '../../lib/stripLegalForm'
import { useChartLoadReporter } from '../../hooks/useChartLoadReporter'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureDuPontSnapshot } from '../action-notes/captureDuPont'
import { buildDuPontNarrative } from './dupontNarrativeEngine'
// ─── Tree definition ──────────────────────────────────────────────────────────

type Unit = 'pct' | 'keur' | 'x' | 'days'

interface TreeNode {
  id: string
  metricKey?: string
  label: string
  unit: Unit
  formula?: string
  children?: TreeNode[]
}

const TREE: TreeNode = {
  id: 'roe', label: 'Return on Equity', unit: 'pct', formula: 'EBIT / Equity',
  children: [
    {
      id: 'roi', label: 'Return on Investment', unit: 'pct', formula: 'EBIT / Total Assets',
      children: [
        {
          id: 'ros', label: 'Return on Sales', unit: 'pct', formula: 'EBIT / Revenue',
          children: [
            {
              id: 'gross_margin', label: 'Gross Margin', unit: 'pct',
              children: [
                { id: 'net_sales',         label: 'Net Sales',         unit: 'keur' },
                { id: 'cost_of_materials', label: 'Cost of Materials', unit: 'keur' },
              ],
            },
            { id: 'ebitda_margin',      label: 'EBITDA Margin',      unit: 'pct'  },
            { id: 'personnel_expenses', label: 'Personnel Expenses', unit: 'keur' },
          ],
        },
        {
          id: 'asset_turnover', label: 'Asset Turnover', unit: 'x', formula: 'Revenue / Total Assets',
          children: [
            { id: 'dso', label: 'DSO', unit: 'days' },
            { id: 'dpo', label: 'DPO', unit: 'days' },
            { id: 'dio', label: 'DIO', unit: 'days' },
            {
              id: 'total_assets', label: 'Total Assets', unit: 'keur',
              children: [
                {
                  id: 'current_assets', label: 'Current Assets', unit: 'keur',
                  children: [
                    { id: 'trade_receivables', label: 'Trade Receivables', unit: 'keur' },
                    { id: 'inventories',       label: 'Inventories',       unit: 'keur' },
                    { id: 'cash',              label: 'Cash & Equivalents', unit: 'keur' },
                  ],
                },
                { id: 'fixed_assets', label: 'Fixed Assets', unit: 'keur' },
              ],
            },
          ],
        },
      ],
    },
    {
      id: 'equity_multiplier', label: 'Equity Multiplier', unit: 'x', formula: 'Total Assets / Equity',
      children: [
        { id: 'ta_em', metricKey: 'total_assets', label: 'Total Assets', unit: 'keur' },
        { id: 'equity',                           label: 'Equity',       unit: 'keur' },
      ],
    },
  ],
}

/** After load / filter change: ROE open so L1 is visible, plus ROI & Equity Multiplier expanded by default */
const DEFAULT_EXPANDED = new Set<string>(['roe', 'roi', 'equity_multiplier'])

// ─── Formatting ───────────────────────────────────────────────────────────────

function fmtValue(v: number | null, unit: Unit): string {
  if (v === null || v === undefined) return '—'
  switch (unit) {
    case 'pct':
      return fmtPct(v)
    case 'keur':
      return `${fmtKpi(v)}`
    case 'x':
      return `${Math.abs(v).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}x`
    case 'days':
      return `${Math.abs(v).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} d`
    default:
      return String(v)
  }
}

function fmtDelta(delta: number, unit: Unit): string {
  const abs = Math.abs(delta)
  const sign = delta >= 0 ? '+' : '−'
  switch (unit) {
    case 'pct':
      return `${sign}${abs.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} pp`
    case 'keur': {
      const k = abs / 1_000
      return `${sign}€\u00a0${k.toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}k`
    }
    case 'x':
      return `${sign}${abs.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}x`
    case 'days':
      return `${sign}${abs.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} d`
    default:
      return `${sign}${abs}`
  }
}


type ContextGrain = 'month' | 'quarter' | 'week'

interface ContextTrendPoint {
  label: string
  value: number
}

async function salesTopToCustomerData(
  year: number,
  month: number,
  kind: 'customer' | 'supplier',
  entityArg: string | undefined,
): Promise<TopCustomerData> {
  const filters = entityArg ? { entity: entityArg.split(',') } : undefined
  const res = await api.salesTopEntities(year, month, kind, 'cm', 'invoiced', filters, {
    period_grain: 'month',
    limit: 12,
  })
  return {
    year,
    month,
    col_labels: {
      py_cm: res.col_labels?.py_cm ?? '',
      pm: res.col_labels?.pm ?? '',
      cm: res.col_labels?.cm ?? '',
      ytd: res.col_labels?.ytd ?? '',
      ytd_py: res.col_labels?.ytd_py ?? '',
    },
    groups: (res.rows ?? []).slice(0, 6).map((row, i) => ({
      group_name: row.name ?? '',
      group_order: i + 1,
      py_cm: (row.py_cm ?? 0) * 1000,
      pm: (row.pm ?? 0) * 1000,
      cm: (row.cm ?? 0) * 1000,
      ytd_cm: (row.ytd ?? 0) * 1000,
      ytd_py: (row.ytd_py ?? 0) * 1000,
      delta_mom: (row.delta_cm_pm ?? 0) * 1000,
      delta_yoy: (row.delta_cm_py ?? 0) * 1000,
      delta_ytd: (row.delta_ytd ?? 0) * 1000,
      customers: [],
    })),
    total: {
      py_cm: 0, pm: 0, cm: 0, ytd_cm: 0, ytd_py: 0,
      delta_mom: 0, delta_yoy: 0, delta_ytd: 0,
    },
  }
}

function mapWcGrain(grain: ContextGrain): 'month' | 'quarter' | 'year' {
  if (grain === 'quarter') return 'quarter'
  if (grain === 'week') return 'month'
  return 'month'
}

async function loadNodeTrend(
  node: TreeNode,
  year: number,
  month: number,
  entity: string | undefined,
  grain: ContextGrain,
): Promise<{ points: ContextTrendPoint[]; note?: string }> {
  const id = node.id

  if (id === 'net_sales') {
    if (grain === 'week') {
      const res = await api.trendChart(year, month, 'net_sales', 'daily', entity)
      return {
        points: res.data.series.map((p) => ({ label: p.label, value: (p.current ?? 0) / 1_000 })),
        note: 'Weekly view uses latest-week day-profile from trend_chart.',
      }
    }
    const res = await api.timeSeries('revenue', entity, undefined, undefined, grain)
    return { points: res.series.map((p) => ({ label: p.period, value: (p.value ?? 0) / 1_000 })) }
  }

  if (id === 'personnel_expenses') {
    const res = await api.timeSeries('personnel_expense', entity, undefined, undefined, grain)
    return { points: res.series.map((p) => ({ label: p.period, value: (p.value ?? 0) / 1_000 })) }
  }

  if (id === 'trade_receivables') {
    const res = await api.timeSeries('ar_open', entity, undefined, undefined, grain)
    return { points: res.series.map((p) => ({ label: p.period, value: (p.value ?? 0) / 1_000 })) }
  }

  if (id === 'dso' || id === 'dpo' || id === 'dio') {
    const wc = await api.wcRatios(year, month, mapWcGrain(grain), entity)
    const series = wc.data.current_series.map((p) => ({
      label: p.period,
      value: Number(p[id as 'dso' | 'dpo' | 'dio'] ?? 0),
    }))
    return {
      points: series,
      note: grain === 'week' ? 'Weekly granularity is proxied via monthly WC snapshots.' : undefined,
    }
  }

  return {
    points: [],
    note: `No direct trend endpoint is available for "${node.label}" yet.`,
  }
}

function ContextTooltip({ active, payload, label, unit }: any) {
  if (!active || !payload?.length) return null
  const v = payload[0]?.value ?? 0
  return (
    <div className="rounded-lg px-3 py-2 text-xs shadow-lg" style={{ background: '#1E3A5F', color: '#F8FAFC' }}>
      <div className="font-semibold mb-1">{label}</div>
      <div className="font-medium tabular-nums">
        {unit === 'days' ? `${v.toFixed(1).replace('.', ',')} d` : `${fmtKpi(v * 1000)} kEUR`}
      </div>
    </div>
  )
}

// ─── NodeCard ─────────────────────────────────────────────────────────────────

interface NodeCardProps {
  node:         TreeNode
  data:         DuPontData
  isOpen:       boolean
  hasChildren:  boolean
  onToggle:     () => void
  onContextOpen?: (node: TreeNode) => void
  pmLabel:      string
  pyLabel:      string
}

function DeltaLine({
  label,
  delta,
  unit,
}: {
  label: string
  delta: number | null
  unit:  Unit
}) {
  if (delta === null) {
    return <div className="mt-0.5 text-[12px] text-slate-300">Δ {label}: —</div>
  }
  const positive = delta >= 0
  return (
    <div className={`text-[12px] font-medium ${positive ? 'text-emerald-600' : 'text-red-500'}`}>
      Δ {label}: {fmtDelta(delta, unit)}
    </div>
  )
}

function NodeCard({ node, data, isOpen, hasChildren, onToggle, onContextOpen, pmLabel, pyLabel }: NodeCardProps) {
  const metricKey = node.metricKey ?? node.id
  const metric    = data.metrics[metricKey]
  const value     = metric?.value ?? null
  const pm        = metric?.pm ?? null
  const py        = metric?.py ?? null
  const deltaPm   = value !== null && pm !== null ? value - pm : null
  const deltaPy   = value !== null && py !== null ? value - py : null

  return (
    <div
      data-dupont-card
      onClick={hasChildren ? onToggle : undefined}
      onContextMenu={(e) => {
        e.preventDefault()
        onContextOpen?.(node)
      }}
      title={node.formula}
      className={[
        'w-44 rounded-xl border bg-white p-3.5 shadow-sm transition-all',
        hasChildren
          ? 'cursor-pointer hover:border-slate-400 hover:shadow-md'
          : 'cursor-default',
        isOpen
          ? 'border-[#1E3A5F] ring-1 ring-[#1E3A5F]/20 shadow-md'
          : 'border-slate-200',
      ].join(' ')}
    >
      <div className="text-[12px] font-medium text-slate-500 uppercase tracking-wide leading-tight">
        {node.label}
      </div>

      <div className="mt-2 text-base font-bold text-slate-800">
        {fmtValue(value, node.unit)}
      </div>

      <div className="mt-1 space-y-0.5">
        <DeltaLine label={pmLabel} delta={deltaPm} unit={node.unit} />
        <DeltaLine label={pyLabel} delta={deltaPy} unit={node.unit} />
      </div>

      {hasChildren && (
        <div className="mt-2 flex items-center gap-1 text-[12px] text-slate-400">
          {isOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          <span>{isOpen ? 'collapse' : 'expand'}</span>
        </div>
      )}
    </div>
  )
}

// ─── Measured horizontal connector (first card centre → last card centre) ─────

interface MeasuredCrossbarProps {
  childCount:   number
  columnRefs:   React.MutableRefObject<(HTMLDivElement | null)[]>
  rowRef:       React.RefObject<HTMLDivElement | null>
  /** Changes whenever any node expand/collapse — triggers connector re-measure */
  layoutSignal: string
}

function MeasuredCrossbar({ childCount, columnRefs, rowRef, layoutSignal }: MeasuredCrossbarProps) {
  const [line, setLine] = useState<{ left: number; width: number } | null>(null)

  const measure = useCallback(() => {
    const row = rowRef.current
    if (!row || childCount < 2) {
      setLine(null)
      return
    }
    const rowRect = row.getBoundingClientRect()
    const centers: number[] = []
    for (let i = 0; i < childCount; i++) {
      const col = columnRefs.current[i]
      if (!col) continue
      const card = col.querySelector<HTMLElement>('[data-dupont-card]')
      if (!card) continue
      const r = card.getBoundingClientRect()
      centers.push(r.left + r.width / 2 - rowRect.left)
    }
    if (centers.length < 2) {
      setLine(null)
      return
    }
    const left = centers[0]
    const right = centers[centers.length - 1]
    if (right <= left) {
      setLine(null)
      return
    }
    setLine({ left, width: right - left })
  }, [childCount, columnRefs, rowRef])

  useLayoutEffect(() => {
    measure()
  }, [measure, childCount, layoutSignal])

  useEffect(() => {
    const row = rowRef.current
    if (!row) return
    const ro = new ResizeObserver(() => measure())
    ro.observe(row)
    window.addEventListener('resize', measure)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [measure, rowRef, childCount, layoutSignal])

  if (!line) return null
  return (
    <div
      className="pointer-events-none absolute top-0 h-px bg-slate-300 z-[1]"
      style={{ left: line.left, width: line.width }}
    />
  )
}

// ─── DuPontNode (recursive) ───────────────────────────────────────────────────

interface DuPontNodeProps {
  node:         TreeNode
  data:         DuPontData
  expanded:     Set<string>
  layoutSignal: string
  onToggle:     (id: string) => void
  onContextOpen: (node: TreeNode) => void
}

function DuPontNode({ node, data, expanded, layoutSignal, onToggle, onContextOpen }: DuPontNodeProps) {
  const isOpen      = expanded.has(node.id)
  const hasChildren = !!node.children?.length
  const rowRef      = useRef<HTMLDivElement>(null)
  const columnRefs  = useRef<(HTMLDivElement | null)[]>([])

  const childCount = node.children?.length ?? 0

  return (
    <div className="flex flex-col items-center">
      <NodeCard
        node={node}
        data={data}
        isOpen={isOpen}
        hasChildren={hasChildren}
        onToggle={() => onToggle(node.id)}
        onContextOpen={onContextOpen}
        pmLabel={data.pm_label}
        pyLabel={data.py_label}
      />

      {isOpen && hasChildren && (
        <>
          <div className="w-px h-4 shrink-0 bg-slate-300" />

          <div
            ref={rowRef}
            className="relative flex max-w-full flex-nowrap justify-center gap-6 overflow-x-auto overflow-y-visible py-0 [scrollbar-gutter:stable]"
          >
            <MeasuredCrossbar
              childCount={childCount}
              columnRefs={columnRefs}
              rowRef={rowRef}
              layoutSignal={layoutSignal}
            />

            {node.children!.map((child, idx) => (
              <div
                key={child.id}
                ref={el => { columnRefs.current[idx] = el }}
                className="flex min-w-0 flex-shrink-0 flex-col items-center"
              >
                <div className="w-px h-4 shrink-0 bg-slate-300" />
                <DuPontNode
                  node={child}
                  data={data}
                  expanded={expanded}
                  layoutSignal={layoutSignal}
                  onToggle={onToggle}
                  onContextOpen={onContextOpen}
                />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ─── DuPontTree (main export) ─────────────────────────────────────────────────

interface DuPontTreeProps {
  year:    number
  month:   number
  entities: Entity[]
  /** Section heading (default: DuPont Analysis) */
  title?: string
}

type EntityScopeMode = 'group' | 'single' | 'multi'

function entityScopeApiArg(mode: EntityScopeMode, single: string, multi: string[]): string | undefined {
  if (mode === 'group') return undefined
  if (mode === 'single') return single === 'all' ? undefined : single
  if (!multi.length) return undefined
  return multi.join(',')
}

function entityScopeLabel(mode: EntityScopeMode, single: string, multi: string[], entities: Entity[]): string {
  if (mode === 'group') return 'Group'
  if (mode === 'single') {
    if (single === 'all') return 'Group'
    const ent = entities.find(e => e.legal_entity_code === single)
    return ent ? stripLegalForm(ent.entity_name) : single
  }
  if (multi.length === 0) return 'Group'
  if (multi.length === 1) {
    const ent = entities.find(e => e.legal_entity_code === multi[0])
    return ent ? stripLegalForm(ent.entity_name) : multi[0]
  }
  return `${multi.length} entities`
}

type DuPontView = 'chart' | 'report'

interface EntityScopeControlProps {
  entities: Entity[]
  scopeMode: EntityScopeMode
  setScopeMode: (mode: EntityScopeMode) => void
  singleEntity: string
  setSingleEntity: (code: string) => void
  multiEntities: string[]
  setMultiEntities: Dispatch<SetStateAction<string[]>>
  scopeOpen: boolean
  setScopeOpen: Dispatch<SetStateAction<boolean>>
  scopeLabel: string
}

function EntityScopeControl({
  entities,
  scopeMode,
  setScopeMode,
  singleEntity,
  setSingleEntity,
  multiEntities,
  setMultiEntities,
  scopeOpen,
  setScopeOpen,
  scopeLabel,
}: EntityScopeControlProps) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setScopeOpen(v => !v)}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[12px] font-medium text-slate-600 shadow-sm hover:bg-slate-50"
        aria-expanded={scopeOpen}
      >
        <span className="text-slate-400">Entity</span>
        <span className="text-slate-800">{scopeLabel}</span>
        <ChevronDown size={12} className={scopeOpen ? 'rotate-180' : ''} />
      </button>
      {scopeOpen && (
        <div
          className="absolute left-0 top-full z-20 mt-1 min-w-[220px] rounded-lg border border-slate-200 bg-white p-2 shadow-lg"
          onClick={e => e.stopPropagation()}
        >
          <button
            type="button"
            className="w-full text-left rounded px-2 py-1.5 text-xs hover:bg-slate-50"
            style={{ fontWeight: scopeMode === 'group' ? 600 : 400 }}
            onClick={() => { setScopeMode('group'); setScopeOpen(false) }}
          >
            Group (all entities)
          </button>
          <div className="my-1 border-t border-slate-100" />
          <p className="px-2 py-1 text-[10px] uppercase tracking-wide text-slate-400">Single entity</p>
          <select
            value={singleEntity}
            onChange={e => {
              setSingleEntity(e.target.value)
              setScopeMode('single')
              setScopeOpen(false)
            }}
            className="w-full rounded border border-slate-200 px-2 py-1.5 text-xs mb-2"
          >
            <option value="all">All entities</option>
            {entities.map(e => (
              <option key={e.legal_entity_code} value={e.legal_entity_code}>
                {stripLegalForm(e.entity_name)}
              </option>
            ))}
          </select>
          <p className="px-2 py-1 text-[10px] uppercase tracking-wide text-slate-400">Multiple entities</p>
          <div className="max-h-36 overflow-auto px-1 space-y-0.5">
            {entities.map(e => {
              const checked = multiEntities.includes(e.legal_entity_code)
              return (
                <label key={e.legal_entity_code} className="flex items-center gap-2 px-1 py-1 text-xs cursor-pointer hover:bg-slate-50 rounded">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => {
                      setScopeMode('multi')
                      setMultiEntities(prev =>
                        checked
                          ? prev.filter(c => c !== e.legal_entity_code)
                          : [...prev, e.legal_entity_code],
                      )
                    }}
                  />
                  <span>{stripLegalForm(e.entity_name)}</span>
                </label>
              )
            })}
          </div>
          {scopeMode === 'multi' && multiEntities.length > 0 && (
            <button
              type="button"
              className="mt-2 w-full rounded bg-[#1E3A5F]/10 px-2 py-1.5 text-xs font-medium text-[#1E3A5F]"
              onClick={() => setScopeOpen(false)}
            >
              Apply ({multiEntities.length} selected)
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default function DuPontTree({ year, month, entities, title = 'DuPont Analysis' }: DuPontTreeProps) {
  const [scopeMode, setScopeMode] = useState<EntityScopeMode>('group')
  const [singleEntity, setSingleEntity] = useState('all')
  const [multiEntities, setMultiEntities] = useState<string[]>([])
  const [scopeOpen, setScopeOpen] = useState(false)
  const entityArg = entityScopeApiArg(scopeMode, singleEntity, multiEntities)
  const [data,     setData]     = useState<DuPontData | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(DEFAULT_EXPANDED))
  const [view, setView] = useState<DuPontView>('report')
  const [contextNode, setContextNode] = useState<TreeNode | null>(null)
  const [contextGrain, setContextGrain] = useState<ContextGrain>('month')
  const [contextTrend, setContextTrend] = useState<ContextTrendPoint[]>([])
  const [contextTrendNote, setContextTrendNote] = useState<string | null>(null)
  const [contextLoading, setContextLoading] = useState(false)
  const [contextError, setContextError] = useState<string | null>(null)
  const [contextTopCustomers, setContextTopCustomers] = useState<TopCustomerData | null>(null)
  const [contextTopSuppliers, setContextTopSuppliers] = useState<TopCustomerData | null>(null)
  const [contextArAging, setContextArAging] = useState<AgingResponse | null>(null)
  const [contextApAging, setContextApAging] = useState<AgingResponse | null>(null)
  const [contextWc, setContextWc] = useState<WcRatiosData | null>(null)

  useEffect(() => {
    setData(null)
    setError(null)
    setLoading(true)
    setExpanded(new Set(DEFAULT_EXPANDED))
    api.dupont(year, month, entityArg)
      .then(r => setData(r.data))
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [year, month, entityArg])

  const handleToggle = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const isDefaultExpanded =
    expanded.size === DEFAULT_EXPANDED.size
    && [...expanded].every(id => DEFAULT_EXPANDED.has(id))

  const layoutSignal = [...expanded].sort().join('|')
  const timedOut = useChartLoadReporter('cockpit-dupont', loading, error)
  const narrative = data ? buildDuPontNarrative(data) : []
  const notesCtx = useOptionalActionNotesContext()
  const scopeLabel = entityScopeLabel(scopeMode, singleEntity, multiEntities, entities)
  const entityScopeControl = (
    <EntityScopeControl
      entities={entities}
      scopeMode={scopeMode}
      setScopeMode={setScopeMode}
      singleEntity={singleEntity}
      setSingleEntity={setSingleEntity}
      multiEntities={multiEntities}
      setMultiEntities={setMultiEntities}
      scopeOpen={scopeOpen}
      setScopeOpen={setScopeOpen}
      scopeLabel={scopeLabel}
    />
  )

  // ─── Pin registration ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!notesCtx) return
    if (!data) {
      notesCtx.unregisterTableCandidate('cockpit-dupont')
      return
    }
    notesCtx.registerTableCandidate({
      id: 'cockpit-dupont',
      label: 'DuPont analysis',
      description: 'ROE/ROI driver tree — value, Δ MoM, Δ YoY',
      capture: () => captureDuPontSnapshot(data),
      viewState: { view_mode: view, tab: 'overview' },
    })
    return () => notesCtx.unregisterTableCandidate('cockpit-dupont')
  }, [notesCtx, data, view])

  useEffect(() => {
    if (!contextNode) return
    let cancelled = false
    setContextLoading(true)
    setContextError(null)
    setContextTrend([])
    setContextTrendNote(null)
    setContextTopCustomers(null)
    setContextTopSuppliers(null)
    setContextArAging(null)
    setContextApAging(null)
    setContextWc(null)

    const needsCustomers = contextNode.id === 'net_sales'
    const needsSuppliers = contextNode.id === 'cost_of_materials'
    const needsArAging = contextNode.id === 'trade_receivables' || contextNode.id === 'dso'
    const needsApAging = contextNode.id === 'dpo'
    const needsWc = contextNode.id === 'dso' || contextNode.id === 'dpo' || contextNode.id === 'dio'

    Promise.allSettled([
      loadNodeTrend(contextNode, year, month, entityArg, contextGrain),
      needsCustomers ? salesTopToCustomerData(year, month, 'customer', entityArg) : Promise.resolve(null),
      needsSuppliers ? salesTopToCustomerData(year, month, 'supplier', entityArg) : Promise.resolve(null),
      needsArAging ? api.arAging(entityArg) : Promise.resolve(null),
      needsApAging ? api.apAging(entityArg) : Promise.resolve(null),
      needsWc ? api.wcRatios(year, month, mapWcGrain(contextGrain), entityArg) : Promise.resolve(null),
    ]).then((results) => {
      if (cancelled) return
      const [trendRes, custRes, suppRes, arRes, apRes, wcRes] = results

      if (trendRes.status === 'fulfilled') {
        setContextTrend(trendRes.value.points)
        setContextTrendNote(trendRes.value.note ?? null)
      } else {
        setContextError(trendRes.reason instanceof Error ? trendRes.reason.message : String(trendRes.reason))
      }

      if (custRes.status === 'fulfilled' && custRes.value) setContextTopCustomers(custRes.value)
      if (suppRes.status === 'fulfilled' && suppRes.value) setContextTopSuppliers(suppRes.value)
      if (arRes.status === 'fulfilled' && arRes.value) setContextArAging(arRes.value)
      if (apRes.status === 'fulfilled' && apRes.value) setContextApAging(apRes.value)
      if (wcRes.status === 'fulfilled' && wcRes.value) setContextWc(wcRes.value.data)

      setContextLoading(false)
    }).catch((e) => {
      if (cancelled) return
      setContextError(e instanceof Error ? e.message : String(e))
      setContextLoading(false)
    })

    return () => {
      cancelled = true
    }
  }, [contextNode, contextGrain, year, month, entityArg])

  if (timedOut) return null

  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-slate-100">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Chart view for full-width DuPont navigation, report view for KPI-driver commentary.
            {data && (
              <span className="ml-1 text-slate-500 font-medium">
                {data.col_label} · Δ MoM {data.pm_label} · Δ YoY {data.py_label}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
          <div className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5">
            <button
              type="button"
              onClick={() => setView('chart')}
              className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors"
              style={{
                background: view === 'chart' ? 'rgba(30,58,95,0.1)' : 'transparent',
                color: view === 'chart' ? '#1E3A5F' : '#64748B',
              }}
            >
              <Share2 size={12} />
              Chart View
            </button>
            <button
              type="button"
              onClick={() => setView('report')}
              className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors"
              style={{
                background: view === 'report' ? 'rgba(30,58,95,0.1)' : 'transparent',
                color: view === 'report' ? '#1E3A5F' : '#64748B',
              }}
            >
              <FileText size={12} />
              Report View
            </button>
          </div>
          {data && !isDefaultExpanded && (
            <button
              type="button"
              onClick={() => setExpanded(new Set(DEFAULT_EXPANDED))}
              className="text-xs text-slate-400 hover:text-slate-600 transition-colors px-2 py-1 rounded-md"
              style={{ border: '1px solid transparent' }}
            >
              Reset view
            </button>
          )}
        </div>
      </div>

      <div className="overflow-x-auto p-6 [scrollbar-gutter:stable]">
        {error && (
          <div className="flex items-center gap-2 text-red-500 text-sm">
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        {!error && view === 'chart' && (
          <div className="rounded-xl border border-slate-200 bg-slate-50/40 p-3">
            <div className="mb-3 flex items-center justify-start">{entityScopeControl}</div>
            {loading && (
              <div className="flex items-center justify-center h-32 text-slate-400">
                <Loader2 size={20} className="animate-spin mr-2" />
                <span className="text-sm">Loading DuPont data…</span>
              </div>
            )}
            {data && !loading && (
            <div className="overflow-x-auto overflow-y-visible pb-2">
              <div className="min-w-max mx-auto">
                <DuPontNode
                  node={TREE}
                  data={data}
                  expanded={expanded}
                  layoutSignal={layoutSignal}
                  onToggle={handleToggle}
                  onContextOpen={(node) => {
                    setContextNode(node)
                    setContextGrain('month')
                  }}
                />
              </div>
            </div>
            )}
          </div>
        )}

        {!error && view === 'report' && (
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-5 items-start">
            <div className="xl:col-span-7">
              <div className="rounded-xl border border-slate-200 bg-slate-50/40 p-3">
                <div className="mb-3 flex items-center justify-start">{entityScopeControl}</div>
                {loading && (
                  <div className="flex items-center justify-center h-32 text-slate-400">
                    <Loader2 size={20} className="animate-spin mr-2" />
                    <span className="text-sm">Loading DuPont data…</span>
                  </div>
                )}
                {data && !loading && (
                <div className="overflow-x-auto overflow-y-visible pb-2">
                  <div className="min-w-max mx-auto">
                    <DuPontNode
                      node={TREE}
                      data={data}
                      expanded={expanded}
                      layoutSignal={layoutSignal}
                      onToggle={handleToggle}
                      onContextOpen={(node) => {
                        setContextNode(node)
                        setContextGrain('month')
                      }}
                    />
                  </div>
                </div>
                )}
              </div>
            </div>
            {data && !loading && (
            <aside className="xl:col-span-5 xl:min-w-[360px] rounded-xl border border-slate-200 bg-slate-50/60 p-4">
              <h4 className="text-sm font-semibold text-slate-800">Driver report</h4>
              <p className="text-xs text-slate-500 mt-1">
                How profitability, capital efficiency and funding affect shareholder returns — aligned with the other cockpit views.
              </p>
              <div className="mt-3 space-y-3">
                {narrative.map((n, idx) => (
                  <div key={`${n.title}-${idx}`} className="rounded-lg border border-slate-200 bg-white p-3">
                    <p className="text-xs font-semibold text-slate-800">{n.title}</p>
                    <p className="text-xs leading-5 text-slate-600 mt-1.5">{n.body}</p>
                  </div>
                ))}
              </div>
            </aside>
            )}
          </div>
        )}
      </div>

      {contextNode && (
        <div
          className="fixed inset-0 z-[120] flex items-center justify-center p-4"
          style={{ background: 'rgba(15,23,42,0.45)' }}
          onClick={() => setContextNode(null)}
        >
          <div
            className="w-full max-w-6xl max-h-[88vh] overflow-hidden rounded-xl border bg-white shadow-xl"
            style={{ borderColor: '#E2E8F0' }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-3 border-b flex items-start justify-between gap-3" style={{ borderColor: '#E2E8F0' }}>
              <div>
                <h4 className="text-sm font-semibold text-slate-800">
                  {contextNode.label} · Deep analysis
                </h4>
                <p className="text-xs text-slate-500 mt-0.5">
                  Right-click drill view · trend + contextual detail tables
                </p>
              </div>
              <button
                type="button"
                onClick={() => setContextNode(null)}
                className="w-7 h-7 rounded-lg flex items-center justify-center transition-colors"
                style={{ background: '#F4F6F9', color: '#475569', border: '1px solid #E2E8F0' }}
              >
                <X size={14} />
              </button>
            </div>

            <div className="px-5 py-4 flex items-center gap-2 border-b" style={{ borderColor: '#F1F5F9' }}>
              <span className="text-xs font-medium text-slate-600">Trend grain</span>
              {(['month', 'quarter', 'week'] as ContextGrain[]).map((g) => (
                <button
                  key={g}
                  type="button"
                  onClick={() => setContextGrain(g)}
                  className="px-2.5 py-1 rounded-md text-xs font-medium transition-all"
                  style={{
                    background: contextGrain === g ? 'rgba(30,58,95,0.1)' : '#F4F6F9',
                    color: contextGrain === g ? '#1E3A5F' : '#475569',
                    border: `1px solid ${contextGrain === g ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
                  }}
                >
                  {g === 'month' ? 'Monthly' : g === 'quarter' ? 'Quarterly' : 'Weekly'}
                </button>
              ))}
            </div>

            <div className="p-5 overflow-auto max-h-[72vh] space-y-5">
              <section className="rounded-xl border border-slate-200 p-4">
                <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                  Trend analysis
                </h5>
                {contextLoading ? (
                  <div className="h-40 rounded animate-pulse" style={{ background: '#F4F6F9' }} />
                ) : contextError ? (
                  <div className="text-xs text-red-600">{contextError}</div>
                ) : contextTrend.length === 0 ? (
                  <div className="text-xs text-slate-500">No trend data available for this metric.</div>
                ) : (
                  <div style={{ width: '100%', height: 242 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={contextTrend} margin={{ top: 10, right: 10, left: 6, bottom: 4 }}>
                        <CartesianGrid vertical={false} stroke="#F1F5F9" />
                        <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#94A3B8' }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fontSize: 12, fill: '#94A3B8' }} axisLine={false} tickLine={false} width={48} />
                        <Tooltip content={<ContextTooltip unit={contextNode.unit} />} cursor={{ stroke: '#E2E8F0' }} />
                        <Line type="monotone" dataKey="value" stroke="#1E3A5F" strokeWidth={2.2} dot={false} activeDot={{ r: 3 }} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                )}
                {contextTrendNote && (
                  <p className="text-[12px] text-slate-500 mt-2">{contextTrendNote}</p>
                )}
              </section>

              {(contextTopCustomers || contextTopSuppliers || contextArAging || contextApAging || contextWc) && (
                <section className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                  {contextTopCustomers && (
                    <div className="rounded-xl border border-slate-200 p-4">
                      <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                        Net sales by customer group
                      </h5>
                      <table className="w-full text-xs">
                        <thead>
                          <tr style={{ borderBottom: '1px solid #E2E8F0' }}>
                            <th className="py-1 text-left text-slate-500">Group</th>
                            <th className="py-1 text-right text-slate-500">CM</th>
                            <th className="py-1 text-right text-slate-500">Δ MoM</th>
                          </tr>
                        </thead>
                        <tbody>
                          {contextTopCustomers.groups.slice(0, 6).map((g) => (
                            <tr key={g.group_name} style={{ borderBottom: '1px solid #F1F5F9' }}>
                              <td className="py-1.5 text-slate-700">{g.group_name}</td>
                              <td className="py-1.5 text-right tabular-nums">{fmtKpi(g.cm)}</td>
                              <td className="py-1.5 text-right tabular-nums">{fmtKpi(g.delta_mom)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {contextTopSuppliers && (
                    <div className="rounded-xl border border-slate-200 p-4">
                      <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                        Cost of materials by supplier group
                      </h5>
                      <table className="w-full text-xs">
                        <thead>
                          <tr style={{ borderBottom: '1px solid #E2E8F0' }}>
                            <th className="py-1 text-left text-slate-500">Group</th>
                            <th className="py-1 text-right text-slate-500">CM</th>
                            <th className="py-1 text-right text-slate-500">Δ MoM</th>
                          </tr>
                        </thead>
                        <tbody>
                          {contextTopSuppliers.groups.slice(0, 6).map((g) => (
                            <tr key={g.group_name} style={{ borderBottom: '1px solid #F1F5F9' }}>
                              <td className="py-1.5 text-slate-700">{g.group_name}</td>
                              <td className="py-1.5 text-right tabular-nums">{fmtKpi(g.cm)}</td>
                              <td className="py-1.5 text-right tabular-nums">{fmtKpi(g.delta_mom)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {contextArAging && (
                    <div className="rounded-xl border border-slate-200 p-4">
                      <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                        Trade receivables aging buckets
                      </h5>
                      <table className="w-full text-xs">
                        <tbody>
                          {contextArAging.series.map((b) => (
                            <tr key={b.band} style={{ borderBottom: '1px solid #F1F5F9' }}>
                              <td className="py-1.5 text-slate-700">{b.label}</td>
                              <td className="py-1.5 text-right tabular-nums text-slate-800">{fmtKpi(b.value)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {contextApAging && (
                    <div className="rounded-xl border border-slate-200 p-4">
                      <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                        Trade payables aging buckets
                      </h5>
                      <table className="w-full text-xs">
                        <tbody>
                          {contextApAging.series.map((b) => (
                            <tr key={b.band} style={{ borderBottom: '1px solid #F1F5F9' }}>
                              <td className="py-1.5 text-slate-700">{b.label}</td>
                              <td className="py-1.5 text-right tabular-nums text-slate-800">{fmtKpi(b.value)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {contextWc && (
                    <div className="rounded-xl border border-slate-200 p-4 xl:col-span-2">
                      <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                        Working-capital KPI snapshot
                      </h5>
                      <div className="grid grid-cols-3 gap-3 text-xs">
                        {(['dso', 'dpo', 'dio'] as const).map((k) => {
                          const cur = contextWc.current_series[contextWc.current_series.length - 1]?.[k]
                          const prev = contextWc.prev_series[contextWc.prev_series.length - 1]?.[k]
                          return (
                            <div key={k} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                              <p className="uppercase tracking-wide text-slate-500 font-semibold">{k.toUpperCase()}</p>
                              <p className="mt-1 text-slate-800 font-semibold tabular-nums">
                                {cur === null || cur === undefined ? '—' : `${cur.toFixed(1).replace('.', ',')} d`}
                              </p>
                              <p className="text-slate-500 tabular-nums">
                                Prev {prev === null || prev === undefined ? '—' : `${prev.toFixed(1).replace('.', ',')} d`}
                              </p>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </section>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
