import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PayablesProcurementLinkPoint } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'

export default function PayablesProcurementLinkChart({ points }: { points: PayablesProcurementLinkPoint[] }) {
  if (!points.length) {
    return (
      <div className="h-[264px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No procurement link data
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={264}>
      <ComposedChart data={points} margin={{ top: 12, right: 48, left: 4, bottom: 8 }}>
        <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#64748B' }} />
        <YAxis
          yAxisId="left"
          tick={{ fontSize: 11, fill: '#94A3B8' }}
          tickFormatter={v => fmtAmount(Number(v))}
          width={52}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          tick={{ fontSize: 11, fill: '#D97706' }}
          unit="%"
          width={40}
        />
        <Tooltip
          formatter={(v: number, name: string) =>
            name === 'payables_to_spend_pct' ? `${v}%` : fmtAmount(v)
          }
        />
        <Line yAxisId="left" type="monotone" dataKey="balance" name="Payables" stroke="#2563EB" strokeWidth={2} dot={false} />
        <Line yAxisId="left" type="monotone" dataKey="procurement_spend" name="Procurement spend" stroke="#64748B" strokeWidth={1.5} strokeDasharray="4 4" dot={false} />
        <Line yAxisId="right" type="monotone" dataKey="payables_to_spend_pct" name="Purchases %" stroke="#D97706" strokeWidth={2} dot={{ r: 2 }} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}


