/**
 * findingsBuilders.ts — client-side findings computation.
 *
 * buildFindingsP1  (P1): derives findings from useOverviewBriefing data.
 * buildFindingsFromSummary (P3): derives findings from the batched summary
 *   endpoint (GET /api/v1/financials/overview/summary), replacing P1 with
 *   a single round-trip.  Wires the DuPont + anomaly slots left unwired in P1.
 *
 * SCOPE: reporting-v2 / port 5177 only — tree-shaken via IS_OVERVIEW_V2 gate.
 */

import type { FinancialsOverviewResponse, OverviewHighlight } from '../../../lib/api'
import { findOverviewRow } from '../overview/overviewBriefingUtils'
import type { OverviewFinding, OverviewV2Route } from './findingsModel'
import { buildDuPontFindings } from './dupontFindings'
import type { OverviewSummaryData } from './hooks/useOverviewSummaryV2'

const MAX_FINDINGS = 5

/** Extract the first complete sentence from a string. */
function firstSentence(text: string): string {
  const trimmed = text.trim()
  if (!trimmed) return ''
  return trimmed.match(/^(.+?[.!?])(?:\s|$)/)?.[1] ?? trimmed
}

/** Map a highlight tab to its deep-link route. */
function tabToRoute(tab: OverviewHighlight['tab']): OverviewV2Route {
  switch (tab) {
    case 'wc': return '/working-capital'
    case 'cf': return '/cash-flow'
    case 'bs': return '/balance-sheet'
    default:   return '/income-statement'
  }
}

/**
 * Build one finding from a highlight block. Returns null if no usable bullet.
 * Severity is 'info' by default; callers may override.
 */
function findingFromHighlight(
  h: OverviewHighlight,
  id: string,
  severity: OverviewFinding['severity'] = 'info',
): OverviewFinding | null {
  const raw = h.bullets?.[0]?.text?.trim()
  if (!raw) return null
  return {
    id,
    severity,
    text: firstSentence(raw),
    route: tabToRoute(h.tab),
  }
}

// ─── Public API ───────────────────────────────────────────────────────────────

export interface FindingsP1Input {
  briefingData: FinancialsOverviewResponse | null
  ccc: { value: number; deltaPrior: number | null } | null
  /**
   * Optional: first sentence from buildDuPontNarrative — pass if DuPont data is
   * already loaded in the page (e.g. when DuPontTree renders first).
   * Deferred to P3 where the batched endpoint supplies it.
   */
  dupontFirstSentence?: string | null
  /**
   * Optional: total anomaly count from AnomaliesPanel.
   * When > 0 appends an anomaly finding pointing to /anomaly-detection.
   * Currently not wired in P1 (AnomaliesPanel owns its data); reserved for P3.
   */
  anomalyCount?: number | null
}

/**
 * Compute up to MAX_FINDINGS actionable findings from already-loaded briefing data.
 * Order: P&L performance → CCC/WC → WC narrative → CF narrative → DuPont → anomalies.
 */
