import { api, type FinPeriodParams } from '../../../../lib/api'
import type { FinancialStatementResponse } from '../../../../lib/api'
import type { PeriodSelection } from '../../../../lib/periodSelection'
import type { FinStatementKind } from '../statementTypes'

function finPeriodFromSelection(p: PeriodSelection, entity?: string): FinPeriodParams {
  if (p.grain === 'week') {
    return { period_grain: 'week', iso_year: p.isoYear, iso_week: p.isoWeek, entity }
  }
  if (p.grain === 'year') {
    return { period_grain: 'year', year: p.year, month: p.month, entity }
  }
  return { year: p.year, month: p.month, entity }
}

export async function fetchStatementForEntity(
  statement: FinStatementKind,
  period: PeriodSelection,
  entityCode: string,
): Promise<FinancialStatementResponse> {
  const fp = finPeriodFromSelection(period, entityCode)
  switch (statement) {
    case 'bs':
      return api.financialsBalanceSheetPeriod(fp)
    case 'cf':
      return api.financialsCashFlowPeriod(fp)
    case 'wc':
      return api.financialsWorkingCapitalPeriod(fp)
    case 'pl':
      return api.financialsPlStatementPeriod(fp)
    default:
      throw new Error(`Consolidation entity statement not supported: ${statement}`)
  }
}

export async function fetchAnnualSnapshotForEntity(
  statement: 'bs' | 'wc',
  year: number,
  month: number,
  entityCode: string,
): Promise<import('../../../../lib/api').ErSnapshotResponse> {
  if (statement === 'bs') return api.exitReadinessBalanceSheet(year, month, entityCode)
  return api.exitReadinessWorkingCapital(year, month, entityCode)
}

export async function fetchConsolidatedStatement(
  statement: Exclude<FinStatementKind, 'pl'>,
  period: PeriodSelection,
): Promise<FinancialStatementResponse> {
  const fp = finPeriodFromSelection(period)
  switch (statement) {
    case 'bs':
      return api.financialsBalanceSheetPeriod(fp)
    case 'cf':
      return api.financialsCashFlowPeriod(fp)
    case 'wc':
      return api.financialsWorkingCapitalPeriod(fp)
    default:
      throw new Error(`Consolidated statement not supported: ${statement}`)
  }
}
