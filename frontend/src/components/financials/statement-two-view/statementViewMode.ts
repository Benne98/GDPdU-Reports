import { getStatementConfig } from './statementConfig'
import type { FinStatementKind } from './statementTypes'
import type { StatementViewMode } from './statementTypes'

export function loadStatementViewMode(kind: FinStatementKind): StatementViewMode {
  const cfg = getStatementConfig(kind)
  if (!cfg.features.narrativeApi) return 'table'
  try {
    const v = localStorage.getItem(cfg.viewModeStorageKey)
    return v === 'table' ? 'table' : 'report'
  } catch {
    return 'report'
  }
}

export function viewModeFromQuery(value: string | null): StatementViewMode | null {
  if (value === 'table' || value === 'report') return value
  return null
}

/** Apply ?view=table|report from URL once (e.g. Action Notes pin navigation). */
export function applyViewModeFromSearchParams(setMode: (m: StatementViewMode) => void): void {
  try {
    const v = new URLSearchParams(window.location.search).get('view')
    const mode = viewModeFromQuery(v)
    if (mode) setMode(mode)
  } catch {
    /* ignore */
  }
}

export function saveStatementViewMode(kind: FinStatementKind, mode: StatementViewMode): void {
  try {
    localStorage.setItem(getStatementConfig(kind).viewModeStorageKey, mode)
  } catch {
    /* ignore */
  }
}
