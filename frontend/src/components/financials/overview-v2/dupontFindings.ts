/**
 * dupontFindings.ts — P2 DuPont findings builder (reporting-v2 / port 5177 only).
 *
 * Derives ≤3 OverviewFinding objects from an existing DuPontData payload.
 *
 * This file lives in overview-v2/ and is tree-shaken from fdd-merge (5176) and
 * v4 (5178) bundles via the IS_OVERVIEW_V2 mode gate in OverviewPage.tsx.
 *
 * FINANCIAL SIGN-OFF (docs/overview-v2-redesign-plan.md §2 Area 7):
 *   - Uses the EBIT-based metrics already computed by the backend:
 *       ROS = EBIT / Revenue  (per DuPontTree.tsx formula annotation)
 *       ROI = EBIT / Total Assets
 *       ROE = EBIT / Equity
 *   - NO net-income ROE, NO new ROCE denominator — those are deferred and unsigned.
 *   - NO formula change — reads existing metric values from the dupont endpoint only.
 *   - DuPont identity (ROE == ROI × EquityMultiplier) is validated server-side;
 *     this module does not re-assert it.
 */

import type { DuPontData } from '../../../lib/api'
import type { OverviewFinding } from './findingsModel'

// ─── Options ──────────────────────────────────────────────────────────────────

export interface BuildDuPontFindingsOptions {
  /** Maximum number of findings to return. Default 3 (plan §6 confirms ≤3). */
  max?: number
}

// ─── Private helpers ──────────────────────────────────────────────────────────

function d(
  a: number | null | undefined,
  b: number | null | undefined,
): number | null {
  if (a == null || b == null) return null
  return a - b
}

/** Format a percentage value already multiplied by 100 in German locale. */
function fmtPct(v: number): string {
  const abs = Math.abs(v)
  const s =
    abs.toLocaleString('de-DE', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    }) + '%'
  return v < 0 ? `(${s})` : s
}

/** Format a pp delta with sign prefix (German decimal separator). */
function fmtPpDelta(v: number): string {
  const sign = v >= 0 ? '+' : '−'
  const abs = Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 1 })
  return `${sign}${abs} pp`
}

/** Format an asset-turnover value as "X,XX×". */
function fmtXVal(v: number): string {
  return (
    Math.abs(v).toLocaleString('de-DE', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }) + '×'
  )
}

/** Format an asset-turnover delta with sign prefix. */
function fmtXDelta(v: number): string {
  const sign = v >= 0 ? '+' : '−'
  const abs = Math.abs(v).toLocaleString('de-DE', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return `${sign}${abs}×`
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Derive ≤max actionable one-liner findings from an existing DuPontData payload.
 *
 * Priority order:
 *   1. ROE headline                 → /income-statement
 *   2. ROS / operating margin       → /income-statement
 *   3a. Asset turnover movement     → /working-capital
 *   3b. Equity multiplier/leverage  → /balance-sheet  (if AT slot already used)
 *
 * Each finding is a single sentence and a confirmed deep-link route.
 * Severity 'warning' is applied when the metric moved more than 1 pp unfavorably.
 */
export function buildDuPontFindings(
  data: DuPontData,
  opts: BuildDuPontFindingsOptions = {},
): OverviewFinding[] {
  const max = opts.max ?? 3
  const m = data.metrics
  const pmLabel = data.pm_label || 'prior month'
  const findings: OverviewFinding[] = []

  // ── 1. ROE headline (always lead if value is available) ───────────────────
  const roe = m.roe
  if (roe?.value != null) {
    const dPm = d(roe.value, roe.pm)
    const dir =
      dPm == null ? '' : dPm >= 0 ? ', improved' : ', weakened'
    const deltaStr =
      dPm != null ? ` (${fmtPpDelta(dPm)} vs ${pmLabel})` : ''
    findings.push({
      id: 'dupont-roe',
      severity: dPm != null && dPm < -1 ? 'warning' : 'info',
      text: `Return on equity at ${fmtPct(roe.value)}${dir}${deltaStr} — review profitability and leverage decomposition.`,
      route: '/income-statement',
    })
  }

  if (findings.length >= max) return findings.slice(0, max)

  // ── 2. ROS / operating margin ─────────────────────────────────────────────
  // Surface if it moved ≥ 0.3 pp vs prior month, or if we have fewer than 2
  // findings so far (fill the slot even with small movement).
  const ros = m.ros
  if (ros?.value != null) {
    const dPm = d(ros.value, ros.pm)
    const isNotable = dPm == null || Math.abs(dPm) >= 0.3 || findings.length < 1
    if (isNotable) {
      const dir =
        dPm == null ? 'stands'
        : dPm >= 0  ? 'expanded'
        :              'compressed'
      const deltaStr =
        dPm != null ? ` (${fmtPpDelta(dPm)} vs ${pmLabel})` : ''
      findings.push({
        id: 'dupont-ros',
        severity: dPm != null && dPm < -1 ? 'warning' : 'info',
        text: `Return on sales ${dir} to ${fmtPct(ros.value)}${deltaStr} — trace revenue and cost drivers on the income statement.`,
        route: '/income-statement',
      })
    }
  }

  if (findings.length >= max) return findings.slice(0, max)

  // ── 3a. Asset turnover → working-capital deep-link ────────────────────────
  const at = m.asset_turnover
  if (at?.value != null) {
    const dPm = d(at.value, at.pm)
    const deltaStr =
      dPm != null ? ` (${fmtXDelta(dPm)} vs ${pmLabel})` : ''
    findings.push({
      id: 'dupont-asset-turnover',
      severity: 'info',
      text: `Asset turnover at ${fmtXVal(at.value)}${deltaStr} — receivables, inventory, and payables drive capital efficiency.`,
      route: '/working-capital',
    })
    if (findings.length >= max) return findings.slice(0, max)
  }

  // ── 3b. Equity multiplier → balance-sheet deep-link ───────────────────────
  const em = m.equity_multiplier
  if (em?.value != null) {
    const dPm = d(em.value, em.pm)
    const deltaStr =
      dPm != null ? ` (${fmtXDelta(dPm)} vs ${pmLabel})` : ''
    findings.push({
      id: 'dupont-equity-multiplier',
      severity: 'info',
      text: `Equity multiplier at ${fmtXVal(em.value)}${deltaStr} — leverage scales investment return into equity return.`,
      route: '/balance-sheet',
    })
  }

  return findings.slice(0, max)
}
