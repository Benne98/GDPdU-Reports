/**
 * OposStep.tsx — DRAFT provisioning sub-flow for Open-Items Lists (D4).
 *
 * Two sides rendered as tabs:
 *   Debitor  = accounts-receivable open items (AR)
 *   Kreditor = accounts-payable open items (AP)
 *
 * Flow per side: entity source selection -> upload (per FY / per entity x FY) ->
 *   preview -> column mapping -> per-slot commit.
 *
 * Aging computation is NOT performed in the UI — see DRAFT notice below.
 */

import { useState } from 'react'
import EntitySourceSelector, { type EntitySource } from './EntitySourceSelector'
import PerEntityPager from './PerEntityPager'
import PerYearPager from './PerYearPager'
import { uploadOposFile, commitOpos, type OposSide } from '../../lib/gdpduApi'
import { glFiscalYearLabel } from '../../lib/fiscalYear'

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

export interface WizardOposState {
  provided: boolean
  debitor: WizardOposSideState
  kreditor: WizardOposSideState
}

// ---------------------------------------------------------------------------
// Target field definitions — same for both sides
// ---------------------------------------------------------------------------

const OPOS_TARGET_FIELDS: Array<{ key: string; label: string; hint?: string; required: boolean }> = [
  { key: 'konto',               label: 'Account',              required: true  },
  { key: 'belegart',            label: 'Document type',     required: false },
  { key: 'beleg_no',            label: 'Document number',  required: false },
  { key: 'referenz',            label: 'Reference',         required: false },
  { key: 'net_due_date',        label: 'Net due date',      required: false },
  { key: 'amount_hauswaehrung', label: 'Amount in house currency (signed)',
    hint: 'Positive = debit; negative = credit. Ensure sign convention matches your source.',
    required: false },
  { key: 'posting_date',        label: 'Posting date', required: false },
]

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
// Helpers
// ---------------------------------------------------------------------------

function SampleTable({ columns, sample }: { columns: string[]; sample: Record<string, unknown>[] }) {
  if (sample.length === 0) return null
  const cols = columns.slice(0, 8)
  return (
    <div className="overflow-x-auto rounded-md border border-slate-100">
      <table className="min-w-full text-xs">
        <thead className="bg-slate-50">
          <tr>
            {cols.map(c => (
              <th key={c} className="px-2 py-1.5 text-left font-medium text-slate-600 whitespace-nowrap">
                {c}
              </th>
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
// Inner component — renders one side (debitor or kreditor)
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
            <span className="text-sm text-slate-600">
              Drop .xlsx / .csv or click to browse
            </span>
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

      {/* Entity column (combined mode, after first upload) */}
      {viewMode === 'combined' && hasUploads && previewColumns.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-slate-700">
            Entity column (company code)
            <span className="ml-0.5 text-red-500" aria-hidden>*</span>
          </p>
          <select
            value={sideState.entityColumn ?? ''}
            onChange={e => onPatchSide({ entityColumn: e.target.value || undefined })}
            className="rounded-md border border-slate-300 px-2 py-1.5 text-sm w-72 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">-- Select entity column --</option>
            {previewColumns.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <p className="text-xs text-slate-400">
            Column identifying the legal entity per row (e.g. company code).
          </p>
        </div>
      )}

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

      {/* Preview + column mapping — only after first upload */}
      {hasUploads && previewColumns.length > 0 && (
        <div className="space-y-5">

          <div className="space-y-1.5">
            <p className="text-xs font-semibold text-slate-600">
              Sample data (from first uploaded file)
            </p>
            <SampleTable columns={previewColumns} sample={previewSample} />
          </div>

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
              fields are optional. The same mapping is applied to all uploaded files on this side.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {OPOS_TARGET_FIELDS.map(field => (
                <div key={field.key} className="space-y-1">
                  <label className="text-xs font-medium text-slate-700">
                    {field.label}
                    {field.required && <span className="ml-0.5 text-red-500" aria-hidden>*</span>}
                  </label>
                  {field.hint && (
                    <p className="text-[11px] text-slate-400">{field.hint}</p>
                  )}
                  <select
                    value={sideState.columnMap[field.key] ?? ''}
                    onChange={e => {
                      const nextMap = { ...sideState.columnMap }
                      if (e.target.value) nextMap[field.key] = e.target.value
                      else delete nextMap[field.key]
                      onPatchSide({ columnMap: nextMap })
                    }}
                    className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">-- Not mapped --</option>
                    {previewColumns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              ))}
            </div>
            {!sideState.columnMap['konto'] && (
              <p className="text-xs text-amber-700">
                Map the Account column before committing.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component — side tabs + per-side panel
// ---------------------------------------------------------------------------

export default function OposStep({ opos, entities, glYears, fyEndMonth, onPatch }: OposStepProps) {
  const [activeSide, setActiveSide] = useState<OposSide>('debitor')

  const fyLabels = glYears.length > 0
    ? glYears.map(y => glFiscalYearLabel(y, fyEndMonth))
    : ['FY2022', 'FY2023', 'FY2024', 'FY2025']

  function handlePatchSide(side: OposSide, patch: Partial<WizardOposSideState>) {
    const current = opos[side]
    onPatch({
      [side]: { ...current, ...patch },
      provided: true,
    })
  }

  const SIDES: Array<{ key: OposSide; label: string; hint: string }> = [
    { key: 'debitor',  label: 'Debitor',  hint: 'Accounts receivable (AR) — customer open items' },
    { key: 'kreditor', label: 'Kreditor', hint: 'Accounts payable (AP) — supplier open items'   },
  ]

  return (
    <div className="space-y-6">

      {/* DRAFT notice */}
      <div className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
        <span className="shrink-0 rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-700">
          Draft
        </span>
        <p className="text-sm text-amber-900">
          Provisioning only. Aging buckets (30 / 60 / 90 days), DSO / DPO ratios, and
          write-off analytics are not yet computed in the UI.
        </p>
      </div>

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
  )
}
