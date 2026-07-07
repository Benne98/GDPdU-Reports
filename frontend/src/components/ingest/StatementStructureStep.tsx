/**
 * StatementStructureStep.tsx — Project Setup Wizard step that detects and
 * resolves Chart-of-Accounts positions that do not map to any row in the
 * L1–L4 PL / BS / CF statement structure.
 *
 * Behaviour:
 *   • On mount (and when fiscalYears / entityPrefixes change): calls
 *     detectUnknownPositions.  Shows a spinner while loading.
 *   • structure_available === false OR positions is empty → auto-pass:
 *     renders a success panel and immediately calls onResolvedChange(true).
 *   • Otherwise: renders each unknown position with placement controls
 *     (target statement, display label, BS section).  The user can also
 *     mark a position as "Skip" and acknowledge leaving it unplaced.
 *   • "Apply placements" calls extendStructure, then onResolvedChange(true)
 *     on success or shows an inline error on failure.
 *   • onResolvedChange(false) whenever positions are unresolved.
 */

import { useState, useEffect, useCallback } from 'react'
import { StepCard } from './IngestStepCard'
import {
  detectUnknownPositions,
  extendStructure,
  type UnknownPosition,
  type StructurePlacement,
} from '../../lib/gdpduApi'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface StatementStructureStepProps {
  projectId: string
  fiscalYears: number[]
  entityPrefixes: string[]
  onResolvedChange: (resolved: boolean) => void
}

// ---------------------------------------------------------------------------
// Per-position placement draft
// ---------------------------------------------------------------------------

interface Draft {
  status: 'place' | 'skip'
  statement: 'PL' | 'BS' | 'CF'
  balance_title: string
  section: 'asset' | 'credit' | ''
}

function defaultDraft(p: UnknownPosition): Draft {
  return {
    status: 'place',
    statement: p.suggested_statement,
    balance_title: p.level_4 ?? p.level_3 ?? p.level_2 ?? p.id,
    section: '',
  }
}

// ---------------------------------------------------------------------------
// Spinner
// ---------------------------------------------------------------------------

