import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type SalesTopEntitiesColLabels,
  type SalesTopEntity,
  type SalesFilters,
} from '../../lib/api'
import type { PeriodSelection } from '../../lib/periodSelection'
import { periodAnchorYearMonth } from '../../lib/periodSelection'
import SalesTopEntitiesSection from '../sales/analytics/SalesTopEntitiesSection'
import { buildTopEntityReportInsights } from './topEntitiesReportInsights'
import { useChartLoadReporter } from '../../hooks/useChartLoadReporter'

interface TopCustomerTableProps {
  period: PeriodSelection
  entity?: string
}

const DEFAULT_COL_LABELS: SalesTopEntitiesColLabels = {
  cm: 'Current month',
  pm: 'Prior month',
  py_cm: 'Prior year',
  ytd: 'YTD',
}

export default function TopCustomerTable({ period, entity }: TopCustomerTableProps) {
  const [rows, setRows] = useState<SalesTopEntity[]>([])
  const [colLabels, setColLabels] = useState<SalesTopEntitiesColLabels>(DEFAULT_COL_LABELS)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [intro, setIntro] = useState('')
  const [bullets, setBullets] = useState<string[]>([])

  const ent = entity === 'all' ? undefined : entity
  const filters = useMemo<SalesFilters | undefined>(
    () => (ent ? { entity: [ent] } : undefined),
    [ent],
  )
  const anchor = periodAnchorYearMonth(period)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    const opts =
      period.grain === 'week'
        ? { period_grain: 'week' as const, iso_year: period.isoYear, iso_week: period.isoWeek, limit: 5000 }
        : { period_grain: 'month' as const, limit: 5000 }

    api
      .salesTopEntities(anchor.year, anchor.month, 'customer', 'cm', 'invoiced', filters, opts)
      .then(res => {
        if (cancelled) return
        const topRows = res.rows ?? []
        setRows(topRows)
        setColLabels(res.col_labels ?? DEFAULT_COL_LABELS)

        const insights = buildTopEntityReportInsights({
          mode: 'customer',
          rows: topRows,
          churn: null,
          pvm: null,
          geo: null,
          opportunities: null,
          aging: null,
        })
        setIntro(insights.intro)
        setBullets(insights.bullets)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setRows([])
        setError(e instanceof Error ? e.message : 'Failed to load top customers')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [anchor.year, anchor.month, period, ent, filters])

  const timedOut = useChartLoadReporter('cockpit-top-customers', loading, error)
  if (timedOut) return null

  return (
    <SalesTopEntitiesSection
      title="Top Customer Analysis"
      subtitle="kEUR invoiced — tiered by cumulative gross sales"
      tableId="cockpit-top-customers"
      rows={rows}
      colLabels={colLabels}
      loading={loading}
      bullets={bullets.length ? bullets : ['No analysis available for this period.']}
      reportIntro={intro}
      exportName="Top_Customers"
      extended
      finTableStyle
      stripLegalNames
      entityKindLabel="customers"
      periodGrain={period.grain === 'week' ? 'week' : 'month'}
    />
  )
}
