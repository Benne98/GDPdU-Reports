import { useState, useEffect } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import {
  api,
  type ForensicPositionsResponse,
  type ForensicCounterPosition,
  type ForensicOtherPosition,
  type ForensicSuspiciousText,
} from '../../lib/api'
import { AnomaliesLoading } from '../financials/AnomaliesPanel'
import AnomalyReport from './AnomalyReport'

function NoveltyBadge({ novelty }: { novelty: string }) {
  const isNew = novelty === 'new'
  return (
    <span
      className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
      style={{ background: isNew ? 'rgba(220,38,38,0.1)' : 'rgba(217,119,6,0.1)', color: isNew ? '#DC2626' : '#D97706' }}
    >
      {novelty}
    </span>
  )
}

function HighlightedText({ text, keyword }: { text: string; keyword: string }) {
  if (!keyword) return <span>{text}</span>
  const idx = text.toLowerCase().indexOf(keyword.toLowerCase())
  if (idx < 0) return <span>{text}</span>
  return (
    <span>
      {text.slice(0, idx)}
      <mark style={{ background: 'rgba(217,119,6,0.2)', color: '#92400E', padding: '0 2px', borderRadius: 2 }}>
        {text.slice(idx, idx + keyword.length)}
      </mark>
      {text.slice(idx + keyword.length)}
    </span>
  )
}

function fmtKEur(v: number): string { return `${v.toFixed(1)} kEUR` }

function fmtDelta(v: number) {
  const color = v > 0 ? '#16A34A' : v < 0 ? '#DC2626' : '#64748B'
  return <span style={{ color }}>{v > 0 ? '+' : ''}{v.toFixed(1)} kEUR</span>
}

function ExpandableRow({ summary, children }: { summary: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ borderBottom: '1px solid #F1F5F9' }}>
      <div
        className="flex items-start gap-2 px-4 py-3 cursor-pointer hover:bg-slate-50 transition-colors"
        onClick={() => setOpen(o => !o)}
      >
        <span className="mt-0.5 flex-shrink-0" style={{ color: '#94A3B8' }}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <div className="flex-1 min-w-0">{summary}</div>
      </div>
      {open && <div className="px-4 pb-4">{children}</div>}
    </div>
  )
}

function OmittedCount({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <p className="text-xs mt-2 px-4 pb-3" style={{ color: '#94A3B8' }}>
      +{count} more positions below the significance threshold (not shown)
    </p>
  )
}

