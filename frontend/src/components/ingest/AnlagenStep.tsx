/**
 * AnlagenStep.tsx — DRAFT provisioning sub-flow for Fixed-Asset Register (D3).
 *
 * Flow: entity source selection -> upload (per FY / per entity x FY) ->
 *       step-by-step column mapping (StepColumnMapper) -> per-slot commit.
 *
 * Roll-forward (carry AHK/NBV to next period) and depreciation schedules are
 * NOT computed in the UI.  This is a data-provisioning scaffold only.
 */

import { useMemo, useState } from 'react'
import EntitySourceSelector, { type EntitySource } from './EntitySourceSelector'
import PerEntityPager from './PerEntityPager'
import PerYearPager from './PerYearPager'
import { uploadAnlagenFile, commitAnlagen } from '../../lib/gdpduApi'
import { glFiscalYearLabel } from '../../lib/fiscalYear'
import StepColumnMapper from '../mapper/StepColumnMapper'
import { fromNamedPreview } from '../mapper/normalizePreview'
import { buildAnlagenSteps, toAnlagenResult } from '../mapper/anlagenSteps'
import type { Answers } from '../mapper/stepMapperTypes'
import FileDrop from '../budget/chat/FileDrop'

// ---------------------------------------------------------------------------
// State shapes — exported so ProjectSetupWizard can reference them in
// WizardState and the PATCH_ANLAGEN reducer action.
// ---------------------------------------------------------------------------

export interface WizardAnlagenUpload {
  entity_index: number   // 0 = "all entities" in combined mode
  entity_name: string
  fy_label: string
  file_id?: string
  columns?: string[]
  sample?: Record<string, unknown>[]
}

