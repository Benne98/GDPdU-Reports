import type { SalesPlanSource, SalesTopEntitiesColLabels, SalesTopEntity, SalesTopOrder } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'

type EntityKind = 'customer' | 'supplier'
type RankKey = 'cm' | 'ytd' | 'mtd'

type Narrative = {
  intro: string
  bullets: string[]
}

function nonZero(n: number): boolean {
  return Number.isFinite(n) && Math.abs(n) > 1e-6
}

function signedKeur(value: number): string {
  return `${value >= 0 ? '+' : ''}${fmtChartKpi(value)}`
}

function pct(part: number, total: number): number {
  if (!nonZero(total)) return 0
  return (part / total) * 100
}

function coverageSourceText(planMix: SalesPlanSource): string {
  if (planMix === 'customer_csv') return 'Plan and coverage are based on loaded partner-plan budgets per customer/supplier.'
  if (planMix === 'entity_allocated') return 'Plan and coverage allocate entity plan amounts using historical mix shares.'
  return 'Plan and coverage use prior-year actuals as proxy where no explicit budget file is loaded.'
}

export function buildTopOrdersNarrative(rows: SalesTopOrder[]): Narrative {
  if (!rows.length) {
    return {
      intro: 'No orders available: no invoiced line items were returned for the selected period.',
      bullets: [
        'Please review entity, time-range, and segment filters.',
        'After data is available, rebuild the top-order ranking and re-evaluate concentration.',
      ],
    }
  }

  const total = rows.reduce((s, r) => s + (Number(r.amount_keur) || 0), 0)
  const top1 = rows[0]
  const top3 = rows.slice(0, 3).reduce((s, r) => s + (Number(r.amount_keur) || 0), 0)
  const top5 = rows.slice(0, 5).reduce((s, r) => s + (Number(r.amount_keur) || 0), 0)
  const hhi = rows.reduce((s, r) => {
    const share = pct(Number(r.amount_keur) || 0, total) / 100
    return s + share * share
  }, 0) * 10000

  const marginRows = rows.filter(r => r.gross_profit_keur != null && r.gross_margin_pct != null)
  const grossProfit = marginRows.reduce((s, r) => s + (Number(r.gross_profit_keur) || 0), 0)
  const weightedMargin = total > 0 ? (grossProfit / total) * 100 : null
  const avgLines = rows.reduce((s, r) => s + (Number(r.line_count) || 0), 0) / rows.length
  const avgProducts = rows.reduce((s, r) => s + (Number(r.product_count) || 0), 0) / rows.length

  const byCustomer = new Map<string, number>()
  for (const row of rows) {
    const key = (row.customer_name || 'Unassigned').trim() || 'Unassigned'
    byCustomer.set(key, (byCustomer.get(key) ?? 0) + (Number(row.amount_keur) || 0))
  }
  const rankedCustomers = [...byCustomer.entries()].sort((a, b) => b[1] - a[1])
  const topCustomer = rankedCustomers[0]
  const concentrationTier = hhi >= 2500 ? 'highly concentrated' : hhi >= 1500 ? 'moderately concentrated' : 'broadly distributed'

  return {
    intro: `Top-order diagnostics: listed invoices sum to ${fmtChartKpi(total)} kEUR. The set is ${concentrationTier} (HHI ${hhi.toFixed(0)}).`,
    bullets: [
      `Concentration: top-1 represents ${pct(top1.amount_keur, total).toFixed(1)}% (${fmtChartKpi(top1.amount_keur)} kEUR), top-3 ${pct(top3, total).toFixed(1)}%, and top-5 ${pct(top5, total).toFixed(1)}% of volume.`,
      `Customer exposure: leading account is ${topCustomer?.[0] ?? 'n/a'} with ${fmtChartKpi(topCustomer?.[1] ?? 0)} kEUR, making this cluster the primary focus for risk and pipeline review.`,
      weightedMargin != null
        ? `Profit quality: aggregated gross profit is ${fmtChartKpi(grossProfit)} kEUR with an implied weighted margin of ${weightedMargin.toFixed(1)}% across top orders.`
        : 'Profit quality: gross-profit/margin fields are missing for these orders; margin review should be completed with enriched data.',
      `Order complexity: average order contains ${avgLines.toFixed(1)} lines and ${avgProducts.toFixed(1)} products; higher values suggest more operational coordination effort.`,
      `Largest single invoice: ${top1.invoice_number} (${fmtChartKpi(top1.amount_keur)} kEUR) for ${top1.customer_name || 'n/a'} - review repeatability and counterparty risk.`,
    ],
  }
}

