/**
 * DropdownInput — styled <select> for chat step dropdowns.
 */

interface DropdownInputProps {
  options: { value: string; label: string }[];
  value: string | undefined;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}

export default function DropdownInput({
  options,
  value,
  onChange,
  placeholder = 'Select...',
  disabled,
}: DropdownInputProps) {
  return (
    <select
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => {
        if (e.target.value !== '') onChange(e.target.value);
      }}
      className={[
        'rounded-lg border border-[#E2E8F0] bg-white px-3 py-2 text-sm text-slate-700',
        'min-w-[180px] appearance-none',
        'focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]',
        'disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed',
      ].join(' ')}
      style={{
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")`,
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'right 10px center',
        paddingRight: '30px',
      }}
    >
      <option value="" disabled>
        {placeholder}
      </option>
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}
