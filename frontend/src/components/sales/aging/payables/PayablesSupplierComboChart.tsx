import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PayablesSupplierComboRow } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'

export default function PayablesSupplierComboChart({ data }: { data: PayablesSupplierComboRow[] }) {
  if (!data.length) {
    return (
      <div className="h-[320px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No supplier data
      </div>
    )
  }

  const short = data.map(d => ({
    ...d,
    name: d.supplier_name.length > 14 ? `${d.supplier_name.slice(0, 12)}…` : d.supplier_name,
  }))

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={short} margin={{ top: 12, right: 48, left: 4, bottom: 64 }}>
        <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" vertical={false} />
        <XAxis
          dataKey="name"
          tick={{ fontSize: 9, fill: '#64748B' }}
          angle={-32}
          textAnchor="end"
          height={72}
          interval={0}
        />
        <YAxis
          yAxisId="left"
          tick={{ fontSize: 10, fill: '#94A3B8' }}
          tickFormatter={v => fmtAmount(Number(v))}
          width={56}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          tick={{ fontSize: 10, fill: '#D97706' }}
          tickFormatter={v => `${v}d`}
          width={40}
        />
        <Tooltip
          formatter={(v: number, name: string) =>
            name === 'days_outstanding' ? `${v} days` : fmtAmount(v)
          }
          labelFormatter={(_, p) => p?.[0]?.payload?.supplier_name ?? ''}
        />
        <defs>
          <linearGradient id="apComboGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1E3A5F" />
            <stop offset="100%" stopColor="#3B82F6" />
          </linearGradient>
        </defs>
        <Bar yAxisId="left" dataKey="balance" fill="url(#apComboGrad)" radius={[6, 6, 0, 0]} maxBarSize={36} />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="days_outstanding"
          stroke="#D97706"
          strokeWidth={2}
          dot={{ r: 3, fill: '#D97706' }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}



