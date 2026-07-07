import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ReceivablesGeoRow } from '../../../lib/api'
import { fmtAmount } from '../../../lib/fmt'

export default function ReceivablesGeoPanel({ rows }: { rows: ReceivablesGeoRow[] }) {
  if (!rows.length) {
    return (
      <div className="h-[308px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No geographic data
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={308}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 11, fill: '#94A3B8' }} tickFormatter={v => fmtAmount(Number(v))} />
        <YAxis type="category" dataKey="country" width={44} tick={{ fontSize: 11, fill: '#64748B' }} />
        <Tooltip
          formatter={(v: number) => fmtAmount(v)}
          labelFormatter={l => `Country: ${l}`}
        />
        <Bar dataKey="before_due" name="Not yet due" stackId="a" fill="#3B82F6" radius={[0, 0, 0, 0]} />
        <Bar dataKey="overdue" name="Overdue" stackId="a" fill="#F59E0B" radius={[0, 4, 4, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}
