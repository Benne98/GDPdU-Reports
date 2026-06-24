interface Props {
  value: number
  onChange: (v: number) => void
}

export default function SensitivitySlider({ value, onChange }: Props) {
  return (
    <div className="flex items-center gap-4 rounded-xl px-4 py-3 mb-6" style={{ background: 'rgba(30,58,95,0.04)', border: '1px solid #E2E8F0' }}>
      <div className="flex-shrink-0">
        <p className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>Signal threshold</p>
        <p className="text-[11px] mt-0.5" style={{ color: '#94A3B8' }}>Lower = show more, including minor signals</p>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="flex-1 accent-blue-700"
        aria-label="Signal score threshold"
      />
      <span className="w-12 text-center text-sm font-semibold flex-shrink-0" style={{ color: '#1E3A5F' }}>
        {value}
      </span>
    </div>
  )
}
