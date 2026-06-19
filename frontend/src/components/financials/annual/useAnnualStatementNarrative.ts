import { useEffect, useMemo, useState } from 'react'
import { api, type FinPeriodParams, type PlNarrativeResponse } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import { isTrustedAnnualNarrative, isTrustedApiNarrative } from '../pl-two-view/plNarrativeEngine'
import { isTrustedApiBsNarrative } from '../statement-two-view/bs/bsNarrativeEngine'
import { isTrustedApiCfNarrative } from '../statement-two-view/cf/cfNarrativeEngine'
import { isTrustedApiWcNarrative } from '../statement-two-view/wc/wcNarrativeEngine'

const BULLET_CAP = 5

function annualPeriodParams(
  year: number,
  month: number,
  entity?: string,
  periodSelection?: PeriodSelection,
): FinPeriodParams {
  if (periodSelection?.grain === 'week') {
    return {
      period_grain: 'week',
      iso_year: periodSelection.isoYear,
      iso_week: periodSelection.isoWeek,
      entity,
    }
  }
  if (periodSelection?.grain === 'year') {
    return { period_grain: 'year', year: periodSelection.year, month: periodSelection.month, entity }
  }
  return { year, month, entity }
}

function isTrusted(statement: FinStatementKind, n: PlNarrativeResponse | null, entity?: string): boolean {
  if (isTrustedAnnualNarrative(n, entity)) return true
  if (!n) return false
  if (statement === 'pl') return isTrustedApiNarrative(n, entity)
  if (statement === 'bs') return isTrustedApiBsNarrative(n, entity)
  if (statement === 'cf') return isTrustedApiCfNarrative(n, entity)
  return isTrustedApiWcNarrative(n, entity)
}

async function fetchNarrative(
  statement: FinStatementKind,
  period: FinPeriodParams,
): Promise<PlNarrativeResponse> {
  const opts = { visible_rows: 24, max_bullets: BULLET_CAP, use_llm: false }
  if (statement === 'pl') return api.financialsPlNarrativePeriod(period, opts)
  if (statement === 'bs') return api.financialsBsNarrativePeriod(period, opts)
  if (statement === 'cf') return api.financialsCfNarrativePeriod(period, opts)
  return api.financialsWcNarrativePeriod(period, opts)
}

export function useAnnualStatementNarrative(
  statement: FinStatementKind,
  year: number,
  month: number,
  clientNarrative: PlNarrativeResponse | null,
  entity?: string,
  periodSelection?: PeriodSelection,
) {
  const narrativePeriod = useMemo(
    () => annualPeriodParams(year, month, entity, periodSelection),
    [year, month, entity, periodSelection],
  )

  const [apiNarrative, setApiNarrative] = useState<PlNarrativeResponse | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setApiNarrative(null)
    void fetchNarrative(statement, narrativePeriod)
      .then(res => {
        if (!cancelled) {
          setApiNarrative(res)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setApiNarrative(null)
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [statement, narrativePeriod])

  const narrative = useMemo(() => {
    if (statement === 'cf') {
      const trusted = isTrusted(statement, apiNarrative, entity)
      const apiCount = apiNarrative?.bullets?.length ?? 0
      const clientCount = clientNarrative?.bullets?.length ?? 0
      if (trusted && apiCount >= 3) return apiNarrative!
      if (clientCount >= 3) return clientNarrative!
      if (trusted && apiNarrative) return apiNarrative
      if (clientNarrative?.bullets?.length || clientNarrative?.intro) return clientNarrative
      return apiNarrative ?? clientNarrative
    }
    if (isTrusted(statement, apiNarrative, entity)) return apiNarrative!
    if (clientNarrative?.bullets?.length || clientNarrative?.intro) return clientNarrative
    return apiNarrative ?? clientNarrative
  }, [statement, apiNarrative, clientNarrative, entity])

  const narrativeBusy = loading && !isTrusted(statement, apiNarrative, entity)

  return { narrative, loading, narrativeBusy, apiNarrative }
}