export function buildTopEntityNarrative({
  kind,
  rows,
  rankBy,
  colLabels,
  planMix,
  periodGrain,
}: {
  kind: EntityKind
  rows: SalesTopEntity[]
  rankBy: RankKey
  colLabels: SalesTopEntitiesColLabels
  planMix: SalesPlanSource
  periodGrain: 'month' | 'week'
}): Narrative {
  const label = kind === 'customer' ? 'customers' : 'suppliers'
  const singular = kind === 'customer' ? 'customer' : 'supplier'
  const metricField: RankKey = periodGrain === 'week' && rankBy === 'ytd' ? 'mtd' : rankBy
  const metricLabel = metricField === 'cm' ? colLabels.cm : metricField === 'mtd' ? (colLabels.mtd ?? 'MTD') : colLabels.ytd

  if (!rows.length) {
    return {
      intro: `No ${label} data available: the ranking is empty for the current filter selection.`,
      bullets: [
        'Check entity, period, and segment filters together with source-data availability.',
        `Once data is available, focus on concentration, movement contribution, and plan coverage per ${singular}.`,
      ],
    }
  }

  const metric = (r: SalesTopEntity): number =>
    metricField === 'cm' ? Number(r.cm || 0) : metricField === 'mtd' ? Number(r.mtd || 0) : Number(r.ytd || 0)
  const sorted = [...rows].sort((a, b) => metric(b) - metric(a))
  const totalMetric = sorted.reduce((s, r) => s + metric(r), 0)
  const top = sorted[0]
  const top3Metric = sorted.slice(0, 3).reduce((s, r) => s + metric(r), 0)
  const movers = sorted
    .map(r => ({ name: r.name, delta: Number(r.delta_cm_pm ?? (r.cm - r.pm)) }))
    .filter(r => nonZero(r.delta))
  const up = [...movers].sort((a, b) => b.delta - a.delta)[0]
  const down = [...movers].sort((a, b) => a.delta - b.delta)[0]

  const planGap = sorted.reduce((s, r) => {
    const cm = Number(r.cm || 0)
    const plan = Number(r.plan_cm ?? 0)
    return s + (nonZero(plan) ? cm - plan : 0)
  }, 0)
  const coverageValues = sorted
    .map(r => Number(r.coverage))
    .filter(v => Number.isFinite(v))
  const avgCoverage = coverageValues.length
    ? coverageValues.reduce((s, v) => s + v, 0) / coverageValues.length
    : null

  const thresholds = [30, 60, 80]
  const countsToThreshold: number[] = []
  let running = 0
  let idx = 0
  for (const t of thresholds) {
    while (idx < sorted.length && pct(running, totalMetric) < t) {
      running += metric(sorted[idx])
      idx += 1
    }
    countsToThreshold.push(idx)
  }

  return {
    intro: `${kind === 'customer' ? 'Customer' : 'Supplier'} analysis: ${metricLabel} volume is ${fmtChartKpi(totalMetric)} kEUR. Top ${singular} is ${top.name} at ${fmtChartKpi(metric(top))} kEUR.`,
    bullets: [
      `Concentration: top-1 contributes ${pct(metric(top), totalMetric).toFixed(1)}%, top-3 ${pct(top3Metric, totalMetric).toFixed(1)}% of ${metricLabel}; 30/60/80% thresholds are reached after ${countsToThreshold.join(' / ')} ${label}.`,
      up && down
        ? `Movement vs prior month: strongest positive driver is ${up.name} (${signedKeur(up.delta)} kEUR), while the largest headwind is ${down.name} (${signedKeur(down.delta)} kEUR).`
        : 'Movement vs prior month: no significant delta values are available.',
      `Plan execution: aggregated CM-vs-plan deviation is ${signedKeur(planGap)} kEUR. ${coverageSourceText(planMix)}`,
      avgCoverage != null
        ? `Coverage quality: average coverage across measurable ${label} is ${avgCoverage.toFixed(1)}%. Values materially below 100% indicate plan risk, while values above 120% can indicate front-loading.`
        : `Coverage quality: no usable coverage values are available at ${singular} level.`,
      `Operational focus: review top ${singular} positions and negative delta drivers first across price, volume, mix, and commercial terms; then assign concrete actions with owners and timelines.`,
    ],
  }
}
