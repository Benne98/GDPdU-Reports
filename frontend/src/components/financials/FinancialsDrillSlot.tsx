import { useEffect, useRef } from 'react'
import DrillDownTable from '../cockpit/DrillDownTable'
import type { FinancialsDrillAnchor, FinancialsDrillOpen } from './FinancialStatementTable'

type Props = {
  anchor: FinancialsDrillAnchor
  drill: FinancialsDrillOpen | null
  entity?: string
  onClose: () => void
}

/** Renders GL drill-down directly below the section that opened it. */
export default function FinancialsDrillSlot({ anchor, drill, entity, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (drill?.anchor === anchor) {
      ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [drill, anchor])

  if (!drill || drill.anchor !== anchor) return null

  return (
    <div ref={ref} className="mt-4">
      <DrillDownTable
        key={`${drill.dateFrom}-${drill.dateTo}-${drill.title}-${drill.entityOverride ?? ''}`}
        entity={drill.entityOverride ?? entity}
        dateFrom={drill.dateFrom}
        dateTo={drill.dateTo}
        level2={drill.level2}
        level3={drill.level3}
        level4={drill.level4}
        glAccountId={drill.glAccountId}
        statementType={drill.statementType}
        title={drill.title}
        onClose={onClose}
      />
    </div>
  )
}
