/**
 * MultiSelectChips — multi-select toggle pill buttons.
 */

import type { StepOption } from '../../../lib/budgetChatFlow';

interface MultiSelectChipsProps {
  options: StepOption[];
  values: string[];
  onChange: (vs: string[]) => void;
  disabled?: boolean;
  minSelect?: number;
}

export default function MultiSelectChips({
  options,
  values,
  onChange,
  disabled,
  minSelect,
}: MultiSelectChipsProps) {
  function toggle(v: string) {
    if (disabled) return;
    const isSelected = values.includes(v);
    if (isSelected) {
      // Respect minSelect floor
      if (minSelect !== undefined && values.length <= minSelect) return;
      onChange(values.filter((x) => x !== v));
    } else {
      onChange([...values, v]);
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      {options.map((opt) => {
        const selected = values.includes(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            disabled={disabled}
            title={opt.description}
            onClick={() => toggle(opt.value)}
            className={[
              'rounded-xl border px-4 py-2 text-sm font-medium transition-colors text-left',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              selected
                ? 'border-[#1E3A5F] bg-[#1E3A5F] text-white'
                : 'border-[#E2E8F0] bg-white text-slate-600 hover:border-[#1E3A5F]/40 hover:bg-slate-50',
            ].join(' ')}
          >
            <div className="flex items-center gap-1.5">
              {selected && (
                <svg
                  className="flex-shrink-0 w-3.5 h-3.5 text-white"
                  viewBox="0 0 14 14"
                  fill="none"
                  aria-hidden="true"
                >
                  <path
                    d="M2 7l3.5 3.5L12 3.5"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
              <span className="font-medium">{opt.label}</span>
            </div>
            {opt.description && (
              <div
                className={[
                  'text-xs mt-0.5',
                  selected ? 'text-white/80' : 'text-slate-400',
                ].join(' ')}
              >
                {opt.description}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}
