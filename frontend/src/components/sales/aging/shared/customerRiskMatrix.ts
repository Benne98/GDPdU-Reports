import type { ReceivablesCustomerScatterRow } from '../../../../lib/api'

export type CustomerRiskQuadrant = 'core' | 'stable' | 'watch' | 'critical'

export type CustomerRiskQuadrantMeta = {
  id: CustomerRiskQuadrant
  label: string
  shortLabel: string
  hint: string
  action: string
  fill: string
  stroke: string
  badgeBg: string
}

export const CUSTOMER_RISK_QUADRANTS: CustomerRiskQuadrantMeta[] = [
  {
    id: 'core',
    label: 'Core customers',
    shortLabel: 'Core',
    hint: 'High volume · low overdue',
    action: 'Protect & grow',
    fill: 'rgba(37, 99, 235, 0.20)',
    stroke: '#1D4ED8',
    badgeBg: 'rgba(37, 99, 235, 0.14)',
  },
  {
    id: 'critical',
    label: 'Critical priority',
    shortLabel: 'Critical',
    hint: 'High volume · high overdue',
    action: 'Collect now',
    fill: 'rgba(220, 38, 38, 0.18)',
    stroke: '#B91C1C',
    badgeBg: 'rgba(220, 38, 38, 0.14)',
  },
  {
    id: 'stable',
    label: 'Stable accounts',
    shortLabel: 'Stable',
    hint: 'Lower volume · low overdue',
    action: 'Monitor',
    fill: 'rgba(16, 185, 129, 0.18)',
    stroke: '#059669',
    badgeBg: 'rgba(16, 185, 129, 0.14)',
  },
  {
    id: 'watch',
    label: 'Watch list',
    shortLabel: 'Watch',
    hint: 'Lower volume · elevated overdue',
    action: 'Early follow-up',
    fill: 'rgba(245, 158, 11, 0.22)',
    stroke: '#D97706',
    badgeBg: 'rgba(245, 158, 11, 0.16)',
  },
]

export type CustomerRiskPoint = ReceivablesCustomerScatterRow & {
  balanceKeur: number
  quadrant: CustomerRiskQuadrant
}

function median(values: number[]): number {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid]
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function assignCustomerQuadrant(
  balanceKeur: number,
  overduePct: number,
  midBalanceKeur: number,
  midOverduePct: number,
): CustomerRiskQuadrant {
  const highBalance = balanceKeur >= midBalanceKeur
  const highOverdue = overduePct >= midOverduePct
  if (highBalance && highOverdue) return 'critical'
  if (highBalance && !highOverdue) return 'core'
  if (!highBalance && highOverdue) return 'watch'
  return 'stable'
}

function overdueSplitThreshold(pcts: number[]): { value: number; label: string } {
  // Keep watch/critical split realistic: never at the chart ceiling (100%).
  const MIN_SPLIT = 10
  const MAX_SPLIT = 70
  const normalized = pcts
    .filter(p => Number.isFinite(p))
    .map(p => clamp(p, 0, 100))
  const withOverdue = normalized.filter(p => p > 0)
  if (!withOverdue.length) return { value: MIN_SPLIT, label: `${MIN_SPLIT}% risk line` }

  const m = median(withOverdue)
  const line = clamp(m, MIN_SPLIT, MAX_SPLIT)
  const label = m > MAX_SPLIT
    ? `${line.toFixed(0)}% capped overdue threshold`
    : `${line.toFixed(0)}% portfolio median`
  return { value: line, label }
}

export function overdueAmount(p: CustomerRiskPoint): number {
  return (p.balance * p.overdue_pct) / 100
}

/** Customers to label on the matrix: elevated overdue + top core accounts by volume. */
export function selectMatrixLabelCustomers(
  points: CustomerRiskPoint[],
  _midOverduePct: number,
  topCoreCount = 2,
): Set<string> {
  const names = new Set<string>()

  const criticalByOverdueAmount = points
    .filter(p => p.quadrant === 'critical')
    .sort((a, b) => overdueAmount(b) - overdueAmount(a))
    .slice(0, 3)

  const watchByOverdueAmount = points
    .filter(p => p.quadrant === 'watch')
    .sort((a, b) => overdueAmount(b) - overdueAmount(a))
    .slice(0, 2)

  for (const p of criticalByOverdueAmount) names.add(p.customer_name)
  for (const p of watchByOverdueAmount) names.add(p.customer_name)

  const coreByBalance = points
    .filter(p => p.quadrant === 'core')
    .sort((a, b) => b.balance - a.balance)
    .slice(0, topCoreCount)

  for (const p of coreByBalance) {
    names.add(p.customer_name)
  }

  return names
}

