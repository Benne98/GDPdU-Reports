import { FA_BORDER, FA_HEADER_BG, FA_NAVY, FA_SNAPSHOT_COL_BG, faHeaderCellStyle } from '../fixed-assets/fixedAssetsTableTheme'

type Props = {
  colLabel: string
}

const SCHEMA_ROWS = [
  { label: 'Leasing liabilities', lt1: '—', mid: '—', gt5: '—', total: '—' },
  { label: 'Rental obligations', lt1: '—', mid: '—', gt5: '—', total: '—' },
  { label: 'Off-balance sheet liabilities', lt1: '—', mid: '—', gt5: '—', total: '—', bold: true },
]

export default function OffBalanceSheetSchema({ colLabel }: Props) {
  const headerStyle = faHeaderCellStyle()

  return (
    <section
      className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden"
    >
      <div className="px-4 py-3 border-b border-slate-100">
        <h3 className="text-sm font-semibold text-slate-900">Off-balance sheet liabilities</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Schema preview — data will be extracted from leasing / rental contract PDFs (not yet available).
        </p>
      </div>
      <div className="p-4 overflow-x-auto">
        <table className="w-full border-collapse text-xs" style={{ minWidth: 560 }}>
          <thead>
            <tr style={{ borderBottom: `1px solid ${FA_BORDER}`, background: FA_HEADER_BG }}>
              <th className="px-2 py-1.5 text-left" style={{ ...headerStyle, fontSize: '0.68rem' }}>EURk</th>
              <th className="px-2 py-1.5 text-right" style={{ ...headerStyle, fontSize: '0.68rem', background: FA_SNAPSHOT_COL_BG }}>
                {colLabel}
              </th>
              <th className="px-2 py-1.5 text-right" style={{ ...headerStyle, fontSize: '0.68rem' }} colSpan={3}>
                thereof
              </th>
            </tr>
            <tr style={{ borderBottom: `1px solid ${FA_BORDER}`, background: FA_HEADER_BG }}>
              <th />
              <th />
              <th className="px-2 py-1 text-right text-xs font-medium" style={{ color: FA_NAVY }}>&lt; 1 year</th>
              <th className="px-2 py-1 text-right text-xs font-medium" style={{ color: FA_NAVY }}>1–5 years</th>
              <th className="px-2 py-1 text-right text-xs font-medium" style={{ color: FA_NAVY }}>&gt; 5 years</th>
            </tr>
          </thead>
          <tbody>
            {SCHEMA_ROWS.map(row => (
              <tr
                key={row.label}
                style={{
                  borderBottom: row.bold ? `2px solid ${FA_BORDER}` : undefined,
                  fontWeight: row.bold ? 600 : 400,
                }}
              >
                <td className="px-2 py-1.5" style={{ color: row.bold ? FA_NAVY : '#334155' }}>{row.label}</td>
                <td className="px-2 py-1.5 text-right tabular-nums" style={{ background: FA_SNAPSHOT_COL_BG }}>{row.total}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{row.lt1}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{row.mid}</td>
                <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{row.gt5}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
