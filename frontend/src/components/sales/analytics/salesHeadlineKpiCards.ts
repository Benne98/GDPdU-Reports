import type { SalesHeadlineKpiBlock, SalesHeadlineKpis } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'

export type SalesHeadlineCardConfig = {
  title: string
  value: number
  deltaPrior: number | null | undefined
  deltaPriorYear: number | null | undefined
  deltaPriorLabel: string
  deltaPriorYearLabel: string
  variant?: 'financial' | 'count' | 'percent'
  invertDelta?: boolean
}

const MONTH_PRIOR = 'Δ Month-over-month'
const MONTH_PY = 'Δ Month in previous year'
const WEEK_PRIOR = 'Δ Prior week'
const WEEK_PY = 'Δ Same week prior year'

function weekBlock(b: SalesHeadlineKpiBlock) {
  return {
    value: b.value,
    deltaPrior: b.delta_pw,
    deltaPriorYear: b.delta_smly,
  }
}

function monthBlock(b: SalesHeadlineKpiBlock) {
  return {
    value: b.value,
    deltaPrior: b.delta_pm,
    deltaPriorYear: b.delta_smly,
  }
}

/** Pick month or week metrics and delta labels from headline payload + period grain. */
export function buildSalesHeadlineCards(
  headline: SalesHeadlineKpis,
  period: PeriodSelection,
): SalesHeadlineCardConfig[] {
  const isWeek = period.grain === 'week'
  const priorLabel = isWeek ? WEEK_PRIOR : MONTH_PRIOR
  const pyLabel = isWeek ? WEEK_PY : MONTH_PY

  const grossSales = isWeek ? weekBlock(headline.week_revenue) : monthBlock(headline.month_revenue)
  const grossProfit = isWeek ? weekBlock(headline.week_gross_profit) : monthBlock(headline.month_gross_profit)

  const unitsSrc = isWeek
    ? headline.week_units_sold ?? headline.units_sold
    : headline.units_sold
  const unitsSold = unitsSrc
    ? (isWeek && headline.week_units_sold ? weekBlock(unitsSrc) : monthBlock(unitsSrc))
    : { value: 0, deltaPrior: 0, deltaPriorYear: 0 }

  const avgSrc = isWeek
    ? headline.week_avg_revenue_per_customer ?? headline.avg_revenue_per_customer
    : headline.avg_revenue_per_customer
  const avg = isWeek && headline.week_avg_revenue_per_customer
    ? weekBlock(avgSrc)
    : monthBlock(avgSrc)

  const churnSrc = isWeek
    ? headline.week_churn_rate ?? headline.churn_rate
    : headline.churn_rate
  const churn = isWeek && headline.week_churn_rate
    ? weekBlock(churnSrc)
    : monthBlock(churnSrc)

  const custSrc = isWeek
    ? headline.week_customer_count ?? headline.customer_count
    : headline.customer_count
  const customers = isWeek && headline.week_customer_count
    ? weekBlock(custSrc)
    : monthBlock(custSrc)

  const base = { deltaPriorLabel: priorLabel, deltaPriorYearLabel: pyLabel }

  return [
    { title: 'Gross sales', ...grossSales, ...base, variant: 'financial' },
    { title: 'Gross profit', ...grossProfit, ...base, variant: 'financial' },
    { title: '# Invoices', ...unitsSold, ...base, variant: 'count' },
    { title: 'Avg revenue / customer', ...avg, ...base, variant: 'financial' },
    { title: 'Churn rate', ...churn, ...base, variant: 'percent', invertDelta: true },
    { title: 'Customers', ...customers, ...base, variant: 'count' },
  ]
}
