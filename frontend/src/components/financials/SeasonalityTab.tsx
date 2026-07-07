/**
 * SeasonalityTab — GL seasonality explorer (Journal Agent, Phase 2).
 *
 * Fetches, once per period/entity/statement, the all-history monthly value series for
 * each material GL account ADDITIVELY decomposed into trend + seasonal_index + residual,
 * with a per-point residual z-score, the 12 seasonal factors, and per-account stats
 * (resid_std / n). A sensitivity slider (σ, default 1.0) re-thresholds off-season months
 * (|z| >= σ) entirely CLIENT-side — moving the slider never triggers a refetch. A
 * selected account is plotted as a ComposedChart of actual vs expected with an
 * expected ± σ·resid_std band and a scatter overlay of the flagged (off-season) months,
 * plus a small bar chart of the 12 seasonal factors.
 *
 * The decomposition always runs on the MONTHLY series regardless of the page period grain
 * (calendar-month seasonality is the whole point); the period only labels the view.
 * Accounts with < min_years of history render an "insufficient history" state.
 *
 * Additive (not multiplicative) decomposition because GL values can be zero/negative.
 * All UI text is in English. Not yet wired into AnomalyDetectionPage (Phase 5).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ComposedChart, Line, Scatter, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, Cell, Legend,
} from 'recharts'
import {
  api,
  type AnomalyPeriodParams,
  type SeasonalityAccount,
  type SeasonalityPoint,
  type SeasonalityStatement,
  type SeasonalityResponse,
} from '../../lib/api'
import FilterableDataTable, {
  type FilterableColumn,
} from '../sales/operational/FilterableDataTable'
import { AnomaliesLoading } from './AnomaliesPanel'

// ─── Helpers ───────────────────────────────────────────────────────────────────

const MONTH_ABBR = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

function fmtKEur(v: number): string {
  const sign = v < 0 ? '-' : ''
  return `${sign}${Math.abs(v).toLocaleString('de-DE', { minimumFractionDigits: 0, maximumFractionDigits: 1 })}`
}

/** An account with its off-season points pre-computed for the current σ. */
interface FlaggedAccount extends SeasonalityAccount {
  flags: SeasonalityPoint[]
}

/** A point is off-season when it has a finite z and |z| >= σ (and history suffices). */
function isOffSeason(p: SeasonalityPoint, sigma: number): boolean {
  return p.z != null && Math.abs(p.z) >= sigma
}

// ─── Selected-account charts ──────────────────────────────────────────────────

