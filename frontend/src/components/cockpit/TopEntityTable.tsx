import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import {
  api,
  type SalesTopEntitiesColLabels,
  type SalesTopEntity,
} from '../../lib/api'
import type { PeriodSelection } from '../../lib/periodSelection'
import { periodAnchorYearMonth } from '../../lib/periodSelection'
import { fmtKpi } from '../../lib/fmt'
import { stripLegalForm } from '../../lib/stripLegalForm'
import { useChartLoadReporter } from '../../hooks/useChartLoadReporter'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureTopEntitiesSnapshot } from '../action-notes/captureTopEntities'

type Kind = 'customer' | 'supplier'

interface TopEntityTableProps {
  period: PeriodSelection
  entity?: string
  kind: Kind
  title: string
  subtitle: string
  /** Pin candidate id + capture component label. */
  pinId: string
  pinLabel: string
  componentName: string
  /** Max rows shown in the table body. */
  limit?: number
}

const DEFAULT_COL_LABELS: SalesTopEntitiesColLabels = {
  cm: 'Current month',
  pm: 'Prior month',
  py_cm: 'Prior year',
  ytd: 'YTD',
}

const TH: React.CSSProperties = {
  color: '#94A3B8',
  background: '#F8FAFC',
  fontSize: '0.7rem',
  position: 'sticky',
  top: 0,
  zIndex: 1,
}

const HL: React.CSSProperties = {
  background: 'rgba(148,163,184,0.18)',
  borderLeft: '1px solid #CBD5E1',
  borderRight: '1px solid #CBD5E1',
}

function deltaCell(v: number | null | undefined) {
  if (v == null) return <span style={{ color: '#94A3B8' }}>—</span>
  const color = v >= 0 ? '#059669' : '#DC2626'
  return <span style={{ color }}>{fmtKpi(v)}</span>
}

