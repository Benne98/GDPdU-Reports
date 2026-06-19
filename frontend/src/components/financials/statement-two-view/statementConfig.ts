import type { FinStatementKind } from './statementTypes'

export type StatementFeatureFlags = {
  /** Report view + narrative API (Phase 1+ per statement) */
  narrativeApi: boolean
  /** Table column editor (Stift) */
  columnEditor: boolean
  /** Plan columns / plan map */
  planData: boolean
  /** Bullet detail overlay */
  lineDetailOverlay: boolean
  /** Monthly cell detail panel */
  monthlyCellDetail: boolean
}

export type StatementConfig = {
  kind: FinStatementKind
  viewModeStorageKey: string
  cardTitle: string
  pinTableLabel: string
  reportSubtitle: string
  tableSubtitle: string
  narrativePath: string
  lineDetailPath: string
  statementApiPath: string
  features: StatementFeatureFlags
}

const BASE = '/api/v1/financials'

export const STATEMENT_CONFIG: Record<FinStatementKind, StatementConfig> = {
  pl: {
    kind: 'pl',
    viewModeStorageKey: 'finssentials.pl.viewMode.v1',
    cardTitle: 'Income statement (consolidated)',
    pinTableLabel: 'P&L statement',
    reportSubtitle: 'Analytical narrative with summary table — click a bullet for detail',
    tableSubtitle: 'Full-width table — click values to drill into GL lines',
    narrativePath: `${BASE}/pl-statement/narrative`,
    lineDetailPath: `${BASE}/pl-line-detail`,
    statementApiPath: `${BASE}/pl-statement`,
    features: {
      narrativeApi: true,
      columnEditor: true,
      planData: true,
      lineDetailOverlay: true,
      monthlyCellDetail: true,
    },
  },
  bs: {
    kind: 'bs',
    viewModeStorageKey: 'finssentials.bs.viewMode.v1',
    cardTitle: 'Balance sheet (consolidated)',
    pinTableLabel: 'Balance sheet',
    reportSubtitle: 'Key drivers with summary table — click a bullet for detail',
    tableSubtitle: 'Full-width table — click values to drill into GL lines',
    narrativePath: `${BASE}/balance-sheet/narrative`,
    lineDetailPath: `${BASE}/balance-sheet/line-detail`,
    statementApiPath: `${BASE}/balance-sheet`,
    features: {
      narrativeApi: true,
      columnEditor: true,
      planData: true,
      lineDetailOverlay: true,
      monthlyCellDetail: true,
    },
  },
  cf: {
    kind: 'cf',
    viewModeStorageKey: 'finssentials.cf.viewMode.v1',
    cardTitle: 'Cash flow statement (consolidated)',
    pinTableLabel: 'Cash flow',
    reportSubtitle: 'Key drivers with summary table — click a bullet for detail',
    tableSubtitle: 'Full-width table — click values to drill into bookings',
    narrativePath: `${BASE}/cash-flow/narrative`,
    lineDetailPath: `${BASE}/cash-flow/line-detail`,
    statementApiPath: `${BASE}/cash-flow`,
    features: {
      narrativeApi: true,
      columnEditor: false,
      planData: true,
      lineDetailOverlay: true,
      monthlyCellDetail: true,
    },
  },
  wc: {
    kind: 'wc',
    viewModeStorageKey: 'finssentials.wc.viewMode.v1',
    cardTitle: 'Working capital (consolidated)',
    pinTableLabel: 'Working capital',
    reportSubtitle: 'Key drivers with summary table — click a bullet for detail',
    tableSubtitle: 'Full-width table — click values to drill into GL lines',
    narrativePath: `${BASE}/working-capital/narrative`,
    lineDetailPath: `${BASE}/working-capital/line-detail`,
    statementApiPath: `${BASE}/working-capital`,
    features: {
      narrativeApi: true,
      columnEditor: true,
      planData: true,
      lineDetailOverlay: true,
      monthlyCellDetail: true,
    },
  },
}

export function getStatementConfig(kind: FinStatementKind): StatementConfig {
  return STATEMENT_CONFIG[kind]
}
