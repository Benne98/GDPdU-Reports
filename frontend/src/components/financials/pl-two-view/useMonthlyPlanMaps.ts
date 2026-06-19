import { useEffect, useState } from 'react'
import { api } from '../../../lib/api'
import type { PlPlanMap } from './usePlStatementData'

function planMapFromResponse(lines: { line_code: string; plan_cm: number }[]): PlPlanMap {
  const m: PlPlanMap = {}
  for (const l of lines) {
    m[l.line_code] = {
      line_code: l.line_code,
      plan_cm: l.plan_cm,
      plan_vs_actual: 0,
      ytd_plan: 0,
      ytg: 0,
      coverage_pct: null,
    }
  }
  return m
}

/** Load plan_cm per period key for aggregate vs plan columns. */
export function useMonthlyPlanMaps(
  periodKeys: string[],
  entity: string | undefined,
  enabled: boolean,
): Map<string, PlPlanMap> {
  const [planByPeriod, setPlanByPeriod] = useState<Map<string, PlPlanMap>>(() => new Map())
  const keysSig = periodKeys.join(',')

  useEffect(() => {
    if (!enabled || periodKeys.length === 0) {
      setPlanByPeriod(new Map())
      return
    }
    const unique = [...new Set(periodKeys)]
    let cancelled = false
    void (async () => {
      const entries = await Promise.all(
        unique.map(async key => {
          const [ys, ms] = key.split('-')
          const y = parseInt(ys, 10)
          const m = parseInt(ms, 10)
          try {
            const res = await api.financialsPlPlan(y, m, entity)
            return [key, planMapFromResponse(res.lines)] as const
          } catch {
            return [key, {}] as const
          }
        }),
      )
      if (!cancelled) setPlanByPeriod(new Map(entries))
    })()
    return () => {
      cancelled = true
    }
  }, [keysSig, entity, enabled])

  return planByPeriod
}
