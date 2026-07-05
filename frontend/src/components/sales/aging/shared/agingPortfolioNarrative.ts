import type { ReceivablesAgingBand, ReceivablesStatusSplit } from '../../../../lib/api'
import { fmtAmount, fmtChartKpi } from '../../../../lib/fmt'

export type AgingPortfolioNarrativeInput = {
  side: 'receivables' | 'payables'
  periodLabel: string
  total: number
  series: ReceivablesAgingBand[]
  statusSplit?: ReceivablesStatusSplit
  overdueDays?: number
}

export type AgingPortfolioMetricChip = {
  label: string
  value: string
  hint?: string
  tone: 'brand' | 'warning' | 'neutral'
}

export type AgingPortfolioInsight = {
  title: string
  body: string
  tone: 'info' | 'watch' | 'positive'
}

export type AgingPortfolioNarrative = {
  eyebrow: string
  headline: string
  metrics: AgingPortfolioMetricChip[]
  summary: string
  insights: AgingPortfolioInsight[]
}

export function buildAgingPortfolioNarrative(input: AgingPortfolioNarrativeInput): AgingPortfolioNarrative {
  const { side, periodLabel, total, series, statusSplit, overdueDays } = input
  const label = side === 'receivables' ? 'receivables' : 'payables'
  const daysLabel = side === 'receivables' ? 'DSO' : 'DPO'
  const collectVerb = side === 'receivables' ? 'collections' : 'payment runs'

  const positive = series.filter(s => s.amount > 0)
  const overdueAmt =
    statusSplit?.overdue ?? positive.filter(s => s.band !== 'not_yet_due').reduce((s, b) => s + b.amount, 0)
  const beforeDueAmt =
    statusSplit?.before_due ?? positive.find(s => s.band === 'not_yet_due')?.amount ?? total - overdueAmt
  const overduePct = statusSplit?.overdue_pct ?? (total > 0 ? Math.round((overdueAmt / total) * 100) : 0)
  const beforePct = statusSplit?.before_due_pct ?? (total > 0 ? Math.round((beforeDueAmt / total) * 100) : 0)

  const sorted = [...positive].sort((a, b) => b.amount - a.amount)
  const top = sorted[0]
  const second = sorted[1]
  const severe = positive.find(s => s.band === 'overdue_over_180')
  const earlyOverdue = positive.filter(s =>
    ['overdue_1_30', 'overdue_31_60'].includes(s.band),
  )
  const earlyOverdueAmt = earlyOverdue.reduce((s, b) => s + b.amount, 0)

  const metrics: AgingPortfolioMetricChip[] = [
    {
      label: `Open ${label}`,
      value: fmtChartKpi(total),
      hint: `${fmtChartKpi(total)} kEUR`,
      tone: 'brand',
    },
    {
      label: 'Overdue share',
      value: `${overduePct}%`,
      hint: fmtAmount(overdueAmt),
      tone: overduePct >= 25 ? 'warning' : 'neutral',
    },
    {
      label: 'Not yet due',
      value: `${beforePct}%`,
      hint: fmtAmount(beforeDueAmt),
      tone: 'neutral',
    },
  ]

  if (overdueDays != null) {
    metrics.push({
      label: daysLabel,
      value: `${overdueDays.toLocaleString('de-DE', { maximumFractionDigits: 1 })} d`,
      hint: side === 'receivables' ? 'Days sales outstanding' : 'Days payables outstanding',
      tone: overdueDays > 45 ? 'warning' : 'neutral',
    })
  }

  const headline =
    overduePct >= 35
      ? `Elevated overdue exposure on open ${label}`
      : overduePct >= 18
        ? `Balanced mix with manageable overdue tail`
        : `Healthy due-date profile on open ${label}`

  const summary =
    `As of ${periodLabel}, ${fmtAmount(total * 1000)} remains open (${fmtChartKpi(total)} kEUR). `
    + `${beforePct}% (${fmtAmount(beforeDueAmt)}) is not yet due, while ${overduePct}% (${fmtAmount(overdueAmt)}) is past due. `
    + (overdueDays != null
      ? `${daysLabel} at ${overdueDays.toLocaleString('de-DE', { maximumFractionDigits: 1 })} days `
        + `${side === 'receivables' ? 'signals how quickly receivables convert relative to recent sales.' : 'reflects how long payables remain outstanding relative to procurement.'} `
      : '')
    + (top
      ? `The dominant bucket is ${top.label} (${fmtAmount(top.amount)}, ${total > 0 ? Math.round((top.amount / total) * 100) : 0}% of the portfolio).`
      : '')

  const insights: AgingPortfolioInsight[] = []

  if (top && second) {
    insights.push({
      title: 'Bucket concentration',
      body:
        `${top.label} and ${second.label} together represent `
        + `${total > 0 ? Math.round(((top.amount + second.amount) / total) * 100) : 0}% of the open balance. `
        + `Focus working capital actions on these buckets first when prioritising ${collectVerb}.`,
      tone: 'info',
    })
  }

  if (earlyOverdueAmt > 0) {
    insights.push({
      title: 'Early overdue (1–60 days)',
      body:
        `${fmtAmount(earlyOverdueAmt)} sits in the 1–60 day overdue bands — typically the highest recovery rate window. `
        + `Accelerate reminders and account reviews before balances migrate into older buckets.`,
      tone: 'watch',
    })
  }

  if (severe && severe.amount > 0) {
    insights.push({
      title: 'Structural overdue (>180 days)',
      body:
        `${fmtAmount(severe.amount)} is more than 180 days overdue. `
        + `Escalate disputes, payment plans, and write-off decisions for these positions.`,
      tone: 'watch',
    })
  } else if (overduePct <= 15) {
    insights.push({
      title: 'Overdue under control',
      body:
        `Overdue balances remain a contained share of total open ${label}. `
        + `Maintain standard dunning while monitoring any month-over-month drift in ${daysLabel}.`,
      tone: 'positive',
    })
  } else {
    insights.push({
      title: 'Working capital watch',
      body:
        `Overdue share above mid-teens warrants tighter payment-term enforcement and a review of top counterparties driving the tail.`,
      tone: 'watch',
    })
  }

  return {
    eyebrow: `${periodLabel} · Portfolio commentary`,
    headline,
    metrics,
    summary,
    insights,
  }
}
