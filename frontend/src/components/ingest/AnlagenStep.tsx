/**
 * AnlagenStep.tsx — DRAFT provisioning sub-flow for Fixed-Asset Register (D3).
 *
 * Flow: entity source selection -> upload (per FY / per entity x FY) ->
 *       preview -> column mapping -> dimension selection -> per-slot commit.
 *
 * Roll-forward (carry AHK/NBV to next period) and depreciation schedules are
 * NOT computed in the UI.  This is a data-provisioning scaffold only.
 */

import { useState } from 'react'
import EntitySourceSelector, { type EntitySource } from './EntitySourceSelector'
import PerEntityPager from './PerEntityPager'
import PerYearPager from './PerYearPager'
import { uploadAnlagenFile, commitAnlagen } from '../../lib/gdpduApi'
import { glFiscalYearLabel } from '../../lib/fiscalYear'

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
// Target field definitions
// ---------------------------------------------------------------------------

const ANLAGEN_TARGET_FIELDS: Array<{ key: string; label: string; required: boolean }> = [
  { key: 'asset_id',            label: 'Anlage (asset ID)',                required: true  },
  { key: 'asset_label',         label: 'Anlagenbezeichnung',               required: false },
  { key: 'asset_sub_no',        label: 'Asset sub-number',        required: false },
  { key: 'asset_class',         label: 'Asset class',             required: false },
  { key: 'segment',             label: 'Segment',                 required: false },
  { key: 'bilanzposition',      label: 'Balance sheet line',      required: false },
  { key: 'capitalization_date', label: 'Capitalization date',     required: false },
  { key: 'opening_cost_ahk',    label: 'Opening cost (AHK)',      required: false },
  { key: 'additions_zugang',    label: 'Additions',               required: false },
  { key: 'disposals_abgang',    label: 'Disposals',               required: false },
  { key: 'transfers_umbuchung', label: 'Transfers',               required: false },
  { key: 'depreciation',        label: 'Depreciation',            required: false },
  { key: 'nbv',                 label: 'Net book value (NBV)',     required: false },
]

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

function SampleTable({ columns, sample }: { columns: string[]; sample: Record<string, unknown>[] }) {
  if (sample.length === 0) return null
  const cols = columns.slice(0, 8)
  return (
    <div className="overflow-x-auto rounded-md border border-slate-100">
      <table className="min-w-full text-xs">
        <thead className="bg-slate-50">
          <tr>
            {cols.map(c => (
              <th key={c} className="px-2 py-1.5 text-left font-medium text-slate-600 whitespace-nowrap">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {sample.slice(0, 3).map((row, i) => (
            <tr key={i} className="even:bg-slate-50/50">
              {cols.map(c => (
                <td key={c} className="px-2 py-1.5 text-slate-700 whitespace-nowrap">
                  {String(row[c] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
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
    const hasMap    = Object.keys(anlagen.columnMap).length > 0

    return (
      <div key={slotKey} className="space-y-2">
        <label
          className={[
            'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-5 cursor-pointer transition',
            staged
              ? 'border-emerald-300 bg-emerald-50'
              : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-blue-50/50',
          ].join(' ')}
        >
          <input
            type="file"
            accept=".xlsx,.xls,.csv"
            className="sr-only"
            disabled={isUp}
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) void handleFileUpload(entityIndex, entityName, fyLabel, f)
              e.target.value = ''
            }}
          />
          {isUp ? (
            <span className="text-sm text-blue-600">Uploading…</span>
          ) : staged ? (
            <>
              <span className="text-sm font-medium text-emerald-700">File staged — {fyLabel}</span>
              {existing?.columns && (
                <span className="text-xs text-slate-500">{existing.columns.length} columns detected</span>
              )}
              <span className="text-xs text-slate-400 italic">Click to replace</span>
            </>
          ) : (
            <span className="text-sm text-slate-600">Drop .xlsx / .csv or click to browse</span>
          )}
        </label>

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

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="space-y-6">

      {/* DRAFT notice */}
      <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
        <span className="shrink-0 rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-[12px] font-semibold uppercase tracking-wide text-amber-700">
          Draft
        </span>
        <p className="text-sm text-amber-900">
          Provisioning only. Roll-forward (carry AHK / NBV to the next period) and
          depreciation schedule computation are not yet calculated in the UI.
        </p>
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

      {/* Entity column (combined mode, shown after first upload) */}
      {viewMode === 'combined' && hasUploads && previewColumns.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-slate-700">
            Entity column
            <span className="ml-0.5 text-red-500" aria-hidden>*</span>
          </p>
          <select
            value={anlagen.entityColumn ?? ''}
            onChange={e => onPatch({ entityColumn: e.target.value || undefined })}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm w-72 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">-- Select entity column (e.g. company code) --</option>
            {previewColumns.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <p className="text-xs text-slate-400">
            Column identifying the legal entity per row in the combined file.
          </p>
        </div>
      )}

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

      {/* Preview + column mapping + dimensions — only after first upload */}
      {hasUploads && previewColumns.length > 0 && (
        <div className="space-y-5">

          {/* Sample preview */}
          <div className="space-y-1.5">
            <p className="text-xs font-semibold text-slate-600">
              Sample data (from first uploaded file)
            </p>
            <SampleTable columns={previewColumns} sample={previewSample} />
          </div>

          {/* Column mapping */}
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
              other fields are optional. The same mapping is applied to all uploaded files.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {ANLAGEN_TARGET_FIELDS.map(field => (
                <div key={field.key} className="space-y-1">
                  <label className="text-xs font-medium text-slate-700">
                    {field.label}
                    {field.required && <span className="ml-0.5 text-red-500" aria-hidden>*</span>}
                  </label>
                  <select
                    value={anlagen.columnMap[field.key] ?? ''}
                    onChange={e => {
                      const nextMap = { ...anlagen.columnMap }
                      if (e.target.value) nextMap[field.key] = e.target.value
                      else delete nextMap[field.key]
                      onPatch({ columnMap: nextMap })
                    }}
                    className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">-- Not mapped --</option>
                    {previewColumns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              ))}
            </div>
            {!anlagen.columnMap['asset_id'] && (
              <p className="text-xs text-amber-700">
                Map the Asset ID column before committing.
              </p>
            )}
          </div>

          {/* Dimension / breakdown selection */}
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-slate-200" />
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Breakdown dimensions (optional)
              </span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
            <p className="text-xs text-slate-500">
              Identify which columns to use as breakdown dimensions in drill-downs.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {(
                [
                  ['segmentCol',       'Segment column'],
                  ['assetClassCol',    'Asset class column'],
                  ['bilanzpositionCol','Balance sheet line column'],
                ] as [keyof WizardAnlagenState['dimensions'], string][]
              ).map(([dimKey, dimLabel]) => (
                <div key={dimKey} className="space-y-1">
                  <label className="text-xs font-medium text-slate-700">{dimLabel}</label>
                  <select
                    value={anlagen.dimensions[dimKey] ?? ''}
                    onChange={e =>
                      onPatch({
                        dimensions: {
                          ...anlagen.dimensions,
                          [dimKey]: e.target.value || undefined,
                        },
                      })
                    }
                    className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">-- Not selected --</option>
                    {previewColumns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
