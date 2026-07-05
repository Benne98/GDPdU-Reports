import { useState } from 'react';
import { Lock, LockOpen, Plus } from 'lucide-react';
import type { SubRowOverride } from './BudgetGrid';
import { annualFromGrowth, monthsFromGrowth } from '../../lib/budgetBlankRollup';
import type { BudgetPosition } from '../../lib/gdpduApi';

interface SandboxBlankRateCellProps {
  baseAnnual: number;
  state: SubRowOverride | undefined;
  pos: BudgetPosition | undefined;
  disabled: boolean;
  onChange: (next: SubRowOverride) => void;
}

export default function SandboxBlankRateCell({
  baseAnnual,
  state,
  pos,
  disabled,
  onChange,
}: SandboxBlankRateCellProps) {
  const [raw, setRaw] = useState<string | null>(null);
  const growthPct = state?.growthPct ?? 0;
  const display = raw !== null ? raw : String(+growthPct.toFixed(2));
  const locked = state?.rateLocked ?? false;
  const escalations = state?.rateEscalations ?? [];

  function commitGrowth(s: string) {
    const n = parseFloat(s);
    if (!Number.isFinite(n)) return;
    const annual = annualFromGrowth(baseAnnual, n);
    const months = monthsFromGrowth(baseAnnual, n, pos, state?.rateEscalations);
    onChange({ ...state, growthPct: n, annual, months });
    setRaw(null);
  }

  function toggleLock() {
    onChange({ ...state, rateLocked: !locked });
  }

  function addEscalation() {
    const nextMonth = escalations.length > 0
      ? Math.min(12, (escalations[escalations.length - 1].fromMonth ?? 1) + 1)
      : 4;
    const next: SubRowOverride = {
      ...state,
      growthPct,
      rateEscalations: [...escalations, { fromMonth: nextMonth, ratePct: growthPct + 2 }],
    };
    if (growthPct !== undefined) {
      next.annual = annualFromGrowth(baseAnnual, growthPct);
      next.months = monthsFromGrowth(baseAnnual, growthPct, pos, next.rateEscalations);
    }
    onChange(next);
  }

  function updateEscalation(idx: number, field: 'fromMonth' | 'ratePct', value: number) {
    const nextEsc = escalations.map((e, i) =>
      i === idx ? { ...e, [field]: value } : e,
    );
    onChange({
      ...state,
      growthPct,
      rateEscalations: nextEsc,
      annual: annualFromGrowth(baseAnnual, growthPct),
      months: monthsFromGrowth(baseAnnual, growthPct, pos, nextEsc),
    });
  }

  return (
    <div className="flex flex-col items-end gap-0.5 min-w-0">
      <div className="flex items-center gap-0.5">
        <button
          type="button"
          onClick={toggleLock}
          disabled={disabled}
          title={locked ? 'Rate locked' : 'Lock rate'}
          className="p-0.5 text-slate-400 hover:text-[#1E3A5F] disabled:opacity-30"
        >
          {locked ? <Lock size={10} /> : <LockOpen size={10} />}
        </button>
        <input
          type="number"
          value={display}
          disabled={disabled || locked}
          onChange={(e) => setRaw(e.target.value)}
          onBlur={(e) => commitGrowth(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              commitGrowth((e.target as HTMLInputElement).value);
              (e.target as HTMLInputElement).blur();
            }
          }}
          className="w-16 rounded border border-slate-200 bg-white px-1 py-0.5 text-right text-xs font-mono
            focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
            disabled:bg-slate-50 disabled:text-slate-400"
          step="0.1"
          title="Growth %"
        />
        <span className="text-[9px] text-slate-400">%</span>
        <button
          type="button"
          onClick={addEscalation}
          disabled={disabled || locked}
          title="Add rate increase from month"
          className="p-0.5 text-slate-400 hover:text-[#1E3A5F] disabled:opacity-30"
        >
          <Plus size={10} />
        </button>
      </div>
      <span className="text-[8px] text-slate-400">Growth %</span>
      {escalations.map((esc, idx) => (
        <div key={idx} className="flex items-center gap-0.5 text-[8px] text-slate-500">
          <span>from</span>
          <input
            type="number"
            min={1}
            max={12}
            value={esc.fromMonth}
            disabled={disabled || locked}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              if (Number.isFinite(n)) updateEscalation(idx, 'fromMonth', n);
            }}
            className="w-7 rounded border border-slate-200 px-0.5 text-center text-[8px]"
          />
          <span>→</span>
          <input
            type="number"
            value={esc.ratePct}
            disabled={disabled || locked}
            onChange={(e) => {
              const n = parseFloat(e.target.value);
              if (Number.isFinite(n)) updateEscalation(idx, 'ratePct', n);
            }}
            className="w-10 rounded border border-slate-200 px-0.5 text-right text-[8px]"
          />
          <span>%</span>
        </div>
      ))}
    </div>
  );
}
