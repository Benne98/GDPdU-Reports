import type {
  FinancialsOverviewResponse,
  FinancialsOverviewSection,
  OverviewHighlight,
  OverviewMetricRow,
} from '../../../lib/api'
import type { PeriodGrain } from '../../../lib/periodSelection'
import type { KpiValueVariant } from '../../cockpit/KpiCard'

export function findOverviewRow(
  sections: FinancialsOverviewSection[],
  sectionId: string,
  rowId: string,
): OverviewMetricRow | undefined {
  const sec = sections.find(s => s.id === sectionId)
  return sec?.rows.find(r => r.id === rowId)
}

export function pickLeadHighlight(highlights: OverviewHighlight[] | undefined): string | null {
  if (!highlights?.length) return null
  for (const block of highlights) {
    const text = block.bullets?.[0]?.text?.trim()
    if (text) return text
  }
  return null
}

function firstSentence(text: string): string {
  const trimmed = text.trim()
  if (!trimmed) return ''
  const match = trimmed.match(/^(.+?[.!?])(?:\s|$)/)
  return match ? match[1] : trimmed
}

export type LeadBriefing = {
  headline: string
  intro: string
  heroTitle: string
  heroValue: number
  heroVariant: KpiValueVariant
  heroDelta: number | null
  heroDeltaLabel: string
}

export function buildLeadBriefing(data: FinancialsOverviewResponse): LeadBriefing {
  const isAnnual = data.period_grain === 'year'
  const np = findOverviewRow(data.sections, 'earnings', 'net_profit')
  const highlight = pickLeadHighlight(data.highlights)

  const cmLabel = data.col_labels.cm ?? data.col_labels.ytd ?? 'the period'
  let headline = highlight ? firstSentence(highlight) : ''
  if (!headline && np) {
    const delta = isAnnual ? np.deltas.yoy : np.deltas.mom
    const dir = (delta ?? 0) >= 0 ? 'up' : 'down'
    headline = `Net profit is ${dir} ${isAnnual ? 'year-on-year' : 'month-on-month'} in ${cmLabel}`
  }
  if (!headline) headline = 'Group performance snapshot'

  const heroTitle = isAnnual ? 'Net profit YTD' : 'Net profit'
  const heroValue = isAnnual ? Number(np?.amounts.ytd ?? 0) : Number(np?.amounts.cm ?? 0)
  const heroDelta = isAnnual
    ? (np?.deltas.ytd ?? null)
    : (np?.deltas.mom ?? null)
  const heroDeltaLabel = isAnnual ? 'Δ YTD vs prior year' : 'Δ Month-over-month'

  return {
    headline,
    intro: data.intro,
    heroTitle,
    heroValue,
    heroVariant: 'financial',
    heroDelta: heroDelta ?? null,
    heroDeltaLabel,
  }
}

export type OverviewKpiStripItem = {
  title: string
  value: number
  deltaPm: number | null
  deltaSmly: number | null
  variant: KpiValueVariant
  invertDelta?: boolean
  deltaPmLabel: string
  deltaSmlyLabel: string
}