function SeasonalityChart({ account, sigma }: { account: FlaggedAccount; sigma: number }) {
  const { stats } = account
  const band = sigma * stats.resid_std_keur

  // Per-point expected ± σ·resid_std band drawn via a banded area when expected is
  // finite; flagged off-season months overlaid as a scatter.
  const data = account.series.map(p => ({
    label: p.label,
    actual: p.actual_keur,
    expected: p.expected_keur,
    bandLow: p.expected_keur != null ? p.expected_keur - band : null,
    bandHigh: p.expected_keur != null ? p.expected_keur + band : null,
    flagged: !account.insufficient_history && isOffSeason(p, sigma) ? p.actual_keur : null,
    z: p.z,
  }))

  return (
    <div className="rounded-xl border p-4" style={{ background: '#FFFFFF', borderColor: '#E2E8F0' }}>
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h4 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>
          {account.account_name || account.gl_account_id}
        </h4>
        <span className="text-[12px]" style={{ color: '#94A3B8' }}>
          {account.gl_account_id} · {account.entity_prefix} · {account.statement.toUpperCase()}
        </span>
        <span className="ml-auto text-[12px]" style={{ color: '#64748B' }}>
          resid std {fmtKEur(stats.resid_std_keur)} kEUR · n={stats.n}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={308}>
        <ComposedChart data={data} margin={{ top: 10, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#94A3B8' }} interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 11, fill: '#94A3B8' }} width={56}
                 tickFormatter={(v: number) => fmtKEur(v)} />
          <Tooltip
            formatter={(v: number | string, name: string) =>
              [typeof v === 'number' ? `${fmtKEur(v)} kEUR` : v, name]}
            labelStyle={{ color: '#1E3A5F', fontWeight: 600 }}
            contentStyle={{ fontSize: 12, borderColor: '#E2E8F0' }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {/* expected ± σ·resid_std band drawn as two dashed boundary lines that follow
              the per-point expected value (band width = σ·resid_std). */}
          <Line type="monotone" dataKey="bandHigh" name={`+${sigma.toFixed(1)}σ`}
                stroke="#93C5FD" strokeWidth={1} strokeDasharray="3 3" dot={false}
                isAnimationActive={false} connectNulls />
          <Line type="monotone" dataKey="bandLow" name={`-${sigma.toFixed(1)}σ`}
                stroke="#93C5FD" strokeWidth={1} strokeDasharray="3 3" dot={false}
                isAnimationActive={false} connectNulls legendType="none" />
          <Line type="monotone" dataKey="expected" name="Expected"
                stroke="#3B82F6" strokeWidth={1.4} strokeDasharray="5 3" dot={false}
                isAnimationActive={false} connectNulls />
          <Line type="monotone" dataKey="actual" name="Actual"
                stroke="#1E3A5F" strokeWidth={1.6} dot={false} isAnimationActive={false} />
          <Scatter dataKey="flagged" name="Off-season" fill="#DC2626"
                   isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="mt-2 text-[10px]" style={{ color: '#94A3B8' }}>
        Expected = trend + seasonal index. Band = expected ± {sigma.toFixed(1)}σ (σ on the
        residual std). Red points are off-season months with |z| ≥ {sigma.toFixed(1)} — a
        flat month where a peak was expected, or an unexpected spike. Seasonality always
        uses the monthly series regardless of the page period grain.
      </p>
    </div>
  )
}

function SeasonalFactorsChart({ account }: { account: FlaggedAccount }) {
  const data = account.month_index.map(f => ({
    label: MONTH_ABBR[f.month - 1] ?? String(f.month),
    factor: f.seasonal_index,
  }))
  return (
    <div className="rounded-xl border p-4" style={{ background: '#FFFFFF', borderColor: '#E2E8F0' }}>
      <h4 className="mb-2 text-sm font-semibold" style={{ color: '#1E3A5F' }}>
        Seasonal factors (kEUR, sum = 0)
      </h4>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 6, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#94A3B8' }} />
          <YAxis tick={{ fontSize: 11, fill: '#94A3B8' }} width={56}
                 tickFormatter={(v: number) => fmtKEur(v)} />
          <Tooltip
            formatter={(v: number | string) =>
              [typeof v === 'number' ? `${fmtKEur(v)} kEUR` : v, 'Seasonal index']}
            labelStyle={{ color: '#1E3A5F', fontWeight: 600 }}
            contentStyle={{ fontSize: 12, borderColor: '#E2E8F0' }}
          />
          <Bar dataKey="factor" isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.factor >= 0 ? '#3B82F6' : '#F59E0B'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="mt-2 text-[10px]" style={{ color: '#94A3B8' }}>
        A large positive factor is a recurring seasonal peak; a large negative factor a
        recurring trough. Factors are normalised to sum to 0 over the 12 months.
      </p>
    </div>
  )
}

// ─── Props ─────────────────────────────────────────────────────────────────────

interface Props {
  periodParams: AnomalyPeriodParams
  entity?: string
  statement?: SeasonalityStatement
}

// ─── Component ───────────────────────────────────────────────────────────────────

export default function SeasonalityTab({ periodParams, entity, statement = 'all' }: Props) {
  const [data, setData] = useState<SeasonalityResponse | null>(null)
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

    api.financialsSeasonality(periodParams, entity, statement)
      .then(res => {
        if (cancelled || id !== loadId.current) return
        setData(res)
        setSigma(res.meta.sigma_default ?? 1.0)
        setSelectedId(res.accounts[0]?.gl_account_id ?? null)
      })
      .catch((e: unknown) => {
        if (cancelled || id !== loadId.current) return
        setError(e instanceof Error ? e.message : 'Failed to load seasonality')
      })
      .finally(() => {
        if (!cancelled && id === loadId.current) setLoading(false)
      })

    return () => { cancelled = true }
  }, [periodParams, entity, statement])

  // Re-threshold off-season flags client-side whenever σ (or the data) changes — no
  // refetch. Accounts with insufficient history never flag.
  const accounts: FlaggedAccount[] = useMemo(
    () =>
      (data?.accounts ?? []).map(a => ({
        ...a,
        flags: a.insufficient_history
          ? []
          : a.series.filter(p => isOffSeason(p, sigma)),
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
  const minYears = data?.meta.min_years ?? 2

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
      id: 'resid_std', label: 'Resid std (kEUR)', align: 'right',
      getValue: a => fmtKEur(a.stats.resid_std_keur),
      sortValue: a => a.stats.resid_std_keur,
    },
    {
      id: 'flags', label: 'Off-season', align: 'right',
      getValue: a => (a.insufficient_history ? 'n/a' : String(a.flags.length)),
      sortValue: a => a.flags.length,
      enableSelect: false,
      render: a =>
        a.insufficient_history ? (
          <span
            className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold"
            style={{ background: '#F1F5F9', color: '#94A3B8' }}
            title={`Needs ≥ ${minYears} years of history`}
          >
            insufficient history
          </span>
        ) : (
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
  ], [selected?.gl_account_id, minYears])

  // Default sort by off-season count (desc): pre-sort rows so the most-flagged surface.
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
        <span className="font-semibold">Seasonality unavailable — </span>{error}
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
      {/* Sensitivity slider — re-thresholds off-season flags live (no refetch) */}
      <div
        className="rounded-xl border px-5 py-3 flex flex-wrap items-center gap-4"
        style={{ background: '#FFFFFF', borderColor: '#E2E8F0' }}
      >
        <div className="flex items-center gap-3">
          <label htmlFor="seasonality-sigma" className="text-xs font-semibold" style={{ color: '#64748B' }}>
            Sensitivity (σ)
          </label>
          <input
            id="seasonality-sigma"
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
          {' '}off-season month{totalFlags === 1 ? '' : 's'} across{' '}
          {accounts.length} account{accounts.length === 1 ? '' : 's'}
        </span>
        <span className="ml-auto text-[12px]" style={{ color: '#94A3B8' }}>
          {data.period.label} · {data.entity} · additive decomposition on monthly series
        </span>
      </div>

      {/* Selected account: actual vs expected + factors (or insufficient-history state) */}
      {selected && selected.insufficient_history ? (
        <div
          className="rounded-xl px-5 py-6 text-center"
          style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
        >
          <p className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>
            {selected.account_name || selected.gl_account_id}
          </p>
          <p className="mt-1 text-xs" style={{ color: '#64748B' }}>
            Insufficient history for a seasonal pattern — at least {minYears} years are
            needed so each calendar month is observed in more than one year.
          </p>
        </div>
      ) : selected ? (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <SeasonalityChart account={selected} sigma={sigma} />
          </div>
          <div className="xl:col-span-1">
            <SeasonalFactorsChart account={selected} />
          </div>
        </div>
      ) : null}

      {/* Account list — sorted by off-season count desc; click an account to chart it */}
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
