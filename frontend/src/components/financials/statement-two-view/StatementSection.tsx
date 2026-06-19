import type { FinancialStatementResponse, MonthlyResponse } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import BsStatementSection from './bs/BsStatementSection'
import CfStatementSection from './cf/CfStatementSection'
import WcStatementSection from './wc/WcStatementSection'
import PlStatementSection from '../pl-two-view/PlStatementSection'
import type { FinStatementKind } from './statementTypes'

export type StatementSectionProps = {
  statement: FinStatementKind
  data: FinancialStatementResponse | null
  monthly: MonthlyResponse | null
  loading: boolean
  error: string | null
  year: number
  month: number
  entity?: string
  entityLabel?: string
  entityDisplayName?: string
  periodSelection?: PeriodSelection
  onDrill: (d: FinancialsDrillOpen) => void
}

/**
 * Unified entry for financial statement two-view (Report + Table).
 * P&L, BS, CF, and WC use dedicated two-view sections.
 */
export default function StatementSection({
  statement,
  data,
  monthly,
  loading,
  error,
  year,
  month,
  entity,
  entityLabel,
  entityDisplayName,
  periodSelection,
  onDrill,
}: StatementSectionProps) {
  if (statement === 'bs') {
    return (
      <BsStatementSection
        data={data}
        monthly={monthly}
        loading={loading}
        error={error}
        year={year}
        month={month}
        entity={entity}
        entityLabel={entityLabel}
        entityDisplayName={entityDisplayName}
        periodSelection={periodSelection}
        onDrill={onDrill}
      />
    )
  }

  if (statement === 'wc') {
    return (
      <WcStatementSection
        data={data}
        monthly={monthly}
        loading={loading}
        error={error}
        year={year}
        month={month}
        entity={entity}
        entityLabel={entityLabel}
        entityDisplayName={entityDisplayName}
        periodSelection={periodSelection}
        onDrill={onDrill}
      />
    )
  }

  if (statement === 'cf') {
    return (
      <CfStatementSection
        data={data}
        monthly={monthly}
        loading={loading}
        error={error}
        year={year}
        month={month}
        entity={entity}
        entityLabel={entityLabel}
        entityDisplayName={entityDisplayName}
        periodSelection={periodSelection}
        onDrill={onDrill}
      />
    )
  }

  if (statement === 'pl') {
    return (
      <PlStatementSection
        data={data}
        monthly={monthly}
        loading={loading}
        error={error}
        year={year}
        month={month}
        entity={entity}
        entityLabel={entityLabel}
        entityDisplayName={entityDisplayName}
        periodSelection={periodSelection}
        onDrill={onDrill}
      />
    )
  }

  return null
}