export function buildFindingsP1({
  briefingData,
  ccc,
  dupontFirstSentence,
  anomalyCount,
}: FindingsP1Input): OverviewFinding[] {
  if (!briefingData) return []

  const findings: OverviewFinding[] = []
  const isAnnual = briefingData.period_grain === 'year'

  // ── 1. P&L performance: try highlight first, fall back to section delta ────
  const plHighlight = briefingData.highlights?.find(h => h.tab === 'pl')
  if (plHighlight) {
    // Determine severity from net-profit delta
    const np = findOverviewRow(briefingData.sections, 'earnings', 'net_profit')
    const npDelta = isAnnual ? (np?.deltas?.ytd ?? null) : (np?.deltas?.mom ?? null)
    const sev: OverviewFinding['severity'] = (npDelta !== null && npDelta < 0) ? 'warning' : 'info'
    const f = findingFromHighlight(plHighlight, 'pl-lead', sev)
    if (f) findings.push(f)
  } else {
    // Fallback: synthesise a sentence from section data
    const np = findOverviewRow(briefingData.sections, 'earnings', 'net_profit')
    if (np) {
      const amtKey = isAnnual ? 'ytd' : 'cm'
      const delta = isAnnual ? (np.deltas?.ytd ?? null) : (np.deltas?.mom ?? null)
      const val = Number(np.amounts?.[amtKey] ?? 0)
      const periodLabel = briefingData.col_labels?.cm ?? briefingData.col_labels?.ytd ?? 'this period'
      if (delta !== null) {
        const dir = delta >= 0 ? 'up' : 'down'
        const absKeur = Math.round(Math.abs(val) / 1000)
        findings.push({
          id: 'np-delta',
          severity: delta < 0 ? 'warning' : 'info',
          text: `Net profit ${dir} ${isAnnual ? 'year-on-year' : 'vs prior month'} — ${absKeur} kEUR in ${periodLabel}.`,
          route: '/income-statement',
        })
      }
    }
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 2. Cash conversion cycle ──────────────────────────────────────────────
  if (ccc) {
    const { value, deltaPrior } = ccc
    if (deltaPrior !== null && Math.abs(deltaPrior) >= 3) {
      const dir = deltaPrior > 0 ? 'up' : 'down'
      const absD = Math.abs(deltaPrior)
      findings.push({
        id: 'ccc-delta',
        severity: deltaPrior > 0 ? 'warning' : 'info',
        text: `Cash conversion cycle ${dir} ${absD}d vs prior period (now ${value}d) — working capital trend.`,
        route: '/working-capital',
      })
    } else {
      findings.push({
        id: 'ccc-snapshot',
        severity: 'info',
        text: `Cash conversion cycle at ${value}d — review DSO, DPO and inventory days for improvement levers.`,
        route: '/working-capital',
      })
    }
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 3. Working-capital highlight ──────────────────────────────────────────
  const wcHighlight = briefingData.highlights?.find(h => h.tab === 'wc')
  if (wcHighlight) {
    const f = findingFromHighlight(wcHighlight, 'wc-highlight')
    if (f) findings.push(f)
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 4. Cash-flow highlight ────────────────────────────────────────────────
  const cfHighlight = briefingData.highlights?.find(h => h.tab === 'cf')
  if (cfHighlight) {
    const f = findingFromHighlight(cfHighlight, 'cf-highlight')
    if (f) findings.push(f)
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 5. DuPont first sentence (optional — supplied by page when available) ─
  if (dupontFirstSentence) {
    findings.push({
      id: 'dupont-roe',
      severity: 'info',
      text: firstSentence(dupontFirstSentence),
      route: '/income-statement',
    })
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 6. Anomaly count (optional — wired in P3 via batched endpoint) ────────
  if (anomalyCount != null && anomalyCount > 0) {
    findings.push({
      id: 'anomaly-count',
      severity: anomalyCount >= 3 ? 'warning' : 'info',
      text: `${anomalyCount} posting ${anomalyCount === 1 ? 'anomaly' : 'anomalies'} flagged — review for data quality and posting errors.`,
      route: '/anomaly-detection',
    })
  }

  return findings.slice(0, MAX_FINDINGS)
}

// ─────────────────────────────────────────────────────────────────────────────
// P3: summary-based findings (replaces buildFindingsP1 for the primary path)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Build up to 5 actionable findings from the batched summary payload.
 * Wires the DuPont and anomaly slots that were left optional (unwired) in P1.
 *
 * Priority:
 *   1. Revenue YoY (hero.revenue.yoy_pct)          → /income-statement
 *   2. CCC / working-capital snapshot              → /working-capital
 *   3. DuPont headline (≤1, from summary.dupont)   → via buildDuPontFindings
 *   4. Recent-month exception alerts               → /anomaly-detection
 *   5. Top customer snapshot                       → /working-capital
 *
 * Note on units: top_customer.cm is in kEUR (from build_top_entities which
 * divides by 1000); hero/cash/wc amounts are in raw EUR.
 */
export function buildFindingsFromSummary(
  summary: OverviewSummaryData | null,
): OverviewFinding[] {
  if (!summary) return []

  const findings: OverviewFinding[] = []

  // ── 1. Revenue YoY ────────────────────────────────────────────────────────
  const yoyPct = summary.hero.revenue.yoy_pct
  if (yoyPct != null) {
    const dir = yoyPct >= 0 ? 'up' : 'down'
    const pctStr = Math.abs(yoyPct).toLocaleString('de-DE', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }) + '%'
    findings.push({
      id: 'rev-yoy',
      severity: yoyPct < -10 ? 'warning' : 'info',
      text: `Revenue ${dir} ${pctStr} year-on-year — review income statement for driver detail.`,
      route: '/income-statement',
    })
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 2. CCC / working-capital snapshot ─────────────────────────────────────
  const ccc = summary.working_capital.ccc
  if (ccc != null && ccc > 0) {
    findings.push({
      id: 'ccc-snapshot',
      severity: 'info',
      text: `Cash conversion cycle at ${Math.round(ccc)}d — DSO, DPO, and DIO drive working capital tie-up.`,
      route: '/working-capital',
    })
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 3. DuPont headline (≤1 finding) ───────────────────────────────────────
  if (summary.dupont) {
    const dpFindings = buildDuPontFindings(summary.dupont, { max: 1 })
    findings.push(...dpFindings)
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 4. Recent-month exception alerts ──────────────────────────────────────
  const alertCount = (summary.alerts ?? []).length
  if (alertCount > 0) {
    findings.push({
      id: 'summary-alerts',
      severity: alertCount >= 3 ? 'warning' : 'info',
      text: `${alertCount} recent-month ${alertCount === 1 ? 'alert' : 'alerts'} flagged — review for plan variances and posting anomalies.`,
      route: '/anomaly-detection',
    })
  }

  if (findings.length >= MAX_FINDINGS) return findings.slice(0, MAX_FINDINGS)

  // ── 5. Top customer snapshot (cm is in kEUR from build_top_entities) ──────
  const tc = summary.top_customer
  if (tc?.name && tc.cm != null && Math.abs(tc.cm) > 1e-6) {
    const cmKeur = Math.round(tc.cm)
    findings.push({
      id: 'top-customer',
      severity: 'info',
      text: `Top customer: ${tc.name} — ${cmKeur} kEUR this month. Review AR exposure and concentration.`,
      route: '/working-capital',
    })
  }

  return findings.slice(0, MAX_FINDINGS)
}
