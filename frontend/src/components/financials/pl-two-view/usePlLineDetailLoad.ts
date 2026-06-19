import { useEffect, useMemo, useState } from 'react'
import type { PlLineDetailResponse } from '../../../lib/api'
import { fetchPlLineDetail } from './usePlStatementData'
import { buildExpertContext, lineTotalsFromAccounts } from './plDetailExpert'
import {
  accountDeltaMap,
  coalesceAccountTimeline,
  sortByAbsDelta,
  timelineEmptyMessage,
  timelineEmptyReason,
} from './plDetailTimeline'
import { periodLabel as formatPeriodLabel, priorPeriod } from './plPeriodLabels'

export type PlLineDetailLoadParams = {
  lineCode: string
  label: string
  year: number
  month: number
  entity?: string
  lineMomKeur?: number
}

export function usePlLineDetailLoad({
  lineCode,
  label,
  year,
  month,
  entity,
  lineMomKeur,
}: PlLineDetailLoadParams) {
  const [detail, setDetail] = useState<PlLineDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)

  const currentPeriodLabel = formatPeriodLabel(year, month)
  const pm = priorPeriod(year, month)
  const priorPeriodLabel = formatPeriodLabel(pm.year, pm.month)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFetchError(null)
    setDetail(null)
    void fetchPlLineDetail(lineCode, year, month, entity, {
      use_llm: true,
      ...(lineMomKeur != null ? { line_mom_keur: lineMomKeur } : {}),
    })
      .then(res => {
        if (!cancelled) {
          setDetail(res)
          setLoading(false)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setFetchError(e instanceof Error ? e.message : 'Failed to load detail')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [lineCode, year, month, entity, lineMomKeur])

  const accountTimeline = useMemo(() => coalesceAccountTimeline(detail), [detail])
  const emptyReason = timelineEmptyReason(detail, loading, Boolean(fetchError))
  const deltaByAccount = useMemo(() => accountDeltaMap(detail?.accounts), [detail?.accounts])
  const accountsByDelta = useMemo(
    () => sortByAbsDelta(detail?.accounts ?? [], deltaByAccount),
    [detail?.accounts, deltaByAccount],
  )
  const timelineByDelta = useMemo(
    () => sortByAbsDelta(accountTimeline, deltaByAccount),
    [accountTimeline, deltaByAccount],
  )
  const expertContext = useMemo(
    () => buildExpertContext(label, currentPeriodLabel, detail),
    [label, currentPeriodLabel, detail],
  )
  const lineTotals = useMemo(() => lineTotalsFromAccounts(detail), [detail])

  return {
    detail,
    loading,
    fetchError,
    currentPeriodLabel,
    priorPeriodLabel,
    emptyReason,
    deltaByAccount,
    accountsByDelta,
    timelineByDelta,
    expertContext,
    lineTotals,
    timelineEmptyMessage,
  }
}
