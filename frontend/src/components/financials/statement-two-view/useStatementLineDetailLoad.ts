import { useEffect, useMemo, useState } from 'react'
import type { PlLineDetailResponse } from '../../../lib/api'
import { fetchStatementLineDetail } from './statementApi'
import type { FinStatementKind } from './statementTypes'
import { buildExpertContext, lineTotalsFromAccounts } from '../pl-two-view/plDetailExpert'
import {
  accountDeltaMap,
  coalesceAccountTimeline,
  sortByAbsDelta,
  timelineEmptyMessage,
  timelineEmptyReason,
} from '../pl-two-view/plDetailTimeline'
import { periodLabel as formatPeriodLabel, priorPeriod } from '../pl-two-view/plPeriodLabels'

export type StatementLineDetailLoadParams = {
  statement: FinStatementKind
  lineCode: string
  label: string
  year: number
  month: number
  anchorYear?: number
  anchorMonth?: number
  entity?: string
  lineMomKeur?: number
}

export function useStatementLineDetailLoad({
  statement,
  lineCode,
  label,
  year,
  month,
  anchorYear,
  anchorMonth,
  entity,
  lineMomKeur,
}: StatementLineDetailLoadParams) {
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
    void fetchStatementLineDetail(statement, lineCode, year, month, entity, {
      use_llm: true,
      ...(lineMomKeur != null ? { line_mom_keur: lineMomKeur } : {}),
      ...(anchorYear != null && anchorMonth != null
        ? { anchor_year: anchorYear, anchor_month: anchorMonth }
        : {}),
    })
      .then(res => {
        if (!cancelled) {
          setDetail(res)
          setLoading(false)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : 'Request failed'
          setFetchError(msg)
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [statement, lineCode, year, month, anchorYear, anchorMonth, entity, lineMomKeur])

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
