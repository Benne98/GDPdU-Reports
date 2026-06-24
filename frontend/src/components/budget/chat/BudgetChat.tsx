/**
 * BudgetChat — main chat shell for the Budget Planning guided flow.
 *
 * Renders completed steps as paired bot/user message bubbles, and the active
 * step as a bot prompt + interactive input control at the bottom.
 */

import { useEffect, useRef } from 'react';
import { Undo2, Redo2 } from 'lucide-react';
import type {
  BudgetDraft,
  StepId,
  ChatStep,
  PositionGranularity,
  PositionKey,
} from '../../../lib/budgetChatFlow';
import {
  CHAT_STEPS,
  visibleSteps,
  isStepCompleted,
  getStepAnswer,
  granularityOptionsFor,
  applyEntitySelection,
  formatEntities,
  formatFiscalYears,
  formatGranularity,
  fyLabel,
} from '../../../lib/budgetChatFlow';
import type { BudgetUploadPreview } from '../../../lib/gdpduApi';
import RadioChips from './RadioChips';
import MultiSelectChips from './MultiSelectChips';
import DropdownInput from './DropdownInput';
import NumberInput from './NumberInput';
import FileDrop from './FileDrop';
import PositionGranularityPicker from './PositionGranularityPicker';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type ChatAnswerValue = string | string[] | number | Record<PositionKey, PositionGranularity>;

