import type { ReceivablesSummaryRow } from '../../../lib/api'
import { fmtAmount } from '../../../lib/fmt'

export default function ReceivablesAgingSummaryTable({ rows }: { rows: ReceivablesSummaryRow[] }) {
  if (!rows.length) {
    return <p className="text-sm py-6 text-center" style={{ color: '#94A3B8' }}>No summary data</p>
  }
  return (
    <div className="overflow-x-auto rounded-lg border" style={{ borderColor: '#E2E8F0' }}>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ background: '#F8FAFC' }}>
            <th className="text-left px-3 py-2 font-semibold" style={{ color: '#64748B' }}>Bucket</th>
            <th className="text-right px-3 py-2 font-semibold" style={{ color: '#64748B' }}>Documents</th>
            <th className="text-right px-3 py-2 font-semibold" style={{ color: '#64748B' }}>Amount</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr
              key={r.band}
              style={{
                borderTop: '1px solid #E2E8F0',
                background: r.band === 'total' ? '#EFF6FF' : undefined,
              }}
            >
              <td className="px-3 py-2 font-medium" style={{ color: r.band === 'total' ? '#1E3A5F' : '#334155' }}>
                {r.label}
              </td>
              <td className="px-3 py-2 text-right tabular-nums" style={{ color: '#64748B' }}>
                {r.document_count}
              </td>
              <td className="px-3 py-2 text-right tabular-nums font-semibold" style={{ color: '#1E3A5F' }}>
                {fmtAmount(r.amount)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
