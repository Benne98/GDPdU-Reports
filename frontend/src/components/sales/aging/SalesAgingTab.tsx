import { useEffect, useState } from 'react'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { loadAgingView, saveAgingView, type AgingView } from './agingView'
import SalesAgingPayablesSection from './SalesAgingPayablesSection'
import SalesAgingReceivablesSection from './SalesAgingReceivablesSection'
import SalesAgingSubNav from './SalesAgingSubNav'
import StichtagSwitch, { STICHTAG_DEFAULT } from './StichtagSwitch'

export default function SalesAgingTab({
  period,
  legalEntity,
}: {
  period: PeriodSelection
  year: number
  month: number
  legalEntity: string
}) {
  const [view, setView] = useState<AgingView>(loadAgingView)
  // Stichtag (as-of date) overrides the page-level year/month for AR/AP charts.
  // Default: Jul 2025 (YTD) — the latest available snapshot.
  const [stichtagYear, setStichtagYear] = useState(STICHTAG_DEFAULT.year)
  const [stichtagMonth, setStichtagMonth] = useState(STICHTAG_DEFAULT.month)

  useEffect(() => {
    saveAgingView(view)
  }, [view])

  function handleStichtagChange(y: number, m: number) {
    setStichtagYear(y)
    setStichtagMonth(m)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <SalesAgingSubNav view={view} onChange={setView} />
        </div>
        <div className="shrink-0">
          <StichtagSwitch
            year={stichtagYear}
            month={stichtagMonth}
            onChange={handleStichtagChange}
          />
        </div>
      </div>

      {view === 'receivables' ? (
        <SalesAgingReceivablesSection
          period={period}
          year={stichtagYear}
          month={stichtagMonth}
          legalEntity={legalEntity}
        />
      ) : (
        <SalesAgingPayablesSection
          period={period}
          year={stichtagYear}
          month={stichtagMonth}
          legalEntity={legalEntity}
        />
      )}
    </div>
  )
}
