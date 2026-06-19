import { useMemo } from 'react'
import type { OperationalKpiMetric } from '../../../../lib/api'
import OperationalKpiGrid, { KpiDef } from '../../operational/OperationalKpiGrid'

export default function AgingKpiGrid({
  defs,
  metrics,
}: {
  defs: KpiDef[]
  metrics?: Record<string, OperationalKpiMetric>
}) {
  const items = useMemo(
    () => defs.map(def => ({ ...def, metric: metrics?.[def.key] ?? null })),
    [defs, metrics],
  )
  return <OperationalKpiGrid items={items} />
}