interface BudgetChatProps {
  draft: BudgetDraft;
  currentStepId: StepId;
  entityOptions: { value: string; label: string }[];
  fiscalYearOptions: { value: string; label: string }[];
  onAnswer: (stepId: StepId, value: ChatAnswerValue) => void;
  /** Update the draft for the current step WITHOUT advancing (multi-select toggles). */
  onSelect: (stepId: StepId, value: ChatAnswerValue) => void;
  onEditStep: (stepId: StepId) => void;
  onDownloadTemplate: () => void;
  onFileUpload: (f: File) => void;
  uploadLoading: boolean;
  uploadError?: string;
  uploadPreview?: BudgetUploadPreview | null;
  onAcceptUpload: () => void;
  heuristicLoading?: boolean;
  heuristicPreview?: { positionCount: number } | null;
  onAcceptHeuristic?: () => void;
  partnerHeuristicLoading?: boolean;
  partnerHeuristicPreview?: { partnerCount: number } | null;
  onAcceptPartnerHeuristic?: () => void;
  /** Undo/redo support during chat phase. */
  canUndo?: boolean;
  canRedo?: boolean;
  onUndo?: () => void;
  onRedo?: () => void;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatAnswer(
  draft: BudgetDraft,
  step: ChatStep,
  entityOptions: { value: string; label: string }[],
): string {
  const raw = getStepAnswer(draft, step.id);
  if (raw === undefined || raw === null) return '';

  switch (step.id) {
    case 'entity': {
      const arr = Array.isArray(raw) ? (raw as string[]) : [String(raw)];
      return formatEntities(arr, entityOptions);
    }
    case 'fiscal_year': {
      const years = Array.isArray(raw) ? (raw as string[]).map(Number) : [Number(raw)];
      return formatFiscalYears(years);
    }
    case 'statement':
      return raw === 'Both' ? 'Both (P&L + BS)' : String(raw);
    case 'granularity': {
      // New model: raw is a Record<PositionKey, PositionGranularity>
      if (typeof raw === 'object' && !Array.isArray(raw)) {
        return formatGranularity(draft);
      }
      // Legacy fallback: string[]
      const vs = raw as string[];
      return vs
        .map((v) =>
          v === 'partner_customers'
            ? 'Customers'
            : v === 'partner_suppliers'
            ? 'Suppliers'
            : v,
        )
        .join(', ');
    }
    case 'period':
    case 'start_values':
    case 'heuristic_method':
    case 'partner_start': {
      const opt = step.options?.find((o) => o.value === String(raw));
      return opt?.label ?? String(raw);
    }
    case 'heuristic_growth':
      return `${raw}%`;
    case 'excel_upload':
      return draft.start.uploadFileId ? 'File uploaded' : '';
    case 'review':
      return 'Reviewed';
    default:
      return String(raw);
  }
}

// ---------------------------------------------------------------------------
// Bot message bubble
// ---------------------------------------------------------------------------

function BotBubble({ prompt, detail }: { prompt: string; detail?: string }) {
  return (
    <div className="flex items-start gap-3 max-w-lg">
      <div
        className="flex-shrink-0 w-7 h-7 rounded flex items-center justify-center text-white text-xs font-bold"
        style={{ background: '#1E3A5F' }}
        aria-hidden="true"
      >
        B
      </div>
      <div
        className="rounded-2xl border border-[#E2E8F0] bg-white px-4 py-3 text-sm shadow-sm"
      >
        <p className="font-medium text-slate-800">{prompt}</p>
        {detail && (
          <p className="mt-1 text-xs text-slate-500">{detail}</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// User answer bubble
// ---------------------------------------------------------------------------

function UserBubble({
  text,
  onEdit,
}: {
  text: string;
  onEdit: () => void;
}) {
  return (
    <div className="flex justify-end items-start gap-2 group">
      <button
        type="button"
        onClick={onEdit}
        title="Edit this answer"
        className="opacity-0 group-hover:opacity-100 transition-opacity mt-1 flex-shrink-0 p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-[#1E3A5F]"
        aria-label="Edit answer"
      >
        {/* Pencil icon */}
        <svg
          className="w-3 h-3"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M11.5 2.5l2 2L5 13H3v-2L11.5 2.5z" />
        </svg>
      </button>
      <div
        className="rounded-2xl px-4 py-3 text-sm text-white max-w-xs"
        style={{ background: '#1E3A5F' }}
      >
        {text}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spinner
// ---------------------------------------------------------------------------

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500">
      <svg
        className="animate-spin h-4 w-4 text-[#1E3A5F]"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
      </svg>
      {label}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Upload diff table
// ---------------------------------------------------------------------------

function UploadDiffTable({ preview }: { preview: BudgetUploadPreview }) {
  if (preview.changes.length === 0) {
    return <p className="text-xs text-slate-500">No changes detected in the uploaded file.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-[#E2E8F0] mt-2">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[#E2E8F0]" style={{ background: '#F4F6F9' }}>
            <th className="py-1.5 px-3 text-left font-semibold text-slate-500">Line code</th>
            <th className="py-1.5 px-3 text-left font-semibold text-slate-500">Field</th>
            <th className="py-1.5 px-3 text-right font-semibold text-slate-500">Old</th>
            <th className="py-1.5 px-3 text-right font-semibold text-slate-500">New</th>
          </tr>
        </thead>
        <tbody>
          {preview.changes.slice(0, 20).map((c, i) => (
            <tr key={i} className="border-b border-slate-100 last:border-0">
              <td className="py-1.5 px-3 font-mono text-slate-700">{c.line_code}</td>
              <td className="py-1.5 px-3 text-slate-600">{c.field}</td>
              <td className="py-1.5 px-3 text-right font-mono text-slate-400">
                {c.old.toLocaleString('en-US')}
              </td>
              <td className="py-1.5 px-3 text-right font-mono text-emerald-700 font-medium">
                {c.new.toLocaleString('en-US')}
              </td>
            </tr>
          ))}
          {preview.changes.length > 20 && (
            <tr>
              <td colSpan={4} className="py-1.5 px-3 text-slate-400 italic text-center">
                …and {preview.changes.length - 20} more rows
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Review card
// ---------------------------------------------------------------------------

function ReviewCard({
  draft,
  entityOptions,
  onEditStep,
  onOpenBudget,
}: {
  draft: BudgetDraft;
  entityOptions: { value: string; label: string }[];
  onEditStep: (stepId: StepId) => void;
  onOpenBudget: () => void;
}) {
  const rows: { label: string; value: string; stepId: StepId }[] = [];

  const visible = visibleSteps(draft);
  const reviewableSteps = visible.filter((s) => s.id !== 'review');

  for (const step of reviewableSteps) {
    const formatted = formatAnswer(draft, step, entityOptions);
    if (formatted) {
      rows.push({ label: step.prompt, value: formatted, stepId: step.id });
    }
  }

  return (
    <div className="rounded-xl border border-[#E2E8F0] bg-white p-5 max-w-lg">
      <h3 className="text-sm font-semibold text-slate-700 mb-3">Setup summary</h3>
      <dl className="space-y-2">
        {rows.map((row) => (
          <div key={row.stepId} className="flex items-center justify-between gap-4 text-sm">
            <dt className="text-slate-500 text-xs">{row.label}</dt>
            <dd className="flex items-center gap-2">
              <span className="font-medium text-slate-800">{row.value}</span>
              <button
                type="button"
                onClick={() => onEditStep(row.stepId)}
                className="text-xs text-[#1E3A5F] hover:underline"
              >
                Edit
              </button>
            </dd>
          </div>
        ))}
      </dl>
      <button
        type="button"
        onClick={onOpenBudget}
        className="mt-5 w-full rounded-xl py-3 px-6 text-sm font-semibold text-white transition-colors hover:opacity-90"
        style={{ background: '#1E3A5F' }}
      >
        Open editable budget view
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Active step input
// ---------------------------------------------------------------------------

function ActiveStepInput({
  step,
  draft,
  entityOptions,
  fiscalYearOptions,
  onAnswer,
  onSelect,
  onDownloadTemplate,
  onFileUpload,
  uploadLoading,
  uploadError,
  uploadPreview,
  onAcceptUpload,
  heuristicLoading,
  heuristicPreview,
  onAcceptHeuristic,
  partnerHeuristicLoading,
  partnerHeuristicPreview,
  onAcceptPartnerHeuristic,
  onEditStep,
  onOpenBudget,
}: {
  step: ChatStep;
  draft: BudgetDraft;
  entityOptions: { value: string; label: string }[];
  fiscalYearOptions: { value: string; label: string }[];
  onAnswer: (stepId: StepId, value: ChatAnswerValue) => void;
  onSelect: (stepId: StepId, value: ChatAnswerValue) => void;
  onDownloadTemplate: () => void;
  onFileUpload: (f: File) => void;
  uploadLoading: boolean;
  uploadError?: string;
  uploadPreview?: BudgetUploadPreview | null;
  onAcceptUpload: () => void;
  heuristicLoading?: boolean;
  heuristicPreview?: { positionCount: number } | null;
  onAcceptHeuristic?: () => void;
  partnerHeuristicLoading?: boolean;
  partnerHeuristicPreview?: { partnerCount: number } | null;
  onAcceptPartnerHeuristic?: () => void;
  onEditStep: (stepId: StepId) => void;
  onOpenBudget: () => void;
}) {
  const currentAnswer = getStepAnswer(draft, step.id);

  switch (step.type) {
    case 'entity_multi_select': {
      // Multi-select checkboxes for entity selection.
      // '' (consolidated) is exclusive with specific entity codes.
      const selected: string[] = Array.isArray(currentAnswer)
        ? (currentAnswer as string[])
        : currentAnswer !== undefined
        ? [String(currentAnswer)]
        : [];

      const opts = entityOptions;
      const isConsolidated = selected.length === 1 && selected[0] === '';
      const hasSelection = selected.length > 0;

      function handleToggle(value: string) {
        const next = applyEntitySelection(selected, value);
        // Update the selection WITHOUT advancing — the user confirms with Continue.
        onSelect(step.id, next);
      }

      return (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-2">
            {/* "All entities (consolidated)" — exclusive option at the top */}
            <label
              className={[
                'flex items-center gap-3 rounded-xl border px-4 py-3 cursor-pointer transition-colors select-none',
                isConsolidated
                  ? 'border-[#1E3A5F] bg-[#1E3A5F]/5'
                  : 'border-[#E2E8F0] bg-white hover:border-[#1E3A5F]/40 hover:bg-slate-50',
              ].join(' ')}
            >
              <input
                type="checkbox"
                className="accent-[#1E3A5F] w-4 h-4 flex-shrink-0"
                checked={isConsolidated}
                onChange={() => handleToggle('')}
              />
              <span className="flex flex-col">
                <span className="text-sm font-medium text-slate-800">
                  All entities (consolidated)
                </span>
                <span className="text-xs text-slate-400">
                  Plan as a single consolidated scope
                </span>
              </span>
            </label>

            {/* Divider */}
            {opts.filter((o) => o.value !== '').length > 0 && (
              <div className="flex items-center gap-2 px-1">
                <div className="h-px flex-1 bg-[#E2E8F0]" />
                <span className="text-[10px] uppercase tracking-widest text-slate-400 font-medium">
                  or select specific entities
                </span>
                <div className="h-px flex-1 bg-[#E2E8F0]" />
              </div>
            )}

            {/* Individual entities */}
            {opts
              .filter((o) => o.value !== '')
              .map((opt) => {
                const checked = selected.includes(opt.value);
                return (
                  <label
                    key={opt.value}
                    className={[
                      'flex items-center gap-3 rounded-xl border px-4 py-3 cursor-pointer transition-colors select-none',
                      checked
                        ? 'border-[#1E3A5F] bg-[#1E3A5F]/5'
                        : 'border-[#E2E8F0] bg-white hover:border-[#1E3A5F]/40 hover:bg-slate-50',
                      isConsolidated ? 'opacity-40 pointer-events-none' : '',
                    ].join(' ')}
                  >
                    <input
                      type="checkbox"
                      className="accent-[#1E3A5F] w-4 h-4 flex-shrink-0"
                      checked={checked}
                      disabled={isConsolidated}
                      onChange={() => handleToggle(opt.value)}
                    />
                    <span className="text-sm font-medium text-slate-800">{opt.label}</span>
                  </label>
                );
              })}
          </div>

          {hasSelection && (
            <button
              type="button"
              onClick={() => onAnswer(step.id, selected)}
              className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
              style={{ background: '#1E3A5F' }}
            >
              Continue
            </button>
          )}
        </div>
      );
    }

    case 'fiscal_year_multi_select': {
      // Multi-select checkboxes for fiscal year selection.
      // Selected state is stored as fiscalYears: number[] on the draft.
      const selectedYears: number[] = draft.fiscalYears ?? [];
      const hasSelection = selectedYears.length > 0;

      function handleYearToggle(year: number) {
        const next = selectedYears.includes(year)
          ? selectedYears.filter((y) => y !== year)
          : [...selectedYears, year].sort((a, b) => a - b);
        onSelect(step.id, next.map(String));
      }

      const yearOpts = fiscalYearOptions; // { value: string (year number), label: string (FYxx) }

      return (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-2">
            {yearOpts.map((opt) => {
              const year = Number(opt.value);
              const checked = selectedYears.includes(year);
              return (
                <label
                  key={opt.value}
                  className={[
                    'flex items-center gap-3 rounded-xl border px-4 py-3 cursor-pointer transition-colors select-none',
                    checked
                      ? 'border-[#1E3A5F] bg-[#1E3A5F]/5'
                      : 'border-[#E2E8F0] bg-white hover:border-[#1E3A5F]/40 hover:bg-slate-50',
                  ].join(' ')}
                >
                  <input
                    type="checkbox"
                    className="accent-[#1E3A5F] w-4 h-4 flex-shrink-0"
                    checked={checked}
                    onChange={() => handleYearToggle(year)}
                  />
                  <span className="text-sm font-medium text-slate-800">{fyLabel(year)}</span>
                </label>
              );
            })}
          </div>

          {hasSelection && (
            <button
              type="button"
              onClick={() => onAnswer(step.id, selectedYears.map(String))}
              className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
              style={{ background: '#1E3A5F' }}
            >
              Continue
            </button>
          )}
        </div>
      );
    }

    case 'dropdown': {
      const opts =
        step.id === 'entity'
          ? entityOptions
          : (step.options ?? []);
      return (
        <div className="flex gap-3 items-center flex-wrap">
          <DropdownInput
            options={opts}
            value={currentAnswer !== undefined ? String(currentAnswer) : undefined}
            onChange={(v) => onAnswer(step.id, v)}
            placeholder="Select..."
          />
          {currentAnswer !== undefined && (
            <button
              type="button"
              onClick={() => onAnswer(step.id, String(currentAnswer))}
              className="rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
              style={{ background: '#1E3A5F' }}
            >
              Continue
            </button>
          )}
        </div>
      );
    }

    case 'radio': {
      const opts = step.options ?? [];
      return (
        <RadioChips
          options={opts}
          value={currentAnswer !== undefined ? String(currentAnswer) : undefined}
          onChange={(v) => onAnswer(step.id, v)}
        />
      );
    }

    case 'multi_select': {
      const opts =
        step.id === 'granularity'
          ? granularityOptionsFor(draft)
          : (step.options ?? []);
      const currentValues = Array.isArray(currentAnswer)
        ? (currentAnswer as string[])
        : currentAnswer !== undefined
        ? [String(currentAnswer)]
        : [];
      return (
        <div className="flex flex-col gap-3">
          <MultiSelectChips
            options={opts}
            values={currentValues}
            onChange={(vs) => {
              // Update the selection WITHOUT advancing — user confirms with Continue.
              onSelect(step.id, vs);
            }}
            minSelect={1}
          />
          {currentValues.length > 0 && (
            <button
              type="button"
              onClick={() => onAnswer(step.id, currentValues)}
              className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
              style={{ background: '#1E3A5F' }}
            >
              Continue
            </button>
          )}
        </div>
      );
    }

    case 'position_granularity': {
      return (
        <PositionGranularityPicker
          draft={draft}
          onSelect={(map) => onSelect(step.id, map)}
          onAnswer={(map) => onAnswer(step.id, map)}
        />
      );
    }

    case 'number': {
      const numVal = currentAnswer !== undefined ? Number(currentAnswer) : step.numDefault;
      return (
        <div className="flex flex-col gap-3">
          <NumberInput
            value={numVal}
            onChange={(v) => onAnswer(step.id, v)}
            min={step.numMin}
            max={step.numMax}
            step={step.numStep}
            suffix={step.numSuffix}
          />
        </div>
      );
    }

    case 'file_drop': {
      const methodLabel =
        draft.start.heuristic === 'prior_year'
          ? 'Prior Year'
          : draft.start.heuristic === 'trend_cagr'
          ? 'Trend CAGR'
          : draft.start.heuristic === 'run_rate'
          ? 'Run Rate'
          : '';

      // Heuristic preview box (shown on start_values step when heuristic chosen)
      if (step.id === 'start_values' && draft.start.mode === 'heuristic') {
        if (heuristicLoading) {
          return <Spinner label="Fetching heuristic suggestions..." />;
        }
        if (heuristicPreview) {
          return (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 max-w-sm">
              <p className="text-sm text-emerald-800 font-medium">
                Proposing starting values for{' '}
                <strong>{heuristicPreview.positionCount}</strong> positions
                {methodLabel ? ` (${methodLabel})` : ''}.
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={onAcceptHeuristic}
                  className="rounded-lg px-4 py-1.5 text-xs font-medium text-white transition-colors hover:opacity-90"
                  style={{ background: '#1E3A5F' }}
                >
                  Use these values
                </button>
                <button
                  type="button"
                  onClick={() => onAnswer(step.id, 'heuristic')}
                  className="rounded-lg px-4 py-1.5 text-xs font-medium text-slate-500 hover:text-[#1E3A5F] underline"
                >
                  Skip for now
                </button>
              </div>
            </div>
          );
        }
      }

      // Excel upload flow
      return (
        <div className="flex flex-col gap-3 max-w-sm">
          <button
            type="button"
            onClick={onDownloadTemplate}
            className="self-start rounded-lg border border-[#1E3A5F] px-4 py-2 text-sm font-medium text-[#1E3A5F] hover:bg-[#1E3A5F] hover:text-white transition-colors"
          >
            Download template
          </button>
          <FileDrop
            onFile={onFileUpload}
            loading={uploadLoading}
            label="Drop your filled Excel template here"
            hint="or click Browse to select (.xlsx)"
          />
          {uploadLoading && <Spinner label="Processing file..." />}
          {uploadError && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {uploadError}
            </div>
          )}
          {uploadPreview && (
            <div className="flex flex-col gap-2">
              <UploadDiffTable preview={uploadPreview} />
              <div className="flex gap-2 mt-1">
                <button
                  type="button"
                  onClick={onAcceptUpload}
                  className="rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
                  style={{ background: '#1E3A5F' }}
                >
                  Use these values
                </button>
                <button
                  type="button"
                  onClick={() => onAnswer(step.id, '')}
                  className="rounded-lg px-4 py-2 text-sm font-medium text-slate-500 hover:text-[#1E3A5F] underline"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      );
    }

    case 'review':
      return (
        <div className="flex flex-col gap-3">
          <ReviewCard
            draft={draft}
            entityOptions={entityOptions}
            onEditStep={onEditStep}
            onOpenBudget={onOpenBudget}
          />
          {/* Partner heuristic preview (shown here when partner_start chosen) */}
          {partnerHeuristicLoading && (
            <Spinner label="Fetching partner heuristic suggestions..." />
          )}
          {partnerHeuristicPreview && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 max-w-sm">
              <p className="text-sm text-emerald-800 font-medium">
                Proposing starting values for{' '}
                <strong>{partnerHeuristicPreview.partnerCount}</strong> partners.
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={onAcceptPartnerHeuristic}
                  className="rounded-lg px-4 py-1.5 text-xs font-medium text-white transition-colors hover:opacity-90"
                  style={{ background: '#1E3A5F' }}
                >
                  Use partner values
                </button>
              </div>
            </div>
          )}
        </div>
      );

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// BudgetChat
// ---------------------------------------------------------------------------

export default function BudgetChat({
  draft,
  currentStepId,
  entityOptions,
  fiscalYearOptions,
  onAnswer,
  onSelect,
  onEditStep,
  onDownloadTemplate,
  onFileUpload,
  uploadLoading,
  uploadError,
  uploadPreview,
  onAcceptUpload,
  heuristicLoading,
  heuristicPreview,
  onAcceptHeuristic,
  partnerHeuristicLoading,
  partnerHeuristicPreview,
  onAcceptPartnerHeuristic,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
}: BudgetChatProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom whenever currentStepId changes
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [currentStepId]);

  const visible = visibleSteps(draft);
  const currentIdx = visible.findIndex((s) => s.id === currentStepId);

  // Steps that are completed and before the current step
  const completedSteps = visible.slice(0, currentIdx).filter((s) =>
    isStepCompleted(draft, s.id),
  );

  // The active step definition (with options filled in for entity/fiscal_year)
  const activeStep = visible.find((s) => s.id === currentStepId);

  // Callback wired through ReviewCard "Open editable budget view"
  // The page listens to onAnswer('review', 'open')
  function handleOpenBudget() {
    onAnswer('review', 'open');
  }

  const showUndoRedo = (canUndo || canRedo) && (onUndo || onRedo);

  return (
    <div className="w-full py-6 flex flex-col gap-6">
      {/* Undo/redo header — shown during chat when history is available */}
      {showUndoRedo && (
        <div className="flex items-center gap-2 px-1">
          <button
            type="button"
            onClick={onUndo}
            disabled={!canUndo}
            title="Undo last answer"
            className="rounded p-1.5 text-slate-500 hover:text-[#1E3A5F] hover:bg-slate-100 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Undo2 size={14} aria-hidden />
          </button>
          <button
            type="button"
            onClick={onRedo}
            disabled={!canRedo}
            title="Redo"
            className="rounded p-1.5 text-slate-500 hover:text-[#1E3A5F] hover:bg-slate-100 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Redo2 size={14} aria-hidden />
          </button>
          <span className="text-[10px] text-slate-400">Undo / redo setup changes</span>
        </div>
      )}
      {/* Completed steps — bot prompt + user answer */}
      {completedSteps.map((step) => {
        // Build the step with correct options for display
        const stepWithOpts = buildStepWithOptions(step, draft, entityOptions, fiscalYearOptions);
        const answerText = formatAnswer(draft, stepWithOpts, entityOptions);
        return (
          <div key={step.id} className="flex flex-col gap-2">
            <BotBubble prompt={step.prompt} detail={step.detail} />
            {answerText && (
              <UserBubble text={answerText} onEdit={() => onEditStep(step.id)} />
            )}
          </div>
        );
      })}

      {/* Active step */}
      {activeStep && (
        <div className="flex flex-col gap-4">
          <BotBubble prompt={activeStep.prompt} detail={activeStep.detail} />
          <div className="pl-10">
            <ActiveStepInput
              step={buildStepWithOptions(activeStep, draft, entityOptions, fiscalYearOptions)}
              draft={draft}
              entityOptions={entityOptions}
              fiscalYearOptions={fiscalYearOptions}
              onAnswer={onAnswer}
              onSelect={onSelect}
              onDownloadTemplate={onDownloadTemplate}
              onFileUpload={onFileUpload}
              uploadLoading={uploadLoading}
              uploadError={uploadError}
              uploadPreview={uploadPreview}
              onAcceptUpload={onAcceptUpload}
              heuristicLoading={heuristicLoading}
              heuristicPreview={heuristicPreview}
              onAcceptHeuristic={onAcceptHeuristic}
              partnerHeuristicLoading={partnerHeuristicLoading}
              partnerHeuristicPreview={partnerHeuristicPreview}
              onAcceptPartnerHeuristic={onAcceptPartnerHeuristic}
              onEditStep={onEditStep}
              onOpenBudget={handleOpenBudget}
            />
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper: inject runtime options into a step definition
// ---------------------------------------------------------------------------

function buildStepWithOptions(
  step: ChatStep,
  draft: BudgetDraft,
  entityOptions: { value: string; label: string }[],
  fiscalYearOptions: { value: string; label: string }[],
): ChatStep {
  if (step.id === 'entity') {
    return { ...step, options: entityOptions };
  }
  if (step.id === 'fiscal_year') {
    return { ...step, options: fiscalYearOptions };
  }
  if (step.id === 'granularity') {
    return { ...step, options: granularityOptionsFor(draft) };
  }
  return step;
}

// Re-export CHAT_STEPS for convenience (used by BudgetChatPage)
export { CHAT_STEPS };
