import { api, finPeriodParamsFromStatement, type FinPeriodParams, type FinancialStatementResponse, type PlLineDetailResponse, type PlNarrativeResponse } from '../../../lib/api'
import { getStatementConfig } from './statementConfig'
import type { FinStatementKind } from './statementTypes'

export type StatementNarrativeFetchOpts = {
  max_bullets?: number
  visible_rows?: number
  use_llm?: boolean
  force_refresh?: boolean
}

export type StatementLineDetailFetchOpts = {
  use_llm?: boolean
  line_mom_keur?: number
  anchor_year?: number
  anchor_month?: number
}

/** Narrative for the active statement and period (month or ISO week). */
export async function fetchStatementNarrative(
  statement: FinStatementKind,
  period: FinPeriodParams,
  opts?: StatementNarrativeFetchOpts,
): Promise<PlNarrativeResponse> {
  switch (statement) {
    case 'pl':
      return api.financialsPlNarrativePeriod(period, opts)
    case 'bs':
      return api.financialsBsNarrativePeriod(period, opts)
    case 'cf':
      return api.financialsCfNarrativePeriod(period, opts)
    case 'wc':
      return api.financialsWcNarrativePeriod(period, opts)
    default:
      throw new Error(`Unknown statement: ${statement satisfies never}`)
  }
}

/** Derive narrative period params from a loaded statement response. */
export function narrativePeriodFromStatement(
  data: FinancialStatementResponse,
  entity?: string,
): FinPeriodParams {
  return finPeriodParamsFromStatement(data, entity)
}

/** Line detail for bullet / monthly cell overlay. */
export async function fetchStatementLineDetail(
  statement: FinStatementKind,
  lineCode: string,
  year: number,
  month: number,
  entity?: string,
  opts?: StatementLineDetailFetchOpts,
): Promise<PlLineDetailResponse> {
  switch (statement) {
    case 'pl':
      return api.financialsPlLineDetail(lineCode, year, month, entity, opts)
    case 'bs':
      return api.financialsBsLineDetail(lineCode, year, month, entity, opts)
    case 'cf':
      return api.financialsCfLineDetail(lineCode, year, month, entity, opts)
    case 'wc':
      return api.financialsWcLineDetail(lineCode, year, month, entity, opts)
    default:
      throw new Error(`Unknown statement: ${statement satisfies never}`)
  }
}

export function statementNarrativePath(kind: FinStatementKind): string {
  return getStatementConfig(kind).narrativePath
}

export function statementLineDetailPath(kind: FinStatementKind): string {
  return getStatementConfig(kind).lineDetailPath
}
