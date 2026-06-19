import { useEffect, useState } from 'react'
import { api, type ProvisionRollforwardResponse } from '../../../../lib/api'
import { fmtKpi } from '../../../../lib/fmt'
import { useChartLoadReporter } from '../../../../hooks/useChartLoadReporter'

type Props = {
  year: number
  month: number
  entity?: string
}

export default function ProvisionRollforwardSection({ year, month, entity }: Props) {
  const [data, setData] = useState<ProvisionRollforwardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    void api
      .financialsBsProvisionRollforward(year, month, entity)
      .then(res => {
        if (!cancelled) {
          setData(res)
          setLoading(false)
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setData(null)
          setError(e instanceof Error ? e.message : 'Could not load provision roll-forward')
          setLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [year, month, entity])

  useChartLoadReporter('fin-bs-provisions', loading, error)

  if (loading) {
    return (
      <div
        id="provisions"
        className="rounded-xl p-8 text-center text-sm mt-6 scroll-mt-24"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}
      >
        Loading provision roll-forward…
      </div>
    )
  }

  if (error) {
    return (
      <div
        id="provisions"
        className="rounded-xl p-6 text-center text-sm mt-6 scroll-mt-24"
        style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}
      >
        {error}
      </div>
    )
  }

  const rows = data?.rows ?? []
  if (!rows.length) return null

  const fmt = (v: number) => fmtKpi(v)

  return (
    <div
      id="provisions"
      className="rounded-xl overflow-hidden mt-6 scroll-mt-24"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div className="px-4 pt-4 pb-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
        <h3 className="text-sm font-semibold" style={{ color: '#111827' }}>
          Provision roll-forward
        </h3>
        <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
          GL-derived bridge for Provisions &amp; accruals — amounts in EURk
        </p>
      </div>
      <div className="overflow-x-auto px-4 pb-4">
        <table className="w-full border-collapse text-xs mt-3">
          <thead>
            <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
              {['Level 3', 'Opening', 'Additions', 'Releases', 'FX / other', 'Closing'].map(h => (
                <th
                  key={h}
                  className="px-2 py-2 text-right font-semibold first:text-left"
                  style={{ color: '#475569' }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={`${r.level_3}-${r.level_4}`} style={{ borderBottom: '1px solid #E2E8F0' }}>
                <td className="px-2 py-1.5 text-left font-medium" style={{ color: '#111827' }}>
                  {r.level_3}
                  {r.level_4 ? (
                    <span className="block text-[0.65rem] font-normal text-slate-500">{r.level_4}</span>
                  ) : null}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmt(r.opening_balance)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmt(r.additions)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmt(r.releases)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums">{fmt(r.fx_adjustment)}</td>
                <td className="px-2 py-1.5 text-right tabular-nums font-semibold">{fmt(r.closing_balance)}</td>
              </tr>
            ))}
            {data?.totals && (
              <tr style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
                <td className="px-2 py-2 text-left font-semibold">Total</td>
                <td className="px-2 py-2 text-right font-semibold tabular-nums">
                  {fmt(data.totals.opening_balance)}
                </td>
                <td colSpan={3} />
                <td className="px-2 py-2 text-right font-semibold tabular-nums">
                  {fmt(data.totals.closing_balance)}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
