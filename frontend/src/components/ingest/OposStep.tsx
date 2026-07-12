/**
 * OposStep.tsx — provisioning sub-flow for Open-Items Lists (D4).
 *
 * Two loading modes:
 *   per_side      — separate uploads for Debitor (AR) and Kreditor (AP) (default, fully functional)
 *   combined_sides — one file containing rows for both sides, filtered by a discriminator column
 *                   Commits via POST /api/v1/opos/combined/commit, which splits the file on the
 *                   side column and writes fact_opos_debitor + fact_opos_kreditor in one step.
 *
 * Flow (per_side): entity source selection -> upload (per FY / per entity x FY) ->
 *   step-by-step column mapping (StepColumnMapper) -> per-slot commit.
 *
 * Aging computation (is_open bucketing, DSO/DPO) is not performed in the UI — see
 * docs/financial-logic.md (F3/F4). Data is stored pass-through; aging_band is NULL.
 */

import { useMemo, useState } from 'react'
import EntitySourceSelector, { type EntitySource } from './EntitySourceSelector'
import PerEntityPager from './PerEntityPager'
import PerYearPager from './PerYearPager'
import { uploadOposFile, commitOpos, commitOposCombined, type OposSide } from '../../lib/gdpduApi'
import { glFiscalYearLabel } from '../../lib/fiscalYear'
import StepColumnMapper from '../mapper/StepColumnMapper'
import { fromNamedPreview } from '../mapper/normalizePreview'
import { buildOposSteps, toOposResult } from '../mapper/oposSteps'
import type { Answers } from '../mapper/stepMapperTypes'
import FileDrop from '../budget/chat/FileDrop'

// ---------------------------------------------------------------------------
// State shapes — exported so ProjectSetupWizard can reference them in
// WizardState and the PATCH_OPOS reducer action.
// ---------------------------------------------------------------------------

export interface WizardOposUpload {
  entity_index: number   // 0 = "all entities" in combined mode
  entity_name: string
  fy_label: string
  file_id?: string
  columns?: string[]
  sample?: Record<string, unknown>[]
}

export interface WizardOposSideState {
  viewMode: 'combined' | 'per_entity'
  /** Combined mode: column identifying entity per row (e.g. Buchungskreis). */
  entityColumn?: string
  /** One entry per entity_index x fy_label slot. */
  uploads: WizardOposUpload[]
  /** file_id of the first uploaded file — drives the column-mapping preview. */
  previewFileId?: string
  /** target_field -> source_column mapping for all committed files on this side. */
  columnMap: Record<string, string>
}

/** State for combined-sides mode: one file contains rows for both debitor and kreditor. */
export interface WizardOposCombinedSidesState {
  viewMode: 'combined' | 'per_entity'
  entityColumn?: string
  uploads: WizardOposUpload[]
  previewFileId?: string
  columnMap: Record<string, string>
  /** Column in the file that identifies whether each row is debitor or kreditor. */
  sideColumn?: string
  /** Value in sideColumn that means Debitor (AR), e.g. "D", "Debitor". */
  debitorValue?: string
  /** Value in sideColumn that means Kreditor (AP), e.g. "K", "Kreditor". */
  kreditorValue?: string
}

