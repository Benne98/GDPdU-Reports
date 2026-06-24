/**
 * NumberInput — inline number input with optional suffix label.
 * Commits on blur or Enter key.
 */

import { useState } from 'react';

interface NumberInputProps {
  value: number | undefined;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
  disabled?: boolean;
  placeholder?: string;
}

export default function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
  suffix,
  disabled,
  placeholder = '0',
}: NumberInputProps) {
  const [raw, setRaw] = useState<string | null>(null);

  function commit(raw: string) {
    const n = parseFloat(raw);
    if (Number.isFinite(n)) {
      const clamped =
        min !== undefined && n < min ? min :
        max !== undefined && n > max ? max :
        n;
      onChange(clamped);
    }
    setRaw(null);
  }

  const displayValue = raw !== null ? raw : value !== undefined ? String(value) : '';

  return (
    <div className="flex items-center gap-2">
      <input
        type="number"
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        value={displayValue}
        placeholder={placeholder}
        onChange={(e) => setRaw(e.target.value)}
        onBlur={() => {
          if (raw !== null) commit(raw);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && raw !== null) {
            commit(raw);
            (e.target as HTMLInputElement).blur();
          }
        }}
        className={[
          'rounded-lg border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-slate-700 w-36',
          'focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]',
          'disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed',
        ].join(' ')}
      />
      {suffix && (
        <span className="text-sm text-slate-500 font-medium">{suffix}</span>
      )}
    </div>
  );
}
