/**
 * BudgetPartnersTab — editable Debtor/Creditor partner planning table.
 *
 * Reuses the visual style of TopCustomerTable / TopSupplierTable (cockpit).
 * Shows partner rows with editable annual budget + Σ+Other reconciliation footer.
 *
 * All state is local until the parent calls onSavePartner.
 */

import { useState } from 'react'
import type { BudgetPositionRow, BudgetPartnerRow } from '../../lib/gdpduApi'
import type { PositionOverride } from './BudgetGrid'

// ---------------------------------------------------------------------------
// Helpers
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

// ---------------------------------------------------------------------------
// Inline number input (duplicated small to avoid cross-component import)
// ---------------------------------------------------------------------------

function NumInput({
  value,
  onChange,
  disabled,
}: {
  value: number
  onChange: (v: number) => void
  disabled?: boolean
}) {
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
      className="w-full rounded border border-slate-200 bg-white px-2 py-0.5 text-right text-xs font-mono
        focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
        disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed"
    />
  )
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BudgetPartnersTabProps {
  /** Only partner-driven positions are shown. */
  positions: BudgetPositionRow[]
  overrides: Record<string, PositionOverride>
  onOverride: (lineCode: string, override: PositionOverride) => void
  saving: boolean
}

// ---------------------------------------------------------------------------
// Single partner-position card
// ---------------------------------------------------------------------------

interface PartnerCardProps {
  position: BudgetPositionRow
  override: PositionOverride
  onOverride: (ov: PositionOverride) => void
  saving: boolean
}

