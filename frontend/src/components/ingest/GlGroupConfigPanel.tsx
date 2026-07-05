/**
 * GlGroupConfigPanel.tsx — Format-group configuration panel.
 *
 * Rendered ONCE per format group at the parent level (ProjectSetupWizard /
 * IngestionPage), not inside individual entity cards. Owns:
 *   - Partner-column layout (single / split)
 *   - Transform options (sign, decimal, date, linking) via OptionsStep
 *   - signComplete gate indicator
 *
 * When the user completes partner + options config the parent patches the group
 * via onPatchGroup, which makes entity cards' groupConfigReady = true and
 * reveals the ValidierungStep for each member.
 */

import { useState } from 'react'
import type { GlEntityState, GlFormatGroup, PartnerColumnsMode, PartnerColumnsSplit } from './GlEntityCard'
import { ConfirmHeadersStep } from './GlEntityCard'
import OptionsStep from './OptionsStep'
import type { OptionsState } from './ingestTypes'
import { StepCard } from './IngestStepCard'
import { validateIngest, type Profile, type ValidationResponse } from '../../lib/gdpduApi'
import MergedValidationReport, { type MemberValidationData } from './MergedValidationReport'
import FullDatasetModal from './FullDatasetModal'
import { missingRequiredGlColumns, GL_COLUMN_FRIENDLY_LABELS } from './ColumnMapper'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface GlGroupConfigPanelProps {
  /** The group to configure. */
  group: GlFormatGroup
  /** All entities — used to display member names and for sourceColumns/dialect. */
  entities: GlEntityState[]
  /** Called whenever partner-columns or options change. */
  onPatchGroup: (groupId: string, patch: Partial<GlFormatGroup>) => void
  /**
   * When provided (wizard mode), renders a group-level header-assignment block
   * BEFORE the partner / options config.  The feature gate: when omitted
   * (IngestionPage) the panel behaves exactly as before — no changes to existing
   * callers required.
   */
  onConfirmGroupHeaders?: (groupId: string, headers: string[]) => Promise<void>
  /**
   * Stage gate forwarded to POST /ingest/validate for the consolidated group
   * validation call.  MUST be 'gl' in the wizard to exclude R1-R4/M1 checks
   * (stage=undefined → full catalog → regression).  Omit to keep IngestionPage
   * behaviour unchanged (no validation section rendered here).
   */
  validationStage?: 'gl' | 'coa' | 'partner' | 'all'
  /**
   * Called after each member entity's validation completes or updates (e.g.
   * after an exclusion round-trip).  When provided together with validationStage,
   * the panel renders the consolidated "Validate Format" button and the merged
   * results view.  Omit to keep IngestionPage behaviour unchanged.
   */
  onMemberValidated?: (
    entityIndex: number,
    result: ValidationResponse,
    assembledProfile: Profile,
    excludedLineIds: string[],
  ) => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function GlGroupConfigPanel({
  group,
  entities,
  onPatchGroup,
  onConfirmGroupHeaders,
  validationStage,
  onMemberValidated,
}: GlGroupConfigPanelProps) {
  // Derive source columns and dialect from the representative entity.
  // Falls back to first member that has combined data if the representative
  // hasn't combined yet (edge case after re-index).
  const rep = entities[group.representativeIndex]
  const repOrFirst = rep?.combinedColumns
    ? rep
    : group.memberIndices.map(i => entities[i]).find(e => e?.combinedColumns)

  const sourceColumns = (repOrFirst?.combinedColumns ?? []).filter(c => c !== 'fiscal_year')
  const dialect = repOrFirst?.combinedDialect ?? { decimal: ',', thousands: '.', delimiter: ';', encoding: 'utf-8' }

  // Local copies of group config for immediate UI feedback.
  // Synced up to parent via onPatchGroup on every change.
  const [partnerMode, setPartnerMode] = useState<PartnerColumnsMode>(group.partnerColumnsMode)
  const [partnerSplit, setPartnerSplit] = useState<PartnerColumnsSplit>(
    () => group.partnerColumnsSplit ?? { creditorNoCol: '', debtorNoCol: '', fixedAssetNoCol: '' }
  )
  const [opts, setOpts] = useState<OptionsState>(group.opts)

  // Header-assignment state (wizard mode only — unused when onConfirmGroupHeaders is omitted)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [editingHeaders, setEditingHeaders] = useState(false)

  // Member entity names for display
  const memberNames = group.memberIndices
    .map(i => {
      const e = entities[i]
      if (!e) return `Entity ${i + 1}`
      return e.entityCode.trim() || `Entity ${i + 1}`
    })
    .join(', ')

  // Sign-complete gate
  const signComplete =
    opts.signMode === 'signed'
      ? Boolean(opts.signAmount)
      : opts.signMode === 'amount_dc'
      ? Boolean(opts.signAmount && opts.signDcFlag)
      : Boolean(opts.signSoll && opts.signHaben)

  const splitComplete =
    partnerMode !== 'split' ||
    (Boolean(partnerSplit.creditorNoCol) &&
      Boolean(partnerSplit.debtorNoCol) &&
      Boolean(partnerSplit.fixedAssetNoCol))

  const configComplete = signComplete && splitComplete

  // ── Consolidated validation state (wizard mode only — unused when onMemberValidated is omitted) ──

  const [memberResults, setMemberResults] = useState<Record<number, ValidationResponse>>({})
  const [memberExclusions, setMemberExclusions] = useState<Record<number, string[]>>({})
  const [validating, setValidating] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [excluding, setExcluding] = useState<Record<number, boolean>>({})

  // Preview show-all toggle for the combined GL lines preview (wizard mode only)
  const [showAllPreview, setShowAllPreview] = useState(false)

  // Full-dataset modal (wizard mode only — gated on onMemberValidated)
  const [showDataset, setShowDataset] = useState(false)

  /**
   * Build the GL Profile for a member entity.
   * Mirrors GlEntityCard.buildEntityProfile — must stay in sync with it.
   * stage='gl' is threaded via the validateIngest call, not the profile.
   */
  function buildMemberProfile(entity: GlEntityState, ea: Record<string, string> = {}): Profile {
    const opts = group.opts
    return {
      entity: { mode: 'fixed', value: entity.entityCode },
      fiscal_year: { mode: 'column', value: 'fiscal_year' },
      sign: (() => {
        if (opts.signMode === 'signed')
          return { mode: 'signed' as const, amount: opts.signAmount }
        if (opts.signMode === 'soll_haben')
          return { mode: 'soll_haben' as const, soll: opts.signSoll, haben: opts.signHaben }
        return {
          mode: 'amount_dc' as const,
          amount: opts.signAmount,
          dc_flag: opts.signDcFlag,
          debit_value: opts.signDebitValue,
        }
      })(),
      decimal: opts.decimal || entity.combinedDialect?.decimal || ',',
      thousands: opts.thousands || entity.combinedDialect?.thousands || '.',
      date_dayfirst: opts.dateDayfirst,
      columns: { ...group.mapping },
      linking_strategy: opts.linking,
      entry_type: 'actual',
      ...(Object.keys(ea).length > 0 ? { entity_assignments: ea } : {}),
    }
  }

  /** Re-validate a single member after exclusions change. */
  async function revalidateMember(memberIdx: number, newExclusions: string[]): Promise<void> {
    const entity = entities[memberIdx]
    if (!entity?.combinedFileId) return
    const profile = buildMemberProfile(entity, entity.entityAssignments ?? {})
    try {
      const result = await validateIngest({
        file_id: entity.combinedFileId,
        profile,
        exclude_line_ids: newExclusions,
        // INVARIANT: stage must always be provided when validationStage is set
        // so checks_for_stage(undefined) never returns 'all' (R1-R4/M1 regression).
        ...(validationStage !== undefined ? { stage: validationStage } : {}),
      })
      setMemberResults(prev => ({ ...prev, [memberIdx]: result }))
      onMemberValidated?.(memberIdx, result, profile, newExclusions)
    } catch (e) {
      console.error(`GlGroupConfigPanel: re-validate member ${memberIdx} failed`, e)
    }
  }

  /** Called by MergedValidationReport's S1IssueExplorer when user excludes lines. */
  async function handleExcludeLines(memberIdx: number, lineIds: string[]): Promise<void> {
    const current = memberExclusions[memberIdx] ?? []
    const next = [...new Set([...current, ...lineIds])]
    setMemberExclusions(prev => ({ ...prev, [memberIdx]: next }))
    setExcluding(prev => ({ ...prev, [memberIdx]: true }))
    try {
      await revalidateMember(memberIdx, next)
    } finally {
      setExcluding(prev => ({ ...prev, [memberIdx]: false }))
    }
  }

  /** Run validateIngest for every member entity in the group (sequential). */
  async function validateAllMembers(): Promise<void> {
    if (!onMemberValidated) return
    setValidating(true)
    setValidationError(null)

    // Pre-flight: all required GoBD column-mapping fields must be present in
    // group.mapping before we attempt backend validation.  This prevents the
    // green-validation / failing-commit divergence: if a required field is
    // missing the user sees a clear message here and cannot advance.
    const missingCols = missingRequiredGlColumns(group.mapping)
    if (missingCols.length > 0) {
      const labels = missingCols
        .map(k => `'${GL_COLUMN_FRIENDLY_LABELS[k]}'`)
        .join(', ')
      setValidationError(
        `Required column${missingCols.length > 1 ? 's' : ''} not mapped for ${group.label}: ` +
        `${labels}. ` +
        `Go back to the header confirmation step, assign the missing column${missingCols.length > 1 ? 's' : ''}, then re-validate.`,
      )
      setValidating(false)
      return
    }

    const freshResults: Record<number, ValidationResponse> = {}
    try {
      for (const memberIdx of group.memberIndices) {
        const entity = entities[memberIdx]
        if (!entity?.combinedFileId) continue
        const exclusions = memberExclusions[memberIdx] ?? []
        const profile = buildMemberProfile(entity, entity.entityAssignments ?? {})
        const result = await validateIngest({
          file_id: entity.combinedFileId,
          profile,
          exclude_line_ids: exclusions,
          ...(validationStage !== undefined ? { stage: validationStage } : {}),
        })
        freshResults[memberIdx] = result
        onMemberValidated(memberIdx, result, profile, exclusions)
      }
      setMemberResults(freshResults)
    } catch (e) {
      setValidationError(e instanceof Error ? e.message : 'Validation failed')
    } finally {
      setValidating(false)
    }
  }

  // ── Header-assignment computed data (wizard mode only) ─────────────────────
  // These are only used when onConfirmGroupHeaders is provided. Computed
  // unconditionally to keep hook ordering stable.

  /** Number of sample rows shown per member entity in the collapsed preview. */
  const PER_MEMBER_PREVIEW = 5

  const repColumns = rep?.combinedColumns ?? []

  // Members whose column count matches the representative (positional re-key is valid)
  const eligibleMembers = group.memberIndices
    .map(i => ({ i, e: entities[i] }))
    .filter(({ e }) =>
      e?.combinedSample != null &&
      (e.combinedColumns?.length ?? 0) === repColumns.length
    )

  // Re-key each member's rows positionally to the rep's column names.
  // Adds __entity__ key so the entity column can identify the source.
  function rekeyRows(
    memberIndex: number,
    memberCols: string[],
    rows: Record<string, unknown>[],
  ): Record<string, unknown>[] {
    const entityCode = entities[memberIndex]?.entityCode?.trim() || `Entity ${memberIndex + 1}`
    return rows.map(row => {
      const out: Record<string, unknown> = {}
      repColumns.forEach((rc, k) => { out[rc] = row[memberCols[k]] })
      out['__entity__'] = entityCode
      return out
    })
  }

  const fullSample: Record<string, unknown>[] = eligibleMembers.flatMap(({ i, e }) =>
    rekeyRows(i, e.combinedColumns ?? [], e.combinedSample ?? [])
  )

  const collapsedSample: Record<string, unknown>[] = eligibleMembers.flatMap(({ i, e }) =>
    rekeyRows(i, e.combinedColumns ?? [], (e.combinedSample ?? []).slice(0, PER_MEMBER_PREVIEW))
  )

  const entityColumn: { key: string; label: string } | undefined =
    group.memberIndices.length > 1 ? { key: '__entity__', label: 'Entity' } : undefined

  const suggestedHeaders: Record<string, string> = editingHeaders
    ? Object.fromEntries(repColumns.filter(c => c !== 'fiscal_year').map(c => [c, c]))
    : (rep?.combinedSuggestedHeaders ?? {})

  // Aggregated misparse warning from member entities
  const misparsedEntityCodes = group.memberIndices
    .map(i => entities[i])
    .filter(e => e?.combinedColumnWarning)
    .map(e => e.entityCode?.trim() || 'unknown')

  // ── Full-dataset modal data (wizard mode only) ─────────────────────────────
  // Members with a combined file → request items for the dataset endpoint.
  // Only computed when the gate is active (configComplete && onMemberValidated).
  const datasetMembers: Array<{ file_id: string; entity: string }> =
    configComplete && onMemberValidated
      ? group.memberIndices
          .map(i => entities[i])
          .filter((e): e is NonNullable<typeof e> & { combinedFileId: string } =>
            Boolean(e?.combinedFileId)
          )
          .map(e => ({ file_id: e.combinedFileId, entity: e.entityCode }))
      : []

  // Profile for the dataset endpoint: use the representative entity's config.
  // The `entity` field in the profile is overridden per-row by the members list.
  const datasetProfile: Profile | null =
    configComplete && onMemberValidated && repOrFirst
      ? buildMemberProfile(repOrFirst, repOrFirst.entityAssignments ?? {})
      : null

  // Gate: partner + options config is only shown once headers are confirmed (wizard)
  // or always (IngestionPage, where onConfirmGroupHeaders is omitted)
  const showConfig = !onConfirmGroupHeaders || group.headersConfirmed

  function patchPartnerMode(mode: PartnerColumnsMode) {
    setPartnerMode(mode)
    onPatchGroup(group.id, {
      partnerColumnsMode: mode,
      partnerColumnsSplit: mode === 'split' ? partnerSplit : undefined,
    })
    // Config changed — stale validation results are no longer valid.
    if (Object.keys(memberResults).length > 0) setMemberResults({})
  }

  function patchPartnerSplit(field: keyof PartnerColumnsSplit, value: string) {
    const next = { ...partnerSplit, [field]: value }
    setPartnerSplit(next)
    onPatchGroup(group.id, { partnerColumnsSplit: next })
    if (Object.keys(memberResults).length > 0) setMemberResults({})
  }

  function patchOpts(next: OptionsState) {
    setOpts(next)
    onPatchGroup(group.id, { opts: next })
    // Config changed — stale validation results are no longer valid.
    if (Object.keys(memberResults).length > 0) setMemberResults({})
  }

  return (
    <>
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Panel header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
        <div className="flex items-center gap-3">
          <span
            className="text-xs font-bold uppercase tracking-wide px-2 py-0.5 rounded"
            style={{ backgroundColor: '#EDF2FF', color: '#1E3A5F' }}
          >
            {group.label}
          </span>
          <span className="text-sm text-slate-700">
            {group.memberIndices.length === 1 ? (
              <span className="font-medium">{memberNames}</span>
            ) : (
              <>
                <span className="font-medium">{group.memberIndices.length} entities</span>
                <span className="text-slate-400 ml-1">— {memberNames}</span>
              </>
            )}
          </span>
          <span className="text-slate-300">·</span>
          <span className="text-xs text-slate-500">{group.columnCount} columns</span>
        </div>
        {configComplete ? (
          <span className="text-xs text-emerald-700 font-semibold">Config complete</span>
        ) : (
          <span className="text-xs text-amber-700 font-medium">Needs config</span>
        )}
      </div>

      <div className="px-5 py-4 space-y-5">

        {/* Aggregated misparse warning (wizard mode only) */}
        {onConfirmGroupHeaders && misparsedEntityCodes.length > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            Column count warning for:{' '}
            <span className="font-semibold">{misparsedEntityCodes.join(', ')}</span>.
            Verify the correct delimiter and header settings for each file before assigning headers.
          </div>
        )}

        {/* Group-level header assignment (wizard mode only) */}
        {onConfirmGroupHeaders && repColumns.length > 0 && (!group.headersConfirmed || editingHeaders) && (
          <ConfirmHeadersStep
            columns={repColumns}
            suggestedHeaders={suggestedHeaders}
            sample={fullSample}
            collapsedSample={collapsedSample}
            entityColumn={entityColumn}
            confirming={confirming}
            confirmError={confirmError}
            onConfirm={async (headers) => {
              if (!onConfirmGroupHeaders) return
              setConfirming(true)
              setConfirmError(null)
              try {
                await onConfirmGroupHeaders(group.id, headers)
                setEditingHeaders(false)
              } catch (e) {
                setConfirmError(e instanceof Error ? e.message : 'Failed to apply headers')
              } finally {
                setConfirming(false)
              }
            }}
            onBack={editingHeaders ? () => setEditingHeaders(false) : undefined}
          />
        )}

        {/* Headers confirmed bar (wizard mode only) */}
        {onConfirmGroupHeaders && group.headersConfirmed && !editingHeaders && (
          <div className="rounded-lg border border-blue-100 bg-blue-50/60 px-4 py-2 text-xs text-blue-800 flex items-center justify-between gap-3">
            <span>
              Column headers confirmed — {group.columnCount} columns
            </span>
            <button
              type="button"
              onClick={() => setEditingHeaders(true)}
              className="text-xs text-blue-700 underline underline-offset-1 hover:text-blue-900 whitespace-nowrap"
            >
              Edit column headers
            </button>
          </div>
        )}

        {/* Hint: complete header assignment before accessing partner / options config */}
        {!showConfig && (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
            Confirm column headers above to configure partner columns and options.
          </div>
        )}

        {/* Partner-column layout + transform options + completion indicator —
            gated behind showConfig so they only appear once headers are confirmed
            (wizard mode).  In IngestionPage onConfirmGroupHeaders is omitted so
            showConfig is always true and existing behaviour is unchanged. */}
        {showConfig && (
          <>
            {/* Partner-column layout */}
            <StepCard
              title="Partner column layout"
              subtitle="How creditor, debtor, and fixed-asset numbers are stored in this format."
            >
              <div className="space-y-3">
                <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
                  <input
                    type="radio"
                    name={`partnerMode-group-${group.id}`}
                    value="single"
                    checked={partnerMode === 'single'}
                    onChange={() => patchPartnerMode('single')}
                    className="mt-0.5 accent-blue-600"
                  />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">One combined column (recommended)</p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      A single "Source No." column holds the partner number, and a companion "Source Type"
                      column distinguishes creditor, debtor, or fixed asset.
                    </p>
                  </div>
                </label>
                <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
                  <input
                    type="radio"
                    name={`partnerMode-group-${group.id}`}
                    value="split"
                    checked={partnerMode === 'split'}
                    onChange={() => patchPartnerMode('split')}
                    className="mt-0.5 accent-blue-600"
                  />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">Three separate columns</p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Creditor number, debtor number, and fixed-asset number each have their own column.
                    </p>
                  </div>
                </label>

                {partnerMode === 'split' && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 space-y-3">
                    <p className="text-xs font-semibold text-amber-800">Map the three partner columns</p>
                    {(
                      [
                        ['creditorNoCol', 'Creditor No. column'],
                        ['debtorNoCol', 'Debtor No. column'],
                        ['fixedAssetNoCol', 'Fixed-Asset No. column'],
                      ] as [keyof PartnerColumnsSplit, string][]
                    ).map(([field, label]) => (
                      <div key={field} className="flex items-center gap-3">
                        <label className="text-xs text-amber-900 w-40 shrink-0">{label}</label>
                        <select
                          value={partnerSplit[field]}
                          onChange={e => patchPartnerSplit(field, e.target.value)}
                          className="rounded-md border border-slate-300 px-2 py-1.5 text-sm flex-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                          <option value="">-- Select column --</option>
                          {sourceColumns.map(c => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>
                      </div>
                    ))}
                  </div>
                )}

                {!splitComplete && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                    Map all three partner columns to continue.
                  </div>
                )}
              </div>
            </StepCard>

            {/* Transform options */}
            <OptionsStep
              sourceColumns={sourceColumns}
              dialect={dialect}
              state={opts}
              onChange={patchOpts}
              onSaveProfile={() => {}}
              savingProfile={false}
              profileSaved={false}
            />

            {/* Sign-complete gate indicator */}
            {configComplete ? (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 font-medium">
                Format config complete — all {group.memberIndices.length}{' '}
                {group.memberIndices.length === 1 ? 'entity is' : 'entities are'} ready to validate.
              </div>
            ) : (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                {!signComplete && (
                  <p>
                    Select the{' '}
                    {opts.signMode === 'soll_haben'
                      ? 'debit and credit columns'
                      : 'amount column'}
                    {opts.signMode === 'amount_dc' ? ' and the debit/credit indicator' : ''}{' '}
                    in "Amount sign logic" above.
                  </p>
                )}
                {!splitComplete && <p>Map all three partner columns above.</p>}
              </div>
            )}

            {/* ── Preview — combined GL lines (wizard mode only, read-only) ── */}
            {configComplete && onMemberValidated && fullSample.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <h4 className="text-sm font-semibold text-slate-700">
                    Preview — combined GL lines for{' '}
                    <span className="font-mono bg-slate-100 px-1 py-0.5 rounded text-slate-800">
                      {group.label}
                    </span>
                  </h4>
                  <span className="text-xs text-slate-400 whitespace-nowrap">
                    {(showAllPreview ? fullSample : collapsedSample).length} of {fullSample.length}{' '}
                    {fullSample.length === 1 ? 'row' : 'rows'} shown
                  </span>
                </div>
                <div className="overflow-x-auto rounded border border-slate-200 bg-white">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="bg-slate-50 border-b border-slate-200 text-left text-slate-500">
                        {entityColumn && (
                          <th className="py-1.5 px-2 font-semibold whitespace-nowrap">
                            {entityColumn.label}
                          </th>
                        )}
                        {repColumns.map(c => (
                          <th key={c} className="py-1.5 px-2 font-semibold whitespace-nowrap">
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(showAllPreview ? fullSample : collapsedSample).map((row, i) => (
                        <tr key={i} className="border-b border-slate-100 last:border-0">
                          {entityColumn && (
                            <td className="py-1.5 px-2 font-mono text-slate-600 whitespace-nowrap">
                              {String(row['__entity__'] ?? '—')}
                            </td>
                          )}
                          {repColumns.map(c => (
                            <td key={c} className="py-1.5 px-2 font-mono text-slate-700 whitespace-nowrap">
                              {row[c] == null ? '—' : String(row[c])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {fullSample.length > collapsedSample.length && (
                  <button
                    type="button"
                    onClick={() => setShowAllPreview(v => !v)}
                    className="text-xs text-blue-600 hover:underline"
                  >
                    {showAllPreview ? 'Show fewer' : `Show all ${fullSample.length} rows`}
                  </button>
                )}
              </div>
            )}

            {/* ── Consolidated validation (wizard mode only — onMemberValidated gate) ── */}
            {configComplete && onMemberValidated && (
              <div className="space-y-4">
                <div className="flex items-center gap-3 flex-wrap">
                  <button
                    type="button"
                    disabled={validating}
                    onClick={() => { void validateAllMembers() }}
                    className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {validating
                      ? `Validating… (${group.memberIndices.length} ${group.memberIndices.length === 1 ? 'entity' : 'entities'})`
                      : Object.keys(memberResults).length > 0
                      ? `Re-validate ${group.label}`
                      : `Validate ${group.label}`}
                  </button>
                  {datasetMembers.length > 0 && datasetProfile && (
                    <button
                      type="button"
                      onClick={() => setShowDataset(true)}
                      className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition"
                    >
                      View full dataset
                    </button>
                  )}
                  {Object.keys(memberResults).length > 0 && !validating && (
                    <span className="text-xs text-slate-500">
                      {group.memberIndices.length}{' '}
                      {group.memberIndices.length === 1 ? 'entity validated' : 'entities validated'}
                    </span>
                  )}
                </div>

                {validationError && (
                  <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
                    {validationError}
                  </div>
                )}

                {Object.keys(memberResults).length > 0 && (() => {
                  const membersData: MemberValidationData[] = group.memberIndices
                    .filter(idx => memberResults[idx] !== undefined && entities[idx]?.combinedFileId)
                    .map(idx => {
                      const entity = entities[idx]!
                      const profile = buildMemberProfile(entity, entity.entityAssignments ?? {})
                      return {
                        entityCode: entity.entityCode.trim() || `Entity ${idx + 1}`,
                        entityIndex: idx,
                        result: memberResults[idx],
                        context: {
                          file_id: entity.combinedFileId!,
                          profile,
                          exclude_line_ids: memberExclusions[idx] ?? [],
                        },
                      }
                    })
                  if (membersData.length === 0) return null
                  return (
                    <MergedValidationReport
                      members={membersData}
                      excluding={excluding}
                      onExcludeLines={handleExcludeLines}
                    />
                  )
                })()}
              </div>
            )}
          </>
        )}
      </div>
    </div>
    {showDataset && datasetProfile && (
      <FullDatasetModal
        title={`Full dataset — ${group.label}`}
        members={datasetMembers}
        profile={datasetProfile}
        onClose={() => setShowDataset(false)}
      />
    )}
    </>
  )
}
