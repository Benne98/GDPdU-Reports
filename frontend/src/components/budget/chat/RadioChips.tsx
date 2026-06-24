/**
 * RadioChips — single-select pill button row.
 */

import type { StepOption } from '../../../lib/budgetChatFlow';

interface RadioChipsProps {
  options: StepOption[];
  value: string | undefined;
  onChange: (v: string) => void;
  disabled?: boolean;
}

export default function RadioChips({ options, value, onChange, disabled }: RadioChipsProps) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((opt) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            disabled={disabled}
            title={opt.description}
            onClick={() => !disabled && onChange(opt.value)}
            className={[
              'rounded-xl border px-4 py-2 text-sm font-medium transition-colors text-left',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              selected
                ? 'border-[#1E3A5F] bg-[#1E3A5F] text-white'
                : 'border-[#E2E8F0] bg-white text-slate-600 hover:border-[#1E3A5F]/40 hover:bg-slate-50',
            ].join(' ')}
          >
            <div className="font-medium">{opt.label}</div>
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
