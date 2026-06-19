import { useEffect, useState } from 'react'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { loadAgingView, saveAgingView, type AgingView } from './agingView'
import SalesAgingPayablesSection from './SalesAgingPayablesSection'
import SalesAgingReceivablesSection from './SalesAgingReceivablesSection'
import SalesAgingSubNav from './SalesAgingSubNav'

export default function SalesAgingTab({
  period,
  year,
  month,
  legalEntity,
}: {
  period: PeriodSelection
  year: number
  month: number
  legalEntity: string
}) {
  const [view, setView] = useState<AgingView>(loadAgingView)

  useEffect(() => {
    saveAgingView(view)
  }, [view])

  return (
    <div className="space-y-6">
      <SalesAgingSubNav view={view} onChange={setView} />

      {view === 'receivables' ? (
        <SalesAgingReceivablesSection
          period={period}
          year={year}
          month={month}
          legalEntity={legalEntity}
        />
      ) : (
        <SalesAgingPayablesSection period={period} year={year} month={month} legalEntity={legalEntity} />
      )}
    </div>
  )
}
