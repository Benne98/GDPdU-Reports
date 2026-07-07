import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

interface Bin {
  bin_lo: number
  bin_hi: number
  count: number
}

interface Props {
  bins: Bin[]
  height?: number
}

export default function HistogramChart({ bins, height = 160 }: Props) {
  const data = bins.map(b => ({
    label: `${b.bin_lo.toFixed(0)}–${b.bin_hi.toFixed(0)}`,
    count: b.count,
  }))

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#94A3B8' }} interval="preserveStartEnd" />
        <YAxis tick={{ fontSize: 10, fill: '#94A3B8' }} allowDecimals={false} width={24} />
        <Tooltip
          formatter={(v: number) => [v, 'Months']}
          labelFormatter={(l: string) => `Range: ${l} kEUR`}
          contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #E2E8F0' }}
        />
        <Bar dataKey="count" fill="#3B82F6" radius={[2, 2, 0, 0]} name="Months" />
      </BarChart>
    </ResponsiveContainer>
  )
}