function CounterPositionsSection({
  positions,
  headline,
  bullets,
  omittedCount,
}: {
  positions: ForensicCounterPosition[]
  headline: string
  bullets: string[]
  omittedCount: number
}) {
  const sorted = [...positions].sort((a, b) => b.abs_amount_keur - a.abs_amount_keur)
  return (
    <div className="mb-8">
      <AnomalyReport headline={headline} bullets={bullets} score={0} band="low" label="Unexpected Counter Accounts">
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
          {sorted.length === 0 ? (
            <p className="px-5 py-6 text-sm text-center" style={{ color: '#94A3B8' }}>No unexpected counter accounts found.</p>
          ) : sorted.map(pos => (
            <ExpandableRow
              key={pos.position}
              summary={
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-semibold text-sm" style={{ color: '#1E3A5F' }}>{pos.position}</span>
                  {pos.new_count > 0 && (
                    <span className="rounded px-1.5 py-0.5 font-semibold" style={{ background: 'rgba(220,38,38,0.1)', color: '#DC2626' }}>
                      {pos.new_count} new
                    </span>
                  )}
                  {pos.rare_count > 0 && (
                    <span className="rounded px-1.5 py-0.5 font-semibold" style={{ background: 'rgba(217,119,6,0.1)', color: '#D97706' }}>
                      {pos.rare_count} rare
                    </span>
                  )}
                  <span style={{ color: '#64748B' }}>{fmtKEur(pos.abs_amount_keur)}</span>
                  <span style={{ color: '#94A3B8' }}>{pos.distinct_counter_accounts} counter accts</span>
                </div>
              }
            >
              {pos.evidence.length > 0 && (
                <div className="overflow-x-auto rounded-lg mt-1" style={{ border: '1px solid #E2E8F0' }}>
                  <table className="w-full text-xs">
                    <thead>
                      <tr style={{ borderBottom: '1px solid #E2E8F0', background: '#F8FAFC' }}>
                        <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Account</th>
                        <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Counter account</th>
                        <th className="px-3 py-2 text-right font-semibold" style={{ color: '#64748B' }}>Amount</th>
                        <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Novelty</th>
                        <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Date</th>
                        <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pos.evidence.map((ev, i) => (
                        <tr key={ev.booking_line_id ?? i} style={{ borderBottom: '1px solid #F1F5F9' }}>
                          <td className="px-3 py-1.5" style={{ color: '#374151' }}>{ev.account_name ?? ev.gl_account_id ?? '—'}</td>
                          <td className="px-3 py-1.5" style={{ color: '#374151' }}>{ev.counter_account_name ?? ev.counter_gl_account_id ?? '—'}</td>
                          <td className="px-3 py-1.5 text-right font-mono" style={{ color: (ev.amount_keur ?? 0) < 0 ? '#DC2626' : '#374151' }}>
                            {ev.amount_keur !== undefined ? ev.amount_keur.toFixed(1) : '—'}
                          </td>
                          <td className="px-3 py-1.5">{ev.novelty ? <NoveltyBadge novelty={ev.novelty} /> : '—'}</td>
                          <td className="px-3 py-1.5" style={{ color: '#374151' }}>{ev.posting_date ?? '—'}</td>
                          <td className="px-3 py-1.5 max-w-xs truncate" style={{ color: '#64748B' }}>{ev.line_note ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </ExpandableRow>
          ))}
          <OmittedCount count={omittedCount} />
        </div>
      </AnomalyReport>
    </div>
  )
}

function OtherPositionsSection({
  positions,
  headline,
  bullets,
  omittedCount,
}: {
  positions: ForensicOtherPosition[]
  headline: string
  bullets: string[]
  omittedCount: number
}) {
  return (
    <div className="mb-8">
      <AnomalyReport headline={headline} bullets={bullets} score={0} band="low" label='Material "Other" Positions'>
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
          {positions.length === 0 ? (
            <p className="px-5 py-6 text-sm text-center" style={{ color: '#94A3B8' }}>No material "Other" positions found.</p>
          ) : positions.map(pos => (
            <ExpandableRow
              key={pos.position}
              summary={
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-semibold text-sm" style={{ color: '#1E3A5F' }}>{pos.position}</span>
                  <span style={{ color: '#64748B' }}>{fmtKEur(pos.balance_cm_keur)}</span>
                  <span>{fmtDelta(pos.delta_keur)}</span>
                  {pos.growth_pct !== 0 && (
                    <span style={{ color: pos.growth_pct > 0 ? '#16A34A' : '#DC2626' }}>
                      {pos.growth_pct > 0 ? '+' : ''}{pos.growth_pct.toFixed(1)}%
                    </span>
                  )}
                  {pos.matched_token && (
                    <span className="rounded px-1.5 py-0.5 font-semibold" style={{ background: 'rgba(37,99,235,0.08)', color: '#2563EB' }}>
                      {pos.matched_token}
                    </span>
                  )}
                </div>
              }
            >
              {pos.evidence.length > 0 && (
                <div className="overflow-x-auto rounded-lg mt-1" style={{ border: '1px solid #E2E8F0' }}>
                  <table className="w-full text-xs">
                    <thead>
                      <tr style={{ borderBottom: '1px solid #E2E8F0', background: '#F8FAFC' }}>
                        <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Account</th>
                        <th className="px-3 py-2 text-right font-semibold" style={{ color: '#64748B' }}>Balance CM</th>
                        <th className="px-3 py-2 text-right font-semibold" style={{ color: '#64748B' }}>Delta</th>
                        <th className="px-3 py-2 text-right font-semibold" style={{ color: '#64748B' }}>YoY</th>
                        <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Entity</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pos.evidence.map((ev, i) => (
                        <tr key={ev.gl_account_id ?? i} style={{ borderBottom: '1px solid #F1F5F9' }}>
                          <td className="px-3 py-1.5" style={{ color: '#374151' }}>{ev.account_name ?? ev.gl_account_id ?? '—'}</td>
                          <td className="px-3 py-1.5 text-right font-mono" style={{ color: '#374151' }}>
                            {ev.balance_cm_keur !== undefined ? ev.balance_cm_keur.toFixed(1) : '—'}
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            {ev.delta_keur !== undefined ? fmtDelta(ev.delta_keur) : '—'}
                          </td>
                          <td className="px-3 py-1.5 text-right">
                            {ev.yoy_keur !== undefined ? fmtDelta(ev.yoy_keur) : '—'}
                          </td>
                          <td className="px-3 py-1.5" style={{ color: '#374151' }}>{ev.entity_prefix ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </ExpandableRow>
          ))}
          <OmittedCount count={omittedCount} />
        </div>
      </AnomalyReport>
    </div>
  )
}

function SuspiciousTextsSection({
  rows,
  headline,
  bullets,
  omittedCount,
}: {
  rows: ForensicSuspiciousText[]
  headline: string
  bullets: string[]
  omittedCount: number
}) {
  return (
    <div className="mb-8">
      <AnomalyReport headline={headline} bullets={bullets} score={0} band="low" label="Suspicious Booking Texts">
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
          {rows.length === 0 ? (
            <p className="px-5 py-6 text-sm text-center" style={{ color: '#94A3B8' }}>No suspicious booking texts found.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ borderBottom: '1px solid #E2E8F0', background: '#F8FAFC' }}>
                    <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Position</th>
                    <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Account</th>
                    <th className="px-3 py-2 text-right font-semibold" style={{ color: '#64748B' }}>Amount</th>
                    <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Date</th>
                    <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Note</th>
                    <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Entity</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={row.booking_line_id ?? i} style={{ borderBottom: '1px solid #F1F5F9' }}>
                      <td className="px-3 py-1.5 max-w-[120px] truncate" style={{ color: '#374151' }}>{row.position ?? '—'}</td>
                      <td className="px-3 py-1.5" style={{ color: '#374151' }}>{row.account_name ?? row.gl_account_id ?? '—'}</td>
                      <td className="px-3 py-1.5 text-right font-mono" style={{ color: (row.amount_keur ?? 0) < 0 ? '#DC2626' : '#374151' }}>
                        {row.amount_keur !== undefined ? row.amount_keur.toFixed(1) : '—'}
                      </td>
                      <td className="px-3 py-1.5" style={{ color: '#374151' }}>{row.posting_date ?? '—'}</td>
                      <td className="px-3 py-1.5 max-w-xs">
                        {row.line_note && row.matched_keyword
                          ? <HighlightedText text={row.line_note} keyword={row.matched_keyword} />
                          : <span style={{ color: '#64748B' }}>{row.line_note ?? '—'}</span>}
                      </td>
                      <td className="px-3 py-1.5" style={{ color: '#374151' }}>{row.entity_prefix ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <OmittedCount count={omittedCount} />
        </div>
      </AnomalyReport>
    </div>
  )
}

export default function ForensicView() {
  const [data, setData] = useState<ForensicPositionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.financialsAnomalyForensic()
      .then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch((e: unknown) => {
        if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setLoading(false) }
      })
    return () => { cancelled = true }
  }, [])

  if (loading) return <AnomaliesLoading compact={false} />
  if (error) return (
    <div className="rounded-xl px-5 py-4 text-sm" style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(220,38,38,0.3)', color: '#991B1B' }}>{error}</div>
  )
  if (!data) return (
    <div className="rounded-xl px-5 py-8 text-center bg-white" style={{ border: '1px solid #E2E8F0' }}>
      <p className="text-sm" style={{ color: '#94A3B8' }}>No forensic data available.</p>
    </div>
  )

  const rv = data.meta.report_views ?? {}
  const oc = data.meta.omitted_counts ?? {}
  const llmUsed = data.meta.llm_used === true

  const counterRv = rv['unexpected_counter_positions'] ?? { headline: 'Unexpected counter accounts', bullets: [] }
  const otherRv = rv['other_positions'] ?? { headline: 'Material "Other" positions', bullets: [] }
  const textsRv = rv['suspicious_texts'] ?? { headline: 'Suspicious booking texts', bullets: [] }

  return (
    <div>
      {llmUsed && (
        <div className="mb-4 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-medium"
          style={{ background: 'rgba(37,99,235,0.08)', color: '#2563EB', border: '1px solid rgba(37,99,235,0.2)' }}>
          <span aria-hidden>&#10024;</span>
          AI-filtered
        </div>
      )}
      <CounterPositionsSection
        positions={data.unexpected_counter_positions}
        headline={counterRv.headline ?? ''}
        bullets={counterRv.bullets ?? []}
        omittedCount={oc['unexpected_counter_positions'] ?? 0}
      />
      <OtherPositionsSection
        positions={data.other_positions}
        headline={otherRv.headline ?? ''}
        bullets={otherRv.bullets ?? []}
        omittedCount={oc['other_positions'] ?? 0}
      />
      <SuspiciousTextsSection
        rows={data.suspicious_texts}
        headline={textsRv.headline ?? ''}
        bullets={textsRv.bullets ?? []}
        omittedCount={oc['suspicious_texts'] ?? 0}
      />
    </div>
  )
}
