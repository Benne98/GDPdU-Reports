/**
 * SaveProgress — inline commit-progress indicator for the whole-draft Save.
 *
 * Shows a progress bar + step counter while writing positions, a success
 * confirmation when done, and a detailed error message on failure.
 */

interface SaveProgressProps {
  /** Number of positions committed so far. */
  step: number;
  /** Total positions to commit. */
  total: number;
  /** The line_code currently being written (for display). */
  currentLabel?: string;
  /** Non-null means an error stopped the run. */
  error?: string;
  /** True when all steps completed successfully. */
  done: boolean;
  /** Called when user clicks "Plan another entity / statement?" */
  onLoop?: () => void;
  /** Called when user dismisses the success banner (stays in grid). */
  onDismiss?: () => void;
}

export default function SaveProgress({
  step,
  total,
  currentLabel,
  error,
  done,
  onLoop,
  onDismiss,
}: SaveProgressProps) {
  const pct = total > 0 ? Math.round((step / total) * 100) : 0;

  if (done && !error) {
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4 flex items-start gap-4">
        <span className="text-emerald-600 mt-0.5 text-xl leading-none" aria-hidden="true">
          &#10003;
        </span>
        <div className="flex-1">
          <p className="font-semibold text-emerald-800 text-sm">
            Budget saved — {total} position{total !== 1 ? 's' : ''} written to the database.
          </p>
          <p className="text-xs text-emerald-700 mt-0.5">
            Values now appear in the P&amp;L / Balance Sheet plan columns.
          </p>
          <div className="flex items-center gap-3 mt-3">
            {onLoop && (
              <button
                type="button"
                onClick={onLoop}
                className="rounded-lg px-3 py-1.5 text-xs font-medium text-white transition-colors"
                style={{ background: '#1E3A5F' }}
              >
                Plan another entity / statement?
              </button>
            )}
            {onDismiss && (
              <button
                type="button"
                onClick={onDismiss}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 transition-colors"
              >
                Stay in this budget
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 px-5 py-4">
        <p className="font-semibold text-red-800 text-sm">Save failed at step {step} / {total}</p>
        <p className="text-xs text-red-700 mt-1 font-mono break-all">{error}</p>
        <p className="text-xs text-slate-600 mt-2">
          Positions written before the error are already saved. Fix the issue and try again, or
          use "Edit setup" to adjust the draft.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-[#1E3A5F]/20 bg-[#1E3A5F]/5 px-5 py-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-slate-700">
          Saving budget&hellip; {step} / {total}
        </span>
        <span className="text-xs text-slate-500">{pct}%</span>
      </div>
      <div className="w-full h-2 rounded-full bg-slate-200 overflow-hidden">
        <div
          className="h-2 rounded-full transition-all duration-300"
          style={{ width: `${pct}%`, background: '#1E3A5F' }}
        />
      </div>
      {currentLabel && (
        <p className="mt-1.5 text-[10px] text-slate-400 font-mono truncate">
          Writing: {currentLabel}
        </p>
      )}
    </div>
  );
}
