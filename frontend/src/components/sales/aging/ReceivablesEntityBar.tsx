import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ReceivablesEntityRow } from '../../../lib/api'
import { fmtAmount } from '../../../lib/fmt'

export default function ReceivablesEntityBar({ rows }: { rows: ReceivablesEntityRow[] }) {
  if (!rows.length) {
    return (
      <div className="h-[220px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No entity breakdown
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={rows} margin={{ top: 8, right: 16, left: 4, bottom: 8 }}>
        <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" vertical={false} />
        <XAxis dataKey="entity_code" tick={{ fontSize: 10, fill: '#64748B' }} />
        <YAxis tick={{ fontSize: 10, fill: '#94A3B8' }} tickFormatter={v => fmtAmount(Number(v))} width={56} />
        <Tooltip formatter={(v: number) => fmtAmount(v)} />
        <Bar dataKey="balance" radius={[6, 6, 0, 0]} maxBarSize={48}>
          {rows.map((_, i) => (
            <Cell key={i} fill={i % 2 === 0 ? '#1E3A5F' : '#3B82F6'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
