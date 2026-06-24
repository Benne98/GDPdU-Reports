/**
 * ChatSummaryBar — compact horizontal bar summarising the BudgetDraft
 * configuration after the chat flow is complete.
 */

import type { BudgetDraft } from '../../../lib/budgetChatFlow';
import { formatEntities, formatFiscalYears } from '../../../lib/budgetChatFlow';

interface ChatSummaryBarProps {
  draft: BudgetDraft;
  entityOptions: { value: string; label: string }[];
  onEditSetup: () => void;
  onReset?: () => void;
  saving?: boolean;
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}>
      <span className="text-[10px] font-normal opacity-70">{label}:</span>
      <span>{value}</span>
    </span>
  );
}

export default function ChatSummaryBar({
  draft,
  entityOptions,
  onEditSetup,
  onReset,
  saving = false,
}: ChatSummaryBarProps) {
  const entityLabel = formatEntities(draft.entities, entityOptions);
  const fyChipValue = formatFiscalYears(draft.fiscalYears);

  const statementsLabel =
    draft.statements.length === 2
      ? 'P&L + BS'
      : draft.statements[0] === 'PL'
      ? 'P&L'
      : 'Balance Sheet';

  const startModeLabel: Record<string, string> = {
    heuristic: 'Heuristics',
    excel: 'Excel upload',
    blank: 'Blank',
  };

  return (
    <div className="w-full border-b border-[#E2E8F0] bg-white px-6 py-3 flex items-center gap-3 flex-wrap">
      <div className="flex items-center gap-2 flex-wrap flex-1 min-w-0">
        <Chip label="Entity" value={entityLabel} />
        <Chip label="FY" value={fyChipValue} />
        <Chip label="Statements" value={statementsLabel} />
        <Chip label="Level" value={draft.level} />
        <Chip label="Period" value={draft.viewMode === 'annual' ? 'Annual' : 'Monthly'} />
        <Chip label="Start" value={startModeLabel[draft.start.mode] ?? draft.start.mode} />
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          type="button"
          onClick={onEditSetup}
          disabled={saving}
          className="rounded-lg border border-[#1E3A5F] px-3 py-1.5 text-xs font-medium text-[#1E3A5F] hover:bg-[#1E3A5F] hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Edit setup
        </button>
        {onReset && (
          <button
            type="button"
            onClick={onReset}
            disabled={saving}
            className="rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Reset
          </button>
        )}
      </div>
    </div>
  );
}
