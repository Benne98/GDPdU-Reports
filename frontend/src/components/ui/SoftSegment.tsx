const NAVY = "#1E3A5F";

export default function SoftSegment({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 p-0.5 bg-slate-50/80">
      {options.map((o) => {
        const active = value === o.value;
        return (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            className={`rounded-md px-3.5 py-1.5 text-xs font-medium transition-colors ${
              active ? "bg-white shadow-sm" : "text-slate-500 hover:text-slate-700"
            }`}
            style={active ? { color: NAVY } : undefined}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
