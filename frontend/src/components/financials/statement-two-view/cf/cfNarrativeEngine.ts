import type {
  FinancialStatementResponse,
  FinancialStatementRow,
  PlNarrativeResponse,
} from '../../../../lib/api'
import { IS_OVERVIEW_V2 } from '../../../../lib/overviewV2Mode'
import { fmtNarrativeEurSigned } from '../../pl-two-view/narrativeFmt'
import { comparedToPriorPeriod } from '../../pl-two-view/proseStyle'
import {
  proseLabel,
  mapApiBulletsToUi,
  narrativeMatchesEntityScope,
  type PlNarrativeBullet,
  type PlNarrativeTone,
} from '../../pl-two-view/plNarrativeEngine'
import { bulletHasPeriodComparison } from '../narrativeTrust'

export type { PlNarrativeBullet as CfNarrativeBullet, PlNarrativeTone as CfNarrativeTone }

const TARGET = 5

function flattenLines(rows: FinancialStatementRow[]): FinancialStatementRow[] {
  const out: FinancialStatementRow[] = []
  function walk(r: FinancialStatementRow) {
    if ((r.row_kind === 'line' || r.row_kind === 'subtotal') && r.amounts) out.push(r)
    for (const c of r.children ?? []) walk(c)
  }
  for (const r of rows) walk(r)
  return out
}

function cfScale(rows: FinancialStatementRow[]): number {
  let total = 0
  for (const r of rows) {
    if (r.label?.toLowerCase().includes('operating cash flow') && r.amounts) {
      return Math.max(Math.abs(r.amounts.cm ?? 0), 1)
    }
  }
  for (const r of flattenLines(rows)) {
    total += Math.abs(r.amounts?.cm ?? 0)
  }
  return Math.max(total * 0.25, 1)
}

function priority(label: string): number {
  const low = label.toLowerCase()
  if (low.includes('operating cash flow')) return 35
  if (low.includes('free cash flow')) return 32
  if (low.includes('ebitda')) return 30
  if (low.includes('trade working capital') || low.includes('Δ trade')) return 28
  if (low.includes('capex')) return 26
  if (low.includes('other working capital') || low.includes('Δ other')) return 24
  if (low.includes('financing')) return 20
  if (low.includes('investing')) return 18
  return 0
}

function scoreRow(row: FinancialStatementRow, scale: number): number {
  const mom = Math.abs(row.deltas?.mom ?? 0)
  const cm = Math.abs(row.amounts?.cm ?? 0)
  const rel = scale ? (cm / scale) * 35 : 0
  return mom * 0.0004 + rel + priority(row.label)
}

function bulletText(
  row: FinancialStatementRow,
  data: FinancialStatementResponse,
  year: number,
  month: number,
): string {
  const lbl = proseLabel(row.label)
  const mom = row.deltas?.mom ?? 0
  const cmp = comparedToPriorPeriod(data, year, month)
  const asOf = data.col_labels?.cm ?? 'current period'
  const dir = mom >= 0 ? 'was higher by' : 'was lower by'
  return `${lbl.charAt(0).toUpperCase() + lbl.slice(1)} ${dir} ${fmtNarrativeEurSigned(Math.abs(mom))} ${cmp} (${asOf}: ${fmtNarrativeEurSigned(Math.abs(row.amounts?.cm ?? 0)).replace(/^\+/, '')}).`
}

export function buildClientCfNarrative(
  data: FinancialStatementResponse,
  year: number,
  month: number,
  maxBullets = TARGET,
): PlNarrativeResponse {
  const lines = flattenLines(data.rows)
  const scale = cfScale(data.rows)
  const ranked = lines
    .filter(r => Math.abs(r.deltas?.mom ?? 0) >= 30_000 || Math.abs(r.amounts?.cm ?? 0) >= 60_000)
    .sort((a, b) => scoreRow(b, scale) - scoreRow(a, scale))
    .slice(0, maxBullets)

  const lbl = data.col_labels
  const cm = lbl?.cm ?? 'current month'
  const cmp = comparedToPriorPeriod(data, year, month)
  const intro =
    ranked.length >= 2
      ? `At ${cm}, cash flow was driven mainly by ${proseLabel(ranked[0].label)} (${fmtNarrativeEurSigned(ranked[0].deltas?.mom ?? 0)} ${cmp}) and ${proseLabel(ranked[1].label)} (${fmtNarrativeEurSigned(ranked[1].deltas?.mom ?? 0)} ${cmp}).`
      : ranked.length === 1
        ? `At ${cm}, the main cash movement was ${proseLabel(ranked[0].label)} (${fmtNarrativeEurSigned(ranked[0].deltas?.mom ?? 0)} ${cmp}).`
        : `At ${cm}, cash flow was broadly stable.`

  return {
    headline: 'Cash flow — key drivers',
    intro,
    intro_facts: {
      period_label: cm,
      group_label: "the group's",
      net_profit_ytd: 0,
      coverage_pct: IS_OVERVIEW_V2
        ? (data.plan?.lines?.find(l => l.coverage_pct != null)?.coverage_pct ?? null)
        : null,
      cm_month_label: cm,
      cm_vs_plan: 0,
      cm_vs_plan_qualifier: '',
      primary_drivers: ranked.slice(0, 2).map(r => ({
        label: r.label,
        delta: r.deltas?.mom ?? 0,
        direction: (r.deltas?.mom ?? 0) >= 0 ? 'up' : 'down',
      })),
    },
    bullets: ranked.map((r, i) => ({
      index: i + 1,
      line_code: r.line_code || r.id,
      label: r.label,
      text: bulletText(r, data, year, month),
      tone: (r.deltas?.mom ?? 0) >= 0 ? 'positive' : 'negative',
    })),
    meta: {
      algorithm_version: 'cf_client_v2',
      llm_used: false,
      entity_scope: '',
    },
  }
}

export function isTrustedApiCfNarrative(
  n: PlNarrativeResponse | null | undefined,
  entity?: string,
): boolean {
  if (!n?.bullets?.length) return false
  if (!narrativeMatchesEntityScope(n, entity)) return false
  if (n.meta?.llm_used) return false
  const ver = n.meta?.algorithm_version ?? ''
  if (ver === 'cf_client_v1' || ver === 'cf_client_v2') return false
  const isBackend =
    ver.startsWith('cf_narrative_compat_v2') ||
    ver.startsWith('cf_narrative_v2') ||
    (ver.startsWith('cf_narrative') && !ver.includes('client'))
  if (!isBackend) return false
  const intro = n.intro ?? ''
  if (!/key drivers consist of:/i.test(intro)) return false
  const depthCount = n.bullets.filter(b => bulletHasPeriodComparison(b.text)).length
  return depthCount >= Math.min(2, n.bullets.length) || n.bullets.length >= 3
}

export { mapApiBulletsToUi }