export default function TopEntityTable({
  period,
  entity,
  kind,
  title,
  subtitle,
  pinId,
  pinLabel,
  componentName,
  limit = 25,
}: TopEntityTableProps) {
  const [rows, setRows] = useState<SalesTopEntity[]>([])
  const [colLabels, setColLabels] = useState<SalesTopEntitiesColLabels>(DEFAULT_COL_LABELS)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ent = entity === 'all' ? undefined : entity
  const filters = useMemo(() => (ent ? { entity: [ent] } : undefined), [ent])
  const anchor = periodAnchorYearMonth(period)
  const notesCtx = useOptionalActionNotesContext()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    const opts =
      period.grain === 'week'
        ? { period_grain: 'week' as const, iso_year: period.isoYear, iso_week: period.isoWeek, limit: 5000 }
        : { period_grain: 'month' as const, limit: 5000 }

    api
      .salesTopEntities(anchor.year, anchor.month, kind, 'cm', 'invoiced', filters, opts)
      .then(res => {
        if (cancelled) return
        setRows(res.rows ?? [])
        setColLabels(res.col_labels ?? DEFAULT_COL_LABELS)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setRows([])
        setError(e instanceof Error ? e.message : `Failed to load top ${kind}s`)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [anchor.year, anchor.month, period, kind, filters])

  // ─── Pin registration ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!notesCtx) return
    if (!rows.length) {
      notesCtx.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: pinLabel,
      description: subtitle,
      capture: () => captureTopEntitiesSnapshot(rows, colLabels, componentName),
      viewState: { tab: 'overview' },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, rows, colLabels, pinId, pinLabel, subtitle, componentName])

  const timedOut = useChartLoadReporter(pinId, loading, error)

  const totals = useMemo(() => {
    return rows.reduce(
      (acc, r) => {
        acc.pm += r.pm
        acc.cm += r.cm
        acc.py_cm += r.py_cm
        acc.ytd += r.ytd
        return acc
      },
      { pm: 0, cm: 0, py_cm: 0, ytd: 0 },
    )
  }, [rows])

  if (error) {
    return (
      <div
        className="rounded-xl px-5 py-4 text-sm"
        style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)', color: '#DC2626' }}
      >
        ⚠ {error}
      </div>
    )
  }

  if (loading) {
    return (
      <div className="rounded-xl p-6 animate-pulse" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}>
        <div className="h-4 w-48 rounded mb-4" style={{ background: '#E2E8F0' }} />
        {[1, 2, 3, 4, 5, 6, 7].map(i => (
          <div key={i} className="h-8 rounded mb-1" style={{ background: '#F4F6F9' }} />
        ))}
      </div>
    )
  }

  if (timedOut) return null

  if (!rows.length) {
    return (
      <div
        className="rounded-xl px-6 py-12 text-center text-sm"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', color: '#94A3B8' }}
      >
        No {kind} data for this period.
      </div>
    )
  }

  const shown = rows.slice(0, limit)

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="rounded-xl overflow-hidden flex flex-col"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}
    >
      <div className="px-4 py-3" style={{ borderBottom: '1px solid #E2E8F0' }}>
        <h3 className="text-sm font-semibold" style={{ color: '#111827' }}>{title}</h3>
        <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
          {subtitle} · {colLabels.pm} · {colLabels.cm} · {colLabels.py_cm} · {colLabels.ytd} — kEUR
        </p>
      </div>

      <div className="overflow-x-auto" style={{ maxHeight: 520 }}>
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr style={{ borderBottom: '2px solid #E2E8F0' }}>
              <th className="px-2 py-2.5 text-left font-semibold tracking-wide uppercase whitespace-nowrap" style={TH}>
                #
              </th>
              <th className="px-2 py-2.5 text-left font-semibold tracking-wide whitespace-nowrap" style={TH}>
                {kind === 'supplier' ? 'Supplier' : 'Customer'}
              </th>
              <th className="px-2 py-2.5 text-right font-semibold tracking-wide whitespace-nowrap" style={TH}>
                {colLabels.pm}
              </th>
              <th
                className="px-2 py-2.5 text-right font-semibold tracking-wide whitespace-nowrap"
                style={{ ...TH, ...HL, color: '#1E3A5F' }}
              >
                {colLabels.cm}
              </th>
              <th className="px-2 py-2.5 text-right font-semibold tracking-wide whitespace-nowrap" style={TH}>
                Δ MoM
              </th>
              <th className="px-2 py-2.5 text-right font-semibold tracking-wide whitespace-nowrap" style={TH}>
                {colLabels.py_cm}
              </th>
              <th
                className="px-2 py-2.5 text-right font-semibold tracking-wide whitespace-nowrap"
                style={{ ...TH, ...HL, color: '#1E3A5F' }}
              >
                {colLabels.ytd}
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map(r => {
              const deltaMom = r.delta_cm_pm ?? r.cm - r.pm
              return (
                <tr key={`${r.rank}-${r.name}`} style={{ borderBottom: '1px solid #F1F5F9' }}>
                  <td className="px-2 py-2 text-left tabular-nums" style={{ color: '#94A3B8' }}>
                    {r.rank}
                  </td>
                  <td className="px-2 py-2 whitespace-nowrap" style={{ color: '#475569', maxWidth: 240 }}>
                    <span className="truncate block max-w-[240px]" title={r.name}>
                      {stripLegalForm(r.name)}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums" style={{ color: '#111827' }}>{fmtKpi(r.pm)}</td>
                  <td className="px-2 py-2 text-right tabular-nums font-semibold" style={{ color: '#111827', ...HL }}>
                    {fmtKpi(r.cm)}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums">{deltaCell(deltaMom)}</td>
                  <td className="px-2 py-2 text-right tabular-nums" style={{ color: '#475569' }}>{fmtKpi(r.py_cm)}</td>
                  <td className="px-2 py-2 text-right tabular-nums font-semibold" style={{ color: '#111827', ...HL }}>
                    {fmtKpi(r.ytd)}
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr style={{ background: '#F4F6F9', borderTop: '2px solid #E2E8F0' }}>
              <td className="px-2 py-2" />
              <td className="px-2 py-2 font-bold" style={{ color: '#111827' }}>
                Total ({rows.length})
              </td>
              <td className="px-2 py-2 text-right tabular-nums font-bold" style={{ color: '#111827' }}>{fmtKpi(totals.pm)}</td>
              <td className="px-2 py-2 text-right tabular-nums font-bold" style={{ color: '#111827', ...HL }}>{fmtKpi(totals.cm)}</td>
              <td className="px-2 py-2 text-right tabular-nums font-bold">{deltaCell(totals.cm - totals.pm)}</td>
              <td className="px-2 py-2 text-right tabular-nums font-bold" style={{ color: '#475569' }}>{fmtKpi(totals.py_cm)}</td>
              <td className="px-2 py-2 text-right tabular-nums font-bold" style={{ color: '#111827', ...HL }}>{fmtKpi(totals.ytd)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
      {rows.length > limit && (
        <div className="px-4 py-2 text-[12px]" style={{ color: '#94A3B8', borderTop: '1px solid #F1F5F9' }}>
          Showing top {limit} of {rows.length} {kind}s by current-period sales · totals reflect all {rows.length}.
        </div>
      )}
    </motion.div>
  )
}
