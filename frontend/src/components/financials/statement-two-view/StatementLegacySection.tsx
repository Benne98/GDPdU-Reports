import { useEffect, useState } from 'react'
import type { FinancialStatementResponse, MonthlyResponse } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import FinancialStatementTable from '../FinancialStatementTable'
import StatementReportPlaceholder from './StatementReportPlaceholder'
import StatementSectionShell from './StatementSectionShell'
import type { FinStatementKind } from './statementTypes'
import { loadStatementViewMode, saveStatementViewMode } from './statementViewMode'
import type { StatementViewMode } from './statementTypes'

type Props = {
  statement: Exclude<FinStatementKind, 'pl'>
  data: FinancialStatementResponse | null
  loading: boolean
  error: string | null
  year: number
  month: number
  onDrill: (d: FinancialsDrillOpen) => void
  /** Reserved for Phase 1+ column editor */
  monthly?: MonthlyResponse | null
}

export default function StatementLegacySection({
  statement,
  data,
  loading,
  error,
  year,
  month,
  onDrill,
}: Props) {
  const [viewMode, setViewMode] = useState<StatementViewMode>(() => loadStatementViewMode(statement))

  useEffect(() => {
    saveStatementViewMode(statement, viewMode)
  }, [statement, viewMode])

  if (loading && !data) {
    return (
      <div
        className="rounded-xl p-8 text-center text-sm"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}
      >
        Loading…
      </div>
    )
  }

  if (error && !data) {
    return (
      <div
        className="rounded-xl p-8 text-center text-sm"
        style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}
      >
        {error}
      </div>
    )
  }

  if (!data?.rows.length) {
    return (
      <div
        className="rounded-xl p-8 text-center text-sm"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}
      >
        No rows for this period / entity. Check demo data (setup_db) and that the period exists in the database.
      </div>
    )
  }

  const periodBadge = data.col_labels?.cm ? `${data.col_labels.cm}A` : ''

  return (
    <StatementSectionShell
      statement={statement}
      viewMode={viewMode}
      onViewModeChange={setViewMode}
      periodBadge={periodBadge}
      loading={loading}
    >
      {viewMode === 'report' ? (
        <StatementReportPlaceholder statement={statement} />
      ) : (
        <FinancialStatementTable
          embedded
          data={data}
          loading={false}
          error={null}
          year={year}
          month={month}
          onDrill={onDrill}
        />
      )}
    </StatementSectionShell>
  )
}
