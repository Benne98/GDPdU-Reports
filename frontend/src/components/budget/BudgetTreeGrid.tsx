/**
 * BudgetTreeGrid — collapsible tree table for Phase 5 budget planning.
 *
 * Renders L3 positions at their per-position granularity:
 *   'L3'        — single position row (editable), children collapsed
 *   'L4'        — L4 children rows (editable), L3 row read-only = sum
 *   'customers' — partner sub-rows (editable), L3 read-only
 *   'suppliers' — partner sub-rows (editable), L3 read-only
 *
 * The `granularityByPosition` map (keyed `${statement}|${line_code}`) drives
 * expansion. Positions absent from the map default to 'L3'.
 *
 * Supports annual and monthly view modes, absolute and growth-% input,
 * and an optional suggestion-delta column with an explanation popover.
 */

import { useState, useCallback } from 'react'
import { Info } from 'lucide-react'
import type { BudgetPosition, BudgetChild, BudgetPartnerRow } from '../../lib/gdpduApi'
import type { PositionOverride } from './BudgetGrid'
import type { PositionGranularity, PositionKey } from '../../lib/budgetChatFlow'
import { getPositionInputMode, togglePositionInputMode, positionKey } from '../../lib/budgetChatFlow'

// ---------------------------------------------------------------------------
// Re-export PositionOverride so callers can import from this file too
// ---------------------------------------------------------------------------
export type { PositionOverride }

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface BudgetTreeGridProps {
  positions: BudgetPosition[]
  statement: 'PL' | 'BS'
  fiscalYear: number
  entity: string
  /**
   * Per-position granularity map keyed by `${statement}|${line_code}`.
   * Positions absent from the map default to 'L3'.
   * Replaces the old global `level` prop.
   */
  granularityByPosition: Record<PositionKey, PositionGranularity>
  viewMode: 'annual' | 'monthly'
  /**
   * @deprecated Kept for backward compat. Per-position mode from inputModeByPosition takes precedence.
   * Falls back to this value when a position has no entry in inputModeByPosition.
   */
  inputMode: 'absolute' | 'growth'
  /**
   * Per-position input mode map keyed by `${statement}|${line_code}`.
   * Missing keys default to 'pct' (growth %).
   * Only relevant for Start blank path; heuristic and Excel fill values directly.
   */
  inputModeByPosition: Record<PositionKey, 'pct' | 'absolute'>
  /**
   * Called when the user toggles the %/abs button on a position row.
   * Only available when startMode === 'blank'.
   */
  onInputModeChange?: (next: Record<PositionKey, 'pct' | 'absolute'>) => void
  /** Whether the start mode is 'blank' — controls visibility of the per-position toggle. */
  startMode: 'heuristic' | 'excel' | 'blank'
  overrides: Record<string, PositionOverride>
  onOverride: (lineCode: string, override: PositionOverride) => void
  saving: boolean
  showSuggestions: boolean
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

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

function fmtGrowth(current: number, base: number): string {
  if (base === 0) return '—'
  const pct = ((current - base) / Math.abs(base)) * 100
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
}

// ---------------------------------------------------------------------------
// Inline numeric input (absolute mode)
// ---------------------------------------------------------------------------

interface NumInputProps {
  value: number
  onChange: (v: number) => void
  disabled?: boolean
}

function NumInput({ value, onChange, disabled }: NumInputProps) {
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
// Growth-% input (growth mode): shows pct, converts to absolute on commit
// ---------------------------------------------------------------------------

interface GrowthInputProps {
  currentAbsolute: number
  base: number
  onChange: (absolute: number) => void
  disabled?: boolean
}

function GrowthInput({ currentAbsolute, base, onChange, disabled }: GrowthInputProps) {
  const initPct = base !== 0 ? ((currentAbsolute - base) / Math.abs(base)) * 100 : 0
  const [raw, setRaw] = useState<string | null>(null)

  const displayPct = raw !== null ? raw : initPct.toFixed(1)

  return (
    <div className="flex items-center gap-0.5">
      <input
        type="number"
        disabled={disabled}
        value={displayPct}
        onChange={(e) => setRaw(e.target.value)}
        onBlur={() => {
          if (raw !== null) {
            const pct = parseFloat(raw)
            if (Number.isFinite(pct)) {
              onChange(base * (1 + pct / 100))
            }
            setRaw(null)
          }
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            if (raw !== null) {
              const pct = parseFloat(raw)
              if (Number.isFinite(pct)) onChange(base * (1 + pct / 100))
              setRaw(null)
            }
            ;(e.target as HTMLInputElement).blur()
          }
        }}
        className="w-16 rounded border border-slate-200 bg-white px-2 py-0.5 text-right text-xs font-mono
          focus:border-[#1E3A5F] focus:outline-none focus:ring-1 focus:ring-[#1E3A5F]
          disabled:bg-slate-50 disabled:text-slate-400 disabled:cursor-not-allowed"
      />
      <span className="text-xs text-slate-400">%</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Explanation popover (inline on click)
// ---------------------------------------------------------------------------

interface ExplanationPopoverProps {
  explanation: Record<string, unknown>
}

function ExplanationPopover({ explanation }: ExplanationPopoverProps) {
  const [open, setOpen] = useState(false)

  return (
    <span className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-slate-400 hover:text-[#1E3A5F] transition-colors align-middle"
        aria-label="Show suggestion explanation"
      >
        <Info size={12} aria-hidden />
      </button>
      {open && (
        <div
          className="absolute z-50 left-0 top-5 w-56 rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-lg text-xs text-slate-700"
          style={{ minWidth: '200px' }}
        >
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="absolute top-1 right-2 text-slate-400 hover:text-slate-700 text-sm leading-none"
            aria-label="Close"
          >
            &times;
          </button>
          <div className="font-semibold text-slate-800 mb-1">Suggestion basis</div>
          {Object.entries(explanation).map(([k, v]) => (
            <div key={k} className="flex gap-1 py-0.5">
              <span className="font-medium text-slate-500 capitalize">{k.replace(/_/g, ' ')}:</span>
              <span>{String(v)}</span>
            </div>
          ))}
        </div>
      )}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Editable cell (handles both annual and monthly, both input modes)
// ---------------------------------------------------------------------------

interface EditableCellProps {
  absolute: number
  base: number
  inputMode: 'absolute' | 'growth'
  readOnly?: boolean
  disabled: boolean
  onChange: (v: number) => void
  className?: string
}

function EditableCell({ absolute, base, inputMode, readOnly, disabled, onChange, className = '' }: EditableCellProps) {
  if (readOnly) {
    return (
      <td className={`py-2 px-2 text-xs text-right font-mono text-slate-400 w-24 ${className}`}>
        {fmtNum(absolute)}
      </td>
    )
  }
  return (
    <td className={`py-1.5 px-1 w-24 ${className}`}>
      {inputMode === 'absolute' ? (
        <NumInput value={absolute} onChange={onChange} disabled={disabled} />
      ) : (
        <GrowthInput currentAbsolute={absolute} base={base} onChange={onChange} disabled={disabled} />
      )}
    </td>
  )
}

// ---------------------------------------------------------------------------
// L4 child row
// ---------------------------------------------------------------------------

interface ChildRowProps {
  child: BudgetChild
  viewMode: 'annual' | 'monthly'
  inputMode: 'absolute' | 'growth'
  baseMonths: number[]
  saving: boolean
  override?: { annual?: number; months?: number[] }
  onChange: (level4: string, ov: { annual?: number; months?: number[] }) => void
}

function ChildRow({ child, viewMode, inputMode, baseMonths, saving, override, onChange }: ChildRowProps) {
  const effectiveAnnual = override?.annual ?? child.annual
  const effectiveMonths = override?.months ?? child.months

  return (
    <tr className="border-b border-slate-100 bg-slate-50/50">
      <td className="py-1.5 pl-10 pr-3 text-xs text-slate-600" colSpan={1}>
        {child.label}
      </td>

      {viewMode === 'annual' ? (
        <>
          <EditableCell
            absolute={effectiveAnnual}
            base={child.annual}
            inputMode={inputMode}
            disabled={saving}
            onChange={(v) => onChange(child.level_4, { annual: v })}
          />
          <td className="py-1.5 px-2 text-xs text-right font-mono text-slate-400 w-24">—</td>
          <td className="py-1.5 px-2 text-xs text-right font-mono text-slate-400 w-20">—</td>
        </>
      ) : (
        MONTH_LABELS.map((_, mi) => (
          <EditableCell
            key={mi}
            absolute={effectiveMonths[mi] ?? 0}
            base={baseMonths[mi] ?? 0}
            inputMode={inputMode}
            disabled={saving}
            onChange={(v) => {
              const updated = [...effectiveMonths]
              updated[mi] = v
              onChange(child.level_4, { months: updated })
            }}
          />
        ))
      )}
    </tr>
  )
}

// ---------------------------------------------------------------------------
// Partner sub-row
// ---------------------------------------------------------------------------

interface PartnerSubRowProps {
  partner: BudgetPartnerRow
  viewMode: 'annual' | 'monthly'
  inputMode: 'absolute' | 'growth'
  saving: boolean
  override?: { annual?: number; months?: number[] }
  onChange: (partnerId: string, ov: { annual?: number; months?: number[] }) => void
}

function PartnerSubRow({ partner, viewMode, inputMode, saving, override, onChange }: PartnerSubRowProps) {
  const effectiveAnnual = override?.annual ?? partner.annual
  const effectiveMonths = override?.months ?? partner.months

  return (
    <tr className="border-b border-slate-100 bg-slate-50/60">
      <td className="py-1.5 pl-12 pr-3 text-xs text-slate-600 italic">{partner.name}</td>

      {viewMode === 'annual' ? (
        <>
          <EditableCell
            absolute={effectiveAnnual}
            base={partner.annual}
            inputMode={inputMode}
            disabled={saving}
            onChange={(v) => onChange(partner.partner_id, { annual: v })}
          />
          <td className="py-1.5 px-2 text-xs text-right font-mono text-slate-400 w-24">—</td>
          <td className="py-1.5 px-2 text-xs text-right font-mono text-slate-400 w-20">—</td>
        </>
      ) : (
        MONTH_LABELS.map((_, mi) => (
          <EditableCell
            key={mi}
            absolute={effectiveMonths[mi] ?? 0}
            base={partner.months[mi] ?? 0}
            inputMode={inputMode}
            disabled={saving}
            onChange={(v) => {
              const updated = [...effectiveMonths]
              updated[mi] = v
              onChange(partner.partner_id, { months: updated })
            }}
          />
        ))
      )}
    </tr>
  )
}

// ---------------------------------------------------------------------------
// BudgetTreeGrid
// ---------------------------------------------------------------------------

export default function BudgetTreeGrid({
  positions,
  statement,
  granularityByPosition,
  viewMode,
  inputMode,
  inputModeByPosition,
  onInputModeChange,
  startMode,
  overrides,
  onOverride,
  saving,
  showSuggestions,
}: BudgetTreeGridProps) {
  const [manualExpanded, setManualExpanded] = useState<Set<string>>(new Set())

  const toggleExpand = useCallback((lineCode: string) => {
    setManualExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(lineCode)) next.delete(lineCode)
      else next.add(lineCode)
      return next
    })
  }, [])

  // ── Empty state ──
  if (positions.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-6 py-12 text-center">
        <p className="text-sm text-slate-500">
          No positions available. Use "Finssentials heuristics → Apply" to populate the budget grid.
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-slate-200" style={{ background: '#F4F6F9' }}>
              <th className="py-2 px-4 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider min-w-[240px]">
                Position
              </th>
              {viewMode === 'annual' ? (
                <>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-24">
                    Budget
                  </th>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-24">
                    Synthetic
                  </th>
                  <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-20">
                    Delta
                  </th>
                  {showSuggestions && (
                    <th className="py-2 px-2 text-right text-xs font-semibold text-slate-500 uppercase tracking-wider w-24">
                      Suggestion
                    </th>
                  )}
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

              // Per-position granularity — default 'L3'
              const posGranularity: PositionGranularity =
                granularityByPosition[`${statement}|${pos.line_code}`] ?? 'L3'

              // Per-position input mode for Start blank path.
              // 'pct' maps to growth %; 'absolute' maps to absolute amounts.
              // For heuristic/excel paths always show absolute (read-only display).
              const posInputModeRaw: 'pct' | 'absolute' = getPositionInputMode(
                inputModeByPosition,
                positionKey(statement, pos.line_code),
              )
              // Map 'pct' → 'growth', 'absolute' → 'absolute' for EditableCell
              const resolvedInputMode: 'absolute' | 'growth' =
                startMode === 'blank'
                  ? (posInputModeRaw === 'pct' ? 'growth' : 'absolute')
                  : inputMode  // fall back to global for heuristic/excel

              // Determine expansion state:
              // L4/customers/suppliers: expanded by default (driven by granularity map)
              // L3 with children: user-toggle only
              const autoExpand =
                posGranularity === 'L4' ||
                posGranularity === 'customers' ||
                posGranularity === 'suppliers'
              const isExp = autoExpand
                ? !manualExpanded.has(pos.line_code)   // auto-expanded, user can collapse
                : manualExpanded.has(pos.line_code)     // manual toggle

              const hasChildren = (pos.children?.length ?? 0) > 0
              const hasPartners = pos.is_partner_driven && (pos.partners?.length ?? 0) > 0

              // What to expand depends on granularity
              const showL4Children =
                isExp && posGranularity === 'L4' && hasChildren
              const showPartners =
                isExp &&
                (posGranularity === 'customers' || posGranularity === 'suppliers') &&
                hasPartners

              // Expandable toggle: show chevron when there's something to expand
              const isExpandable =
                (posGranularity === 'L4' && hasChildren) ||
                ((posGranularity === 'customers' || posGranularity === 'suppliers') && hasPartners) ||
                (posGranularity === 'L3' && (hasChildren || hasPartners))

              // Position row read-only when in L4 or partner granularity
              // (total is derived from children/partners)
              const posReadOnly =
                posGranularity === 'L4' ||
                posGranularity === 'customers' ||
                posGranularity === 'suppliers'

              // Σ partners for reconciliation
              const partnerSum = (pos.partners ?? []).reduce((acc, p) => {
                return acc + (ov.partners?.[p.partner_id]?.annual ?? p.annual)
              }, 0)
              const other = pos.other ?? (effectiveAnnual - partnerSum)

              // Suggestion delta
              const suggDelta = pos.suggestion != null ? pos.suggestion.annual - effectiveAnnual : null

              // Granularity badge label
              const granularityBadge =
                posGranularity === 'L4'
                  ? 'L4'
                  : posGranularity === 'customers'
                  ? 'By customers'
                  : posGranularity === 'suppliers'
                  ? 'By suppliers'
                  : null

              return (
                <>
                  {/* ── Position row ── */}
                  <tr
                    key={pos.line_code}
                    className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors"
                  >
                    <td className="py-2 px-4">
                      <div className="flex items-center gap-2">
                        {isExpandable ? (
                          <button
                            type="button"
                            onClick={() => toggleExpand(pos.line_code)}
                            className="flex-shrink-0 w-4 h-4 text-slate-400 hover:text-[#1E3A5F] transition-colors"
                            aria-expanded={isExp}
                            title={isExp ? 'Collapse' : 'Expand'}
                          >
                            <span className="text-xs leading-none">{isExp ? '▾' : '▸'}</span>
                          </button>
                        ) : (
                          <span className="w-4 flex-shrink-0" />
                        )}
                        <span
                          className="text-xs text-slate-800"
                          style={{ fontWeight: posReadOnly ? 600 : 400 }}
                        >
                          {pos.label}
                        </span>
                        {granularityBadge && (
                          <span
                            className="text-[10px] rounded-full px-1.5 py-0.5 font-medium"
                            style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
                          >
                            {granularityBadge}
                          </span>
                        )}
                        {/* Per-position %/abs toggle — only for Start blank, only on editable rows */}
                        {startMode === 'blank' && !posReadOnly && onInputModeChange && (
                          <button
                            type="button"
                            title={posInputModeRaw === 'pct' ? 'Switch to absolute amounts' : 'Switch to growth %'}
                            onClick={() =>
                              onInputModeChange(
                                togglePositionInputMode(
                                  inputModeByPosition,
                                  positionKey(statement, pos.line_code),
                                ),
                              )
                            }
                            className="ml-auto flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold border transition-colors"
                            style={
                              posInputModeRaw === 'pct'
                                ? { borderColor: '#1E3A5F', color: '#1E3A5F', background: 'rgba(30,58,95,0.06)' }
                                : { borderColor: '#94a3b8', color: '#64748b', background: '#f8fafc' }
                            }
                          >
                            {posInputModeRaw === 'pct' ? '%' : 'abs'}
                          </button>
                        )}
                      </div>
                    </td>

                    {viewMode === 'annual' ? (
                      <>
                        {/* Budget cell */}
                        {posReadOnly ? (
                          <td className="py-2 px-2 text-xs text-right font-mono text-slate-500 w-24">
                            {fmtNum(effectiveAnnual)}
                          </td>
                        ) : (
                          <td className="py-1.5 px-1 w-24">
                            {resolvedInputMode === 'absolute' ? (
                              <NumInput
                                value={effectiveAnnual}
                                disabled={saving}
                                onChange={(v) => onOverride(pos.line_code, { ...ov, annual: v })}
                              />
                            ) : (
                              <GrowthInput
                                currentAbsolute={effectiveAnnual}
                                base={pos.synthetic_annual}
                                disabled={saving}
                                onChange={(v) => onOverride(pos.line_code, { ...ov, annual: v })}
                              />
                            )}
                          </td>
                        )}
                        {/* Synthetic */}
                        <td className="py-2 px-2 text-xs text-right font-mono text-slate-400 w-24">
                          {fmtNum(pos.synthetic_annual)}
                        </td>
                        {/* Delta */}
                        <td className={`py-2 px-2 text-xs text-right font-mono w-20 ${deltaClass(delta)}`}>
                          {resolvedInputMode === 'growth'
                            ? fmtGrowth(effectiveAnnual, pos.synthetic_annual)
                            : fmtDelta(delta)}
                        </td>
                        {/* Suggestion Δ */}
                        {showSuggestions && (
                          <td className="py-2 px-2 text-xs text-right font-mono w-24">
                            {pos.suggestion != null && suggDelta != null ? (
                              <span
                                className="inline-flex items-center gap-1"
                                style={{ color: suggDelta >= 0 ? '#059669' : '#DC2626' }}
                              >
                                {fmtDelta(suggDelta)}
                                {pos.explanation && (
                                  <ExplanationPopover explanation={pos.explanation} />
                                )}
                              </span>
                            ) : (
                              <span className="text-slate-300">—</span>
                            )}
                          </td>
                        )}
                      </>
                    ) : (
                      /* Monthly columns */
                      MONTH_LABELS.map((_, mi) => {
                        const monthVal = effectiveMonths[mi] ?? 0
                        return posReadOnly ? (
                          <td key={mi} className="py-2 px-1 text-xs text-right font-mono text-slate-500 w-20">
                            {fmtNum(monthVal)}
                          </td>
                        ) : (
                          <td key={mi} className="py-1.5 px-1 w-20">
                            {resolvedInputMode === 'absolute' ? (
                              <NumInput
                                value={monthVal}
                                disabled={saving}
                                onChange={(v) => {
                                  const updated = [...effectiveMonths]
                                  updated[mi] = v
                                  onOverride(pos.line_code, { ...ov, months: updated })
                                }}
                              />
                            ) : (
                              <GrowthInput
                                currentAbsolute={monthVal}
                                base={pos.months[mi] ?? 0}
                                disabled={saving}
                                onChange={(v) => {
                                  const updated = [...effectiveMonths]
                                  updated[mi] = v
                                  onOverride(pos.line_code, { ...ov, months: updated })
                                }}
                              />
                            )}
                          </td>
                        )
                      })
                    )}
                  </tr>

                  {/* ── L4 children (when granularity === 'L4') ── */}
                  {showL4Children && (
                    <>
                      {(pos.children ?? []).map((child) => (
                        <ChildRow
                          key={child.level_4}
                          child={child}
                          viewMode={viewMode}
                          inputMode={inputMode}
                          baseMonths={pos.months}
                          saving={saving}
                          override={ov.l4?.[child.level_4]}
                          onChange={(level4Key, childOv) =>
                            onOverride(pos.line_code, {
                              ...ov,
                              l4: { ...(ov.l4 ?? {}), [level4Key]: childOv },
                            })
                          }
                        />
                      ))}
                    </>
                  )}

                  {/* ── Partner rows (when granularity === 'customers' | 'suppliers') ── */}
                  {showPartners && (
                    <>
                      {(pos.partners ?? []).map((partner) => (
                        <PartnerSubRow
                          key={partner.partner_id}
                          partner={partner}
                          viewMode={viewMode}
                          inputMode={inputMode}
                          saving={saving}
                          override={ov.partners?.[partner.partner_id]}
                          onChange={(pid, partnerOv) =>
                            onOverride(pos.line_code, {
                              ...ov,
                              partners: { ...(ov.partners ?? {}), [pid]: partnerOv },
                            })
                          }
                        />
                      ))}

                      {/* Other (residual) row */}
                      <tr className="border-b border-slate-100 bg-slate-50/60">
                        <td className="py-1.5 pl-12 pr-3 text-xs text-slate-400 italic">
                          Other (residual)
                        </td>
                        {viewMode === 'annual' ? (
                          <>
                            <td className="py-1.5 px-2 text-xs text-right font-mono text-slate-400 w-24">
                              {fmtNum(other)}
                            </td>
                            <td colSpan={showSuggestions ? 3 : 2} />
                          </>
                        ) : (
                          MONTH_LABELS.map((_, mi) => {
                            const partnerMonthSum = (pos.partners ?? []).reduce((acc, p) => {
                              const pm = ov.partners?.[p.partner_id]?.months ?? p.months
                              return acc + (pm[mi] ?? 0)
                            }, 0)
                            const otherMonth = (effectiveMonths[mi] ?? 0) - partnerMonthSum
                            return (
                              <td
                                key={mi}
                                className="py-1.5 px-1 text-xs text-right font-mono text-slate-400 w-20"
                              >
                                {fmtNum(otherMonth)}
                              </td>
                            )
                          })
                        )}
                      </tr>

                      {/* Reconciliation row */}
                      <tr className="border-b border-dashed border-slate-200 bg-slate-50">
                        <td className="py-1 pl-12 pr-3 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                          Reconciliation
                        </td>
                        {viewMode === 'annual' ? (
                          <>
                            {(() => {
                              const total = partnerSum + other
                              const ok = Math.abs(total - effectiveAnnual) < 0.5
                              return (
                                <td
                                  className={`py-1 px-2 text-[10px] text-right font-mono w-24 ${ok ? 'text-slate-400' : 'text-amber-600 font-semibold'}`}
                                >
                                  {ok
                                    ? `Σ ${fmtNum(total)} OK`
                                    : `Σ ${fmtNum(total)} vs ${fmtNum(effectiveAnnual)}`}
                                </td>
                              )
                            })()}
                            <td colSpan={showSuggestions ? 3 : 2} />
                          </>
                        ) : (
                          MONTH_LABELS.map((_, mi) => {
                            const partnerMonthSum = (pos.partners ?? []).reduce((acc, p) => {
                              const pm = ov.partners?.[p.partner_id]?.months ?? p.months
                              return acc + (pm[mi] ?? 0)
                            }, 0)
                            const otherMonth = (effectiveMonths[mi] ?? 0) - partnerMonthSum
                            const total = partnerMonthSum + otherMonth
                            const posMonth = effectiveMonths[mi] ?? 0
                            const ok = Math.abs(total - posMonth) < 0.5
                            return (
                              <td
                                key={mi}
                                className={`py-1 px-1 text-[10px] text-right font-mono w-20 ${ok ? 'text-slate-300' : 'text-amber-600 font-semibold'}`}
                              >
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
      {/* Footer hint */}
      <div
        className="px-4 py-2 border-t border-slate-100 text-xs text-slate-400"
        style={{ background: '#F8FAFC' }}
      >
        All values in kEUR. Edit cells then click Save.{' '}
        {showSuggestions && viewMode === 'annual'
          ? 'Suggestion column shows heuristic delta vs. current budget.'
          : ''}
      </div>
    </div>
  )
}
