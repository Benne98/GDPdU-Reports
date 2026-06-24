/**
 * OutliersTab — GL outlier explorer (Journal Agent, Phase 1).
 *
 * Fetches, once per period/entity/statement, the all-history monthly value series
 * for each material GL account together with a per-point mean-residual z-score and
 * per-account stats (mean / sample std / n). A sensitivity slider (σ, default 1.0)
 * re-thresholds flagged points (|z| >= σ) entirely CLIENT-side — moving the slider
 * never triggers a refetch. A selected account is plotted as a ComposedChart with a
 * mean line, a mean ± σ·std band, and a scatter overlay of the flagged months.
 *
 * The z-score always runs on the MONTHLY series regardless of the page period grain
 * (a week has too few points for a stable mean/σ); the period only labels the view.
 *
 * All UI text is in English. Not yet wired into AnomalyDetectionPage (Phase 5).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ComposedChart, Line, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceArea, ReferenceLine,
} from 'recharts'
import {
  api,
  type AnomalyPeriodParams,
  type OutlierAccount,
  type OutlierPoint,
  type OutlierStatement,
  type OutliersResponse,
} from '../../lib/api'
import FilterableDataTable, {
  type FilterableColumn,
} from '../sales/operational/FilterableDataTable'
import { AnomaliesLoading } from './AnomaliesPanel'

// ─── Helpers ───────────────────────────────────────────────────────────────────

function fmtKEur(v: number): string {
  const sign = v < 0 ? '-' : ''
  return `${sign}${Math.abs(v).toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 1 })}`
}

/** An account with its flagged points pre-computed for the current σ. */
interface FlaggedAccount extends OutlierAccount {
  flags: OutlierPoint[]
}

// ─── Selected-account chart ──────────────────────────────────────────────────

function OutlierChart({ account, sigma }: { account: FlaggedAccount; sigma: number }) {
  const { stats } = account
  const band = sigma * stats.std_keur
  const upper = stats.mean_keur + band
  const lower = stats.mean_keur - band

  const data = account.series.map(p => ({
    label: p.label,
    value: p.value_keur,
    flagged: Math.abs(p.z) >= sigma ? p.value_keur : null,
    z: p.z,
  }))

  const firstLabel = data[0]?.label
  const lastLabel = data[data.length - 1]?.label

  return (
    <div className="rounded-xl border p-4" style={{ background: '#FFFFFF', borderColor: '#E2E8F0' }}>
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h4 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>
          {account.account_name || account.gl_account_id}
        </h4>
        <span className="text-[11px]" style={{ color: '#94A3B8' }}>
          {account.gl_account_id} · {account.entity_prefix} · {account.statement.toUpperCase()}
        </span>
        <span className="ml-auto text-[11px]" style={{ color: '#64748B' }}>
          mean {fmtKEur(stats.mean_keur)} · std {fmtKEur(stats.std_keur)} kEUR · n={stats.n}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#94A3B8' }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 10, fill: '#94A3B8' }} width={56}
                 tickFormatter={(v: number) => fmtKEur(v)} />
          <Tooltip
            formatter={(v: number | string, name: string) =>
              [typeof v === 'number' ? `${fmtKEur(v)} kEUR` : v, name]}
            labelStyle={{ color: '#1E3A5F', fontWeight: 600 }}
            contentStyle={{ fontSize: 11, borderColor: '#E2E8F0' }}
          />
          {/* mean ± σ·std band */}
          {stats.std_keur > 0 && firstLabel != null && lastLabel != null && (
            <ReferenceArea
              x1={firstLabel} x2={lastLabel} y1={lower} y2={upper}
              fill="#3B82F6" fillOpacity={0.08} stroke="none"
              ifOverflow="extendDomain"
            />
          )}
          <ReferenceLine y={stats.mean_keur} stroke="#3B82F6" strokeDasharray="4 4"
                         strokeOpacity={0.7} />
          <Line type="monotone" dataKey="value" name="Value"
                stroke="#1E3A5F" strokeWidth={1.6} dot={false} isAnimationActive={false} />
          <Scatter dataKey="flagged" name="Outlier" fill="#DC2626"
                   isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="mt-2 text-[10px]" style={{ color: '#94A3B8' }}>
        Band = mean ± {sigma.toFixed(1)}σ. Red points are months with |z| ≥ {sigma.toFixed(1)}.
        Outliers always use the monthly series regardless of the page period grain.
      </p>
    </div>
  )
}

// ─── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  periodParams: AnomalyPeriodParams
  entity?: string
  statement?: OutlierStatement
}

// ─── Component ───────────────────────────────────────────────────────────────────