export function buildKpiStripItems(
  data: FinancialsOverviewResponse,
  ccc: { value: number; deltaPrior: number | null } | null,
  grain: PeriodGrain,
): OverviewKpiStripItem[] {
  const isAnnual = data.period_grain === 'year'
  const isWeek = grain === 'week'
  const priorLabel = isWeek ? 'Δ Prior week' : isAnnual ? 'Δ YTD vs prior year' : 'Δ Month-over-month'
  const pyLabel = isWeek ? 'Δ Same week prior year' : isAnnual ? 'Δ vs prior year CM' : 'Δ Month in previous year'

  const np = findOverviewRow(data.sections, 'earnings', 'net_profit')
  const ebitdaMargin = findOverviewRow(data.sections, 'earnings', 'ebitda_margin')
  const fcf = findOverviewRow(data.sections, 'finance', 'free_cash_flow')

  const amountKey = isAnnual ? 'ytd' : 'cm'
  const deltaPriorKey = isAnnual ? 'ytd' : 'mom'
  const deltaPyKey = isAnnual ? 'yoy' : 'yoy'

  const base = { deltaPmLabel: priorLabel, deltaSmlyLabel: pyLabel }

  const items: OverviewKpiStripItem[] = [
    {
      title: isAnnual ? 'Net profit YTD' : 'Net profit',
      value: Number(np?.amounts[amountKey] ?? 0),
      deltaPm: np?.deltas[deltaPriorKey] ?? null,
      deltaSmly: np?.deltas[deltaPyKey] ?? null,
      variant: 'financial',
      ...base,
    },
    {
      title: 'EBITDA margin',
      value: Number(ebitdaMargin?.amounts[amountKey] ?? ebitdaMargin?.amounts.cm ?? 0),
      deltaPm: ebitdaMargin?.deltas[deltaPriorKey] ?? null,
      deltaSmly: ebitdaMargin?.deltas[deltaPyKey] ?? null,
      variant: 'percent',
      ...base,
    },
    {
      title: isAnnual ? 'Free cash flow YTD' : 'Free cash flow',
      value: Number(fcf?.amounts[amountKey] ?? 0),
      deltaPm: fcf?.deltas[deltaPriorKey] ?? null,
      deltaSmly: fcf?.deltas[deltaPyKey] ?? null,
      variant: 'financial',
      ...base,
    },
  ]

  if (ccc) {
    items.push({
      title: 'Cash conversion cycle',
      value: ccc.value,
      deltaPm: ccc.deltaPrior,
      deltaSmly: null,
      variant: 'days',
      invertDelta: true,
      deltaPmLabel: priorLabel,
      deltaSmlyLabel: pyLabel,
    })
  }

  return items
}

export type MarginInsightRow = {
  label: string
  value: number
}

export function buildMarginInsightRows(data: FinancialsOverviewResponse): MarginInsightRow[] {
  const isAnnual = data.period_grain === 'year'
  const key = isAnnual ? 'ytd' : 'cm'
  const ids = [
    ['gross_margin', 'Gross margin'],
    ['ebitda_margin', 'EBITDA margin'],
    ['net_profit_margin', 'Net margin'],
  ] as const

  return ids.map(([id, label]) => {
    const row = findOverviewRow(data.sections, 'earnings', id)
    return { label, value: Number(row?.amounts[key] ?? row?.amounts.cm ?? 0) }
  })
}

export function buildMarginInsightHeadline(data: FinancialsOverviewResponse): string {
  const ebitda = findOverviewRow(data.sections, 'earnings', 'ebitda_margin')
  const gross = findOverviewRow(data.sections, 'earnings', 'gross_margin')
  const isAnnual = data.period_grain === 'year'
  const key = isAnnual ? 'ytd' : 'cm'
  const eb = Number(ebitda?.amounts[key] ?? 0)
  const gm = Number(gross?.amounts[key] ?? 0)
  const delta = isAnnual ? ebitda?.deltas.ytd : ebitda?.deltas.mom
  if (delta == null) {
    return `Gross margin at ${gm.toFixed(1).replace('.', ',')}%, EBITDA margin at ${eb.toFixed(1).replace('.', ',')}%.`
  }
  const dir = delta >= 0 ? 'expanded' : 'compressed'
  return `EBITDA margin ${dir} to ${eb.toFixed(1).replace('.', ',')}% (${delta >= 0 ? '+' : ''}${delta.toFixed(1).replace('.', ',')} pp) on gross margin of ${gm.toFixed(1).replace('.', ',')}%.`
}

export function computeCccFromSeries(
  points: Array<{ dso: number | null; dio: number | null; dpo: number | null }>,
): { value: number; deltaPrior: number | null } | null {
  if (!points.length) return null
  const cccValues = points.map(p => {
    const dso = Number(p.dso ?? 0)
    const dio = Number(p.dio ?? 0)
    const dpo = Number(p.dpo ?? 0)
    return dso + dio - dpo
  })
  const value = cccValues[cccValues.length - 1]
  const prior = cccValues.length > 1 ? cccValues[cccValues.length - 2] : null
  return {
    value: Math.round(value),
    deltaPrior: prior != null ? Math.round(value - prior) : null,
  }
}
