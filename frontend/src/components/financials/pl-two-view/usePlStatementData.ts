import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type FinancialStatementResponse,
  type MonthlyResponse,
  type PlLineDetailResponse,
  type PlPlanLine,
  type PlPlanResponse,
} from '../../../lib/api'

export type PlPlanMap = Record<string, PlPlanLine>

export type { PlLineDetailResponse, PlPlanLine, PlPlanResponse }

function planToMap(res: PlPlanResponse | null): PlPlanMap {
  const m: PlPlanMap = {}
  if (!res?.lines) return m
  for (const l of res.lines) m[l.line_code] = l
  return m
}

export function usePlStatementData(year: number, month: number, entity?: string) {
  const [statement, setStatement] = useState<FinancialStatementResponse | null>(null)
  const [monthly, setMonthly] = useState<MonthlyResponse | null>(null)
  const [planRes, setPlanRes] = useState<PlPlanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [pl, mon, plan] = await Promise.all([
        api.financialsPlStatement(year, month, entity),
        api.financialsPlMonthly({ year, month, entity }),
        api.financialsPlPlan(year, month, entity),
      ])
      setStatement(pl)
      setMonthly(mon)
      setPlanRes(plan)
    } catch (e: unknown) {
      setStatement(null)
      setMonthly(null)
      setPlanRes(null)
      setError(e instanceof Error ? e.message : 'Failed to load P&L')
    } finally {
      setLoading(false)
    }
  }, [year, month, entity])

  useEffect(() => {
    void load()
  }, [load])

  return {
    statement,
    monthly,
    planMap: planToMap(planRes),
    hasPlanData: planRes?.has_plan_data ?? false,
    loading,
    error,
    reload: load,
  }
}

export function fetchPlLineDetail(
  lineCode: string,
  year: number,
  month: number,
  entity?: string,
  opts?: { use_llm?: boolean; line_mom_keur?: number },
) {
  return api.financialsPlLineDetail(lineCode, year, month, entity, opts)
}