export interface WizardAnlagenState {
  provided: boolean
  viewMode: 'combined' | 'per_entity'
  /** Combined mode: column in the file that identifies the legal entity per row. */
  entityColumn?: string
  /** One entry per entity_index x fy_label slot. */
  uploads: WizardAnlagenUpload[]
  /** file_id of the first uploaded file — used to drive the column-mapping preview. */
  previewFileId?: string
  /** target_field -> source_column mapping applied to all committed files. */
  columnMap: Record<string, string>
  /** Optional dimension / breakdown column assignments. */
  dimensions: {
    segmentCol?: string
    assetClassCol?: string
    bilanzpositionCol?: string
  }
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface AnlagenStepProps {
  anlagen: WizardAnlagenState
  entities: Array<{ code: string; prefix: string; name: string }>
  glYears: number[]
  fyEndMonth: number
  onPatch: (patch: Partial<WizardAnlagenState>) => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ANLAGEN_HEADER_ALIASES: Record<string, string> = {
  anlage: 'asset_id',
  anlagenbezeichnung: 'asset_label',
  bezeichnung: 'asset_label',
  unternummer: 'asset_sub_no',
  bilanzposition: 'bilanzposition',
  geschäftsbereich: 'segment',
  geschaftsbereich: 'segment',
  anlagenklasse: 'asset_class',
  zugang: 'additions_zugang',
  abgang: 'disposals_abgang',
  umbuchung: 'transfers_umbuchung',
  'afa des jahres': 'depreciation',
  'lfd buchwert': 'nbv',
}

function normHeader(col: string): string {
  return col.trim().toLowerCase().replace(/\xa0/g, ' ')
}

function suggestColumnMap(columns: string[], existing: Record<string, string>): Record<string, string> {
  const next = { ...existing }
  for (const col of columns) {
    const field = ANLAGEN_HEADER_ALIASES[normHeader(col)]
    if (!field || next[field]) continue
    next[field] = col
  }
  return next
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AnlagenStep({ anlagen, entities, glYears, fyEndMonth, onPatch }: AnlagenStepProps) {
  const validEntities = entities.filter(e => e.code.trim())

  const fyLabels = glYears.length > 0
    ? glYears.map(y => glFiscalYearLabel(y, fyEndMonth))
    : ['FY2022', 'FY2023', 'FY2024', 'FY2025']

  // Local async-operation state (not persisted to wizard state)
  const [uploading, setUploading]     = useState<Record<string, boolean>>({})
  const [uploadError, setUploadError] = useState<Record<string, string>>({})
  const [committing, setCommitting]   = useState<Record<string, boolean>>({})
  const [commitMsg, setCommitMsg]     = useState<Record<string, string>>({})
  const [commitErr, setCommitErr]     = useState<Record<string, string>>({})

  const viewMode = anlagen.viewMode

  function getUpload(entityIndex: number, fyLabel: string) {
    return anlagen.uploads.find(u => u.entity_index === entityIndex && u.fy_label === fyLabel)
  }

  function isEntityStaged(entityIndex: number) {
    return anlagen.uploads.some(u => u.entity_index === entityIndex && !!u.file_id)
  }

  // First uploaded file drives the column-mapping preview
  const previewUpload =
    anlagen.uploads.find(u => u.file_id === anlagen.previewFileId) ??
    anlagen.uploads.find(u => !!u.file_id)
  const previewColumns = previewUpload?.columns ?? []
  const previewSample  = previewUpload?.sample  ?? []

  const hasUploads = anlagen.uploads.some(u => !!u.file_id)

  // Build initial answers: seed from suggestColumnMap (alias auto-detect) +
  // any previously confirmed entity column / dimensions from wizard state.
  const mapperInitial = useMemo((): Answers => {
    const suggested = suggestColumnMap(previewColumns, anlagen.columnMap)
    const ans: Answers = {}
    for (const [field, col] of Object.entries(suggested)) {
      ans[field] = { t: 'column', id: col }
    }
    if (anlagen.entityColumn)
      ans['entity_column'] = { t: 'column', id: anlagen.entityColumn }
    if (anlagen.dimensions.segmentCol)
      ans['segmentCol'] = { t: 'column', id: anlagen.dimensions.segmentCol }
    if (anlagen.dimensions.assetClassCol)
      ans['assetClassCol'] = { t: 'column', id: anlagen.dimensions.assetClassCol }
    if (anlagen.dimensions.bilanzpositionCol)
      ans['bilanzpositionCol'] = { t: 'column', id: anlagen.dimensions.bilanzpositionCol }
    return ans
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewColumns.join(','), anlagen.columnMap, anlagen.entityColumn, anlagen.dimensions])

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

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
      const result = await uploadAnlagenFile(file)
      const newEntry: WizardAnlagenUpload = {
        entity_index: entityIndex,
        entity_name: entityName,
        fy_label: fyLabel,
        file_id: result.file_id,
        columns: result.columns,
        sample: result.sample,
      }
      const rest = anlagen.uploads.filter(
        u => !(u.entity_index === entityIndex && u.fy_label === fyLabel)
      )
      const allUploads = [...rest, newEntry]
      const previewFileId = allUploads.find(u => !!u.file_id)?.file_id
      onPatch({
        uploads: allUploads,
        previewFileId,
        provided: true,
        columnMap: suggestColumnMap(result.columns, anlagen.columnMap),
      })
    } catch (e) {
      setUploadError(prev => ({ ...prev, [slotKey]: e instanceof Error ? e.message : 'Upload failed' }))
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
      const result = await commitAnlagen({
        file_id: upload.file_id,
        fy_label: fyLabel,
        entity_mode: viewMode === 'per_entity' ? 'per_entity' : 'combined',
        ...(viewMode === 'per_entity' && entityPrefix ? { entity_prefix: entityPrefix } : {}),
        ...(viewMode === 'combined' && anlagen.entityColumn ? { entity_column: anlagen.entityColumn } : {}),
        column_map: anlagen.columnMap,
      })
      setCommitMsg(prev => ({
        ...prev,
        [slotKey]: `${result.inserted} rows committed for ${fyLabel}`,
      }))
    } catch (e) {
      setCommitErr(prev => ({ ...prev, [slotKey]: e instanceof Error ? e.message : 'Commit failed' }))
    } finally {
      setCommitting(prev => ({ ...prev, [slotKey]: false }))
    }
  }

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  function renderUploadZone(
    entityIndex: number,
    entityName: string,
    fyLabel: string,
    entityPrefix = '',
  ) {
    const slotKey   = `${entityIndex}__${fyLabel}`
    const existing  = getUpload(entityIndex, fyLabel)
    const isUp      = uploading[slotKey] ?? false
    const upErr     = uploadError[slotKey] ?? ''
    const isCom     = committing[slotKey] ?? false
    const comMsg    = commitMsg[slotKey] ?? ''
    const comErr    = commitErr[slotKey] ?? ''
    const staged    = !!existing?.file_id
    // Gate commit on the required asset_id field being mapped
    const hasMap    = !!anlagen.columnMap['asset_id']

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
              title={!hasMap ? 'Map the Asset ID column before committing' : undefined}
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

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-6">

      {/* Info note */}
      <div className="rounded-lg border border-blue-200 bg-blue-50/80 px-4 py-3 text-sm text-blue-900">
        Upload the fixed-asset register to enable the Fixed Assets reporting page.
        Roll-forward (carry AHK / NBV to the next period) and depreciation schedule
        computation will be added in a future release.
      </div>

      {/* Entity source selector */}
      <EntitySourceSelector
        value={viewMode === 'per_entity' ? 'per_entity' : 'combined'}
        onChange={(v: EntitySource) =>
          onPatch({
            viewMode: v === 'per_entity' ? 'per_entity' : 'combined',
            uploads: [],
            previewFileId: undefined,
          })
        }
        dataLabel="Fixed-asset register data"
      />

      {/* Upload zones */}
      <div className="space-y-3">
        <p className="text-sm font-medium text-slate-700">
          Upload fixed-asset register file{fyLabels.length > 1 ? 's' : ''}
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
            Map source columns to fixed-asset register target fields. Asset ID is required; all
            other fields are optional.
            {viewMode === 'combined' && ' Then identify the entity column.'}
            {' '}Segment, asset-class, and balance-sheet line columns can be assigned as
            drill-down dimensions. The same mapping is applied to all uploaded files.
          </p>
          <StepColumnMapper
            key={previewColumns.join(',')}
            preview={fromNamedPreview(previewColumns, previewSample)}
            buildSteps={() => buildAnlagenSteps(viewMode)}
            toResult={toAnlagenResult}
            initial={mapperInitial}
            onComplete={r =>
              onPatch({
                columnMap: r.columnMap,
                entityColumn: r.entityColumn,
                dimensions: r.dimensions,
                provided: true,
              })
            }
          />
        </div>
      )}
    </div>
  )
}