export type CustomerRiskInsights = {
  intro: string
  splitVolumeLabel: string
  splitOverdueLabel: string
  topOpportunity: CustomerRiskPoint | null
  topCore: CustomerRiskPoint | null
  criticalOverdueTotal: number
  bullets: string[]
}

export function buildCustomerRiskInsights(
  points: CustomerRiskPoint[],
  midBalanceKeur: number,
  overdueSplitLabel: string,
): CustomerRiskInsights {
  const critical = points.filter(p => p.quadrant === 'critical')
  const core = points.filter(p => p.quadrant === 'core')
  const criticalOverdueTotal = critical.reduce((s, p) => s + overdueAmount(p), 0)

  const topOpportunity = [...points].sort((a, b) => overdueAmount(b) - overdueAmount(a))[0] ?? null
  const topCore = [...core].sort((a, b) => b.balance - a.balance)[0] ?? null

  const bullets: string[] = []
  if (topOpportunity && overdueAmount(topOpportunity) > 0) {
    bullets.push(
      `Highest recovery potential: ${topOpportunity.customer_name} — focus collections on this account first.`,
    )
  }
  if (critical.length > 0) {
    bullets.push(
      `${critical.length} critical customer${critical.length === 1 ? '' : 's'} combine high open receivables with elevated overdue — largest lever for risk reduction.`,
    )
  }
  if (topCore) {
    bullets.push(
      `Strongest low-risk relationship: ${topCore.customer_name} — maintain terms while protecting volume.`,
    )
  }
  if (bullets.length === 0) {
    bullets.push('No elevated overdue exposure in the current customer set — portfolio risk is broadly contained.')
  }

  return {
    intro:
      'Move right for larger open receivables per customer; move up for a higher overdue share. '
      + 'The top-right quadrant flags where volume and overdue risk overlap.',
    splitVolumeLabel: `${midBalanceKeur.toFixed(0)} kEUR median volume`,
    splitOverdueLabel: overdueSplitLabel,
    topOpportunity: topOpportunity && overdueAmount(topOpportunity) > 0 ? topOpportunity : null,
    topCore,
    criticalOverdueTotal,
    bullets: bullets.slice(0, 3),
  }
}

export function buildCustomerRiskMatrix(rows: ReceivablesCustomerScatterRow[]) {
  const points: CustomerRiskPoint[] = rows.map(r => ({
    ...r,
    balanceKeur: r.balance / 1000,
    quadrant: 'stable' as CustomerRiskQuadrant,
  }))

  const midBalanceKeur = median(points.map(p => p.balanceKeur))
  const overdueSplit = overdueSplitThreshold(points.map(p => p.overdue_pct))
  const midOverduePct = overdueSplit.value

  for (const p of points) {
    p.quadrant = assignCustomerQuadrant(p.balanceKeur, p.overdue_pct, midBalanceKeur, midOverduePct)
  }

  const maxBalanceKeur = Math.max(...points.map(p => p.balanceKeur), midBalanceKeur * 1.2, 1)
  const maxOverduePct = Math.min(
    100,
    Math.max(...points.map(p => p.overdue_pct), midOverduePct * 1.35, 15),
  )

  const counts = CUSTOMER_RISK_QUADRANTS.reduce(
    (acc, q) => {
      acc[q.id] = points.filter(p => p.quadrant === q.id).length
      return acc
    },
    {} as Record<CustomerRiskQuadrant, number>,
  )

  const insights = buildCustomerRiskInsights(points, midBalanceKeur, overdueSplit.label)

  return {
    points,
    midBalanceKeur,
    midOverduePct,
    overdueSplitLabel: overdueSplit.label,
    maxBalanceKeur,
    maxOverduePct,
    counts,
    insights,
  }
}

export function quadrantMeta(id: CustomerRiskQuadrant): CustomerRiskQuadrantMeta {
  return CUSTOMER_RISK_QUADRANTS.find(q => q.id === id) ?? CUSTOMER_RISK_QUADRANTS[0]
}