export interface WizardOposState {
  provided: boolean
  /**
   * 'per_side' (default) — separate upload for each side (Debitor / Kreditor).
   * 'combined_sides' — one file containing rows for both sides, committed via
   *   POST /api/v1/opos/combined/commit (splits on the side-discriminator column).
   */
  loadMode: 'per_side' | 'combined_sides'
  /** State for combined-sides upload mode. */
  combinedSides: WizardOposCombinedSidesState
  debitor: WizardOposSideState
  kreditor: WizardOposSideState
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface OposStepProps {
  opos: WizardOposState
  entities: Array<{ code: string; prefix: string; name: string }>
  glYears: number[]
  fyEndMonth: number
  /** Patch the top-level opos state (shallow-merged by the wizard reducer). */
  onPatch: (patch: Partial<WizardOposState>) => void
}

// ---------------------------------------------------------------------------
// OposSidePanel — renders one side (debitor or kreditor) in per_side mode
// ---------------------------------------------------------------------------

interface OposSidePanelProps {
  side: OposSide
  sideState: WizardOposSideState
  entities: Array<{ code: string; prefix: string; name: string }>
  fyLabels: string[]
  onPatchSide: (patch: Partial<WizardOposSideState>) => void
}

function OposSidePanel({ side, sideState, entities, fyLabels, onPatchSide }: OposSidePanelProps) {
  const validEntities = entities.filter(e => e.code.trim())
  const viewMode = sideState.viewMode

  const [uploading, setUploading]     = useState<Record<string, boolean>>({})
  const [uploadError, setUploadError] = useState<Record<string, string>>({})
  const [committing, setCommitting]   = useState<Record<string, boolean>>({})
  const [commitMsg, setCommitMsg]     = useState<Record<string, string>>({})
  const [commitErr, setCommitErr]     = useState<Record<string, string>>({})

  function getUpload(entityIndex: number, fyLabel: string) {
    return sideState.uploads.find(u => u.entity_index === entityIndex && u.fy_label === fyLabel)
  }

  function isEntityStaged(entityIndex: number) {
    return sideState.uploads.some(u => u.entity_index === entityIndex && !!u.file_id)
  }

  const previewUpload =
    sideState.uploads.find(u => u.file_id === sideState.previewFileId) ??
    sideState.uploads.find(u => !!u.file_id)
  const previewColumns = previewUpload?.columns ?? []
  const previewSample  = previewUpload?.sample  ?? []
  const hasUploads     = sideState.uploads.some(u => !!u.file_id)

  // Seed mapper from existing wizard state
  const mapperInitial = useMemo((): Answers => {
    const ans: Answers = {}
    for (const [field, col] of Object.entries(sideState.columnMap)) {
      ans[field] = { t: 'column', id: col }
    }
    if (sideState.entityColumn)
      ans['entity_column'] = { t: 'column', id: sideState.entityColumn }
    return ans
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewColumns.join(','), sideState.columnMap, sideState.entityColumn])

  async function handleFileUpload(
    entityIndex: number,
    entityName: string,
    fyLabel: string,
    file: File,
  ) {
    const slotKey = `${entityIndex}__${fyLabel}`
    setUploading(prev => ({ ...prev, [slotKey]: true }))
    setUploadError(prev => ({ ...prev, [slotKey]: '' }))
    try {
      const result = await uploadOposFile(side, file)
      const newEntry: WizardOposUpload = {
        entity_index: entityIndex,
        entity_name: entityName,
        fy_label: fyLabel,
        file_id: result.file_id,
        columns: result.columns,
        sample: result.sample,
      }
      const rest = sideState.uploads.filter(
        u => !(u.entity_index === entityIndex && u.fy_label === fyLabel)
      )
      const allUploads = [...rest, newEntry]
      const previewFileId = allUploads.find(u => !!u.file_id)?.file_id
      onPatchSide({ uploads: allUploads, previewFileId })
    } catch (e) {
      setUploadError(prev => ({
        ...prev,
        [slotKey]: e instanceof Error ? e.message : 'Upload failed',
      }))
    } finally {
      setUploading(prev => ({ ...prev, [slotKey]: false }))
    }
  }

  async function handleCommit(entityIndex: number, entityPrefix: string, fyLabel: string) {
    const upload = getUpload(entityIndex, fyLabel)
    if (!upload?.file_id) return
    const slotKey = `${entityIndex}__${fyLabel}`
    setCommitting(prev => ({ ...prev, [slotKey]: true }))
    setCommitMsg(prev => ({ ...prev, [slotKey]: '' }))
    setCommitErr(prev => ({ ...prev, [slotKey]: '' }))
    try {
      const result = await commitOpos(side, {
        file_id: upload.file_id,
        fy_label: fyLabel,
        entity_mode: viewMode === 'per_entity' ? 'per_entity' : 'combined',
        ...(viewMode === 'per_entity' && entityPrefix ? { entity_prefix: entityPrefix } : {}),
        ...(viewMode === 'combined' && sideState.entityColumn
          ? { entity_column: sideState.entityColumn }
          : {}),
        column_map: sideState.columnMap,
      })
      setCommitMsg(prev => ({
        ...prev,
        [slotKey]: `${result.inserted} rows committed for ${fyLabel}`,
      }))
    } catch (e) {
      setCommitErr(prev => ({
        ...prev,
        [slotKey]: e instanceof Error ? e.message : 'Commit failed',
      }))
    } finally {
      setCommitting(prev => ({ ...prev, [slotKey]: false }))
    }
  }

  function renderUploadZone(
    entityIndex: number,
    entityName: string,
    fyLabel: string,
    entityPrefix = '',
  ) {
    const slotKey  = `${entityIndex}__${fyLabel}`
    const existing = getUpload(entityIndex, fyLabel)
    const isUp     = uploading[slotKey] ?? false
    const upErr    = uploadError[slotKey] ?? ''
    const isCom    = committing[slotKey] ?? false
    const comMsg   = commitMsg[slotKey] ?? ''
    const comErr   = commitErr[slotKey] ?? ''
    const staged   = !!existing?.file_id
    const hasMap   = Object.keys(sideState.columnMap).length > 0

    return (
      <div key={slotKey} className="space-y-2">
        {staged && (
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-emerald-700">File staged — {fyLabel}</span>
            {existing?.columns && (
              <span className="text-xs text-slate-500">({existing.columns.length} columns)</span>
            )}
          </div>
        )}

        <FileDrop
          onFile={f => void handleFileUpload(entityIndex, entityName, fyLabel, f)}
          accept=".xlsx,.xls,.csv"
          loading={isUp}
          label={staged ? 'Replace file' : 'Drop .xlsx / .csv here'}
          hint={staged ? 'Drop or click Browse to select a different file' : 'or click Browse to select'}
        />

        {upErr && <p className="text-xs text-red-600">{upErr}</p>}

        {staged && (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={isCom || !hasMap}
              onClick={() => void handleCommit(entityIndex, entityPrefix, fyLabel)}
              className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40 transition"
              title={!hasMap ? 'Map at least one target field before committing' : undefined}
            >
              {isCom ? 'Committing…' : `Commit ${fyLabel}`}
            </button>
            {comMsg && <span className="text-xs text-emerald-600">{comMsg}</span>}
            {comErr && <span className="text-xs text-red-600">{comErr}</span>}
          </div>
        )}
      </div>
    )
  }

  const sideLabel = side === 'debitor' ? 'Debitor (AR)' : 'Kreditor (AP)'

  return (
    <div className="space-y-5">

      {/* Entity source */}
      <EntitySourceSelector
        value={viewMode === 'per_entity' ? 'per_entity' : 'combined'}
        onChange={(v: EntitySource) =>
          onPatchSide({
            viewMode: v === 'per_entity' ? 'per_entity' : 'combined',
            uploads: [],
            previewFileId: undefined,
          })
        }
        dataLabel={`${sideLabel} open-items data`}
      />

      {/* Upload zones */}
      <div className="space-y-3">
        <p className="text-sm font-medium text-slate-700">
          Upload {sideLabel} file{fyLabels.length > 1 ? 's' : ''}
        </p>
        <p className="text-xs text-slate-500">
          One file per fiscal year{viewMode === 'per_entity' ? ' per entity' : ''}.
          Excel (.xlsx) or CSV accepted.
          {fyLabels.length > 0 && ` FY range: ${fyLabels[0]}–${fyLabels[fyLabels.length - 1]}.`}
        </p>

        {viewMode === 'combined' && (
          <PerYearPager
            fyLabels={fyLabels}
            stagedCount={fyLabels.filter(fy => !!getUpload(0, fy)?.file_id).length}
            renderYear={fyLabel =>
              renderUploadZone(0, 'All entities (combined)', fyLabel)
            }
          />
        )}

        {viewMode === 'per_entity' && (
          <PerEntityPager
            entities={validEntities}
            stagedCount={validEntities.filter((_, i) => isEntityStaged(i)).length}
            renderEntity={(entity, idx) => (
              <PerYearPager
                fyLabels={fyLabels}
                stagedCount={fyLabels.filter(fy => !!getUpload(idx, fy)?.file_id).length}
                renderYear={fyLabel =>
                  renderUploadZone(idx, entity.name || entity.code, fyLabel, entity.prefix || entity.code)
                }
              />
            )}
          />
        )}
      </div>

      {/* Column mapping — shown after first upload via StepColumnMapper */}
      {hasUploads && previewColumns.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Column mapping
            </span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>
          <p className="text-xs text-slate-500">
            Map source columns to OPOS target fields. Account is required; all other
            fields are optional.
            {viewMode === 'combined' && ' Then identify the entity column.'}
            {' '}The same mapping is applied to all uploaded files on this side.
          </p>
          <StepColumnMapper
            key={previewColumns.join(',')}
            preview={fromNamedPreview(previewColumns, previewSample)}
            buildSteps={() => buildOposSteps('per_side', viewMode)}
            toResult={toOposResult}
            initial={mapperInitial}
            onComplete={r =>
              onPatchSide({
                columnMap: r.columnMap,
                entityColumn: r.entityColumn,
              })
            }
          />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// OposCombinedSidesPanel — one file containing both debitor + kreditor rows
// ---------------------------------------------------------------------------

interface OposCombinedSidesPanelProps {
  combinedSides: WizardOposCombinedSidesState
  entities: Array<{ code: string; prefix: string; name: string }>
  fyLabels: string[]
  onPatch: (patch: Partial<WizardOposCombinedSidesState>) => void
}

function OposCombinedSidesPanel({
  combinedSides,
  entities,
  fyLabels,
  onPatch,
}: OposCombinedSidesPanelProps) {
  const validEntities = entities.filter(e => e.code.trim())
  const [uploading, setUploading]     = useState<Record<string, boolean>>({})
  const [uploadError, setUploadError] = useState<Record<string, string>>({})
  const [committing, setCommitting]   = useState<Record<string, boolean>>({})
  const [commitMsg, setCommitMsg]     = useState<Record<string, string>>({})
  const [commitErr, setCommitErr]     = useState<Record<string, string>>({})

  const previewUpload =
    combinedSides.uploads.find(u => u.file_id === combinedSides.previewFileId) ??
    combinedSides.uploads.find(u => !!u.file_id)
  const previewColumns = previewUpload?.columns ?? []
  const previewSample  = previewUpload?.sample  ?? []
  const hasUploads     = combinedSides.uploads.some(u => !!u.file_id)

  // Commit prerequisites: at least one target field mapped, a side column, and a
  // distinct debitor/kreditor value for the split.
  const hasMap        = Object.keys(combinedSides.columnMap).length > 0
  const hasSideConfig =
    !!combinedSides.sideColumn &&
    !!combinedSides.debitorValue &&
    !!combinedSides.kreditorValue &&
    combinedSides.debitorValue !== combinedSides.kreditorValue
  const canCommit     = hasMap && hasSideConfig

  // Seed mapper from existing wizard state
  const mapperInitial = useMemo((): Answers => {
    const ans: Answers = {}
    for (const [field, col] of Object.entries(combinedSides.columnMap)) {
      ans[field] = { t: 'column', id: col }
    }
    if (combinedSides.entityColumn)
      ans['entity_column'] = { t: 'column', id: combinedSides.entityColumn }
    if (combinedSides.sideColumn)
      ans['side_column'] = { t: 'column', id: combinedSides.sideColumn }
    if (combinedSides.debitorValue)
      ans['debitor_value'] = { t: 'choice', value: combinedSides.debitorValue }
    if (combinedSides.kreditorValue)
      ans['kreditor_value'] = { t: 'choice', value: combinedSides.kreditorValue }
    return ans
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    previewColumns.join(','),
    combinedSides.columnMap,
    combinedSides.entityColumn,
    combinedSides.sideColumn,
    combinedSides.debitorValue,
    combinedSides.kreditorValue,
  ])

  function getUpload(entityIndex: number, fyLabel: string) {
    return combinedSides.uploads.find(u => u.entity_index === entityIndex && u.fy_label === fyLabel)
  }

  function isEntityStaged(entityIndex: number) {
    return combinedSides.uploads.some(u => u.entity_index === entityIndex && !!u.file_id)
  }

  async function handleFileUpload(
    entityIndex: number,
    entityName: string,
    fyLabel: string,
    file: File,
  ) {
    const slotKey = `${entityIndex}__${fyLabel}`
    setUploading(prev => ({ ...prev, [slotKey]: true }))
    setUploadError(prev => ({ ...prev, [slotKey]: '' }))
    try {
      // Side param is for validation only; file storage is side-agnostic
      const result = await uploadOposFile('debitor', file)
      const newEntry: WizardOposUpload = {
        entity_index: entityIndex,
        entity_name: entityName,
        fy_label: fyLabel,
        file_id: result.file_id,
        columns: result.columns,
        sample: result.sample,
      }
      const rest = combinedSides.uploads.filter(
        u => !(u.entity_index === entityIndex && u.fy_label === fyLabel)
      )
      const allUploads = [...rest, newEntry]
      const previewFileId = allUploads.find(u => !!u.file_id)?.file_id
      onPatch({ uploads: allUploads, previewFileId })
    } catch (e) {
      setUploadError(prev => ({
        ...prev,
        [slotKey]: e instanceof Error ? e.message : 'Upload failed',
      }))
    } finally {
      setUploading(prev => ({ ...prev, [slotKey]: false }))
    }
  }

  async function handleCommit(entityIndex: number, entityPrefix: string, fyLabel: string) {
    const upload = getUpload(entityIndex, fyLabel)
    if (!upload?.file_id) return
    const slotKey = `${entityIndex}__${fyLabel}`
    setCommitting(prev => ({ ...prev, [slotKey]: true }))
    setCommitMsg(prev => ({ ...prev, [slotKey]: '' }))
    setCommitErr(prev => ({ ...prev, [slotKey]: '' }))
    try {
      const result = await commitOposCombined({
        file_id: upload.file_id,
        fy_label: fyLabel,
        entity_mode: combinedSides.viewMode === 'per_entity' ? 'per_entity' : 'combined',
        ...(combinedSides.viewMode === 'per_entity' && entityPrefix ? { entity_prefix: entityPrefix } : {}),
        ...(combinedSides.viewMode === 'combined' && combinedSides.entityColumn
          ? { entity_column: combinedSides.entityColumn }
          : {}),
        column_map: combinedSides.columnMap,
        side_column: combinedSides.sideColumn!,
        debitor_value: combinedSides.debitorValue!,
        kreditor_value: combinedSides.kreditorValue!,
      })
      const skippedNote = result.skipped_unmatched
        ? `, ${result.skipped_unmatched} unmatched skipped`
        : ''
      setCommitMsg(prev => ({
        ...prev,
        [slotKey]:
          `${result.debitor_inserted} debitor + ${result.kreditor_inserted} kreditor ` +
          `rows committed${skippedNote} for ${fyLabel}`,
      }))
    } catch (e) {
      setCommitErr(prev => ({
        ...prev,
        [slotKey]: e instanceof Error ? e.message : 'Commit failed',
      }))
    } finally {
      setCommitting(prev => ({ ...prev, [slotKey]: false }))
    }
  }

  function renderUploadZone(
    entityIndex: number,
    entityName: string,
    fyLabel: string,
    entityPrefix = '',
  ) {
    const slotKey  = `${entityIndex}__${fyLabel}`
    const existing = getUpload(entityIndex, fyLabel)
    const isUp     = uploading[slotKey] ?? false
    const upErr    = uploadError[slotKey] ?? ''
    const isCom    = committing[slotKey] ?? false
    const comMsg   = commitMsg[slotKey] ?? ''
    const comErr   = commitErr[slotKey] ?? ''
    const staged   = !!existing?.file_id

    return (
      <div key={slotKey} className="space-y-2">
        {staged && (
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-emerald-700">File staged — {fyLabel}</span>
            {existing?.columns && (
              <span className="text-xs text-slate-500">({existing.columns.length} columns)</span>
            )}
          </div>
        )}

        <FileDrop
          onFile={f => void handleFileUpload(entityIndex, entityName, fyLabel, f)}
          accept=".xlsx,.xls,.csv"
          loading={isUp}
          label={staged ? 'Replace file' : 'Drop .xlsx / .csv here'}
          hint={staged ? 'Drop or click Browse to select a different file' : 'or click Browse to select'}
        />

        {upErr && <p className="text-xs text-red-600">{upErr}</p>}

        {staged && (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={isCom || !canCommit}
              onClick={() => void handleCommit(entityIndex, entityPrefix, fyLabel)}
              className="rounded-md border border-blue-300 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40 transition"
              title={
                !canCommit
                  ? 'Map at least one target field and set the side column with distinct debitor / kreditor values before committing'
                  : undefined
              }
            >
              {isCom ? 'Committing…' : `Commit ${fyLabel}`}
            </button>
            {comMsg && <span className="text-xs text-emerald-600">{comMsg}</span>}
            {comErr && <span className="text-xs text-red-600">{comErr}</span>}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-5">

      {/* Entity source */}
      <EntitySourceSelector
        value={combinedSides.viewMode === 'per_entity' ? 'per_entity' : 'combined'}
        onChange={(v: EntitySource) =>
          onPatch({
            viewMode: v === 'per_entity' ? 'per_entity' : 'combined',
            uploads: [],
            previewFileId: undefined,
          })
        }
        dataLabel="combined OPOS data (both sides)"
      />

      {/* Upload zones */}
      <div className="space-y-3">
        <p className="text-sm font-medium text-slate-700">
          Upload combined OPOS file{fyLabels.length > 1 ? 's' : ''}
        </p>
        <p className="text-xs text-slate-500">
          One file per fiscal year{combinedSides.viewMode === 'per_entity' ? ' per entity' : ''}.
          The file must contain rows for both Debitor (AR) and Kreditor (AP).
          Excel (.xlsx) or CSV accepted.
          {fyLabels.length > 0 && ` FY range: ${fyLabels[0]}–${fyLabels[fyLabels.length - 1]}.`}
        </p>

        {combinedSides.viewMode === 'combined' && (
          <PerYearPager
            fyLabels={fyLabels}
            stagedCount={fyLabels.filter(fy => !!getUpload(0, fy)?.file_id).length}
            renderYear={fyLabel =>
              renderUploadZone(0, 'All entities (combined)', fyLabel)
            }
          />
        )}

        {combinedSides.viewMode === 'per_entity' && (
          <PerEntityPager
            entities={validEntities}
            stagedCount={validEntities.filter((_, i) => isEntityStaged(i)).length}
            renderEntity={(entity, idx) => (
              <PerYearPager
                fyLabels={fyLabels}
                stagedCount={fyLabels.filter(fy => !!getUpload(idx, fy)?.file_id).length}
                renderYear={fyLabel =>
                  renderUploadZone(idx, entity.name || entity.code, fyLabel, entity.prefix || entity.code)
                }
              />
            )}
          />
        )}
      </div>

      {/* Column mapping + side discriminator — shown after first upload via StepColumnMapper */}
      {hasUploads && previewColumns.length > 0 && (
        <div className="space-y-5">
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-slate-200" />
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Column mapping &amp; side discriminator
              </span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
            <p className="text-xs text-slate-500">
              Map source columns to OPOS target fields, then identify the side discriminator column
              and the values that distinguish Debitor (AR) from Kreditor (AP) rows.
              {combinedSides.viewMode === 'combined' && ' Then identify the entity column.'}
              {' '}The same mapping applies to both sides in the file.
            </p>
            <StepColumnMapper
              key={previewColumns.join(',')}
              preview={fromNamedPreview(previewColumns, previewSample)}
              buildSteps={() => buildOposSteps('combined_sides', combinedSides.viewMode)}
              toResult={toOposResult}
              initial={mapperInitial}
              onComplete={r =>
                onPatch({
                  columnMap: r.columnMap,
                  entityColumn: r.entityColumn,
                  sideColumn: r.sideColumn,
                  debitorValue: r.debitorValue,
                  kreditorValue: r.kreditorValue,
                })
              }
            />
          </div>

          {/* Commit hint — split runs server-side on the side column */}
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
            <p className="text-xs text-slate-600">
              Committing splits each file on the side column and writes Debitor (AR) and
              Kreditor (AP) rows to their respective tables in one step. Rows whose side
              value matches neither the Debitor nor the Kreditor value are skipped.
              {!canCommit && (
                <>
                  {' '}Map at least one target field and set the side column with distinct
                  Debitor / Kreditor values to enable committing.
                </>
              )}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component — loading mode selector + per-side or combined panel
// ---------------------------------------------------------------------------

const SIDES: Array<{ key: OposSide; label: string; hint: string }> = [
  { key: 'debitor',  label: 'Debitor',  hint: 'Accounts receivable (AR) — customer open items' },
  { key: 'kreditor', label: 'Kreditor', hint: 'Accounts payable (AP) — supplier open items'   },
]

export default function OposStep({ opos, entities, glYears, fyEndMonth, onPatch }: OposStepProps) {
  const [activeSide, setActiveSide] = useState<OposSide>('debitor')

  const fyLabels = glYears.length > 0
    ? glYears.map(y => glFiscalYearLabel(y, fyEndMonth))
    : ['FY2022', 'FY2023', 'FY2024', 'FY2025']

  const loadMode = opos.loadMode ?? 'per_side'

  function handlePatchSide(side: OposSide, patch: Partial<WizardOposSideState>) {
    const current = opos[side]
    onPatch({
      [side]: { ...current, ...patch },
      provided: true,
    })
  }

  function handlePatchCombinedSides(patch: Partial<WizardOposCombinedSidesState>) {
    onPatch({
      combinedSides: { ...opos.combinedSides, ...patch },
      provided: true,
    })
  }

  return (
    <div className="space-y-6">

      {/* Info note */}
      <div className="rounded-lg border border-blue-200 bg-blue-50/80 px-4 py-3 text-sm text-blue-900">
        Upload open-items lists to enable the Receivables Aging (Debitor) and Payables Aging
        (Kreditor) reporting pages. Data is stored pass-through; aging buckets (30 / 60 / 90 days),
        DSO / DPO ratios, and settlement matching will be added in a future release.
      </div>

      {/* Loading mode selector */}
      <div className="space-y-3">
        <p className="text-sm font-medium text-slate-700">File loading mode</p>
        <div className="flex gap-3 flex-wrap">
          {(
            [
              ['per_side',       'Separate files per side',  'Upload one set of files for Debitor and one for Kreditor (recommended).'],
              ['combined_sides', 'One combined file',        'Upload a single file with rows for both sides, filtered by a discriminator column.'],
            ] as const
          ).map(([val, label, hint]) => (
            <label
              key={val}
              className={[
                'flex-1 min-w-[200px] flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition',
                loadMode === val
                  ? 'border-blue-400 bg-blue-50'
                  : 'border-slate-200 bg-white hover:bg-slate-50',
              ].join(' ')}
            >
              <input
                type="radio"
                name="oposLoadMode"
                value={val}
                checked={loadMode === val}
                onChange={() => onPatch({ loadMode: val })}
                className="mt-0.5 accent-blue-600"
              />
              <div>
                <p className="text-sm font-semibold text-slate-800">{label}</p>
                <p className="text-xs text-slate-500 mt-0.5">{hint}</p>
              </div>
            </label>
          ))}
        </div>
        <p className="text-xs text-slate-400">
          Switching modes does not clear already-uploaded files for each mode.
        </p>
      </div>

      {/* Combined-sides panel */}
      {loadMode === 'combined_sides' && (
        <OposCombinedSidesPanel
          combinedSides={opos.combinedSides}
          entities={entities}
          fyLabels={fyLabels}
          onPatch={handlePatchCombinedSides}
        />
      )}

      {/* Per-side panels */}
      {loadMode === 'per_side' && (
        <div className="space-y-5">

          {/* Side toggle */}
          <div className="space-y-2">
            <p className="text-sm font-medium text-slate-700">OPOS side</p>
            <div className="flex gap-3 flex-wrap">
              {SIDES.map(({ key, label, hint }) => (
                <label
                  key={key}
                  className={[
                    'flex-1 min-w-[180px] flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition',
                    activeSide === key
                      ? 'border-blue-400 bg-blue-50'
                      : 'border-slate-200 bg-white hover:bg-slate-50',
                  ].join(' ')}
                >
                  <input
                    type="radio"
                    name="oposSide"
                    value={key}
                    checked={activeSide === key}
                    onChange={() => setActiveSide(key)}
                    className="mt-0.5 accent-blue-600"
                  />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">{label}</p>
                    <p className="text-xs text-slate-500 mt-0.5">{hint}</p>
                  </div>
                </label>
              ))}
            </div>
            <p className="text-xs text-slate-400">
              Upload and map both sides here. Switching sides preserves each side's files and
              column mapping independently.
            </p>
          </div>

          {/* Per-side panel — key remounts on side switch to reset local async state */}
          <OposSidePanel
            key={activeSide}
            side={activeSide}
            sideState={opos[activeSide]}
            entities={entities}
            fyLabels={fyLabels}
            onPatchSide={patch => handlePatchSide(activeSide, patch)}
          />
        </div>
      )}
    </div>
  )
}
