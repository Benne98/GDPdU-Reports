import { getStatementConfig } from './statementConfig'
import type { FinStatementKind } from './statementTypes'
import { FIN_REPORT_SPLIT_GRID } from './finReportLayout'
import StatementSectionHeading from './StatementSectionHeading'

const PHASE_LABEL: Record<FinStatementKind, string> = {
  pl: '',
  bs: 'Phase 1 — Balance sheet',
  cf: 'Phase 3 — Cash flow',
  wc: 'Phase 2 — Working capital',
}

type Props = {
  statement: FinStatementKind
}

export default function StatementReportPlaceholder({ statement }: Props) {
  const cfg = getStatementConfig(statement)
  const phase = PHASE_LABEL[statement]

  return (
    <div className="px-4 pt-6 pb-6">
      <div className={FIN_REPORT_SPLIT_GRID}>
        <div className="min-w-0">
          <StatementSectionHeading>{cfg.cardTitle}</StatementSectionHeading>
          <p className="text-xs text-slate-500 mt-2">
            Summary table and key-driver bullets will appear here. Use Table View for the full statement.
          </p>
        </div>
        <div className="min-w-0">
          <StatementSectionHeading>Key drivers</StatementSectionHeading>
          <p className="text-xs leading-relaxed text-slate-500 mt-2">
            {phase
              ? `${phase}: narrative will use statement-specific checks (e.g. debtor/creditor aging, inventory, CAPEX for balance sheet and working capital).`
              : 'Loading narrative…'}
          </p>
        </div>
      </div>
    </div>
  )
}
