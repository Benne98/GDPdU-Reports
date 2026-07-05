import { useEffect, useState } from 'react'
import { api, type FinancialsOverviewResponse, type FinPeriodParams } from '../../../lib/api'
import type { PeriodGrain } from '../../../lib/periodSelection'
import { computeCccFromSeries } from './overviewBriefingUtils'

export type OverviewBriefingState = {
  data: FinancialsOverviewResponse | null
  loading: boolean
  highlightsLoading: boolean
  error: string | null
  ccc: { value: number; deltaPrior: number | null } | null
}

export function useOverviewBriefing(
  periodParams: FinPeriodParams,
  resetKey: string,
  anchorYear: number,
  anchorMonth: number,
  entity: string | undefined,
  _grain: PeriodGrain,
): OverviewBriefingState {
  const [data, setData] = useState<FinancialsOverviewResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [highlightsLoading, setHighlightsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ccc, setCcc] = useState<{ value: number; deltaPrior: number | null } | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setHighlightsLoading(true)
    setError(null)
    setCcc(null)

    void api
      .financialsOverview(periodParams)
      .then(res => {
        if (!cancelled) {
          setData(res)
          setError(null)
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setData(null)
        setError(e instanceof Error ? e.message : 'Failed to load overview briefing')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    void api
      .financialsOverviewHighlights(periodParams)
      .then(({ highlights }) => {
        if (!cancelled) setData(prev => (prev ? { ...prev, highlights } : prev))
      })
      .catch(() => {
        /* optional */
      })
      .finally(() => {
        if (!cancelled) setHighlightsLoading(false)
      })

    const wcGrain = 'month'
    void api
      .wcRatios(anchorYear, anchorMonth, wcGrain, entity)
      .then(res => {
        if (cancelled) return
        setCcc(computeCccFromSeries(res.data.current_series ?? []))
      })
      .catch(() => {
        if (!cancelled) setCcc(null)
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  return { data, loading, highlightsLoading, error, ccc }
}