function PartnerCard({ position, override, onOverride, saving }: PartnerCardProps) {
  const [mode, setMode] = useState<'annual' | 'monthly'>('annual')

  const partners: BudgetPartnerRow[] = position.partners ?? []

  // Effective values after overrides
  const posAnnual = override.annual ?? position.annual
  const partnerRows = partners.map((p) => ({
    ...p,
    effectiveAnnual: override.partners?.[p.partner_id]?.annual ?? p.annual,
    effectiveMonths: override.partners?.[p.partner_id]?.months ?? p.months,
  }))
  const partnerSum = partnerRows.reduce((s, p) => s + p.effectiveAnnual, 0)
  const other = posAnnual - partnerSum
  const reconciled = Math.abs(other) < 0.5

  function setPartnerAnnual(partnerId: string, v: number) {
    onOverride({
      ...override,
      partners: {
        ...(override.partners ?? {}),
        [partnerId]: { ...(override.partners?.[partnerId] ?? {}), annual: v },
      },
    })
  }

  function setPartnerMonth(partnerId: string, mi: number, v: number) {
    const prev = override.partners?.[partnerId]?.months ?? partners.find(p => p.partner_id === partnerId)?.months ?? Array(12).fill(0) as number[]
    const updated = [...prev]
    updated[mi] = v
    onOverride({
      ...override,
      partners: {
        ...(override.partners ?? {}),
        [partnerId]: { ...(override.partners?.[partnerId] ?? {}), months: updated },
      },
    })
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden mb-4">
      {/* Card header */}
      <div
        className="flex items-center justify-between px-5 py-3 border-b border-slate-100"
        style={{ background: '#F8FAFC' }}
      >
        <div>
          <span className="font-semibold text-sm text-slate-800">{position.label}</span>
          <span
            className="ml-2 text-[10px] rounded-full px-1.5 py-0.5 font-medium"
            style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
          >
            {position.label.toLowerCase().includes('revenue') ||
            position.label.toLowerCase().includes('receivable')
              ? 'Debtors'
              : 'Creditors'}
          </span>
        </div>
        <div className="flex items-center gap-3">
          {/* Reconciliation badge */}
          <span
            className="text-xs font-medium px-2 py-0.5 rounded-full"
            style={{
              background: reconciled ? 'rgba(5,150,105,0.08)' : 'rgba(245,158,11,0.12)',
              color: reconciled ? '#059669' : '#B45309',
            }}
          >
            {reconciled
              ? `Balanced · ${fmtNum(posAnnual)}`
              : `Other: ${fmtNum(other)} (imbalance)`}
          </span>
          {/* Mode toggle */}
          <div className="flex rounded-lg border border-slate-200 overflow-hidden">
            {(['annual', 'monthly'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className="px-2 py-1 text-[12px] font-medium transition-colors"
                style={{
                  background: mode === m ? '#1E3A5F' : '#FFFFFF',
                  color: mode === m ? '#FFFFFF' : '#64748B',
                }}
              >
                {m === 'annual' ? 'Annual' : 'Monthly'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-100" style={{ background: '#F4F6F9' }}>
              <th className="py-2 px-4 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider min-w-[200px]">
                Partner
              </th>
              {mode === 'annual' ? (
                <>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-28">
                    Budget
                  </th>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-24">
                    Prior (Synthetic)
                  </th>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-24">
                    Delta
                  </th>
                </>
              ) : (
                MONTH_LABELS.map((ml) => (
                  <th
                    key={ml}
                    className="py-2 px-1 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-20"
                  >
                    {ml}
                  </th>
                ))
              )}
            </tr>
          </thead>
          <tbody>
            {partnerRows.map((p, idx) => {
              const syntheticAnnual = partners[idx]?.annual ?? p.effectiveAnnual
              const delta = p.effectiveAnnual - syntheticAnnual
              return (
                <tr key={p.partner_id} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                  <td className="py-2 px-4">
                    <div className="flex items-center gap-2">
                      <span
                        className="inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold text-white flex-shrink-0"
                        style={{ background: '#1E3A5F' }}
                      >
                        {idx + 1}
                      </span>
                      <span className="text-xs font-medium text-slate-700">{p.name}</span>
                    </div>
                  </td>
                  {mode === 'annual' ? (
                    <>
                      <td className="py-2 px-2 w-28">
                        <NumInput
                          value={p.effectiveAnnual}
                          disabled={saving}
                          onChange={(v) => setPartnerAnnual(p.partner_id, v)}
                        />
                      </td>
                      <td className="py-2 px-2 text-xs text-right font-mono text-slate-400 w-24">
                        {fmtNum(syntheticAnnual)}
                      </td>
                      <td
                        className={`py-2 px-2 text-xs text-right font-mono w-24 ${
                          Math.abs(delta) < 0.5
                            ? 'text-slate-300'
                            : delta > 0
                            ? 'text-emerald-600'
                            : 'text-red-600'
                        }`}
                      >
                        {fmtDelta(delta)}
                      </td>
                    </>
                  ) : (
                    MONTH_LABELS.map((_, mi) => (
                      <td key={mi} className="py-2 px-1 w-20">
                        <NumInput
                          value={p.effectiveMonths[mi] ?? 0}
                          disabled={saving}
                          onChange={(v) => setPartnerMonth(p.partner_id, mi, v)}
                        />
                      </td>
                    ))
                  )}
                </tr>
              )
            })}

            {/* Other (residual) row */}
            <tr className="border-b border-dashed border-slate-200 bg-slate-50/60">
              <td className="py-2 px-4 text-xs italic text-slate-400">Other (residual)</td>
              {mode === 'annual' ? (
                <>
                  <td className="py-2 px-2 text-xs text-right font-mono text-slate-400 w-28">
                    {fmtNum(other)}
                  </td>
                  <td colSpan={2} />
                </>
              ) : (
                MONTH_LABELS.map((_, mi) => {
                  const partnerMiSum = partnerRows.reduce((acc, p) => acc + (p.effectiveMonths[mi] ?? 0), 0)
                  const posMonth = (override.months ?? position.months)[mi] ?? 0
                  const otherMonth = posMonth - partnerMiSum
                  return (
                    <td key={mi} className="py-2 px-1 text-xs text-right font-mono text-slate-400 w-20">
                      {fmtNum(otherMonth)}
                    </td>
                  )
                })
              )}
            </tr>

            {/* Σ total row */}
            <tr className="bg-slate-50">
              <td className="py-2 px-4 text-xs font-semibold text-slate-600">
                Position total
              </td>
              {mode === 'annual' ? (
                <>
                  <td className="py-2 px-2 text-xs text-right font-mono font-semibold text-slate-700 w-28">
                    {fmtNum(posAnnual)}
                  </td>
                  <td colSpan={2} />
                </>
              ) : (
                MONTH_LABELS.map((_, mi) => {
                  const posMonth = (override.months ?? position.months)[mi] ?? 0
                  return (
                    <td key={mi} className="py-2 px-1 text-xs text-right font-mono font-semibold text-slate-700 w-20">
                      {fmtNum(posMonth)}
                    </td>
                  )
                })
              )}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// BudgetPartnersTab
// ---------------------------------------------------------------------------

export default function BudgetPartnersTab({
  positions,
  overrides,
  onOverride,
  saving,
}: BudgetPartnersTabProps) {
  const partnerPositions = positions.filter(
    (p) => p.is_partner_driven && (p.partners?.length ?? 0) > 0,
  )

  if (partnerPositions.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-12 text-center">
        <p className="text-sm text-slate-500">
          No partner-driven positions found. Seed the budget or check that the backend
          has Revenue / Cost of Materials / Trade Receivables / Trade Payables positions.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-500 mb-4">
        Plan individual Debtor (customer) and Creditor (supplier) budgets. The Σ of all
        partners plus the residual "Other" row must equal the position total. Edit cells and
        click Save on the top bar to persist changes.
      </p>
      {partnerPositions.map((pos) => (
        <PartnerCard
          key={pos.line_code}
          position={pos}
          override={overrides[pos.line_code] ?? {}}
          onOverride={(ov) => onOverride(pos.line_code, ov)}
          saving={saving}
        />
      ))}
    </div>
  )
}
