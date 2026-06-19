import type { ReactNode } from 'react'
import { getStatementConfig } from './statementConfig'
import type { FinStatementKind } from './statementTypes'
import type { StatementViewMode } from './statementTypes'
import StatementViewToggleButton from './StatementViewToggleButton'

type Props = {
  statement: FinStatementKind
  viewMode: StatementViewMode
  onViewModeChange: (mode: StatementViewMode) => void
  periodBadge?: string
  loading?: boolean
  toolbarEnd?: ReactNode
  children: ReactNode
}

export default function StatementSectionShell({
  statement,
  viewMode,
  onViewModeChange,
  periodBadge,
  loading,
  toolbarEnd,
  children,
}: Props) {
  const cfg = getStatementConfig(statement)
  const reportAvailable = cfg.features.narrativeApi

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div
        className="px-4 pt-4 pb-3 flex items-start justify-between gap-3"
        style={{ borderBottom: '1px solid #F1F5F9' }}
      >
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold" style={{ color: '#111827' }}>
              {cfg.cardTitle}
            </span>
            {periodBadge ? (
              <span
                className="text-xs font-medium px-2 py-0.5 rounded-md"
                style={{
                  background: 'rgba(30,58,95,0.08)',
                  color: '#1E3A5F',
                  border: '1px solid rgba(30,58,95,0.15)',
                }}
              >
                {periodBadge}
              </span>
            ) : null}
          </div>
          <p className="text-xs" style={{ color: '#94A3B8' }}>
            {viewMode === 'report' ? cfg.reportSubtitle : cfg.tableSubtitle}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <StatementViewToggleButton
            mode={viewMode}
            onChange={onViewModeChange}
            disabled={loading}
            reportDisabled={!reportAvailable}
          />
          {toolbarEnd}
        </div>
      </div>
      {children}
    </div>
  )
}
