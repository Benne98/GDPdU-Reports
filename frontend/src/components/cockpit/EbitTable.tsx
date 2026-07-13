import { useEffect, useState } from 'react'
import { api, EbitTableData, EbitTableRow } from '../../lib/api'
import type { PeriodSelection } from '../../lib/periodSelection'
import { periodQueryParams, periodAnchorYearMonth } from '../../lib/periodSelection'
import { fmtKpi, fmtPct } from '../../lib/fmt'
import { motion } from 'framer-motion'
import PlExportMenu, { type PlExportKind } from '../financials/pl-two-view/PlExportMenu'
import { exportFlatTablePptx } from '../../lib/finssentialsExport/exportFlatTablePptx'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureEbitTableSnapshot } from '../action-notes/captureEbitTable'
import { useChartLoadReporter } from '../../hooks/useChartLoadReporter'
import { exportToXlsx, XlsxRow, todayStr } from '../../lib/exportXlsx'
import { stripLegalForm } from '../../lib/stripLegalForm'
import EbitColumnEditor from './EbitColumnEditor'
import {
  columnHeaderLabel,
  loadEbitColumns,
  type EbitDisplayColumn,
} from './ebitColumnRegistry'
// ─── Types ────────────────────────────────────────────────────────────────────

export interface DrillDownRequest {
  entityCode?:   string     // undefined = all entities (Total row)
  dateFrom:      string
  dateTo:        string
  level3?:       string
  statementType?: string
  customerName?: string
  title:         string
}

interface EbitTableProps {
  period:                  PeriodSelection
  entity?:                 string
  onDrillDown?:            (req: DrillDownRequest) => void
  activeDrillKey?:         string
  /** cockpit = standalone card with sidebar; embedded = table-only inside Group overview */
  variant?:                'cockpit' | 'embedded'
  showSidebar?:            boolean
  showColumnEditor?:       boolean
  showExport?:             boolean
  title?:                  string
}

// ─── Date helpers ─────────────────────────────────────────────────────────────

function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}
function pad2(n: number): string { return String(n).padStart(2, '0') }

type Period = 'cm_py' | 'pm' | 'cm' | 'ytd'
type TablePeriod = 'pm' | 'cm' | 'ytd'
type DisplayColumn = EbitDisplayColumn
type Section = 'output' | 'ebit' | 'margin'

type PlanPair = {
  outputPlanCm: number | null
  ebitPlanCm: number | null
}

function periodRange(period: Period, year: number, month: number): { from: string; to: string } {
  const pmYear  = month === 1 ? year - 1 : year
  const pmMonth = month === 1 ? 12 : month - 1
  switch (period) {
    case 'cm_py': return { from: `${year - 1}-${pad2(month)}-01`,    to: lastDay(year - 1, month) }
    case 'pm':    return { from: `${pmYear}-${pad2(pmMonth)}-01`,     to: lastDay(pmYear, pmMonth) }
    case 'cm':    return { from: `${year}-${pad2(month)}-01`,         to: lastDay(year, month) }
    case 'ytd':   return { from: `${year}-01-01`,                     to: lastDay(year, month) }
  }
}

