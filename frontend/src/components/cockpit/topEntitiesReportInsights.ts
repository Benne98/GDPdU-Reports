import type {
  AgingResponse,
  OpportunityPlanCoverageBridgeResponse,
  SalesBreakdownResponse,
  SalesChurnBridgeResponse,
  SalesPvmBridgeResponse,
  SalesTopEntity,
} from '../../lib/api'
import { fmtChartKpi } from '../../lib/fmt'

type Mode = 'customer' | 'supplier'

type Inputs = {
  mode: Mode
  rows: SalesTopEntity[]
  churn: SalesChurnBridgeResponse | null
  pvm: SalesPvmBridgeResponse | null
  geo: SalesBreakdownResponse | null
  opportunities: OpportunityPlanCoverageBridgeResponse | null
  aging: AgingResponse | null
}

function sum(rows: SalesTopEntity[], field: keyof SalesTopEntity): number {
  return rows.reduce((s, r) => s + (Number(r[field] ?? 0) || 0), 0)
}

function signed(v: number): string {
  return `${v >= 0 ? '+' : ''}${fmtChartKpi(v)}`
}

function pct(cur: number, base: number): string {
  if (!isFinite(base) || Math.abs(base) < 1e-6) return 'n/a'
  return `${((cur - base) / Math.abs(base) * 100).toFixed(1)}%`
}

function overdueAmount(aging: AgingResponse): number {
  const total = aging.series.reduce((s, b) => s + (Number(b.value ?? 0) || 0), 0)
  const notDue = aging.series
    .filter(b => {
      const t = `${b.band ?? ''} ${b.label ?? ''}`.toLowerCase()
      return t.includes('current') || t.includes('not due') || t.includes('before due')
    })
    .reduce((s, b) => s + (Number(b.value ?? 0) || 0), 0)
  const overdue = total - notDue
  return overdue > 0 ? overdue : 0
}

function buildGeoBullet(geo: SalesBreakdownResponse | null): string {
  if (!geo || !geo.rows.length) {
    return 'Geography (PM/Plan): no usable regional breakdown was returned for this period.'
  }
  const byRegion = new Map<string, { cm: number; pm: number; plan: number }>()
  for (const r of geo.rows) {
    const key = String(r.l1 || '').trim() || 'Unassigned'
    const prev = byRegion.get(key) ?? { cm: 0, pm: 0, plan: 0 }
    prev.cm += Number(r.gs_cm ?? 0) || 0
    prev.pm += Number(r.gs_pm ?? 0) || 0
    prev.plan += Number(r.gs_plan_cm ?? 0) || 0
    byRegion.set(key, prev)
  }
  const ranked = [...byRegion.entries()]
    .map(([region, v]) => ({
      region,
      ...v,
      deltaPm: v.cm - v.pm,
      deltaPlan: v.cm - v.plan,
    }))
    .sort((a, b) => b.cm - a.cm)
  const top = ranked[0]
  const strongestPm = [...ranked].sort((a, b) => b.deltaPm - a.deltaPm)[0]
  return `Geography (PM/Plan): ${top.region} leads CM at ${fmtChartKpi(top.cm)} kEUR; strongest PM uplift comes from ${strongestPm.region} (${signed(strongestPm.deltaPm)} kEUR, ${pct(strongestPm.cm, strongestPm.pm)}), while top-region plan gap is ${signed(top.deltaPlan)} kEUR.`
}

export function buildTopEntityReportInsights({
  mode,
  rows,
  churn,
  pvm,
  geo,
  opportunities,
  aging,
}: Inputs): { intro: string; bullets: string[] } {
  const totalCm = sum(rows, 'cm')
  const totalPm = sum(rows, 'pm')
  const totalPlan = sum(rows, 'plan_cm')
  const top = [...rows].sort((a, b) => (b.cm ?? 0) - (a.cm ?? 0))[0] ?? null

  const intro = mode === 'customer'
    ? `Overall development: CM customer portfolio stands at ${fmtChartKpi(totalCm)} kEUR (${signed(totalCm - totalPm)} vs PM; ${signed(totalCm - totalPlan)} vs plan). Top customer is ${top?.name ?? 'n/a'} with ${fmtChartKpi(top?.cm ?? 0)} kEUR.`
    : `Overall development: CM supplier portfolio stands at ${fmtChartKpi(totalCm)} kEUR (${signed(totalCm - totalPm)} vs PM; ${signed(totalCm - totalPlan)} vs plan). Largest supplier is ${top?.name ?? 'n/a'} with ${fmtChartKpi(top?.cm ?? 0)} kEUR.`

  const churnBridge = churn?.bridges?.[churn.bridges.length - 1]
  const churnNet = churnBridge
    ? (churnBridge.new + churnBridge.upsell + churnBridge.cross_sell - churnBridge.downsell - churnBridge.lost)
    : null
  const churnBullet = churnBridge
    ? `Churn: latest bridge (${churnBridge.from}→${churnBridge.to}) nets ${signed(churnNet ?? 0)} kEUR (new ${fmtChartKpi(churnBridge.new)}, upsell ${fmtChartKpi(churnBridge.upsell)}, cross-sell ${fmtChartKpi(churnBridge.cross_sell)}, downsell ${fmtChartKpi(churnBridge.downsell)}, lost ${fmtChartKpi(churnBridge.lost)}).`
    : 'Churn: no bridge data returned for this period.'

  const pvmBridge = pvm?.bridges?.[pvm.bridges.length - 1]
  const pvmBullet = pvmBridge
    ? `Price-Volume-Mix: price ${signed(pvmBridge.price_effect)} kEUR, volume ${signed(pvmBridge.volume_effect)} kEUR, mix ${signed(pvmBridge.mix_effect ?? 0)} kEUR in the latest step.`
    : 'Price-Volume-Mix: no bridge data returned for this period.'

  const geoBullet = buildGeoBullet(geo)

  const oppCurrent = opportunities?.monthly_coverage.find(m => m.is_current)
    ?? opportunities?.monthly_coverage[opportunities.monthly_coverage.length - 1]
  const oppBullet = opportunities && oppCurrent
    ? `Opportunities execution: coverage sits at ${(oppCurrent.coverage_pct ?? 0).toFixed(1)}% (${fmtChartKpi(oppCurrent.covered_keur)} kEUR covered vs ${fmtChartKpi(oppCurrent.plan_keur)} kEUR plan), with remaining gap ${fmtChartKpi(oppCurrent.gap_keur)} kEUR.`
    : 'Opportunities execution: plan-coverage bridge is not available for this period.'

  const overdue = aging ? overdueAmount(aging) : null
  const agingBullet = overdue !== null
    ? `Aging linkage: overdue exposure in portfolio is ${fmtChartKpi(overdue)} kEUR, which should be prioritised for conversion to protect CM/PVM gains.`
    : 'Aging linkage: aging data is not available for this period.'

  return {
    intro,
    bullets: [churnBullet, pvmBullet, geoBullet, oppBullet, agingBullet],
  }
}