function Spinner() {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-slate-500 text-sm">
      <svg
        className="animate-spin h-5 w-5"
        style={{ color: '#1E3A5F' }}
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
      >
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
      </svg>
      Checking statement structure for unknown positions…
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function StatementStructureStep({
  projectId,
  fiscalYears,
  entityPrefixes,
  onResolvedChange,
}: StatementStructureStepProps) {
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [positions, setPositions] = useState<UnknownPosition[]>([])
  const [structureAvailable, setStructureAvailable] = useState(true)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [leaveUnplacedAck, setLeaveUnplacedAck] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [applyResult, setApplyResult] = useState<{ inserted: string[]; skipped: string[] } | null>(null)

  // Stable string keys for deep-equality comparison of the array props so that
  // inline-created arrays in the wizard JSX do not cause spurious re-fetches.
  const fiscalYearsKey = JSON.stringify([...fiscalYears].sort((a, b) => a - b))
  const entityPrefixesKey = JSON.stringify([...entityPrefixes].sort())

  // Fetch unknown positions on mount and whenever the effective inputs change.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFetchError(null)
    setApplyResult(null)
    setApplyError(null)
    setLeaveUnplacedAck(false)
    onResolvedChange(false)

    detectUnknownPositions({
      project_id: projectId,
      fiscal_years: fiscalYears,
      entity_prefixes: entityPrefixes,
    })
      .then(res => {
        if (cancelled) return
        setStructureAvailable(res.structure_available)
        setPositions(res.positions)
        // Initialise one draft per position with suggested defaults.
        const initial: Record<string, Draft> = {}
        for (const p of res.positions) {
          initial[p.id] = defaultDraft(p)
        }
        setDrafts(initial)
        // Auto-pass when there is nothing to classify.
        if (!res.structure_available || res.positions.length === 0) {
          onResolvedChange(true)
        }
      })
      .catch(err => {
        if (cancelled) return
        setFetchError(err instanceof Error ? err.message : 'Failed to detect unknown positions')
        // Keep onResolvedChange(false) — the step stays blocked on error.
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
    // onResolvedChange is intentionally omitted — it is a stable setter reference
    // and adding it would cause infinite loops when the parent re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, fiscalYearsKey, entityPrefixesKey])

  const patchDraft = useCallback((id: string, patch: Partial<Draft>) => {
    setDrafts(prev => ({ ...prev, [id]: { ...prev[id], ...patch } }))
  }, [])

  const skippedCount = positions.filter(p => drafts[p.id]?.status === 'skip').length
  const allPlaced = skippedCount === 0

  // Apply is enabled when there is something to do and the user has either
  // placed all positions or acknowledged leaving some unplaced.
  const canApply =
    applyResult === null &&
    !applying &&
    positions.length > 0 &&
    (allPlaced || leaveUnplacedAck)

  async function handleApply() {
    setApplying(true)
    setApplyError(null)

    // Build the placements array from non-skipped drafts.
    const placements: StructurePlacement[] = positions
      .filter(p => drafts[p.id]?.status === 'place')
      .map(p => {
        const d = drafts[p.id]
        const pl: StructurePlacement = {
          statement: d.statement,
          level_2: p.level_2,
          level_3: p.level_3,
          level_4: p.level_4,
          row_type: 'mapping',
          balance_title: d.balance_title.trim() || undefined,
          after_line_code: p.suggested_after_line_code ?? undefined,
        }
        if (d.statement === 'BS' && d.section) {
          pl.section = d.section as 'asset' | 'credit'
        }
        return pl
      })

    try {
      const res = await extendStructure({ project_id: projectId, placements })
      setApplyResult({ inserted: res.inserted, skipped: res.skipped })
      onResolvedChange(true)
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : 'Failed to apply placements')
      onResolvedChange(false)
    } finally {
      setApplying(false)
    }
  }

  return (
    <StepCard
      title="Statement structure"
      subtitle="Review Chart-of-Accounts positions that are not yet assigned to a row in the P&L, Balance Sheet, or Cash Flow structure. If there are none, this step passes automatically."
    >
      <div className="space-y-5">

        {/* ── Loading ── */}
        {loading && <Spinner />}

        {/* ── Fetch error ── */}
        {!loading && fetchError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            <p className="font-semibold">Could not check structure.</p>
            <p className="mt-1 text-xs">{fetchError}</p>
          </div>
        )}

        {/* ── Auto-pass: no unknowns ── */}
        {!loading && !fetchError && (!structureAvailable || positions.length === 0) && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            <p className="font-semibold">No unknown positions — all good.</p>
            <p className="mt-0.5 text-xs">
              {!structureAvailable
                ? 'The statement structure table is not available for this project. This step is skipped automatically.'
                : 'Every account in the Chart of Accounts already maps to a row in the statement structure. No action is required.'}
            </p>
          </div>
        )}

        {/* ── Positions list ── */}
        {!loading && !fetchError && positions.length > 0 && (
          <>
            {/* Summary banner */}
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <p className="font-semibold">
                {positions.length} unknown {positions.length === 1 ? 'position' : 'positions'} found.
              </p>
              <p className="mt-0.5 text-xs">
                These CoA positions do not resolve to any row in the PL / BS / CF structure.
                Assign each to a statement below, then click "Apply placements" to add them.
                You may also skip individual positions and acknowledge leaving them unplaced.
              </p>
            </div>

            {/* Per-position cards */}
            <div className="space-y-3">
              {positions.map(pos => {
                const d = drafts[pos.id]
                if (!d) return null
                const isSkipped = d.status === 'skip'
                const pathParts = [pos.level_1, pos.level_2, pos.level_3, pos.level_4].filter(Boolean)
                const sampleAccounts = pos.accounts.slice(0, 3)

                return (
                  <div
                    key={pos.id}
                    className={`rounded-xl border shadow-sm overflow-hidden transition ${
                      isSkipped
                        ? 'border-slate-200 bg-slate-50 opacity-60'
                        : 'border-slate-200 bg-white'
                    }`}
                  >
                    {/* Card header: path + account count + Skip/Place toggle */}
                    <div className="px-5 py-3 border-b border-slate-100 flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <p className="text-xs text-slate-500 truncate">
                          {pathParts.length > 0
                            ? pathParts.join(' › ')
                            : <span className="italic text-slate-400">No hierarchy path</span>}
                        </p>
                        <p className="text-xs text-slate-400 mt-0.5">
                          {pos.account_count} {pos.account_count === 1 ? 'account' : 'accounts'}
                          {sampleAccounts.length > 0 && (
                            <>
                              {' — '}
                              {sampleAccounts.map(a => a.gl_account_id).join(', ')}
                              {pos.account_count > sampleAccounts.length && ' …'}
                            </>
                          )}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() =>
                          patchDraft(pos.id, { status: isSkipped ? 'place' : 'skip' })
                        }
                        className={`shrink-0 rounded-md border px-3 py-1 text-xs font-medium transition ${
                          isSkipped
                            ? 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
                            : 'border-slate-200 bg-slate-100 text-slate-500 hover:bg-slate-200'
                        }`}
                      >
                        {isSkipped ? 'Place' : 'Skip'}
                      </button>
                    </div>

                    {/* Placement controls — hidden when skipped */}
                    {!isSkipped && (
                      <div className="px-5 py-4 space-y-3">
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">

                          {/* Target statement */}
                          <div className="space-y-1">
                            <label className="text-xs font-medium text-slate-700">
                              Target statement
                              <span className="ml-0.5 text-red-500" aria-hidden>*</span>
                            </label>
                            <select
                              value={d.statement}
                              onChange={e =>
                                patchDraft(pos.id, {
                                  statement: e.target.value as 'PL' | 'BS' | 'CF',
                                  section: '',
                                })
                              }
                              className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                              <option value="PL">P&L (Profit &amp; Loss)</option>
                              <option value="BS">BS (Balance Sheet)</option>
                              <option value="CF">CF (Cash Flow)</option>
                            </select>
                          </div>

                          {/* Display label */}
                          <div className="space-y-1">
                            <label className="text-xs font-medium text-slate-700">
                              Display label
                            </label>
                            <input
                              type="text"
                              value={d.balance_title}
                              onChange={e =>
                                patchDraft(pos.id, { balance_title: e.target.value })
                              }
                              placeholder="Label in the statement"
                              className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            />
                          </div>

                          {/* Balance sheet side — only when statement is BS */}
                          {d.statement === 'BS' && (
                            <div className="space-y-1">
                              <label className="text-xs font-medium text-slate-700">
                                Balance sheet side
                              </label>
                              <select
                                value={d.section}
                                onChange={e =>
                                  patchDraft(pos.id, {
                                    section: e.target.value as 'asset' | 'credit' | '',
                                  })
                                }
                                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                              >
                                <option value="">-- Select side --</option>
                                <option value="asset">Asset</option>
                                <option value="credit">Credit (liabilities &amp; equity)</option>
                              </select>
                            </div>
                          )}
                        </div>

                        {/* Sample accounts */}
                        {sampleAccounts.length > 0 && (
                          <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-2">
                            <p className="text-xs font-medium text-slate-500 mb-1">
                              Sample accounts
                            </p>
                            <div className="space-y-0.5">
                              {sampleAccounts.map(a => (
                                <p key={a.gl_account_id} className="text-xs text-slate-600">
                                  <span className="font-mono">{a.account_number_group}</span>
                                  {a.account_name && (
                                    <span className="text-slate-400"> — {a.account_name}</span>
                                  )}
                                </p>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>

            {/* Leave-unplaced acknowledgement — only shown when some positions are skipped */}
            {skippedCount > 0 && (
              <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 select-none">
                <input
                  type="checkbox"
                  checked={leaveUnplacedAck}
                  onChange={e => setLeaveUnplacedAck(e.target.checked)}
                  className="mt-0.5 h-4 w-4 accent-amber-600 shrink-0"
                />
                <span className="text-sm text-amber-900">
                  I acknowledge that {skippedCount}{' '}
                  {skippedCount === 1 ? 'position' : 'positions'} will be left unplaced.
                  These accounts will fall into the statement's residual bucket until
                  reclassified in the CoA Editor.
                </span>
              </label>
            )}

            {/* Apply result */}
            {applyResult && (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <p className="font-semibold">Placements applied successfully.</p>
                <p className="mt-0.5 text-xs">
                  {applyResult.inserted.length} inserted
                  {applyResult.skipped.length > 0 && (
                    <>, {applyResult.skipped.length} already existed (skipped)</>
                  )}
                  . You may now proceed to the next step.
                </p>
              </div>
            )}

            {/* Apply error */}
            {applyError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                <p className="font-semibold">Apply failed.</p>
                <p className="mt-1 text-xs">{applyError}</p>
              </div>
            )}

            {/* Apply button — hidden after a successful apply */}
            {!applyResult && (
              <div className="flex justify-end">
                <button
                  type="button"
                  onClick={() => void handleApply()}
                  disabled={!canApply}
                  className="inline-flex items-center gap-2 rounded-md px-5 py-2 text-sm font-semibold text-white transition disabled:opacity-40 disabled:cursor-not-allowed"
                  style={{ backgroundColor: '#1E3A5F' }}
                >
                  {applying ? (
                    <>
                      <svg
                        className="animate-spin h-4 w-4"
                        fill="none"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                        />
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8v8H4z"
                        />
                      </svg>
                      Applying…
                    </>
                  ) : (
                    'Apply placements'
                  )}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </StepCard>
  )
}
