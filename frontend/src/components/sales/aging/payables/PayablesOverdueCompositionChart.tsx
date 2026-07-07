import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PayablesCompositionSegment } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'

const COLORS: Record<string, string> = {
  not_yet_due: '#3B82F6',
  overdue_1_30: '#60A5FA',
  overdue_31_60: '#93C5FD',
  overdue_61_90: '#FBBF24',
  overdue_91_180: '#F59E0B',
  overdue_over_180: '#D97706',
}

export default function PayablesOverdueCompositionChart({
  segments,
}: {
  segments: PayablesCompositionSegment[]
}) {
  const data = segments.filter(s => s.amount > 0)
  if (!data.length) {
    return (
      <div className="h-[242px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No composition data
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={242}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 11, fill: '#94A3B8' }} tickFormatter={v => fmtAmount(Number(v))} />
        <YAxis type="category" dataKey="label" width={120} tick={{ fontSize: 11, fill: '#64748B' }} />
        <Tooltip formatter={(v: number) => fmtAmount(v)} />
        <Bar dataKey="amount" radius={[0, 4, 4, 0]} maxBarSize={28}>
          {data.map(entry => (
            <Cell key={entry.band} fill={COLORS[entry.band] ?? '#3B82F6'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}