function drillKey(entityCode: string | undefined, section: string, period: TablePeriod): string {
  return `${entityCode ?? '__total__'}|${section}|${period}`
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function margin(ebit: number, to: number): number | null {
  return to !== 0 ? (ebit / Math.abs(to)) * 100 : null
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function planFromLines(lines: Array<{ line_code: string; plan_cm: number }>): PlanPair {
  const lineMap = new Map(lines.map(l => [String(l.line_code ?? '').toUpperCase(), Number(l.plan_cm ?? 0)]))
  const pick = (...codes: string[]) => {
    for (const code of codes) {
      const v = lineMap.get(code)
      if (v !== undefined && Number.isFinite(v)) return v
    }
    return null
  }
  return {
    outputPlanCm: pick('TOTAL_OUTPUT', 'NET_SALES'),
    ebitPlanCm: pick('EBIT_ROW', 'EBIT'),
  }
}

function displayEntityName(name: string): string {
  return stripLegalForm(name ?? '')
}

function planVsQualifier(actual: number, plan: number | null): string {
  if (plan === null || Math.abs(plan) < 1e-6) return 'without usable plan baseline'
  const gap = actual - plan
  const ratio = Math.abs(gap) / Math.max(Math.abs(plan), 1)
  if (ratio < 0.05) return 'in line with plan'
  if (ratio < 0.15) return gap >= 0 ? 'slightly ahead of plan' : 'slightly below plan'
  return gap >= 0 ? 'materially ahead of plan' : 'materially below plan'
}

function buildEbitNarrative(
  rows: EbitTableRow[],
  plans: Record<string, PlanPair>,
  totalRow: EbitTableRow,
  cols: EbitTableData['col_labels'],
): { intro: string; bullets: string[]; closing: string } {
  const deltas = rows.map(r => ({ name: displayEntityName(r.entity_name), delta: r.ebit_cm - r.ebit_pm }))
  const best = [...deltas].sort((a, b) => b.delta - a.delta)[0]
  const worst = [...deltas].sort((a, b) => a.delta - b.delta)[0]
  const planGaps = rows
    .map(r => {
      const p = plans[r.entity_code]
      if (!p || p.ebitPlanCm === null) return null
      return { name: displayEntityName(r.entity_name), gap: r.ebit_cm - p.ebitPlanCm }
    })
    .filter((v): v is { name: string; gap: number } => v !== null)
  const largestPlanGap = planGaps.length ? [...planGaps].sort((a, b) => a.gap - b.gap)[0] : null

  const cmMarginTotal = margin(totalRow.ebit_cm, totalRow.to_cm)
  const ytdMarginTotal = margin(totalRow.ebit_ytd, totalRow.to_ytd)

  const totalPlan = plans.__total__ ?? null
  const outputGap = totalPlan?.outputPlanCm == null ? null : totalRow.to_cm - totalPlan.outputPlanCm
  const ebitGap = totalPlan?.ebitPlanCm == null ? null : totalRow.ebit_cm - totalPlan.ebitPlanCm
  const intro = `For ${cols.cm}, the table consolidates Total output, EBIT and EBIT margin by legal entity and compares each position versus ${cols.pm}, plan and ${cols.ytd}.`

  const bullets: string[] = [
    `Total output is ${fmtKpi(totalRow.to_cm)} kEUR (${fmtKpi(totalRow.to_cm - totalRow.to_pm)} kEUR vs ${cols.pm}) and ${planVsQualifier(totalRow.to_cm, totalPlan?.outputPlanCm ?? null)}${outputGap === null ? '' : ` (${fmtKpi(outputGap)} kEUR vs plan)`}.`,
    `EBIT amounts to ${fmtKpi(totalRow.ebit_cm)} kEUR with a movement of ${fmtKpi(totalRow.ebit_cm - totalRow.ebit_pm)} kEUR versus ${cols.pm}; versus plan this is ${planVsQualifier(totalRow.ebit_cm, totalPlan?.ebitPlanCm ?? null)}${ebitGap === null ? '' : ` (${fmtKpi(ebitGap)} kEUR)`}.`,
  ]
  if (best && worst) {
    bullets.push(
      `Entity bridge: strongest EBIT uplift is ${best.name} (${fmtKpi(best.delta)} kEUR), while the main drag comes from ${worst.name} (${fmtKpi(worst.delta)} kEUR).`,
    )
  }
  if (largestPlanGap) {
    bullets.push(`Largest CM vs plan deviation is in ${largestPlanGap.name} (${fmtKpi(largestPlanGap.gap)} kEUR), which should be validated at posting/account level.`)
  }
  if (cmMarginTotal !== null && ytdMarginTotal !== null) {
    bullets.push(`Margin quality check: CM EBIT margin is ${fmtPct(cmMarginTotal)} versus ${fmtPct(ytdMarginTotal)} on ${cols.ytd}; this helps distinguish structural trend vs one-off month effects.`)
  }

  return {
    intro,
    bullets: bullets.slice(0, 5),
    closing:
      'Recommended follow-up in report-view style: review account-level drivers for the top positive and negative movers, validate one-offs, and convert confirmed variances into action owners and timing.',
  }
}

// ─── Value / delta cells ──────────────────────────────────────────────────────

interface ValCellProps {
  v:          number | null
  pct?:       boolean
  highlight?: 'cm' | 'ytd' | null
  isActive?:  boolean
  onClick?:   () => void
}

function ValCell({ v, pct = false, highlight = null, isActive = false, onClick }: ValCellProps) {
  const text  = v === null ? '—' : pct ? fmtPct(v) : fmtKpi(v)
  const muted = v === null

  const isHighlighted = highlight === 'cm' || highlight === 'ytd'
  const base: React.CSSProperties = isHighlighted
    ? {
        background:   isActive ? 'rgba(100,116,139,0.24)' : 'rgba(148,163,184,0.16)',
        borderLeft:   '1px solid #CBD5E1',
        borderRight:  '1px solid #CBD5E1',
        fontWeight:   600,
      }
    : { background: isActive ? 'rgba(30,58,95,0.08)' : undefined }

  const cursor = onClick ? 'pointer' : 'default'

  return (
    <td
      className="px-2 py-2 text-right tabular-nums whitespace-nowrap text-xs transition-colors"
      style={{
        color:  muted ? '#94A3B8' : '#111827',
        cursor,
        userSelect: 'none',
        ...base,
      }}
      onClick={onClick}
      title={onClick ? 'Click to see bookings' : undefined}
    >
      {text}
    </td>
  )
}

function DeltaBarCell({
  value,
  maxAbs,
  pct = false,
}: {
  value: number | null
  maxAbs: number
  pct?: boolean
}) {
  const color = value === null ? '#94A3B8' : value >= 0 ? '#059669' : '#DC2626'
  const text = value === null ? '—' : pct ? fmtPct(value) : fmtKpi(value)
  const widthPct = value === null || maxAbs <= 0 ? 0 : clamp((Math.abs(value) / maxAbs) * 100, 0, 100)
  return (
    <td className="px-2 py-2 text-right tabular-nums whitespace-nowrap text-xs font-normal" style={{ color: value === null ? '#94A3B8' : '#111827' }}>
      <span className="inline-flex items-center justify-end gap-1.5">
        <span className="font-normal" style={{ color }}>{text}</span>
        <span className="inline-block align-middle" style={{ width: 22, height: 6, background: '#E2E8F0', borderRadius: 999, overflow: 'hidden' }}>
          <span
            style={{
              display: 'block',
              width: `${widthPct}%`,
              height: '100%',
              background: color,
              borderRadius: 999,
            }}
          />
        </span>
      </span>
    </td>
  )
}

// ─── Section header row ───────────────────────────────────────────────────────

function SectionHeader({ label }: { label: string }) {
  return (
    <tr style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
      <td colSpan={7} className="px-2 py-2 text-xs font-bold uppercase tracking-wide" style={{ color: '#1E3A5F' }}>
        {label}
      </td>
    </tr>
  )
}

// ─── Entity row ───────────────────────────────────────────────────────────────

interface EntityRowProps {
  row:          EbitTableRow
  section:      Section
  plan:         PlanPair | null
  deltaPmMaxAbs: number
  deltaPlanMaxAbs: number
  isTotal:      boolean
  isIcElim?:    boolean
  visibleColumns: DisplayColumn[]
  year:         number
  month:        number
  activeDrillKey?: string
  onDrillDown?: (req: DrillDownRequest) => void
}

function EntityRow({
  row,
  section,
  plan,
  deltaPmMaxAbs,
  deltaPlanMaxAbs,
  isTotal,
  isIcElim,
  visibleColumns,
  year,
  month,
  activeDrillKey,
  onDrillDown,
}: EntityRowProps) {
  const bg = isTotal ? '#F4F6F9' : isIcElim ? '#FAFBFC' : 'transparent'
  const fw = isTotal ? 'font-bold' : isIcElim ? 'font-medium italic' : 'font-normal'

  const getBaseVal = (period: TablePeriod): number | null => {
    if (section === 'output') {
      const map: Record<TablePeriod, number> = { pm: row.to_pm, cm: row.to_cm, ytd: row.to_ytd }
      return map[period]
    }
    if (section === 'ebit') {
      const map: Record<TablePeriod, number> = { pm: row.ebit_pm, cm: row.ebit_cm, ytd: row.ebit_ytd }
      return map[period]
    }
    const toMap:   Record<TablePeriod, number> = { pm: row.to_pm, cm: row.to_cm, ytd: row.to_ytd }
    const ebitMap: Record<TablePeriod, number> = { pm: row.ebit_pm, cm: row.ebit_cm, ytd: row.ebit_ytd }
    return margin(ebitMap[period], toMap[period])
  }

  const getPlanVal = (): number | null => {
    if (!plan) return null
    if (section === 'output') return plan.outputPlanCm
    if (section === 'ebit') return plan.ebitPlanCm
    if (plan.outputPlanCm === null || plan.ebitPlanCm === null) return null
    return margin(plan.ebitPlanCm, plan.outputPlanCm)
  }

  const getVal = (col: DisplayColumn): number | null => {
    if (col === 'pm' || col === 'cm' || col === 'ytd') return getBaseVal(col)
    if (col === 'plan_cm') return getPlanVal()
    if (col === 'delta_pm') {
      const cm = getBaseVal('cm')
      const pm = getBaseVal('pm')
      if (cm === null || pm === null) return null
      return cm - pm
    }
    const cm = getBaseVal('cm')
    const planVal = getPlanVal()
    if (cm === null || planVal === null) return null
    return cm - planVal
  }

  const handleClick = (period: TablePeriod) => {
    if (!onDrillDown || section === 'margin' || isIcElim) return
    const { from, to } = periodRange(period, year, month)
    const entityCode = isTotal ? undefined : row.entity_code
    onDrillDown({
      entityCode,
      dateFrom: from,
      dateTo:   to,
      level3:        section === 'output' ? 'Net sales' : undefined,
      statementType: section === 'ebit'   ? 'PL'        : undefined,
      title: `${section === 'output' ? 'Total Output' : 'EBIT'} — ${isTotal ? 'All Entities' : displayEntityName(row.entity_name)} — ${period.toUpperCase()}`,
    })
  }

  return (
    <tr className={`${fw} transition-colors`} style={{ background: bg, borderBottom: '1px solid #F1F5F9' }}>
      {/* Entity name */}
      <td className="px-2 py-2 whitespace-nowrap text-xs" style={{ color: isTotal ? '#111827' : isIcElim ? '#64748B' : '#475569' }}>
        <span className={isTotal ? 'font-bold' : isIcElim ? 'italic' : ''}>
          {isIcElim ? 'IC eliminations' : displayEntityName(row.entity_name)}
        </span>
      </td>

      {visibleColumns.map(col => {
        if (col === 'delta_pm') {
          return (
            <DeltaBarCell
              key={col}
              value={getVal(col)}
              maxAbs={deltaPmMaxAbs}
              pct={section === 'margin'}
            />
          )
        }
        if (col === 'delta_plan') {
          return (
            <DeltaBarCell
              key={col}
              value={getVal(col)}
              maxAbs={deltaPlanMaxAbs}
              pct={section === 'margin'}
            />
          )
        }
        const clickable = (col === 'pm' || col === 'cm' || col === 'ytd') && section !== 'margin'
        const key = clickable
          ? drillKey(isTotal ? undefined : row.entity_code, section, col as TablePeriod)
          : ''
        const active = clickable && activeDrillKey === key
        return (
          <ValCell
            key={col}
            v={getVal(col)}
            pct={section === 'margin'}
            highlight={col === 'cm' ? 'cm' : col === 'ytd' ? 'ytd' : null}
            isActive={active}
            onClick={clickable ? () => handleClick(col as TablePeriod) : undefined}
          />
        )
      })}
    </tr>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function EbitTable({
  period,
  entity,
  onDrillDown,
  activeDrillKey,
  variant = 'cockpit',
  showSidebar = variant === 'cockpit',
  showColumnEditor = false,
  showExport = variant === 'cockpit',
  title = 'Total output, EBIT and EBIT Margin by Entity',
}: EbitTableProps) {
  const { year, month } = periodAnchorYearMonth(period)
  const [data,    setData]    = useState<EbitTableData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [plansByEntity, setPlansByEntity] = useState<Record<string, PlanPair>>({})
  const [planLoading, setPlanLoading] = useState(false)
  const [visibleColumns, setVisibleColumns] = useState<EbitDisplayColumn[]>(() => loadEbitColumns())
  const notesCtx = useOptionalActionNotesContext()
  const timedOut = useChartLoadReporter('cockpit-ebit', loading, error)

  useEffect(() => {
    if (!notesCtx) return
    if (!data) {
      notesCtx.unregisterTableCandidate('cockpit-ebit')
      return
    }
    notesCtx.registerTableCandidate({
      id: 'cockpit-ebit',
      label: 'EBIT by entity',
      description: 'Entity EBIT margin table',
      capture: () => captureEbitTableSnapshot(data),
      viewState: { tab: 'overview' },
    })
    return () => notesCtx.unregisterTableCandidate('cockpit-ebit')
  }, [notesCtx, data])

  useEffect(() => {
    setLoading(true)
    setError(null)
    setPlansByEntity({})
    const params = periodQueryParams(period)
    api
      .ebitTablePeriod({ ...params, entity } as Parameters<typeof api.ebitTablePeriod>[0])
      .then(res => setData(res.data))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [period, entity])

  useEffect(() => {
    if (!data || period.grain !== 'month') {
      setPlansByEntity({})
      setPlanLoading(false)
      return
    }
    let cancelled = false
    const entityCodes = data.rows
      .filter(r => r.entity_code !== '__total__' && r.entity_code !== '__ic_elim__')
      .map(r => r.entity_code)
    const totalScopeEntity = entity && entity !== 'all' ? entity : undefined

    async function loadPlans() {
      setPlanLoading(true)
      const map: Record<string, PlanPair> = {}
      const tasks = [
        ...entityCodes.map(async code => {
          try {
            const res = await api.financialsPlPlan(year, month, code)
            return { key: code, pair: planFromLines(res.lines) }
          } catch {
            return { key: code, pair: { outputPlanCm: null, ebitPlanCm: null } as PlanPair }
          }
        }),
        (async () => {
          try {
            const res = await api.financialsPlPlan(year, month, totalScopeEntity)
            return { key: '__total__', pair: planFromLines(res.lines) }
          } catch {
            return { key: '__total__', pair: { outputPlanCm: null, ebitPlanCm: null } as PlanPair }
          }
        })(),
      ]
      const resolved = await Promise.all(tasks)
      if (cancelled) return
      for (const item of resolved) map[item.key] = item.pair
      setPlansByEntity(map)
      setPlanLoading(false)
    }
    void loadPlans()
    return () => {
      cancelled = true
    }
  }, [data, period.grain, year, month, entity])

  if (error) return (
    <div className="rounded-xl px-5 py-4 text-sm" style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)', color: '#DC2626' }}>
      ⚠ {error}
    </div>
  )

  if (loading) return (
    <div className="rounded-xl p-6 animate-pulse" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}>
      <div className="h-4 w-48 rounded mb-4" style={{ background: '#E2E8F0' }} />
      {[1, 2, 3, 4, 5, 6, 7].map(i => (
        <div key={i} className="h-8 rounded mb-1" style={{ background: '#F4F6F9' }} />
      ))}
    </div>
  )

  if (timedOut) return null

  if (!data) return null

  const { col_labels: cols, rows } = data
  const entityRows = rows.filter(r => r.entity_code !== '__total__' && r.entity_code !== '__ic_elim__')
  const icElimRow = rows.find(r => r.entity_code === '__ic_elim__')
  const totalRow   = rows.find(r => r.entity_code === '__total__')!
  const allRowsWithTotal = icElimRow ? [...entityRows, icElimRow, totalRow] : [...entityRows, totalRow]

  const deltaMax = (section: Section, kind: 'delta_pm' | 'delta_plan') => {
    const values = allRowsWithTotal.map(r => {
      const plan = plansByEntity[r.entity_code] ?? plansByEntity.__total__ ?? null
      const cm = section === 'output' ? r.to_cm : section === 'ebit' ? r.ebit_cm : margin(r.ebit_cm, r.to_cm)
      const base = kind === 'delta_pm'
        ? (section === 'output' ? r.to_pm : section === 'ebit' ? r.ebit_pm : margin(r.ebit_pm, r.to_pm))
        : section === 'output'
          ? (plan?.outputPlanCm ?? null)
          : section === 'ebit'
            ? (plan?.ebitPlanCm ?? null)
            : (plan?.outputPlanCm != null && plan?.ebitPlanCm != null ? margin(plan.ebitPlanCm, plan.outputPlanCm) : null)
      if (cm === null || base === null) return 0
      return Math.abs(cm - base)
    })
    return Math.max(1, ...values)
  }

  const deltaOutputPmMax = deltaMax('output', 'delta_pm')
  const deltaOutputPlanMax = deltaMax('output', 'delta_plan')
  const deltaEbitPmMax = deltaMax('ebit', 'delta_pm')
  const deltaEbitPlanMax = deltaMax('ebit', 'delta_plan')
  const deltaMarginPmMax = deltaMax('margin', 'delta_pm')
  const deltaMarginPlanMax = deltaMax('margin', 'delta_plan')

  const narrative = buildEbitNarrative(entityRows, plansByEntity, totalRow, cols)

  const thBase: React.CSSProperties = {
    color: '#94A3B8', background: '#F8FAFC', fontSize: '0.7rem',
    position: 'sticky', top: 0, zIndex: 1,
  }

  const rowProps = (
    row: EbitTableRow,
    section: Section,
    isTotal: boolean,
    deltaPmMaxAbs: number,
    deltaPlanMaxAbs: number,
    isIcElim = false,
  ) => ({
    row,
    section,
    plan: isIcElim ? null : (plansByEntity[row.entity_code] ?? plansByEntity.__total__ ?? null),
    deltaPmMaxAbs,
    deltaPlanMaxAbs,
    isTotal,
    isIcElim,
    visibleColumns,
    year,
    month,
    activeDrillKey,
    onDrillDown: section !== 'margin' && !isIcElim ? onDrillDown : undefined,
  })

  async function handleExport(kind: PlExportKind) {
    if (!data) return
    const { col_labels: cols, rows } = data
    const entityRows = rows.filter(r => r.entity_code !== '__total__')
    const totalRow   = rows.find(r => r.entity_code === '__total__')!
    const colsWithPlan = cols as typeof cols & { plan_cm?: string; delta_cm_pm?: string }
    const deltaPmLabel = colsWithPlan.delta_cm_pm ?? `Δ ${cols.cm} − ${cols.pm}`
    const planLabel = colsWithPlan.plan_cm ?? `Plan ${cols.cm}`
    const deltaPlanLabel = `Δ ${cols.cm} − Plan`
    const headers = ['Entity', cols.pm, cols.cm, deltaPmLabel, planLabel, deltaPlanLabel, cols.ytd]

    const mkRow = (row: EbitTableRow, section: Section, isTotal: boolean): XlsxRow => {
      const plan = plansByEntity[row.entity_code] ?? plansByEntity.__total__ ?? null
      const outputPlan = plan?.outputPlanCm ?? null
      const ebitPlan = plan?.ebitPlanCm ?? null
      const outputDeltaPm = row.to_cm - row.to_pm
      const outputDeltaPlan = outputPlan === null ? null : row.to_cm - outputPlan
      const ebitDeltaPm = row.ebit_cm - row.ebit_pm
      const ebitDeltaPlan = ebitPlan === null ? null : row.ebit_cm - ebitPlan
      const mPm = margin(row.ebit_pm, row.to_pm)
      const mCm = margin(row.ebit_cm, row.to_cm)
      const mYtd = margin(row.ebit_ytd, row.to_ytd)
      const mPlan = outputPlan !== null && ebitPlan !== null ? margin(ebitPlan, outputPlan) : null
      const mDeltaPm = mPm === null || mCm === null ? null : mCm - mPm
      const mDeltaPlan = mPlan === null || mCm === null ? null : mCm - mPlan

      return {
      label: displayEntityName(row.entity_name),
      values: section === 'output'
        ? [
            Math.round(row.to_pm / 1000),
            Math.round(row.to_cm / 1000),
            Math.round(outputDeltaPm / 1000),
            outputPlan === null ? null : Math.round(outputPlan / 1000),
            outputDeltaPlan === null ? null : Math.round(outputDeltaPlan / 1000),
            Math.round(row.to_ytd / 1000),
          ]
        : section === 'ebit'
        ? [
            Math.round(row.ebit_pm / 1000),
            Math.round(row.ebit_cm / 1000),
            Math.round(ebitDeltaPm / 1000),
            ebitPlan === null ? null : Math.round(ebitPlan / 1000),
            ebitDeltaPlan === null ? null : Math.round(ebitDeltaPlan / 1000),
            Math.round(row.ebit_ytd / 1000),
          ]
        : [
            mPm == null ? null : +mPm.toFixed(1),
            mCm == null ? null : +mCm.toFixed(1),
            mDeltaPm == null ? null : +mDeltaPm.toFixed(1),
            mPlan == null ? null : +mPlan.toFixed(1),
            mDeltaPlan == null ? null : +mDeltaPlan.toFixed(1),
            mYtd == null ? null : +mYtd.toFixed(1),
          ],
      kind: isTotal ? 'subtotal' : 'data',
      kpiCols: section === 'margin' ? [0, 1, 2, 3, 4, 5] : undefined,
      }
    }
    const xlsxRows: XlsxRow[] = [
      { label: 'Total Output', values: [], kind: 'section' },
      ...entityRows.map(r => mkRow(r, 'output', false)),
      mkRow(totalRow, 'output', true),
      { label: 'EBIT', values: [], kind: 'section' },
      ...entityRows.map(r => mkRow(r, 'ebit', false)),
      mkRow(totalRow, 'ebit', true),
      { label: 'EBIT Margin', values: [], kind: 'section' },
      ...entityRows.map(r => mkRow(r, 'margin', false)),
      mkRow(totalRow, 'margin', true),
    ]
    const base = `EBIT_by_Entity_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: 'Total output, EBIT and EBIT Margin by Entity',
        tableHeading: 'Total output, EBIT and EBIT Margin by Entity',
        breadcrumbCurrent: 'Cockpit',
        footerRight: `${cols.pm} · ${cols.cm} · ${deltaPmLabel} · ${planLabel} · ${deltaPlanLabel} · ${cols.ytd} — kEUR`,
        headers,
        columnKinds: ['', '', 'cm', 'delta', '', 'delta', 'ytd'],
        rows: xlsxRows,
      })
      return
    }
    await exportToXlsx({
      title: 'Total output, EBIT and EBIT Margin by Entity',
      subtitle: `${cols.pm} · ${cols.cm} · ${deltaPmLabel} · ${planLabel} · ${deltaPlanLabel} · ${cols.ytd} — kEUR`,
      headers,
      rows: xlsxRows,
      filename: `${base}.xlsx`,
    })
  }

  const renderMetricBlock = (
    section: Section,
    label: string,
    deltaPmMax: number,
    deltaPlanMax: number,
  ) => (
    <>
      <SectionHeader label={label} />
      {entityRows.map(row => (
        <EntityRow
          key={`${section}-${row.entity_code}`}
          {...rowProps(row, section, false, deltaPmMax, deltaPlanMax)}
        />
      ))}
      {icElimRow && (
        <EntityRow
          key={`${section}-ic-elim`}
          {...rowProps(icElimRow, section, false, deltaPmMax, deltaPlanMax, true)}
        />
      )}
      <EntityRow {...rowProps(totalRow, section, true, deltaPmMax, deltaPlanMax)} />
    </>
  )

  const tableBlock = (
    <div className="overflow-x-auto min-w-0">
      <table className="w-full text-xs border-collapse table-fixed">
        <colgroup>
          <col style={{ width: '28%' }} />
          {visibleColumns.map(col => (
            <col key={col} style={{ width: `${72 / visibleColumns.length}%` }} />
          ))}
        </colgroup>
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0' }}>
            <th className="px-2 py-2.5 text-left font-semibold tracking-wide uppercase whitespace-nowrap" style={thBase}>
              kEUR
            </th>
            {visibleColumns.map(col => {
              const isCm = col === 'cm'
              const isYtd = col === 'ytd'
              const highlight = isCm || isYtd
              return (
                <th
                  key={col}
                  className="px-2 py-2.5 text-right font-semibold tracking-wide whitespace-nowrap"
                  style={{
                    ...thBase,
                    ...(highlight
                      ? {
                          background: 'rgba(148,163,184,0.18)',
                          color: '#1E3A5F',
                          borderLeft: '1px solid #CBD5E1',
                          borderRight: '1px solid #CBD5E1',
                        }
                      : {}),
                  }}
                >
                  {columnHeaderLabel(col, cols)}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {renderMetricBlock('output', 'Total output', deltaOutputPmMax, deltaOutputPlanMax)}
          {renderMetricBlock('ebit', 'EBIT', deltaEbitPmMax, deltaEbitPlanMax)}
          {renderMetricBlock('margin', 'EBIT margin', deltaMarginPmMax, deltaMarginPlanMax)}
        </tbody>
      </table>
    </div>
  )

  const headerToolbar = (
    <div className="flex items-center gap-2 shrink-0">
      {showColumnEditor && (
        <EbitColumnEditor visible={visibleColumns} onChange={setVisibleColumns} />
      )}
      {showExport && <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!data} />}
    </div>
  )

  if (variant === 'embedded') {
    return (
      <div>
        <div className="flex items-start justify-between gap-3 mb-3">
          <p className="text-xs m-0" style={{ color: '#94A3B8' }}>
            {visibleColumns.map(c => columnHeaderLabel(c, cols)).join(' · ')} — kEUR
            {planLoading ? ' · loading plan…' : ''}
          </p>
          {headerToolbar}
        </div>
        {tableBlock}
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="rounded-xl overflow-hidden flex flex-col"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}
    >
      <div className="px-4 py-3 flex items-start justify-between gap-3" style={{ borderBottom: '1px solid #E2E8F0' }}>
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#111827' }}>{title}</h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            {visibleColumns.map(c => columnHeaderLabel(c, cols)).join(' · ')} — kEUR
            {onDrillDown ? ' · click a value to drill down' : ''}
            {planLoading ? ' · loading plan…' : ''}
          </p>
        </div>
        {headerToolbar}
      </div>

      <div className={`grid grid-cols-1 gap-4 px-3 pb-3 ${showSidebar ? 'xl:grid-cols-[minmax(0,1.05fr)_minmax(380px,0.95fr)]' : ''}`}>
        {tableBlock}

        {showSidebar && (
        <aside className="rounded-xl border p-4 h-full" style={{ borderColor: '#E2E8F0', background: '#F8FAFC' }}>
          <p className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: '#64748B' }}>
            Text analysis
          </p>
          <p className="text-sm font-semibold mt-1" style={{ color: '#1E3A5F' }}>
            EBIT momentum and plan view
          </p>
          <p className="text-xs mt-2 leading-relaxed" style={{ color: '#475569' }}>
            {narrative.intro}
          </p>
          <ul className="mt-3 space-y-2 text-xs leading-relaxed list-disc pl-4" style={{ color: '#475569' }}>
            {narrative.bullets.map(line => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="text-xs mt-3 leading-relaxed" style={{ color: '#475569' }}>
            {narrative.closing}
          </p>
        </aside>
        )}
      </div>
    </motion.div>
  )
}
