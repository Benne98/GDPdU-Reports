import type {
  FinancialStatementResponse,
  FinancialStatementRow,
  PlNarrativeResponse,
} from '../../../../lib/api'
import { IS_OVERVIEW_V2 } from '../../../../lib/overviewV2Mode'
import { fmtNarrativeEurSigned } from '../../pl-two-view/narrativeFmt'
import {
  proseLabel,
  mapApiBulletsToUi,
  narrativeMatchesEntityScope,
  type PlNarrativeBullet,
  type PlNarrativeTone,
} from '../../pl-two-view/plNarrativeEngine'
import { comparedToPriorPeriod } from '../../pl-two-view/proseStyle'
import { bulletHasBsNarrativeDepth } from '../narrativeTrust'

export type { PlNarrativeBullet as BsNarrativeBullet, PlNarrativeTone as BsNarrativeTone }

const TARGET = 5

function monthLabel(year: number, month: number): string {
  const abbr = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${abbr[month - 1]}${String(year).slice(-2)}`
}

function priorMonthLabel(year: number, month: number): string {
  const pm = month > 1 ? month - 1 : 12
  const py = month > 1 ? year : year - 1
  return monthLabel(py, pm)
}

/** Mapping lines only — excludes Assets / Current assets aggregates. */
function isBsNarrativeLine(row: FinancialStatementRow): boolean {
  return row.row_kind === 'line'
}

function findRow(
  rows: FinancialStatementRow[],
  lineCode: string,
): FinancialStatementRow | null {
  for (const r of rows) {
    if ((r.line_code || r.id) === lineCode) return r
    const hit = findRow(r.children ?? [], lineCode)
    if (hit) return hit
  }
  return null
}

function flattenLines(rows: FinancialStatementRow[]): FinancialStatementRow[] {
  const out: FinancialStatementRow[] = []
  function walk(r: FinancialStatementRow) {
    if (isBsNarrativeLine(r) && r.amounts) out.push(r)
    for (const c of r.children ?? []) walk(c)
  }
  for (const r of rows) walk(r)
  return out
}

function sublineLeaders(row: FinancialStatementRow): { label: string; mom: number }[] {
  const out: { label: string; mom: number }[] = []
  for (const ch of row.children ?? []) {
    if (ch.row_kind !== 'line' && ch.row_kind !== 'subtotal') continue
    const mom = ch.deltas?.mom ?? 0
    if (Math.abs(mom) < 500) continue
    out.push({ label: ch.label ?? '', mom })
  }
  out.sort((a, b) => Math.abs(b.mom) - Math.abs(a.mom))
  return out
}

function priority(label: string): number {
  const low = label.toLowerCase()
  if (low.includes('receivable')) return 28
  if (low.includes('payable')) return 26
  if (low.includes('inventor')) return 22
  if (low.includes('provision') || low.includes('accrual')) return 20
  if (low.includes('fixed asset')) return 18
  return 0
}

function scoreRow(row: FinancialStatementRow, totalAssets: number): number {
  const mom = Math.abs(row.deltas?.mom ?? 0)
  const cm = Math.abs(row.amounts?.cm ?? 0)
  const rel = totalAssets ? (cm / totalAssets) * 40 : 0
  return mom * 0.0004 + rel + priority(row.label ?? '')
}

function totalAssets(rows: FinancialStatementRow[]): number {
  for (const r of rows) {
    if (r.label?.toLowerCase().includes('asset') && r.row_kind === 'subtotal') {
      return Math.abs(r.amounts?.cm ?? 0)
    }
  }
  return 0
}

function material(row: FinancialStatementRow, assets: number): boolean {
  const mom = Math.abs(row.deltas?.mom ?? 0)
  const cm = Math.abs(row.amounts?.cm ?? 0)
  return mom >= 50_000 || cm >= Math.max(100_000, assets * 0.02)
}

function bulletText(
  row: FinancialStatementRow,
  data: FinancialStatementResponse,
  year: number,
  month: number,
  treeRows: FinancialStatementRow[],
): string {
  const lbl = proseLabel(row.label ?? '')
  const mom = row.deltas?.mom ?? 0
  const isWeek = data.period_grain === 'week'
  const asOf = data.col_labels.cm ?? monthLabel(year, month)
  const pm = data.col_labels.pm ?? priorMonthLabel(year, month)
  const cmp = comparedToPriorPeriod(data, year, month)
  const cm = Math.abs(row.amounts?.cm ?? 0)
  const cmK = fmtNarrativeEurSigned(cm).replace(/^\+/, '')
  let base: string
  if (Math.abs(mom) < 50_000) {
    base = `${lbl.charAt(0).toUpperCase() + lbl.slice(1)} stood at ${cmK} as of ${asOf}.`
  } else {
    const dir = mom >= 0 ? 'up' : 'down'
    base =
      `${lbl.charAt(0).toUpperCase() + lbl.slice(1)} stood at ${cmK} as of ${asOf}, ${dir} ` +
      `${fmtNarrativeEurSigned(mom)} ${isWeek ? cmp : `from the ${pm} month-end`}.`
  }
  const lc = row.line_code || row.id || ''
  const treeRow = findRow(treeRows, lc) ?? row
  const leaders = sublineLeaders(treeRow)
  if (leaders.length && Math.abs(mom) >= 500) {
    const lead = leaders[0]
    const share = Math.abs(lead.mom) / Math.abs(mom) * 100
    base +=
      ` The move is led by ${proseLabel(lead.label)} (${fmtNarrativeEurSigned(lead.mom)}, ` +
      `${share.toFixed(0)}% of the change ${isWeek ? cmp : `vs ${pm} month-end`}).`
    if (leaders.length >= 2) {
      const sec = leaders[1]
      const share2 = Math.abs(sec.mom) / Math.abs(mom) * 100
      base +=
        ` A secondary contributor is ${proseLabel(sec.label)} ` +
        `(${fmtNarrativeEurSigned(sec.mom)}, ${share2.toFixed(0)}% of the movement).`
    }
  }
  return base
}

function selectClientBullets(
  lines: FinancialStatementRow[],
  assets: number,
  cap: number,
): FinancialStatementRow[] {
  const scored = lines
    .filter(r => material(r, assets))
    .map(r => ({ row: r, score: scoreRow(r, assets) }))
  if (scored.length <= cap) {
    return scored.map(s => s.row)
  }
  const byScore = [...scored].sort((a, b) => b.score - a.score)
  const keep = new Set(byScore.slice(0, cap).map(s => s.row.line_code || s.row.id))
  return scored.filter(s => keep.has(s.row.line_code || s.row.id)).map(s => s.row)
}

function totalAssetsRow(rows: FinancialStatementRow[]): FinancialStatementRow | null {
  for (const r of rows) {
    const lbl = (r.label || '').toLowerCase()
    if (lbl.includes('total asset') && !lbl.includes('liab')) return r
    const hit = totalAssetsRow(r.children ?? [])
    if (hit) return hit
  }
  return null
}

export function buildClientBsNarrative(
  data: FinancialStatementResponse,
  year: number,
  month: number,
  maxBullets = TARGET,
): PlNarrativeResponse {
  const lines = flattenLines(data.rows)
  const assets = totalAssets(data.rows)
  const chosen = selectClientBullets(lines, assets, maxBullets)
  const isWeek = data.period_grain === 'week'
  const cmLabel = data.col_labels?.cm ?? monthLabel(year, month)
  const asOfLabel = isWeek ? cmLabel : monthLabel(year, month)
  const cmp = comparedToPriorPeriod(data, year, month)

  const driverPhrase = (row: FinancialStatementRow) => {
    const d = row.deltas?.mom ?? 0
    return `${proseLabel(row.label ?? '')} (${fmtNarrativeEurSigned(d)} ${cmp})`
  }

  let intro =
    chosen.length >= 2
      ? `As of ${asOfLabel}, the balance sheet was shaped mainly by ${driverPhrase(chosen[0])} and ${driverPhrase(chosen[1])}.`
      : chosen.length === 1
        ? `As of ${asOfLabel}, the main balance sheet movement was ${driverPhrase(chosen[0])}.`
        : `As of ${asOfLabel}, balance sheet positions were broadly stable.`

  const ta = totalAssetsRow(data.rows)
  if (ta?.amounts) {
    const taCm = Math.abs(ta.amounts.cm ?? 0)
    const taMom = ta.deltas?.mom ?? 0
    if (taCm > 0) {
      const taK = fmtNarrativeEurSigned(taCm).replace(/^\+/, '')
      let taBit = `Total assets stood at ${taK} as of ${cmLabel}`
      if (Math.abs(taMom) >= 50_000) {
        const dir = taMom >= 0 ? 'up' : 'down'
        taBit += `, ${dir} ${fmtNarrativeEurSigned(Math.abs(taMom)).replace(/^\+/, '')} ${cmp}`
      }
      intro = `${taBit}. ${intro}`
    }
  }

  intro += ' Key drivers consist of:'

  return {
    headline: 'Balance sheet — key drivers',
    intro,
    intro_facts: {
      period_label: cmLabel,
      group_label: "the group's",
      net_profit_ytd: 0,
      coverage_pct: IS_OVERVIEW_V2
        ? (data.plan?.lines?.find(l => l.coverage_pct != null)?.coverage_pct ?? null)
        : null,
      cm_month_label: cmLabel,
      cm_vs_plan: 0,
      cm_vs_plan_qualifier: '',
      primary_drivers: chosen.slice(0, 2).map(r => ({
        label: r.label,
        delta: r.deltas?.mom ?? 0,
        direction: (r.deltas?.mom ?? 0) >= 0 ? 'up' : 'down',
      })),
    },
    bullets: chosen.map((r, i) => ({
      index: i + 1,
      line_code: r.line_code || r.id,
      label: r.label,
      text: bulletText(r, data, year, month, data.rows),
      tone: (r.deltas?.mom ?? 0) >= 0 ? 'positive' : 'negative',
    })),
    meta: {
      algorithm_version: 'bs_client_v2',
      llm_used: false,
      entity_scope: '',
    },
  }
}

export function isTrustedApiBsNarrative(
  n: PlNarrativeResponse | null | undefined,
  entity?: string,
): boolean {
  if (!n?.bullets?.length) return false
  if (!narrativeMatchesEntityScope(n, entity)) return false
  if (n.meta?.llm_used) return false
  const ver = n.meta?.algorithm_version ?? ''
  if (ver === 'bs_client_v2') return false
  const isBackend =
    ver.startsWith('bs_narrative_compat_v2') ||
    ver.startsWith('bs_narrative_v2') ||
    (ver.startsWith('bs_narrative') && !ver.includes('client'))
  if (!isBackend) return false
  const intro = n.intro ?? ''
  if (!/key drivers consist of:/i.test(intro)) return false
  const depthCount = n.bullets.filter(b => bulletHasBsNarrativeDepth(b.text)).length
  return depthCount >= Math.min(2, n.bullets.length)
}

export { mapApiBulletsToUi }
