/**
 * BudgetGrid — editable grid for manual BS/PL position budget planning.
 *
 * Annual mode: one editable annual budget cell per row; Δ vs synthetic shown.
 * Monthly mode: 12 editable month columns; server seasonalizes on SAVE when
 *   Annual mode sends `annual`; Monthly mode sends `months[12]`.
 *
 * Partner-driven positions expand to show Top-N partners + read-only Other row.
 * Running Σ reconciliation: Σ partners + Other == position total.
 *
 * SAVE path: batched via onSavePosition callback (PATCH /budget/position per changed row).
 * Cell edits are local state only until Save is clicked.
 */

import { useState, useCallback } from 'react'
import type {
  BudgetPositionRow,
  BudgetPartnerRow,
} from '../../lib/gdpduApi'

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

function fmtNum(v: number): string {
  return v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

function fmtDelta(delta: number): string {
  const sign = delta >= 0 ? '+' : ''
  return `${sign}${fmtNum(delta)}`
}

function deltaClass(delta: number): string {
  if (Math.abs(delta) < 0.5) return 'text-slate-400'
  return delta > 0 ? 'text-emerald-600' : 'text-red-600'
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Mutable override state for one position row. */
export interface PositionOverride {
  annual?: number
  months?: number[]
  /** Per-plan-year annual EUR overrides — keyed by fiscal year number. */
  annualByYear?: Record<number, number>
  /** Per-plan-year months arrays (EUR) — keyed by fiscal year number. */
  monthsByYear?: Record<number, number[]>
  /** Per-plan-year rate value — keyed by fiscal year number. */
  rateByYear?: Record<number, number>
  partners?: Record<string, { annual?: number; months?: number[] }>
  /** Per-L4 child overrides, keyed by level_4 value. */
  l4?: Record<string, { annual?: number; months?: number[] }>
}

export interface BudgetGridProps {
  positions: BudgetPositionRow[]
  statement: 'PL' | 'BS'
  fiscalYear: number
  entity: string
  /** Changes accumulated here; parent batches them on Save. */
  overrides: Record<string, PositionOverride>
  onOverride: (lineCode: string, override: PositionOverride) => void
  saving: boolean
}

// ---------------------------------------------------------------------------
// Inline number input
// ---------------------------------------------------------------------------

interface NumInputProps {
  value: number
  onChange: (v: number) => void
  disabled?: boolean
  className?: string
}

function NumInput({ value, onChange, disabled, className = '' }: NumInputProps) {
  const [raw, setRaw] = useState<string | null>(null)

  return (
    <input
      type="number"
      disabled={disabled}
      value={raw ?? String(Math.round(value))}
      onChange={(e) => setRaw(e.target.value)}
      onBlur={() => {
        if (raw !== null) {
          const n = parseFloat(raw)
          if (Number.isFinite(n)) onChange(n)
          setRaw(null)
        }
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          if (raw !== null) {
            const n = parseFloat(raw)
            if (Number.isFinite(n)) onChange(n)
            setRaw(null)
          }
          ;(e.target as HTMLInputElement).blur()
        }
      }}
      className={`w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-right text-xs font-mono
        focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
        disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed
        ${className}`}
    />
  )
}

// ---------------------------------------------------------------------------
// Partner sub-row
// ---------------------------------------------------------------------------

interface PartnerRowProps {
  partner: BudgetPartnerRow
  mode: 'annual' | 'monthly'
  override?: { annual?: number; months?: number[] }
  onChange: (partnerId: string, override: { annual?: number; months?: number[] }) => void
  saving: boolean
}

function PartnerRow({ partner, mode, override, onChange, saving }: PartnerRowProps) {
  const effectiveAnnual = override?.annual ?? partner.annual
  const effectiveMonths = override?.months ?? partner.months

  return (
    <tr className="border-b border-slate-100 bg-slate-50/60">
      <td className="py-1.5 pl-10 pr-3 text-xs text-slate-600 italic">{partner.name}</td>

      {mode === 'annual' ? (
        <>
          <td className="py-1.5 px-2 w-28">
            <NumInput
              value={effectiveAnnual}
              disabled={saving}
              onChange={(v) => onChange(partner.partner_id, { annual: v })}
            />
          </td>
          <td className="py-1.5 px-2 text-xs text-slate-400 text-right w-24">—</td>
        </>
      ) : (
        MONTH_LABELS.map((_, mi) => (
          <td key={mi} className="py-1.5 px-1 w-20">
            <NumInput
              value={effectiveMonths[mi] ?? 0}
              disabled={saving}
              onChange={(v) => {
                const updated = [...effectiveMonths]
                updated[mi] = v
                onChange(partner.partner_id, { months: updated })
              }}
            />
          </td>
        ))
      )}
    </tr>
  )
}

// ---------------------------------------------------------------------------
// BudgetGrid
// ---------------------------------------------------------------------------

export default function BudgetGrid({
  positions,
  overrides,
  onOverride,
  saving,
}: BudgetGridProps) {
  const [mode, setMode] = useState<'annual' | 'monthly'>('annual')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const toggleExpand = useCallback((lineCode: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(lineCode)) next.delete(lineCode)
      else next.add(lineCode)
      return next
    })
  }, [])

  if (positions.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-12 text-center">
        <p className="text-sm text-slate-500">
          No positions available. Use "Seed from synthetic" to populate the budget grid.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
      {/* Toolbar: Annual / Monthly toggle */}
      <div
        className="flex items-center gap-3 px-4 py-3 border-b border-slate-100"
        style={{ background: '#F8FAFC' }}
      >
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">View mode</span>
        <div className="flex rounded-lg border border-slate-200 overflow-hidden">
          {(['annual', 'monthly'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className="px-3 py-1 text-xs font-medium transition-colors"
              style={{
                background: mode === m ? '#1E3A5F' : '#FFFFFF',
                color: mode === m ? '#FFFFFF' : '#64748B',
              }}
            >
              {m === 'annual' ? 'Annual' : 'Monthly'}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-slate-400">
          All values in kEUR. Edit cells then click Save.
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-200" style={{ background: '#F4F6F9' }}>
              <th className="py-2 px-4 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider min-w-[220px]">
                Position
              </th>
              {mode === 'annual' ? (
                <>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-28">
                    Budget
                  </th>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-24">
                    Synthetic
                  </th>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-24">
                    Delta
                  </th>
                </>
              ) : (
                MONTH_LABELS.map((m) => (
                  <th
                    key={m}
                    className="py-2 px-1 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-20"
                  >
                    {m}
                  </th>
                ))
              )}
            </tr>
          </thead>
          <tbody>
            {positions.map((pos) => {
              const ov = overrides[pos.line_code] ?? {}
              const effectiveAnnual = ov.annual ?? pos.annual
              const effectiveMonths = ov.months ?? pos.months
              const delta = effectiveAnnual - pos.synthetic_annual
              const isExp = expanded.has(pos.line_code)
              const hasPartners = pos.is_partner_driven && (pos.partners?.length ?? 0) > 0

              // Partner-driven: position total is read-only (derived from partners + other)
              const posReadOnly = pos.is_partner_driven

              // Σ partners for reconciliation
              const partnerSum = (pos.partners ?? []).reduce((acc, p) => {
                const povAnnual = ov.partners?.[p.partner_id]?.annual ?? p.annual
                return acc + povAnnual
              }, 0)
              const other = pos.other ?? (effectiveAnnual - partnerSum)

              return (
                <>
                  {/* Position row */}
                  <tr
                    key={pos.line_code}
                    className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors"
                  >
                    <td className="py-2 px-4">
                      <div className="flex items-center gap-2">
                        {hasPartners && (
                          <button
                            type="button"
                            onClick={() => toggleExpand(pos.line_code)}
                            className="flex-shrink-0 w-4 h-4 rounded text-slate-400 hover:text-[#1E3A5F] transition-colors"
                            title={isExp ? 'Collapse partners' : 'Expand partners'}
                            aria-expanded={isExp}
                          >
                            <span className="text-xs leading-none">{isExp ? '▾' : '▸'}</span>
                          </button>
                        )}
                        {!hasPartners && <span className="w-4 flex-shrink-0" />}
                        <span className="font-medium text-slate-800 text-xs">{pos.label}</span>
                        {pos.is_partner_driven && (
                          <span className="text-[10px] rounded-full px-1.5 py-0.5 font-medium"
                            style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}>
                            Partner-driven
                          </span>
                        )}
                      </div>
                    </td>

                    {mode === 'annual' ? (
                      <>
                        <td className="py-2 px-2 w-28">
                          <NumInput
                            value={effectiveAnnual}
                            disabled={saving || posReadOnly}
                            onChange={(v) =>
                              onOverride(pos.line_code, { ...ov, annual: v })
                            }
                          />
                        </td>
                        <td className="py-2 px-2 text-xs text-right text-slate-400 font-mono w-24">
                          {fmtNum(pos.synthetic_annual)}
                        </td>
                        <td className={`py-2 px-2 text-xs text-right font-mono w-24 ${deltaClass(delta)}`}>
                          {fmtDelta(delta)}
                        </td>
                      </>
                    ) : (
                      MONTH_LABELS.map((_, mi) => (
                        <td key={mi} className="py-2 px-1 w-20">
                          <NumInput
                            value={effectiveMonths[mi] ?? 0}
                            disabled={saving || posReadOnly}
                            onChange={(v) => {
                              const updated = [...effectiveMonths]
                              updated[mi] = v
                              onOverride(pos.line_code, { ...ov, months: updated })
                            }}
                          />
                        </td>
                      ))
                    )}
                  </tr>

                  {/* Partner rows (expanded) */}
                  {isExp && hasPartners && (
                    <>
                      {(pos.partners ?? []).map((partner) => (
                        <PartnerRow
                          key={partner.partner_id}
                          partner={partner}
                          mode={mode}
                          override={ov.partners?.[partner.partner_id]}
                          saving={saving}
                          onChange={(pid, partnerOv) =>
                            onOverride(pos.line_code, {
                              ...ov,
                              partners: { ...(ov.partners ?? {}), [pid]: partnerOv },
                            })
                          }
                        />
                      ))}

                      {/* Other row (read-only residual) */}
                      <tr className="border-b border-slate-100 bg-slate-50/60">
                        <td className="py-1.5 pl-10 pr-3 text-xs text-slate-400 italic">Other (residual)</td>
                        {mode === 'annual' ? (
                          <>
                            <td className="py-1.5 px-2 text-xs text-right font-mono text-slate-400 w-28">
                              {fmtNum(other)}
                            </td>
                            <td colSpan={2} />
                          </>
                        ) : (
                          MONTH_LABELS.map((_, mi) => {
                            const partnerMonthSum = (pos.partners ?? []).reduce((acc, p) => {
                              const pm = ov.partners?.[p.partner_id]?.months ?? p.months
                              return acc + (pm[mi] ?? 0)
                            }, 0)
                            const otherMonth = (effectiveMonths[mi] ?? 0) - partnerMonthSum
                            return (
                              <td key={mi} className="py-1.5 px-1 text-xs text-right font-mono text-slate-400 w-20">
                                {fmtNum(otherMonth)}
                              </td>
                            )
                          })
                        )}
                      </tr>

                      {/* Reconciliation row */}
                      <tr className="border-b border-dashed border-slate-200 bg-slate-50">
                        <td className="py-1 pl-10 pr-3 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                          Reconciliation
                        </td>
                        {mode === 'annual' ? (
                          <>
                            <td className="py-1 px-2 text-[10px] text-right font-mono text-slate-400 w-28">
                              {`Σ ${fmtNum(partnerSum + other)} / Position ${fmtNum(effectiveAnnual)}`}
                            </td>
                            <td colSpan={2} />
                          </>
                        ) : (
                          MONTH_LABELS.map((_, mi) => {
                            const partnerMonthSum = (pos.partners ?? []).reduce((acc, p) => {
                              const pm = ov.partners?.[p.partner_id]?.months ?? p.months
                              return acc + (pm[mi] ?? 0)
                            }, 0)
                            const otherMonth = (effectiveMonths[mi] ?? 0) - partnerMonthSum
                            const totalMonth = partnerMonthSum + otherMonth
                            const posMonth = effectiveMonths[mi] ?? 0
                            const ok = Math.abs(totalMonth - posMonth) < 0.5
                            return (
                              <td key={mi} className={`py-1 px-1 text-[10px] text-right font-mono w-20 ${ok ? 'text-slate-300' : 'text-amber-600 font-semibold'}`}>
                                {ok ? 'OK' : '!'}
                              </td>
                            )
                          })
                        )}
                      </tr>
                    </>
                  )}
                </>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