export default function OutliersTab({ periodParams, entity, statement = 'all' }: Props) {
  const [data, setData] = useState<OutliersResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sigma, setSigma] = useState(1.0)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const loadId = useRef(0)

  // Fetch once per period/entity/statement — the σ slider does NOT refetch.
  useEffect(() => {
    let cancelled = false
    const id = ++loadId.current
    setLoading(true)
    setError(null)

    api.financialsOutliers(periodParams, entity, statement)
      .then(res => {
        if (cancelled || id !== loadId.current) return
        setData(res)
        setSigma(res.meta.sigma_default ?? 1.0)
        setSelectedId(res.accounts[0]?.gl_account_id ?? null)
      })
      .catch((e: unknown) => {
        if (cancelled || id !== loadId.current) return
        setError(e instanceof Error ? e.message : 'Failed to load outliers')
      })
      .finally(() => {
        if (!cancelled && id === loadId.current) setLoading(false)
      })

    return () => { cancelled = true }
  }, [periodParams, entity, statement])

  // Re-threshold flags client-side whenever σ (or the data) changes — no refetch.
  const accounts: FlaggedAccount[] = useMemo(
    () =>
      (data?.accounts ?? []).map(a => ({
        ...a,
        flags: a.series.filter(p => Math.abs(p.z) >= sigma),
      })),
    [data, sigma],
  )

  const totalFlags = useMemo(
    () => accounts.reduce((sum, a) => sum + a.flags.length, 0),
    [accounts],
  )

  const selected = useMemo(
    () => accounts.find(a => a.gl_account_id === selectedId) ?? accounts[0] ?? null,
    [accounts, selectedId],
  )

  const sigmaMin = data?.meta.sigma_min ?? 0.5
  const sigmaMax = data?.meta.sigma_max ?? 3.0

  const columns: FilterableColumn<FlaggedAccount>[] = useMemo(() => [
    {
      id: 'account',
      label: 'Account',
      getValue: a => `${a.account_name} (${a.gl_account_id})`,
      sortValue: a => a.gl_account_id,
      render: a => (
        <button
          type="button"
          className="text-left hover:underline"
          style={{ color: a.gl_account_id === selected?.gl_account_id ? '#1E3A5F' : '#334155',
                   fontWeight: a.gl_account_id === selected?.gl_account_id ? 600 : 400 }}
          onClick={() => setSelectedId(a.gl_account_id)}
        >
          <span className="block truncate max-w-[260px]">{a.account_name || a.gl_account_id}</span>
          <span className="text-[10px]" style={{ color: '#94A3B8' }}>{a.gl_account_id}</span>
        </button>
      ),
    },
    { id: 'entity', label: 'Entity', getValue: a => a.entity_prefix },
    { id: 'statement', label: 'Type', getValue: a => a.statement.toUpperCase() },
    { id: 'level_2', label: 'L2', getValue: a => a.level_2 },
    {
      id: 'mean', label: 'Mean (kEUR)', align: 'right',
      getValue: a => fmtKEur(a.stats.mean_keur),
      sortValue: a => a.stats.mean_keur,
    },
    {
      id: 'std', label: 'Std (kEUR)', align: 'right',
      getValue: a => fmtKEur(a.stats.std_keur),
      sortValue: a => a.stats.std_keur,
    },
    {
      id: 'flags', label: 'Flagged', align: 'right',
      getValue: a => String(a.flags.length),
      sortValue: a => a.flags.length,
      enableSelect: false,
      render: a => (
        <span
          className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold"
          style={a.flags.length > 0
            ? { background: 'rgba(220,38,38,0.10)', color: '#B91C1C' }
            : { background: '#F1F5F9', color: '#94A3B8' }}
        >
          {a.flags.length}
        </span>
      ),
    },
  ], [selected?.gl_account_id])

  // Default sort by flagged count (desc): pre-sort rows so the most-flagged surface.
  const sortedRows = useMemo(
    () => [...accounts].sort((a, b) => b.flags.length - a.flags.length),
    [accounts],
  )

  if (loading) return <AnomaliesLoading compact={false} />

  if (error) {
    return (
      <div
        className="rounded-xl px-5 py-4 text-xs"
        style={{ background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(220,38,38,0.3)', color: '#991B1B' }}
        role="alert"
      >
        <span className="font-semibold">Outliers unavailable — </span>{error}
      </div>
    )
  }

  if (!data || data.accounts.length === 0) {
    return (
      <div
        className="rounded-xl px-5 py-4 text-center"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
      >
        <p className="text-xs font-medium" style={{ color: '#64748B' }}>
          No material accounts to analyse for this period.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Sensitivity slider — re-thresholds flags live (no refetch) */}
      <div
        className="rounded-xl border px-5 py-3 flex flex-wrap items-center gap-4"
        style={{ background: '#FFFFFF', borderColor: '#E2E8F0' }}
      >
        <div className="flex items-center gap-3">
          <label htmlFor="outlier-sigma" className="text-xs font-semibold" style={{ color: '#64748B' }}>
            Sensitivity (σ)
          </label>
          <input
            id="outlier-sigma"
            type="range"
            min={sigmaMin}
            max={sigmaMax}
            step={0.1}
            value={sigma}
            onChange={e => setSigma(Number(e.target.value))}
            className="w-48"
          />
          <span className="text-xs font-mono w-10 text-right" style={{ color: '#1E3A5F' }}>
            {sigma.toFixed(1)}σ
          </span>
        </div>
        <span className="text-xs" style={{ color: '#64748B' }}>
          <span className="font-semibold" style={{ color: '#B91C1C' }}>{totalFlags}</span>
          {' '}flagged month{totalFlags === 1 ? '' : 's'} across{' '}
          {accounts.length} account{accounts.length === 1 ? '' : 's'}
        </span>
        <span className="ml-auto text-[11px]" style={{ color: '#94A3B8' }}>
          {data.period.label} · {data.entity} · z-score on monthly series
        </span>
      </div>

      {/* Selected account chart */}
      {selected && <OutlierChart account={selected} sigma={sigma} />}

      {/* Account list — sorted by flagged count desc; click an account to chart it */}
      <FilterableDataTable<FlaggedAccount>
        columns={columns}
        rows={sortedRows}
        maxHeight={360}
        className="mt-0"
        emptyMessage="No accounts"
      />
    </div>
  )
}
