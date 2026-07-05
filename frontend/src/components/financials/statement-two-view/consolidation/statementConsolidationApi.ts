import { api, type FinPeriodParams, type PlPlanResponse } from '../../../../lib/api'
import type { FinancialStatementResponse } from '../../../../lib/api'
import { periodAnchorYearMonth, type PeriodSelection } from '../../../../lib/periodSelection'
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

function planAnchorFromSelection(p: PeriodSelection): { year: number; month: number } {
  if (p.grain === 'month') return { year: p.year, month: p.month }
  return periodAnchorYearMonth(p)
}

async function fetchPlanForStatement(
  statement: FinStatementKind,
  year: number,
  month: number,
  entity?: string,
): Promise<PlPlanResponse | null> {
  try {
    switch (statement) {
      case 'pl':
        return await api.financialsPlPlan(year, month, entity)
      case 'bs':
        return await api.financialsBsPlan(year, month, entity)
      case 'wc':
        return await api.financialsWcPlan(year, month, entity)
      case 'cf':
        return await api.financialsCfPlan(year, month, entity)
      default:
        return null
    }
  } catch {
    return null
  }
}

async function fetchStatementWithPlan(
  statement: FinStatementKind,
  period: PeriodSelection,
  entity?: string,
): Promise<FinancialStatementResponse> {
  const fp = finPeriodFromSelection(period, entity)
  const { year, month } = planAnchorFromSelection(period)
  const [stmt, plan] = await Promise.all([
    fetchStatementBody(statement, fp),
    fetchPlanForStatement(statement, year, month, entity),
  ])
  return plan ? { ...stmt, plan } : stmt
}

async function fetchStatementBody(
  statement: FinStatementKind,
  fp: FinPeriodParams,
): Promise<FinancialStatementResponse> {
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

export async function fetchStatementForEntity(
  statement: FinStatementKind,
  period: PeriodSelection,
  entityCode: string,
): Promise<FinancialStatementResponse> {
  return fetchStatementWithPlan(statement, period, entityCode)
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
  return fetchStatementWithPlan(statement, period)
}
