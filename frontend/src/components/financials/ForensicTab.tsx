/**
 * ForensicTab — GL forensic explorer (Journal Agent, Phase 4).
 *
 * Fetches, once per period/entity, the forensic analysis results from the backend:
 *   1. Unexpected counter-accounts — pairs that appear rarely or for the first time
 *      in the selected period (novelty: 'new' | 'rare'), surfaced by a co-occurrence
 *      learner over the full GL history.
 *   2. Material / growing "Other" positions — balance-sheet and P&L line items whose
 *      GL account name contains a token like "other", "misc", "sonstige" and whose
 *      delta or absolute balance exceeds materiality thresholds.
 *   3. Suspicious booking texts — booking lines whose line_note matches a keyword
 *      watchlist (e.g. "correction", "reclassification", "manual", "adjustment").
 *
 * All UI text is in English. Not yet wired into AnomalyDetectionPage (Phase 5).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  type AnomalyPeriodParams,
  type CounterAccountRow,
  type ForensicResponse,
  type OtherPositionRow,
  type SuspiciousTextRow,
} from '../../lib/api'
import FilterableDataTable, {
  type FilterableColumn,
} from '../sales/operational/FilterableDataTable'
import { AnomaliesLoading } from './AnomaliesPanel'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtKEur(v: number): string {
  const sign = v < 0 ? '-' : ''
  return `${sign}${Math.abs(v).toLocaleString('de-DE', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 1,
  })}`
}

function fmtGrowth(v: number): string {
  const sign = v > 0 ? '+' : ''
  return `${sign}${(v * 100).toFixed(1)}%`
}

// ─── Novelty badge — red for 'new', amber for 'rare' ─────────────────────────

function NoveltyBadge({ novelty }: { novelty: 'new' | 'rare' }) {
  const isNew = novelty === 'new'
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
      style={
        isNew
          ? { background: 'rgba(220,38,38,0.12)', color: '#DC2626' }
          : { background: 'rgba(217,119,6,0.12)', color: '#D97706' }
      }
    >
      {isNew ? 'NEW' : 'RARE'}
    </span>
  )
}

// ─── Keyword highlight — wraps each match in a yellow-tinted span ─────────────

function HighlightedText({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword || !text) return <span>{text}</span>

  // Split the note on the keyword (case-insensitive) and interleave matches.
  const escapedKw = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escapedKw})`, 'gi'))

  return (
    <span>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase() ? (
          <mark
            key={i}
            style={{
              background: '#FEF3C7',
              color: '#92400E',
              borderRadius: '2px',
              padding: '0 2px',
            }}
          >
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </span>
  )
}

// ─── Section header ───────────────────────────────────────────────────────────

function SectionHeader({ title, count }: { title: string; count: number }) {
  return (
    <div
      className="flex items-center gap-3 px-4 py-2.5 rounded-t-xl border-b"
      style={{
        background: '#F8FAFC',
        borderColor: '#E2E8F0',
        borderTop: '1px solid #E2E8F0',
        borderLeft: '1px solid #E2E8F0',
        borderRight: '1px solid #E2E8F0',
      }}
    >
      <h3 className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>
        {title}
      </h3>
      <span
        className="text-[10px] font-semibold rounded-full px-2 py-0.5"
        style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
      >
        {count}
      </span>
    </div>
  )
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  periodParams: AnomalyPeriodParams
  entity?: string
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ForensicTab({ periodParams, entity }: Props) {
  const [data, setData] = useState<ForensicResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadId = useRef(0)

  // Fetch once per period/entity — forensic analysis is a heavy backend call.
  useEffect(() => {
    let cancelled = false
    const id = ++loadId.current
    setLoading(true)
    setError(null)

    api
      .financialsForensic(periodParams, entity)
      .then(res => {
        if (cancelled || id !== loadId.current) return
        setData(res)
      })
      .catch((e: unknown) => {
        if (cancelled || id !== loadId.current) return
        setError(e instanceof Error ? e.message : 'Failed to load forensic analysis')
      })
      .finally(() => {
        if (!cancelled && id === loadId.current) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [periodParams, entity])

  // ── Section 1: Unexpected counter-accounts ────────────────────────────────

  const counterAccountColumns: FilterableColumn<CounterAccountRow>[] = useMemo(
    () => [
      {
        id: 'account',
        label: 'Account',
        getValue: r => `${r.account_name} (${r.gl_account_id})`,
        sortValue: r => r.gl_account_id,
        render: r => (
          <div className="min-w-0">
            <span className="block truncate max-w-[200px] font-medium" style={{ color: '#1E3A5F' }}>
              {r.account_name || r.gl_account_id}
            </span>
            <span className="text-[10px]" style={{ color: '#94A3B8' }}>
              {r.gl_account_id}
            </span>
          </div>
        ),
      },
      {
        id: 'counter_account',
        label: 'Counter-Account',
        getValue: r => `${r.counter_account_name} (${r.counter_gl_account_id})`,
        sortValue: r => r.counter_gl_account_id,
        render: r => (
          <div className="min-w-0">
            <span className="block truncate max-w-[200px]" style={{ color: '#334155' }}>
              {r.counter_account_name || r.counter_gl_account_id}
            </span>
            <span className="text-[10px]" style={{ color: '#94A3B8' }}>
              {r.counter_gl_account_id}
            </span>
          </div>
        ),
      },
      {
        id: 'amount',
        label: 'Amount (kEUR)',
        align: 'right',
        getValue: r => fmtKEur(r.amount_keur),
        sortValue: r => r.amount_keur,
        render: r => (
          <span
            className="tabular-nums"
            style={{ color: r.amount_keur < 0 ? '#B91C1C' : '#334155' }}
          >
            {fmtKEur(r.amount_keur)}
          </span>
        ),
      },
      {
        id: 'novelty',
        label: 'Novelty',
        getValue: r => r.novelty,
        render: r => <NoveltyBadge novelty={r.novelty} />,
      },
      {
        id: 'freq_pct',
        label: 'Freq %',
        align: 'right',
        getValue: r => `${(r.freq_pct * 100).toFixed(1)}%`,
        sortValue: r => r.freq_pct,
      },
      {
        id: 'posting_date',
        label: 'Posting Date',
        getValue: r => r.posting_date,
        sortValue: r => r.posting_date,
      },
      {
        id: 'journal_entry_number',
        label: 'Journal #',
        getValue: r => r.journal_entry_number,
      },
      {
        id: 'line_note',
        label: 'Line Note',
        getValue: r => r.line_note,
        render: r => (
          <span
            className="truncate max-w-[240px] block"
            style={{ color: '#475569' }}
            title={r.line_note}
          >
            {r.line_note || '—'}
          </span>
        ),
      },
    ],
    [],
  )

  // Default sort: absolute amount descending (most material anomalies first).
  const sortedCounterAccounts = useMemo(() => {
    if (!data) return []
    return [...data.unexpected_counter_accounts].sort(
      (a, b) => Math.abs(b.amount_keur) - Math.abs(a.amount_keur),
    )
  }, [data])

  // ── Section 2: Material / growing "Other" positions ───────────────────────

  const otherPositionColumns: FilterableColumn<OtherPositionRow>[] = useMemo(
    () => [
      {
        id: 'account',
        label: 'Account',
        getValue: r => `${r.account_name} (${r.gl_account_id})`,
        sortValue: r => r.gl_account_id,
        render: r => (
          <div className="min-w-0">
            <span className="block truncate max-w-[200px] font-medium" style={{ color: '#1E3A5F' }}>
              {r.account_name || r.gl_account_id}
            </span>
            <span className="text-[10px]" style={{ color: '#94A3B8' }}>
              {r.gl_account_id}
            </span>
          </div>
        ),
      },
      {
        id: 'level_path',
        label: 'Level Path',
        getValue: r => r.level_path,
        render: r => (
          <span
            className="truncate max-w-[200px] block text-[10px]"
            style={{ color: '#64748B' }}
            title={r.level_path}
          >
            {r.level_path || '—'}
          </span>
        ),
      },
      {
        id: 'balance_cm',
        label: 'CM (kEUR)',
        align: 'right',
        getValue: r => fmtKEur(r.balance_cm_keur),
        sortValue: r => r.balance_cm_keur,
        render: r => (
          <span
            className="tabular-nums"
            style={{ color: r.balance_cm_keur < 0 ? '#B91C1C' : '#334155' }}
          >
            {fmtKEur(r.balance_cm_keur)}
          </span>
        ),
      },
      {
        id: 'balance_pm',
        label: 'PM (kEUR)',
        align: 'right',
        getValue: r => fmtKEur(r.balance_pm_keur),
        sortValue: r => r.balance_pm_keur,
        render: r => (
          <span
            className="tabular-nums"
            style={{ color: r.balance_pm_keur < 0 ? '#B91C1C' : '#334155' }}
          >
            {fmtKEur(r.balance_pm_keur)}
          </span>
        ),
      },
      {
        id: 'delta',
        label: 'Δ (kEUR)',
        align: 'right',
        getValue: r => fmtKEur(r.delta_keur),
        sortValue: r => r.delta_keur,
        render: r => (
          <span
            className="tabular-nums font-medium"
            style={{ color: r.delta_keur < 0 ? '#B91C1C' : r.delta_keur > 0 ? '#15803D' : '#94A3B8' }}
          >
            {r.delta_keur > 0 ? '+' : ''}{fmtKEur(r.delta_keur)}
          </span>
        ),
      },
      {
        id: 'yoy',
        label: 'YoY (kEUR)',
        align: 'right',
        getValue: r => fmtKEur(r.yoy_keur),
        sortValue: r => r.yoy_keur,
        render: r => (
          <span
            className="tabular-nums"
            style={{ color: r.yoy_keur < 0 ? '#B91C1C' : r.yoy_keur > 0 ? '#15803D' : '#94A3B8' }}
          >
            {r.yoy_keur > 0 ? '+' : ''}{fmtKEur(r.yoy_keur)}
          </span>
        ),
      },
      {
        id: 'growth_pct',
        label: 'Growth %',
        align: 'right',
        getValue: r => fmtGrowth(r.growth_pct),
        sortValue: r => r.growth_pct,
        render: r => (
          <span
            className="tabular-nums"
            style={{ color: r.growth_pct < 0 ? '#B91C1C' : r.growth_pct > 0 ? '#15803D' : '#94A3B8' }}
          >
            {fmtGrowth(r.growth_pct)}
          </span>
        ),
      },
      {
        id: 'matched_token',
        label: 'Matched Token',
        getValue: r => r.matched_token,
        render: r => (
          <span
            className="inline-block rounded px-1.5 py-0.5 text-[10px] font-medium"
            style={{ background: 'rgba(30,58,95,0.07)', color: '#475569' }}
          >
            {r.matched_token || '—'}
          </span>
        ),
      },
    ],
    [],
  )

  // Default sort: delta descending (largest movement first).
  const sortedOtherPositions = useMemo(() => {
    if (!data) return []
    return [...data.other_positions].sort((a, b) => b.delta_keur - a.delta_keur)
  }, [data])

  // ── Section 3: Suspicious booking texts ──────────────────────────────────

  const suspiciousTextColumns: FilterableColumn<SuspiciousTextRow>[] = useMemo(
    () => [
      {
        id: 'account',
        label: 'Account',
        getValue: r => `${r.account_name} (${r.gl_account_id})`,
        sortValue: r => r.gl_account_id,
        render: r => (
          <div className="min-w-0">
            <span className="block truncate max-w-[180px] font-medium" style={{ color: '#1E3A5F' }}>
              {r.account_name || r.gl_account_id}
            </span>
            <span className="text-[10px]" style={{ color: '#94A3B8' }}>
              {r.gl_account_id}
            </span>
          </div>
        ),
      },
      {
        id: 'line_note',
        label: 'Line Note',
        getValue: r => r.line_note,
        render: r => (
          <span
            className="block max-w-[320px] leading-relaxed"
            style={{ color: '#334155' }}
            title={r.line_note}
          >
            <HighlightedText text={r.line_note} keyword={r.matched_keyword} />
          </span>
        ),
      },
      {
        id: 'amount',
        label: 'Amount (kEUR)',
        align: 'right',
        getValue: r => fmtKEur(r.amount_keur),
        sortValue: r => r.amount_keur,
        render: r => (
          <span
            className="tabular-nums"
            style={{ color: r.amount_keur < 0 ? '#B91C1C' : '#334155' }}
          >
            {fmtKEur(r.amount_keur)}
          </span>
        ),
      },
      {
        id: 'posting_date',
        label: 'Posting Date',
        getValue: r => r.posting_date,
        sortValue: r => r.posting_date,
      },
      {
        id: 'journal_entry_number',
        label: 'Journal #',
        getValue: r => r.journal_entry_number,
      },
    ],
    [],
  )

  // ── Render states ─────────────────────────────────────────────────────────

  if (loading) return <AnomaliesLoading compact={false} />

  if (error) {
    return (
      <div
        className="rounded-xl px-5 py-4 text-xs"
        style={{
          background: 'rgba(239,68,68,0.07)',
          border: '1px solid rgba(220,38,38,0.3)',
          color: '#991B1B',
        }}
        role="alert"
      >
        <span className="font-semibold">Forensic analysis unavailable — </span>
        {error}
      </div>
    )
  }

  if (!data) {
    return (
      <div
        className="rounded-xl px-5 py-4 text-center"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
      >
        <p className="text-xs font-medium" style={{ color: '#64748B' }}>
          No forensic data available for this period.
        </p>
      </div>
    )
  }

  const totalFindings =
    data.unexpected_counter_accounts.length +
    data.other_positions.length +
    data.suspicious_texts.length

  if (totalFindings === 0) {
    return (
      <div
        className="rounded-xl px-5 py-6 text-center"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
      >
        <p className="text-xs font-medium" style={{ color: '#64748B' }}>
          No forensic findings for this period.
        </p>
        <p className="text-[12px] mt-1" style={{ color: '#94A3B8' }}>
          No unexpected counter-account pairs, material "other" positions, or suspicious booking
          texts detected.
        </p>
      </div>
    )
  }

  // ── Main render ──────────────────────────────────────────────────────────

  return (
    <div className="space-y-6">
      {/* Summary bar */}
      <div
        className="rounded-xl border px-5 py-3 flex flex-wrap items-center gap-4"
        style={{ background: '#FFFFFF', borderColor: '#E2E8F0' }}
      >
        <span className="text-xs font-semibold" style={{ color: '#1E3A5F' }}>
          Forensic Analysis
        </span>
        <span className="text-xs" style={{ color: '#64748B' }}>
          <span className="font-semibold" style={{ color: '#DC2626' }}>{totalFindings}</span>{' '}
          finding{totalFindings === 1 ? '' : 's'} across all three forensic checks
        </span>
        <span className="ml-auto text-[12px]" style={{ color: '#94A3B8' }}>
          {data.period.label} · {data.entity}
        </span>
      </div>

      {/* ── Section 1: Unexpected counter-accounts ── */}
      <div
        className="rounded-xl overflow-hidden"
        style={{ border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
      >
        <SectionHeader
          title="Unexpected Counter-Accounts"
          count={data.unexpected_counter_accounts.length}
        />
        {data.unexpected_counter_accounts.length === 0 ? (
          <div
            className="px-5 py-6 text-center"
            style={{ background: '#FFFFFF' }}
          >
            <p className="text-xs" style={{ color: '#94A3B8' }}>
              No unexpected counter-account pairs detected.
            </p>
          </div>
        ) : (
          <FilterableDataTable<CounterAccountRow>
            columns={counterAccountColumns}
            rows={sortedCounterAccounts}
            maxHeight={340}
            className="mt-0"
            emptyMessage="No counter-account findings"
          />
        )}
      </div>

      {/* ── Section 2: Material / growing "Other" positions ── */}
      <div
        className="rounded-xl overflow-hidden"
        style={{ border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
      >
        <SectionHeader
          title='Material / Growing "Other" Positions'
          count={data.other_positions.length}
        />
        {data.other_positions.length === 0 ? (
          <div
            className="px-5 py-6 text-center"
            style={{ background: '#FFFFFF' }}
          >
            <p className="text-xs" style={{ color: '#94A3B8' }}>
              No material or growing "Other" positions detected.
            </p>
          </div>
        ) : (
          <FilterableDataTable<OtherPositionRow>
            columns={otherPositionColumns}
            rows={sortedOtherPositions}
            maxHeight={340}
            className="mt-0"
            emptyMessage="No position findings"
          />
        )}
      </div>

      {/* ── Section 3: Suspicious booking texts ── */}
      <div
        className="rounded-xl overflow-hidden"
        style={{ border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}
      >
        <SectionHeader
          title="Suspicious Booking Texts"
          count={data.suspicious_texts.length}
        />
        {data.suspicious_texts.length === 0 ? (
          <div
            className="px-5 py-6 text-center"
            style={{ background: '#FFFFFF' }}
          >
            <p className="text-xs" style={{ color: '#94A3B8' }}>
              No suspicious booking text patterns detected.
            </p>
          </div>
        ) : (
          <FilterableDataTable<SuspiciousTextRow>
            columns={suspiciousTextColumns}
            rows={data.suspicious_texts}
            maxHeight={340}
            className="mt-0"
            emptyMessage="No text findings"
          />
        )}
      </div>
    </div>
  )
}
