import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ReceivablesSalesLinkPoint } from '../../../lib/api'
import { fmtAmount } from '../../../lib/fmt'

export default function ReceivablesSalesLinkChart({ points }: { points: ReceivablesSalesLinkPoint[] }) {
  if (!points.length) {
    return (
      <div className="h-[240px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No sales link data
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={240}>
      <ComposedChart data={points} margin={{ top: 12, right: 48, left: 4, bottom: 8 }}>
        <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#64748B' }} />
        <YAxis
          yAxisId="left"
          tick={{ fontSize: 10, fill: '#94A3B8' }}
          tickFormatter={v => fmtAmount(Number(v))}
          width={52}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          tick={{ fontSize: 10, fill: '#D97706' }}
          unit="%"
          width={40}
        />
        <Tooltip
          formatter={(v: number, name: string) =>
            name === 'credit_sales_pct' ? `${v}%` : fmtAmount(v)
          }
        />
        <Line yAxisId="left" type="monotone" dataKey="balance" name="Receivables" stroke="#2563EB" strokeWidth={2} dot={false} />
        <Line yAxisId="left" type="monotone" dataKey="net_sales" name="Net sales" stroke="#64748B" strokeWidth={1.5} strokeDasharray="4 4" dot={false} />
        <Line yAxisId="right" type="monotone" dataKey="credit_sales_pct" name="Credit %" stroke="#D97706" strokeWidth={2} dot={{ r: 2 }} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
