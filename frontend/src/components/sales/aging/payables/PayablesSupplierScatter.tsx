import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import type { PayablesSupplierScatterRow } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'

export default function PayablesSupplierScatter({
  data,
  selectedSupplier,
  onSelect,
}: {
  data: PayablesSupplierScatterRow[]
  selectedSupplier: string | null
  onSelect: (name: string | null) => void
}) {
  if (!data.length) {
    return (
      <div className="h-[308px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
        No scatter data
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={308}>
      <ScatterChart margin={{ top: 12, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" />
        <XAxis
          type="number"
          dataKey="balance"
          name="Balance"
          tick={{ fontSize: 11, fill: '#94A3B8' }}
          tickFormatter={v => fmtAmount(Number(v))}
        />
        <YAxis
          type="number"
          dataKey="overdue_pct"
          name="Overdue %"
          tick={{ fontSize: 11, fill: '#94A3B8' }}
          unit="%"
        />
        <ZAxis type="number" dataKey="balance" range={[40, 400]} />
        <Tooltip
          cursor={{ strokeDasharray: '3 3' }}
          formatter={(v: number, name: string) =>
            name === 'Overdue %' ? `${v}%` : fmtAmount(v)
          }
          labelFormatter={(_, p) => p?.[0]?.payload?.supplier_name ?? ''}
        />
        <Scatter
          data={data}
          fill="#3B82F6"
          fillOpacity={0.75}
          onClick={p => {
            const name = (p as { payload?: { supplier_name?: string } })?.payload?.supplier_name
            onSelect(name === selectedSupplier ? null : name ?? null)
          }}
        />
      </ScatterChart>
    </ResponsiveContainer>
  )
}



