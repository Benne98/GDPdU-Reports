/**
 * GlEntityCard.tsx — Shared per-entity GL upload card.
 *
 * Used by:
 *   - ProjectSetupWizard (entityMode='create') — user enters entity code + label
 *   - IngestionPage Data Update (entityMode='select') — user picks from existing entities
 *
 * Exports:
 *   - GlEntityCard component
 *   - GlYearSlot component
 *   - Types: PartnerColumnsMode, PartnerColumnsSplit, GlYearFileState, GlEntityState,
 *            WizardGlState, GlFormatGroup
 *   - Helpers: suggestGlMapping, defaultGlOpts, defaultGlEntityState
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { missingRequiredFields } from './ColumnMapper'
import ValidierungStep from './ValidierungStep'
import GlGroupSelect from './GlGroupSelect'
import type { KontextState, OptionsState } from './ingestTypes'
import {
  uploadFile,
  combineGlFiles,
  applyHeaders,
  budgetEntities,
  type Profile,
  type UploadResponse,
  type ValidationResponse,
  type Dialect,
} from '../../lib/gdpduApi'
import { glFiscalYearLabel } from '../../lib/fiscalYear'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * Describes how creditor / debtor / fixed-asset numbers appear in the GL file.
 *
 * 'single' (default): one "source_no" column holds the partner number; a
 *   companion "source_type" column distinguishes creditor / debtor / fixed-asset.
 *
 * 'split': three separate columns (creditor_no, debtor_no, fixed_asset_no).
 */
export type PartnerColumnsMode = 'single' | 'split'

export interface PartnerColumnsSplit {
  creditorNoCol: string
  debtorNoCol: string
  fixedAssetNoCol: string
}

/**
 * State for one year-slot within a GlEntityState.
 */
export interface GlYearFileState {
  fileId: string
  filename?: string
  sheet?: string
}

/**
 * State for a single entity card in the GL structure.
 *
 * Each entity gets N year-slots (one per gl.years entry). Once all required
 * slots are filled the frontend calls POST /ingest/gl/combine, which returns a
 * combined file with a "fiscal_year" column. That combined file drives ONE
 * column-mapping + options + validation flow for the whole entity.
 */
export interface GlEntityState {
  entityCode: string
  entityLabel?: string
  yearFiles: Partial<Record<number, GlYearFileState>>
  combinedFileId?: string
  combinedColumns?: string[]
  combinedSample?: Record<string, unknown>[]
  combinedDialect?: Dialect
  /** Present when the combined file has synthetic headers (or headered-but-incomplete). */
  combinedSuggestedHeaders?: Record<string, string>
  /** True once the user has confirmed the header step (or auto-confirmed for headered files). */
  headersConfirmed?: boolean
  /** User's explicit header-detection override for all year-slot uploads ('auto' | 'yes' | 'no'). */
  headerOverride?: 'auto' | 'yes' | 'no'
  /** Hint derived from the first uploaded file's header_detected field. */
  headerHint?: string
  /** Soft heads-up from /gl/combine when the column count looks inflated (likely a misparse). */
  combinedColumnWarning?: string
  /** ID of the GlFormatGroup this entity belongs to (set after header confirmation). */
  formatGroupId?: string
  validationOk?: boolean
  entityAssignments?: Record<string, string>
  assembledProfile?: Profile
  /**
   * Line IDs excluded during the most recent validation run.
   * Propagated to /commit so excluded rows are not written to the DB.
   * Populated by handleValidationResult from ValidationResponse.exclusions.active_line_ids.
   */
  excludedLineIds?: string[]
}

/**
 * A format group owns the shared column config for entities with the same
 * column layout. The representative entity's headers are applied to all members.
 * Each member entity references the group via GlEntityState.formatGroupId.
 */
export interface GlFormatGroup {
  /** Stable UUID. */
  id: string
  /** Display label: "Format A", "Format B", … */
  label: string
  /** Index into WizardGlState.entities — this entity's headers are canonical. */
  representativeIndex: number
  /** Number of columns in the combined file (includes fiscal_year). */
  columnCount: number
  /** Positional column labels in combined-file order. */
  headers: string[]
  /** True once the representative has confirmed headers. */
  headersConfirmed: boolean
  /** Mapping from GoBD field name → source column name. */
  mapping: Record<string, string>
  /** How partner numbers appear in the GL file. */
  partnerColumnsMode: PartnerColumnsMode
  /** Set when partnerColumnsMode === 'split'. */
  partnerColumnsSplit?: PartnerColumnsSplit
  /** Transform options (sign, decimal, date, linking). Owned by the group. */
  opts: OptionsState
  /** Indices of all member entities (always includes representativeIndex). */
  memberIndices: number[]
}

/**
 * Fiscal years chosen up-front, then one entity card each.
 */
export interface WizardGlState {
  years: number[]
  entities: GlEntityState[]
  /**
   * Format groups owning shared headers/mapping/opts for entities with
   * matching column counts. Replaces the old savedFormat concept.
   */
  formatGroups: GlFormatGroup[]
}

// ---------------------------------------------------------------------------
// Constants + helpers
// ---------------------------------------------------------------------------

/** @deprecated Sub-steps are now owned by GlGroupConfigPanel at parent level. */
export const GL_ENTITY_SUB_STEPS = [
  'Partner columns',
  'Column mapping',
  'Options',
  'Validation',
] as const

/**
 * Default GoBD column mapping when source headers match a standard DATEV/Decidra export.
 */
// The GL column picker offers ONLY the fields relevant to a GL booking line
// (order = dropdown order). Amount/sign columns are configured separately in the
// sign options (profile.sign), NOT here. source_no is the partner number and — via
// the companion "Source type" column (or split mode) — represents the creditor /
// debtor / fixed-asset number.
const GOBD_GL_DEFAULTS: Record<string, string> = {
  journal_entry_number: 'Booking ID',
  line_note: 'Booking text',
  account_number: 'Account number',
  posting_date: 'Posting date',
  amount: 'Amount',
  source_type: 'Source type',
  source_no: 'Source number',
}

// Legacy / alternate source-header spellings that still auto-map to each field, so
// files whose headers use an older label (e.g. DATEV 'Transaction number') keep
// auto-detecting even though the picker now shows the canonical label.
const GOBD_GL_SOURCE_ALIASES: Record<string, readonly string[]> = {
  journal_entry_number: ['Transaction number', 'Journal number', 'Belegnummer', 'Buchungs-ID'],
  source_no: ['Source No.', 'Creditor number', 'Debitor number', 'Debtor number', 'Fixed asset number'],
}

/** Ordered list of the standard GoBD label options for the per-column dropdown. */
const GOBD_LABEL_OPTIONS: readonly string[] = Object.values(GOBD_GL_DEFAULTS)

export function suggestGlMapping(columns: string[]): Record<string, string> {
  const colSet = new Set(columns)
  const out: Record<string, string> = {}
  for (const [target, source] of Object.entries(GOBD_GL_DEFAULTS)) {
    if (colSet.has(source)) {
      out[target] = source
      continue
    }
    const alias = GOBD_GL_SOURCE_ALIASES[target]?.find(a => colSet.has(a))
    if (alias) out[target] = alias
  }
  return out
}

export function defaultGlOpts(): OptionsState {
  return {
    signMode: 'signed',
    signAmount: '',
    signSoll: '',
    signHaben: '',
    signDcFlag: '',
    signDebitValue: 'S',
    decimal: ',',
    thousands: '.',
    dateDayfirst: true,
    linking: 'txn',
    profileName: '',
    profileSystem: '',
  }
}

export function defaultGlEntityState(): GlEntityState {
  return {
    entityCode: '',
    yearFiles: {},
  }
}

// ---------------------------------------------------------------------------
// Shared primitive components (inlined so GlEntityCard is self-contained)
// ---------------------------------------------------------------------------

function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/80 px-4 py-3 text-sm text-blue-900">
      {children}
    </div>
  )
}

function WarnBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      {children}
    </div>
  )
}

// ---------------------------------------------------------------------------
// GlYearSlot — single file drop zone for one fiscal year within an entity card
// ---------------------------------------------------------------------------

export function GlYearSlot({
  year,
  slot,
  fyEndMonth = 12,
  hasHeaderOverride,
  onUploaded,
}: {
  year: number
  slot: GlYearFileState | undefined
  fyEndMonth?: number
  /** Forwarded from the entity-level header override. undefined = auto-detect. */
  hasHeaderOverride?: boolean
  onUploaded: (year: number, result: UploadResponse) => void
}) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function handleFile(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const result = await uploadFile(file, undefined, hasHeaderOverride)
      onUploaded(year, result)
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="space-y-1">
      <p className="text-xs font-semibold text-slate-600">{glFiscalYearLabel(year, fyEndMonth)} file</p>
      <div
        onClick={() => !slot && fileInputRef.current?.click()}
        onDragOver={e => e.preventDefault()}
        onDrop={e => {
          e.preventDefault()
          const f = e.dataTransfer.files[0]
          if (f) void handleFile(f)
        }}
        className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-4 transition cursor-pointer ${
          uploading
            ? 'border-blue-400 bg-blue-50'
            : slot
            ? 'border-emerald-300 bg-emerald-50 cursor-default'
            : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xls,.csv,.txt"
          className="hidden"
          onChange={e => {
            const f = e.target.files?.[0]
            if (f) void handleFile(f)
          }}
        />
        {uploading ? (
          <p className="text-xs font-medium text-blue-600">Processing…</p>
        ) : slot ? (
          <div className="text-center">
            <p className="text-xs font-semibold text-emerald-700">Uploaded</p>
            <p className="text-xs text-slate-400 mt-0.5 font-mono truncate max-w-[160px]">
              {slot.filename ?? slot.fileId.slice(0, 12) + '…'}
            </p>
            <button
              type="button"
              onClick={e => { e.stopPropagation(); fileInputRef.current?.click() }}
              className="mt-1 text-xs text-slate-400 hover:text-blue-600 underline underline-offset-1"
            >
              Replace
            </button>
          </div>
        ) : (
          <p className="text-xs text-slate-400">Drop or click to upload</p>
        )}
      </div>
      {uploadError && (
        <p className="text-xs text-red-600">{uploadError}</p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Helper: detect whether a column list is entirely synthetic (headerless)
// ---------------------------------------------------------------------------

function hasSyntheticHeaders(columns: string[]): boolean {
  const dataColumns = columns.filter(c => c !== 'fiscal_year')
  if (dataColumns.length === 0) return false
  return dataColumns.every(c => /^Column \d+$/.test(c))
}

// ---------------------------------------------------------------------------
// ConfirmHeadersStep — dropdown-based header assignment for headerless files
// ---------------------------------------------------------------------------

/** Sentinel values used internally to represent special dropdown selections. */
const IGNORE_SENTINEL = '__ignore__'
const CUSTOM_SENTINEL = '__custom__'

interface ColumnAssignment {
  /** The dropdown value: a standard label, IGNORE_SENTINEL, or CUSTOM_SENTINEL. */
  dropdownValue: string
  /** Free text used only when dropdownValue === CUSTOM_SENTINEL. */
  customText: string
}

/** Resolve an assignment to the final header string. */
function resolveAssignment(col: string, a: ColumnAssignment): string {
  if (col === 'fiscal_year') return 'fiscal_year'
  if (a.dropdownValue === IGNORE_SENTINEL) return col   // keep synthetic "Column N" name
  if (a.dropdownValue === CUSTOM_SENTINEL) return a.customText.trim()
  return a.dropdownValue
}

export interface ConfirmHeadersStepProps {
  /** All columns including fiscal_year (which is locked). */
  columns: string[]
  /** Backend suggestions keyed by synthetic column name (identity map for headered files). */
  suggestedHeaders: Record<string, string>
  /** Up to 15 rows of the combined sample. */
  sample: Record<string, unknown>[]
  /** Called with confirmed header array (in column order, including "fiscal_year"). */
  onConfirm: (headers: string[]) => Promise<void>
  /** Called when user clicks Back. When undefined the Back button is hidden. */
  onBack?: () => void
  confirming: boolean
  confirmError: string | null
  /**
   * When set, renders a leading locked column in the preview table showing the entity
   * code for each row. The key must be present on every sample row; it is NOT part of
   * the column-assignment UI and is ignored by duplicate/validation checks.
   * Omit for single-entity groups.
   */
  entityColumn?: { key: string; label: string }
  /**
   * When set this subset is shown in the default (non-expanded) view; clicking
   * "Show all rows" switches to the full `sample`.  Allows showing a few rows
   * per entity without overwhelming the preview.  Falls back to the first
   * DEFAULT_PREVIEW_ROWS rows of `sample` when omitted.
   */
  collapsedSample?: Record<string, unknown>[]
}

export function ConfirmHeadersStep({
  columns,
  suggestedHeaders,
  sample,
  onConfirm,
  onBack,
  confirming,
  confirmError,
  entityColumn,
  collapsedSample,
}: ConfirmHeadersStepProps) {
  const editableColumns = columns.filter(c => c !== 'fiscal_year')

  // Build initial assignments from suggestions (unchanged)
  const [assignments, setAssignments] = useState<Record<string, ColumnAssignment>>(() => {
    const init: Record<string, ColumnAssignment> = {}
    for (const col of editableColumns) {
      const suggested = suggestedHeaders[col] ?? ''
      const isStandard = GOBD_LABEL_OPTIONS.includes(suggested)
      if (suggested && isStandard) {
        init[col] = { dropdownValue: suggested, customText: '' }
      } else if (suggested) {
        init[col] = { dropdownValue: CUSTOM_SENTINEL, customText: suggested }
      } else {
        init[col] = { dropdownValue: IGNORE_SENTINEL, customText: '' }
      }
    }
    return init
  })

  function setAssignment(col: string, patch: Partial<ColumnAssignment>) {
    setAssignments(prev => ({ ...prev, [col]: { ...prev[col], ...patch } }))
  }

  // ---------- preview row expansion ----------
  const [showAllRows, setShowAllRows] = useState(false)

  // ---------- inline-edit + dropdown state ----------
  const [editingCol, setEditingCol] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [openDropdown, setOpenDropdown] = useState<string | null>(null)
  /**
   * Position of the active popover. Uses position:fixed so it escapes the
   * overflow-x-auto clip context of the table wrapper.
   */
  const [popoverPos, setPopoverPos] = useState<{ top: number; left: number; width: number } | null>(null)

  const thRefs = useRef<Map<string, HTMLTableCellElement>>(new Map())
  const editInputRef = useRef<HTMLInputElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)

  // Focus + select-all when inline editing begins
  useEffect(() => {
    if (editingCol && editInputRef.current) {
      editInputRef.current.focus()
      editInputRef.current.select()
    }
  }, [editingCol])

  function measureAndSetPos(col: string) {
    const el = thRefs.current.get(col)
    if (el) {
      const rect = el.getBoundingClientRect()
      setPopoverPos({ top: rect.bottom + 2, left: rect.left, width: Math.max(rect.width, 180) })
    }
  }

  /** Classify the typed text and persist the assignment. */
  function applyEdit(col: string, text: string) {
    const trimmed = text.trim()
    if (trimmed === '') {
      setAssignment(col, { dropdownValue: IGNORE_SENTINEL, customText: '' })
    } else {
      const matched = GOBD_LABEL_OPTIONS.find(
        opt => opt.toLowerCase() === trimmed.toLowerCase()
      )
      if (matched) {
        setAssignment(col, { dropdownValue: matched, customText: '' })
      } else {
        setAssignment(col, { dropdownValue: CUSTOM_SENTINEL, customText: trimmed })
      }
    }
    setEditingCol(null)
    setEditText('')
    setPopoverPos(null)
  }

  function startEdit(col: string) {
    const a = assignments[col]
    const currentText =
      a.dropdownValue === IGNORE_SENTINEL ? '' :
      a.dropdownValue === CUSTOM_SENTINEL ? a.customText :
      a.dropdownValue
    setEditText(currentText)
    setEditingCol(col)
    setOpenDropdown(null)
    measureAndSetPos(col)
  }

  function openDropdownFor(col: string) {
    setOpenDropdown(col)
    setEditingCol(null)
    setEditText('')
    measureAndSetPos(col)
  }

  function pickFromDropdown(col: string, value: string) {
    if (value === IGNORE_SENTINEL) {
      setAssignment(col, { dropdownValue: IGNORE_SENTINEL, customText: '' })
    } else {
      setAssignment(col, { dropdownValue: value, customText: '' })
    }
    setOpenDropdown(null)
    setPopoverPos(null)
  }

  function pickSuggestion(col: string, label: string) {
    setAssignment(col, { dropdownValue: label, customText: '' })
    setEditingCol(null)
    setEditText('')
    setPopoverPos(null)
  }

  // Autocomplete suggestions while inline-editing
  const suggestions =
    editingCol !== null && editText.length > 0
      ? GOBD_LABEL_OPTIONS.filter(opt => opt.toLowerCase().includes(editText.toLowerCase()))
      : []
  const showSuggestions = suggestions.length > 0

  // Close dropdown / commit edit on outside click.
  // Uses only stable refs + dep-array values, so no eslint-disable needed.
  useEffect(() => {
    if (openDropdown === null && editingCol === null) return

    function handleMouseDown(e: MouseEvent) {
      const target = e.target as Node
      if (popoverRef.current?.contains(target)) return
      const activeCol = openDropdown ?? editingCol
      if (activeCol && thRefs.current.get(activeCol)?.contains(target)) return

      if (openDropdown !== null) {
        setOpenDropdown(null)
        setPopoverPos(null)
      }
      if (editingCol !== null) {
        // Inline commit — mirrors applyEdit() but uses only stable setters in closure
        const trimmed = editText.trim()
        const matched = trimmed !== ''
          ? GOBD_LABEL_OPTIONS.find(opt => opt.toLowerCase() === trimmed.toLowerCase())
          : undefined
        setAssignments(prev => ({
          ...prev,
          [editingCol]: trimmed === ''
            ? { dropdownValue: IGNORE_SENTINEL, customText: '' }
            : matched
            ? { dropdownValue: matched, customText: '' }
            : { dropdownValue: CUSTOM_SENTINEL, customText: trimmed },
        }))
        setEditingCol(null)
        setEditText('')
        setPopoverPos(null)
      }
    }

    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [openDropdown, editingCol, editText])

  // What to show in the fixed-position popover
  const popoverMode: 'dropdown' | 'suggestions' | null =
    openDropdown !== null ? 'dropdown' :
    showSuggestions ? 'suggestions' :
    null
  const showPopover = popoverMode !== null && popoverPos !== null

  // ---------- validation (unchanged logic) ----------
  const hasInvalidCustom = editableColumns.some(
    col => assignments[col].dropdownValue === CUSTOM_SENTINEL && assignments[col].customText.trim() === ''
  )
  // Count standard-label occurrences to find duplicates
  const labelCounts: Record<string, number> = {}
  for (const col of editableColumns) {
    const a = assignments[col]
    if (a.dropdownValue !== IGNORE_SENTINEL && a.dropdownValue !== CUSTOM_SENTINEL) {
      const key = a.dropdownValue.toLowerCase()
      labelCounts[key] = (labelCounts[key] ?? 0) + 1
    }
  }
  const duplicateLabels = new Set(
    Object.entries(labelCounts).filter(([, n]) => n > 1).map(([k]) => k)
  )
  const hasDuplicateStandard = duplicateLabels.size > 0
  // Required GoBD fields must be satisfied before Confirm is enabled
  const resolvedNonFY = editableColumns.map(c => resolveAssignment(c, assignments[c]))
  const suggestedFromResolved = suggestGlMapping(resolvedNonFY)
  const missingRequiredGoBD = missingRequiredFields(suggestedFromResolved)
  const isValid = !hasInvalidCustom && !hasDuplicateStandard && missingRequiredGoBD.length === 0

  function buildHeaders(): string[] {
    return columns.map(c =>
      c === 'fiscal_year' ? 'fiscal_year' : resolveAssignment(c, assignments[c])
    )
  }

  const DEFAULT_PREVIEW_ROWS = 15
  const collapsed = collapsedSample ?? sample.slice(0, DEFAULT_PREVIEW_ROWS)
  const previewRows = showAllRows ? sample : collapsed
  const hasMoreRows = sample.length > collapsed.length

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        Assign each column one of our standard GoBD headers.
      </div>

      {/* Interactive data preview — controls live in the header cells */}
      {previewRows.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-semibold text-slate-700">
            Data preview{' '}
            <span className="font-normal text-slate-400">
              ({previewRows.length === sample.length
                ? `All ${sample.length} rows shown`
                : `${previewRows.length} of ${sample.length} rows shown`})
            </span>
          </p>
          <p className="text-xs text-slate-500">
            Click a column header to rename it, or use the ▼ button to pick a standard GoBD label.
          </p>
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="min-w-full text-xs">
              <thead>
                <tr className="bg-slate-50 border-b border-slate-200">
                  {entityColumn && (
                    <th className="px-3 py-2 text-left font-semibold whitespace-nowrap text-slate-400">
                      {entityColumn.label}
                    </th>
                  )}
                  {columns.map(col => {
                    if (col === 'fiscal_year') {
                      return (
                        <th
                          key={col}
                          className="px-3 py-2 text-left font-semibold whitespace-nowrap text-slate-400"
                        >
                          fiscal_year
                        </th>
                      )
                    }

                    const a = assignments[col]
                    if (!a) return null
                    const isDuplicate =
                      a.dropdownValue !== IGNORE_SENTINEL &&
                      a.dropdownValue !== CUSTOM_SENTINEL &&
                      duplicateLabels.has(a.dropdownValue.toLowerCase())
                    const isEditing = editingCol === col
                    const displayText =
                      a.dropdownValue === IGNORE_SENTINEL
                        ? col
                        : a.dropdownValue === CUSTOM_SENTINEL
                        ? (a.customText || col)
                        : a.dropdownValue

                    return (
                      <th
                        key={col}
                        ref={el => {
                          if (el) thRefs.current.set(col, el)
                          else thRefs.current.delete(col)
                        }}
                        className={`px-3 py-2 text-left font-semibold whitespace-nowrap ${
                          isDuplicate ? 'bg-red-50' : ''
                        }`}
                      >
                        {isEditing ? (
                          <input
                            ref={editInputRef}
                            type="text"
                            value={editText}
                            onChange={e => setEditText(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') { e.preventDefault(); applyEdit(col, editText) }
                              if (e.key === 'Escape') {
                                e.preventDefault()
                                setEditingCol(null)
                                setEditText('')
                                setPopoverPos(null)
                              }
                            }}
                            className="w-full min-w-[100px] rounded border border-blue-400 bg-white px-1.5 py-0.5 text-xs font-semibold text-slate-700 focus:outline-none focus:ring-1 focus:ring-blue-500"
                          />
                        ) : (
                          <div className="flex items-center gap-1 group min-w-[80px]">
                            <button
                              type="button"
                              onClick={() => startEdit(col)}
                              title="Click to rename"
                              className={`flex-1 text-left text-xs font-semibold cursor-text truncate ${
                                isDuplicate ? 'text-red-600' : 'text-slate-700'
                              } hover:text-blue-600`}
                            >
                              {displayText}
                            </button>
                            <button
                              type="button"
                              onClick={e => { e.stopPropagation(); openDropdownFor(col) }}
                              title="Pick a standard label"
                              aria-label="Pick a standard label"
                              className="shrink-0 rounded border border-slate-300 bg-white px-1 text-[10px] text-slate-500 hover:text-blue-600 hover:border-blue-400 hover:bg-blue-50 transition leading-none"
                            >
                              ▼
                            </button>
                          </div>
                        )}
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {previewRows.map((row, ri) => (
                  <tr key={ri} className={ri % 2 === 0 ? 'bg-white' : 'bg-slate-50/60'}>
                    {entityColumn && (
                      <td className="px-3 py-1.5 text-slate-600 whitespace-nowrap font-mono">
                        {String(row[entityColumn.key] ?? '')}
                      </td>
                    )}
                    {columns.map(col => (
                      <td key={col} className="px-3 py-1.5 text-slate-600 whitespace-nowrap font-mono">
                        {String(row[col] ?? '')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMoreRows && (
            <button
              type="button"
              onClick={() => setShowAllRows(v => !v)}
              className="text-xs font-medium text-blue-600 hover:text-blue-800 hover:underline underline-offset-2"
            >
              {showAllRows ? 'Show fewer rows' : `Show all ${sample.length} rows`}
            </button>
          )}
        </div>
      )}

      {/* Validation messages */}
      {hasDuplicateStandard && (
        <p className="text-xs text-red-600">
          Two or more columns are assigned the same standard label. Each standard label may only be used once.
        </p>
      )}
      {hasInvalidCustom && (
        <p className="text-xs text-red-600">
          Custom column name cannot be empty. Enter a name or switch to "— ignore —".
        </p>
      )}

      {/*
        Fixed-position popover — rendered in the normal DOM flow but uses
        position:fixed so it escapes the overflow-x-auto clip context of the
        table wrapper. Serves both the ▼ dropdown list and inline-edit suggestions.
      */}
      {showPopover && popoverPos && (
        <div
          ref={popoverRef}
          style={{
            position: 'fixed',
            top: popoverPos.top,
            left: Math.max(0, Math.min(popoverPos.left, window.innerWidth - popoverPos.width - 8)),
            minWidth: popoverPos.width,
            zIndex: 9999,
          }}
          className="rounded-md border border-slate-200 bg-white shadow-lg overflow-hidden"
        >
          {popoverMode === 'dropdown' && openDropdown !== null && (
            <ul className="py-1 max-h-60 overflow-y-auto">
              <li>
                <button
                  type="button"
                  onMouseDown={e => { e.preventDefault(); pickFromDropdown(openDropdown, IGNORE_SENTINEL) }}
                  className="w-full text-left px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-100"
                >
                  — ignore —
                </button>
              </li>
              {GOBD_LABEL_OPTIONS.map(label => (
                <li key={label}>
                  <button
                    type="button"
                    onMouseDown={e => { e.preventDefault(); pickFromDropdown(openDropdown, label) }}
                    className={`w-full text-left px-3 py-1.5 text-xs hover:bg-blue-50 hover:text-blue-700 ${
                      assignments[openDropdown]?.dropdownValue === label
                        ? 'font-semibold text-blue-700 bg-blue-50/60'
                        : 'text-slate-700'
                    }`}
                  >
                    {label}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {popoverMode === 'suggestions' && editingCol !== null && (
            <ul className="py-1 max-h-48 overflow-y-auto">
              {suggestions.map(label => (
                <li key={label}>
                  <button
                    type="button"
                    onMouseDown={e => { e.preventDefault(); pickSuggestion(editingCol, label) }}
                    className="w-full text-left px-3 py-1.5 text-xs text-slate-700 hover:bg-blue-50 hover:text-blue-700"
                  >
                    {label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {confirmError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {confirmError}
        </div>
      )}

      {/* Required-field gate hint */}
      {missingRequiredGoBD.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
          Required GoBD fields not yet assigned:{' '}
          <span className="font-semibold">{missingRequiredGoBD.join(', ')}</span>.
          Assign them above to enable confirmation.
        </div>
      )}

      {/* Action buttons */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {onBack && (
          <button
            type="button"
            onClick={onBack}
            disabled={confirming}
            className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            Back
          </button>
        )}
        <button
          type="button"
          onClick={() => void onConfirm(buildHeaders())}
          disabled={!isValid || confirming}
          className="rounded-md px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
          style={{ backgroundColor: '#1E3A5F' }}
        >
          {confirming ? 'Applying…' : 'Confirm column names'}
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// GlEntityCard props
// ---------------------------------------------------------------------------

export interface GlEntityCardProps {
  entity: GlEntityState
  index: number
  total: number
  years: number[]
  entityMode: 'create' | 'select'
  /** Fiscal year end month (1–12). Drives GlYearSlot labels. Defaults to 12. */
  fyEndMonth?: number
  onPatch: (patch: Partial<GlEntityState>) => void
  onRemove?: () => void
  /** Format group this entity belongs to (undefined until headers are confirmed). */
  group?: GlFormatGroup
  /** All groups in the wizard — passed to GlGroupSelect for eligibility checks. */
  groups: GlFormatGroup[]
  /** All entities — passed to GlGroupSelect for the member-picker list. */
  entities?: GlEntityState[]
  /** Called when this entity becomes the representative of a new format group. */
  onCreateGroup: (fromIndex: number, headers: string[], mapping: Record<string, string>) => void
  /**
   * Called when the user confirms which other entities share this format.
   * The parent applies the group's headers to each selected entity.
   * Returns a promise so the card can show a loading state.
   */
  onAssignGroup: (groupId: string, selectedIndices: number[]) => Promise<void>
  /** Called when group-level config (partner mode / opts) changes. */
  onPatchGroup: (groupId: string, patch: Partial<GlFormatGroup>) => void
  /**
   * 'inline'   (default) — inline GlGroupSelect after header confirm; IngestionPage path.
   * 'external' — combine stores data only (no auto-confirm, no onCreateGroup); GlGroupSelect hidden;
   *              ConfirmHeadersStep shown only for the group representative; non-reps see a waiting msg.
   *              Used by ProjectSetupWizard after GlFormatAssignmentStep.
   */
  groupingMode?: 'inline' | 'external'
  /**
   * 'inline'  (default) — per-entity Combine button shown inside the card. Used by IngestionPage.
   * 'footer'  — per-entity Combine button and info box are suppressed; combining is driven by the
   *             parent's footer button (batchCombineGlEntities in ProjectSetupWizard). The card
   *             still stores combine results via onPatch when the parent calls combineGlFiles.
   */
  combineMode?: 'footer' | 'inline'
  /**
   * Stage gate forwarded to ValidierungStep → POST /ingest/validate.
   * 'gl': only S1, S2, B1, B2, B3, Q2. Omit for full catalog (IngestionPage behavior).
   */
  validationStage?: 'gl' | 'coa' | 'partner' | 'all'
}

// ---------------------------------------------------------------------------
// GlEntityCard — one card per entity: N year-slots + combine + mapping/validate
// ---------------------------------------------------------------------------

export function GlEntityCard({
  entity,
  index,
  total,
  years,
  entityMode,
  fyEndMonth = 12,
  onPatch,
  onRemove,
  group,
  groups: _groups,
  entities,
  onCreateGroup,
  onAssignGroup,
  onPatchGroup,
  groupingMode = 'inline',
  combineMode = 'inline',
  validationStage,
}: GlEntityCardProps) {
  const [expanded, setExpanded] = useState(() => entity.validationOk === undefined)

  // Combine state
  const [combining, setCombining] = useState(false)
  const [combineError, setCombineError] = useState<string | null>(null)

  const [validationResult, setValidationResult] = useState<ValidationResponse | null>(null)
  const [entityAssignments, setEntityAssignments] = useState<Record<string, string>>(
    () => entity.entityAssignments ?? {},
  )

  // Header confirm step state
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  // True while user is re-editing headers on an already-confirmed entity
  const [editingHeaders, setEditingHeaders] = useState(false)

  // Entity-level header detection override ('auto' | 'yes' | 'no')
  const [headerOverride, setHeaderOverride] = useState<'auto' | 'yes' | 'no'>(
    () => entity.headerOverride ?? 'auto',
  )

  // GlGroupSelect: shown once when this entity first becomes a group representative
  const [showGroupSelect, setShowGroupSelect] = useState(false)
  const [assigningGroup, setAssigningGroup] = useState(false)
  const prevFormatGroupIdRef = useRef<string | undefined>(entity.formatGroupId)

  useEffect(() => {
    const prev = prevFormatGroupIdRef.current
    prevFormatGroupIdRef.current = entity.formatGroupId
    // Show group-select panel when this entity first gets assigned as a representative.
    // In external mode the assignment was already done by GlFormatAssignmentStep — skip.
    if (!prev && entity.formatGroupId && group?.representativeIndex === index && total > 1 && groupingMode !== 'external') {
      setShowGroupSelect(true)
    }
  }, [entity.formatGroupId, group?.representativeIndex, index, total])

  // Entity select mode — load existing entities from API
  const [existingEntities, setExistingEntities] = useState<Array<{ code: string; label: string; prefix: string }>>([])
  const [entitiesLoading, setEntitiesLoading] = useState(false)

  useEffect(() => {
    if (entityMode !== 'select') return
    setEntitiesLoading(true)
    budgetEntities()
      .then(r => setExistingEntities(r.entities))
      .catch(() => {})
      .finally(() => setEntitiesLoading(false))
  }, [entityMode])

  // Whether all year slots are filled
  const allYearsFilled = years.length > 0 && years.every(y => entity.yearFiles[y] !== undefined)
  // Count and flag for partial-year gating (at least one year file present)
  const filledYearCount = years.filter(y => entity.yearFiles[y] !== undefined).length
  const hasAnyYearFile = filledYearCount > 0

  // The combined UploadResponse shape (built from entity state when combine succeeded)
  const combinedUploadResult: UploadResponse | null = entity.combinedFileId && entity.combinedColumns && entity.combinedSample && entity.combinedDialect
    ? {
        file_id: entity.combinedFileId,
        filename: '',
        sheets: [],
        columns: entity.combinedColumns,
        sample: entity.combinedSample,
        dialect: entity.combinedDialect,
      }
    : null

  function handleYearFileUploaded(year: number, result: UploadResponse) {
    const newYearFiles: Partial<Record<number, GlYearFileState>> = {
      ...entity.yearFiles,
      [year]: { fileId: result.file_id, filename: result.filename, sheet: result.sheets[0] },
    }

    // Derive a hint from the first uploaded file's header detection result
    const isFirstFile = Object.keys(entity.yearFiles).length === 0
    const hint = isFirstFile && result.header_detected === false
      ? 'No header row detected in this file.'
      : isFirstFile && result.header_detected === true
      ? 'Header row detected in this file.'
      : undefined

    onPatch({
      yearFiles: newYearFiles,
      combinedFileId: undefined,
      combinedColumns: undefined,
      combinedSample: undefined,
      combinedDialect: undefined,
      combinedSuggestedHeaders: undefined,
      headersConfirmed: undefined,
      formatGroupId: undefined,
      validationOk: undefined,
      assembledProfile: undefined,
      entityAssignments: undefined,
      ...(hint !== undefined ? { headerHint: hint } : {}),
    })
    setValidationResult(null)
    setConfirmError(null)
    setShowGroupSelect(false)
  }

  async function handleCombine() {
    setCombining(true)
    setCombineError(null)
    setConfirmError(null)
    try {
      const inputs = years
        .filter(y => entity.yearFiles[y] !== undefined)
        .map(y => ({ file_id: entity.yearFiles[y]!.fileId, fiscal_year: y }))
      const result = await combineGlFiles({ inputs })

      const isSynthetic = hasSyntheticHeaders(result.columns)
      const suggestedHdrs = result.suggested_headers ?? {}
      const colWarning = result.column_warning ?? undefined

      if (isSynthetic) {
        // Headerless path — show ConfirmHeadersStep so the user assigns GoBD labels
        onPatch({
          combinedFileId: result.file_id,
          combinedColumns: result.columns,
          combinedSample: result.sample,
          combinedDialect: result.dialect,
          combinedColumnWarning: colWarning,
          combinedSuggestedHeaders: suggestedHdrs,
          headersConfirmed: false,
          validationOk: undefined,
          assembledProfile: undefined,
          entityAssignments: undefined,
        })
      } else if (groupingMode === 'external') {
        // External mode: never auto-confirm; always show ConfirmHeadersStep pre-filled with existing column names.
        // onCreateGroup is NOT called — group assignment happened earlier via GlFormatAssignmentStep.
        const preFill = Object.fromEntries(
          result.columns.filter(c => c !== 'fiscal_year').map(c => [c, c])
        )
        onPatch({
          combinedFileId: result.file_id,
          combinedColumns: result.columns,
          combinedSample: result.sample,
          combinedDialect: result.dialect,
          combinedColumnWarning: colWarning,
          combinedSuggestedHeaders: preFill,
          headersConfirmed: false,
          validationOk: undefined,
          assembledProfile: undefined,
          entityAssignments: undefined,
        })
      } else {
        // Inline mode, headered path — check if all required GoBD fields are already satisfied
        const suggested = suggestGlMapping(result.columns.filter(c => c !== 'fiscal_year'))
        const missingRequired = missingRequiredFields(suggested)
        if (missingRequired.length === 0) {
          // All required fields satisfied — auto-confirm and create group
          onPatch({
            combinedFileId: result.file_id,
            combinedColumns: result.columns,
            combinedSample: result.sample,
            combinedDialect: result.dialect,
            combinedColumnWarning: colWarning,
            combinedSuggestedHeaders: undefined,
            headersConfirmed: true,
            validationOk: undefined,
            assembledProfile: undefined,
            entityAssignments: undefined,
          })
          if (!entity.formatGroupId) {
            onCreateGroup(index, result.columns, suggested)
          }
        } else {
          // Some required fields missing — show ConfirmHeadersStep pre-filled with existing column names
          const preFill = Object.fromEntries(
            result.columns.filter(c => c !== 'fiscal_year').map(c => [c, c])
          )
          onPatch({
            combinedFileId: result.file_id,
            combinedColumns: result.columns,
            combinedSample: result.sample,
            combinedDialect: result.dialect,
            combinedColumnWarning: colWarning,
            combinedSuggestedHeaders: preFill,
            headersConfirmed: false,
            validationOk: undefined,
            assembledProfile: undefined,
            entityAssignments: undefined,
          })
        }
      }
    } catch (e) {
      setCombineError(e instanceof Error ? e.message : 'Combine failed')
    } finally {
      setCombining(false)
    }
  }

  /**
   * Confirm headers — called by ConfirmHeadersStep for both synthetic files
   * and headered-but-incomplete files, and also when user edits headers again.
   * Creates or updates the format group for this entity.
   */
  async function handleApplyHeaders(headers: string[]) {
    if (!entity.combinedFileId) return
    setConfirming(true)
    setConfirmError(null)
    try {
      const result = await applyHeaders(entity.combinedFileId, headers)
      const suggested = suggestGlMapping(result.columns.filter(c => c !== 'fiscal_year'))
      const newMapping = Object.keys(suggested).length > 0 ? suggested : {}
      onPatch({
        combinedFileId: result.file_id,
        combinedColumns: result.columns,
        combinedSample: result.sample,
        combinedSuggestedHeaders: undefined,
        headersConfirmed: true,
        validationOk: undefined,
        assembledProfile: undefined,
        entityAssignments: undefined,
      })
      setEditingHeaders(false)
      if (entity.formatGroupId && group) {
        // Re-editing headers on an entity already in a group (or external-mode first confirm):
        // update the group's shared config. headersConfirmed: true is safe for re-edit (already was true).
        onPatchGroup(entity.formatGroupId, {
          headers: result.columns,
          mapping: newMapping,
          columnCount: result.columns.length,
          headersConfirmed: true,
        })
      } else {
        // First-time confirm (inline mode): create a new group for this entity as representative
        onCreateGroup(index, result.columns, newMapping)
      }
    } catch (e) {
      setConfirmError(e instanceof Error ? e.message : 'Failed to apply headers')
    } finally {
      setConfirming(false)
    }
  }

  /** Build the GL profile for validation. Reads opts and mapping from the group. */
  function buildEntityProfile(ea: Record<string, string> = {}): Profile {
    const effectiveOpts = group?.opts ?? defaultGlOpts()
    const effectiveMapping = group?.mapping ?? {}
    return {
      entity: { mode: 'fixed', value: entity.entityCode },
      fiscal_year: { mode: 'column', value: 'fiscal_year' },
      sign: (() => {
        if (effectiveOpts.signMode === 'signed') return { mode: 'signed' as const, amount: effectiveOpts.signAmount }
        if (effectiveOpts.signMode === 'soll_haben') return { mode: 'soll_haben' as const, soll: effectiveOpts.signSoll, haben: effectiveOpts.signHaben }
        return { mode: 'amount_dc' as const, amount: effectiveOpts.signAmount, dc_flag: effectiveOpts.signDcFlag, debit_value: effectiveOpts.signDebitValue }
      })(),
      decimal: effectiveOpts.decimal || entity.combinedDialect?.decimal || ',',
      thousands: effectiveOpts.thousands || entity.combinedDialect?.thousands || '.',
      date_dayfirst: effectiveOpts.dateDayfirst,
      columns: { ...effectiveMapping },
      linking_strategy: effectiveOpts.linking,
      entry_type: 'actual',
      ...(Object.keys(ea).length > 0 ? { entity_assignments: ea } : {}),
    }
  }

  function handleValidationResult(r: ValidationResponse) {
    setValidationResult(r)
    const profile = buildEntityProfile(entityAssignments)
    onPatch({
      validationOk: r.summary.passed,
      entityAssignments,
      assembledProfile: profile,
      // Propagate active exclusions so the Finish commit loop can honour them (F-1 fix).
      excludedLineIds: r.exclusions?.active_line_ids ?? [],
    })
  }

  const handleEntityAssignmentsChange = useCallback((ea: Record<string, string>) => {
    setEntityAssignments(ea)
  }, [])

  const noopImportSuccess = useCallback((_r: import('../../lib/gdpduApi').CommitResponse) => {}, [])

  const columns = combinedUploadResult?.columns ?? []
  const sample = combinedUploadResult?.sample ?? []
  const glProfile = combinedUploadResult ? buildEntityProfile(entityAssignments) : null

  // Entity identity gate: required before uploading/combining/validating
  const entityChosen = entity.entityCode.trim().length > 0

  // Group config readiness gate (sign options must be set before validation)
  const effectiveOpts = group?.opts ?? defaultGlOpts()
  const signComplete =
    effectiveOpts.signMode === 'signed' ? Boolean(effectiveOpts.signAmount)
    : effectiveOpts.signMode === 'amount_dc' ? Boolean(effectiveOpts.signAmount && effectiveOpts.signDcFlag)
    : Boolean(effectiveOpts.signSoll && effectiveOpts.signHaben)
  const splitComplete =
    (group?.partnerColumnsMode ?? 'single') !== 'split' || (
      Boolean(group?.partnerColumnsSplit?.creditorNoCol) &&
      Boolean(group?.partnerColumnsSplit?.debtorNoCol) &&
      Boolean(group?.partnerColumnsSplit?.fixedAssetNoCol)
    )
  // inGroup: entity belongs to a group (or is single-entity project that auto-groups)
  const inGroup = entity.formatGroupId !== undefined || total === 1
  const groupConfigReady = inGroup && signComplete && splitComplete

  // Card header badge
  const validationBadge = entity.validationOk === true
    ? <span className="text-emerald-600 font-medium text-xs">Validated</span>
    : entity.validationOk === false
    ? <span className="text-amber-600 font-medium text-xs">Warnings</span>
    : entity.headersConfirmed && groupConfigReady
    ? <span className="text-blue-600 font-medium text-xs">Ready to validate</span>
    : entity.headersConfirmed && inGroup
    ? <span className="text-blue-600 font-medium text-xs">Complete format config</span>
    : entity.headersConfirmed
    ? <span className="text-blue-600 font-medium text-xs">Assign format group</span>
    : entity.combinedFileId
    ? <span className="text-blue-600 font-medium text-xs">Confirm column headers</span>
    : allYearsFilled
    ? <span className="text-blue-500 font-medium text-xs">Ready to combine</span>
    : <span className="text-slate-400 text-xs">{years.length === 0 ? 'Select years first' : `${Object.keys(entity.yearFiles).length}/${years.length} files uploaded`}</span>

  const entityDisplayName = entity.entityCode.trim() || `Entity ${index + 1}`

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Card header */}
      <div
        className="flex items-center justify-between px-5 py-3 cursor-pointer hover:bg-slate-50 transition"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-400 w-20 shrink-0">
            Entity {index + 1}
          </span>
          <span className="text-sm text-slate-700 font-medium">{entityDisplayName}</span>
          <span className="text-slate-300">·</span>
          {validationBadge}
        </div>
        <div className="flex items-center gap-2">
          {total > 1 && onRemove && (
            <button
              type="button"
              onClick={e => {
                e.stopPropagation()
                onRemove()
              }}
              className="rounded px-2 py-0.5 text-xs text-red-500 hover:bg-red-50 border border-red-200 transition"
              title="Remove this entity"
            >
              Remove
            </button>
          )}
          <span className="text-slate-400 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </div>

      {/* Expandable body */}
      {expanded && (
        <div className="border-t border-slate-100 px-5 pt-4 pb-5 space-y-5">

          {/* Entity identity — create mode: text inputs; select mode: dropdown */}
          {entityMode === 'create' ? (
            <div className="flex items-center gap-4">
              <div className="space-y-1 w-56">
                <label className="text-xs font-medium text-slate-700">
                  Entity code
                  <span className="ml-0.5 text-red-500" aria-hidden>*</span>
                </label>
                <input
                  type="text"
                  value={entity.entityCode}
                  onChange={e => onPatch({ entityCode: e.target.value })}
                  placeholder="e.g. DE, AT, 01"
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="text-xs text-slate-400">Short code stored as entity prefix in the GL.</p>
              </div>
              <div className="space-y-1 flex-1">
                <label className="text-xs font-medium text-slate-700">Display label (optional)</label>
                <input
                  type="text"
                  value={entity.entityLabel ?? ''}
                  onChange={e => onPatch({ entityLabel: e.target.value })}
                  placeholder="e.g. Germany, Austria"
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          ) : (
            <div className="space-y-1 max-w-md">
              <label className="text-xs font-medium text-slate-700">
                Select entity
                <span className="ml-0.5 text-red-500" aria-hidden>*</span>
              </label>
              {entitiesLoading ? (
                <p className="text-xs text-slate-400">Loading entities…</p>
              ) : (
                <select
                  value={entity.entityCode}
                  onChange={e => {
                    const chosen = existingEntities.find(x => x.code === e.target.value)
                    onPatch({ entityCode: e.target.value, entityLabel: chosen?.label })
                  }}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Select entity --</option>
                  {existingEntities.map(ent => (
                    <option key={ent.code} value={ent.code}>
                      {ent.label || ent.code} ({ent.code})
                    </option>
                  ))}
                </select>
              )}
              {existingEntities.length === 0 && !entitiesLoading && (
                <p className="text-xs text-amber-600">No entities found. Make sure the API is running.</p>
              )}
            </div>
          )}

          {/* Per-year upload slots */}
          {!entityChosen ? (
            <WarnBox>
              {entityMode === 'create'
                ? 'Enter the entity code above before uploading files for this entity.'
                : 'Select an entity above before uploading files for this entity.'}
            </WarnBox>
          ) : years.length === 0 ? (
            <WarnBox>Select at least one fiscal year at the top of this step before uploading files.</WarnBox>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-4 flex-wrap">
                <p className="text-xs font-semibold text-slate-700">
                  Upload one file per fiscal year
                  <span className="ml-2 font-normal text-slate-400">
                    — each file will be tagged with its year and concatenated into one combined file
                  </span>
                </p>
                {/* Header-row override toggle */}
                <div className="flex items-center gap-2 shrink-0">
                  <label className="text-xs text-slate-600 whitespace-nowrap">Files include a header row?</label>
                  <select
                    value={headerOverride}
                    onChange={e => {
                      const v = e.target.value as 'auto' | 'yes' | 'no'
                      setHeaderOverride(v)
                      onPatch({ headerOverride: v })
                    }}
                    className="rounded border border-slate-300 px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="auto">Auto (detect)</option>
                    <option value="yes">Yes</option>
                    <option value="no">No</option>
                  </select>
                  {entity.headerHint && headerOverride === 'auto' && (
                    <span className="text-xs text-amber-700 italic">{entity.headerHint}</span>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {years.map(year => (
                  <GlYearSlot
                    key={year}
                    year={year}
                    slot={entity.yearFiles[year]}
                    fyEndMonth={fyEndMonth}
                    hasHeaderOverride={
                      headerOverride === 'yes' ? true
                      : headerOverride === 'no' ? false
                      : undefined
                    }
                    onUploaded={handleYearFileUploaded}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Combine button — suppressed in footer mode; parent footer handles batch combine */}
          {combineMode === 'inline' && years.length > 0 && hasAnyYearFile && !entity.combinedFileId && (
            <div className="space-y-2">
              <InfoBox>
                {filledYearCount} of {years.length} year {filledYearCount === 1 ? 'file has' : 'files have'} been uploaded. Click "Combine files" to
                concatenate them into one staging file with a{' '}
                <span className="font-mono text-xs bg-white border border-blue-200 rounded px-1">fiscal_year</span> column
                — you will then assign column headers and validate once for this entity.
              </InfoBox>
              <button
                type="button"
                onClick={() => void handleCombine()}
                disabled={combining}
                className="rounded-md px-5 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
                style={{ backgroundColor: '#1E3A5F' }}
              >
                {combining ? 'Combining…' : `Combine ${filledYearCount} file${filledYearCount === 1 ? '' : 's'} for this entity`}
              </button>
              {combineError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {combineError}
                </div>
              )}
            </div>
          )}

          {/* Replace combined file notice — suppressed in external mode (header assignment moved to group panel) */}
          {entity.combinedFileId && groupingMode !== 'external' && (
            <div className="rounded-lg border border-blue-100 bg-blue-50/60 px-4 py-2 text-xs text-blue-800 flex items-center justify-between gap-3">
              <span>
                Combined file ready — {entity.combinedColumns?.length ?? 0} columns, includes{' '}
                <span className="font-mono bg-white border border-blue-200 rounded px-1">fiscal_year</span>.
              </span>
              <button
                type="button"
                onClick={() => {
                  onPatch({
                    combinedFileId: undefined, combinedColumns: undefined,
                    combinedSample: undefined, combinedDialect: undefined,
                    combinedColumnWarning: undefined,
                    combinedSuggestedHeaders: undefined, headersConfirmed: undefined,
                    formatGroupId: undefined,
                    validationOk: undefined, assembledProfile: undefined, entityAssignments: undefined,
                  })
                  setValidationResult(null)
                  setCombineError(null)
                  setConfirmError(null)
                  setShowGroupSelect(false)
                }}
                className="text-xs text-blue-700 underline underline-offset-1 hover:text-blue-900 whitespace-nowrap"
              >
                Re-upload files
              </button>
            </div>
          )}

          {/* Column-count heads-up — likely wrong-delimiter misparse during combine.
              In external mode this warning is aggregated at group level. */}
          {entity.combinedFileId && entity.combinedColumnWarning && groupingMode !== 'external' && (
            <WarnBox>{entity.combinedColumnWarning}</WarnBox>
          )}

          {/* Post-combine flow */}
          {entity.combinedFileId && columns.length > 0 && (
            <div className="space-y-4">

              {/* External-mode note: header assignment is handled at group level (GlGroupConfigPanel). */}
              {groupingMode === 'external' && entity.combinedFileId && !entity.headersConfirmed && (
                <div className="rounded-lg border border-blue-100 bg-blue-50/60 px-4 py-3 text-sm text-blue-700">
                  Assign column headers in the format panel above.
                </div>
              )}

              {/* ConfirmHeadersStep — shown for headerless files, headered-but-incomplete, or when re-editing.
                  Suppressed in external mode: header assignment is at group level via GlGroupConfigPanel. */}
              {groupingMode !== 'external' && (!entity.headersConfirmed || editingHeaders) && (
                <ConfirmHeadersStep
                  columns={columns}
                  suggestedHeaders={
                    editingHeaders
                      ? Object.fromEntries(columns.filter(c => c !== 'fiscal_year').map(c => [c, c]))
                      : (entity.combinedSuggestedHeaders ?? {})
                  }
                  sample={sample}
                  onConfirm={handleApplyHeaders}
                  onBack={() => {
                    if (editingHeaders) {
                      setEditingHeaders(false)
                      return
                    }
                    onPatch({
                      combinedFileId: undefined, combinedColumns: undefined,
                      combinedSample: undefined, combinedDialect: undefined,
                      combinedSuggestedHeaders: undefined, headersConfirmed: undefined,
                      formatGroupId: undefined,
                      validationOk: undefined, assembledProfile: undefined, entityAssignments: undefined,
                    })
                    setValidationResult(null)
                    setCombineError(null)
                    setConfirmError(null)
                    setShowGroupSelect(false)
                  }}
                  confirming={confirming}
                  confirmError={confirmError}
                />
              )}

              {/* Headers confirmed — info bar with optional edit affordance.
                  Suppressed in external mode: the group panel owns confirmation UI. */}
              {entity.headersConfirmed && !editingHeaders && groupingMode !== 'external' && (
                <div className="rounded-lg border border-blue-100 bg-blue-50/60 px-4 py-2 text-xs text-blue-800 flex items-center justify-between gap-3">
                  <span>
                    Column headers confirmed — {columns.length} columns (includes{' '}
                    <span className="font-mono bg-white border border-blue-200 rounded px-1">fiscal_year</span>).
                    {group && group.memberIndices.length > 1 && group.representativeIndex !== index && (
                      <span className="ml-2 text-blue-600">Applied from {group.label}.</span>
                    )}
                  </span>
                  {/* Only the representative can re-edit headers (changes propagate to the whole group) */}
                  {(!group || group.representativeIndex === index) && (
                    <button
                      type="button"
                      onClick={() => setEditingHeaders(true)}
                      className="text-xs text-blue-700 underline underline-offset-1 hover:text-blue-900 whitespace-nowrap"
                    >
                      Edit column headers
                    </button>
                  )}
                </div>
              )}

              {/* GlGroupSelect — shown once, for the representative entity, multi-entity wizards only */}
              {showGroupSelect && group && group.representativeIndex === index && total > 1 && (
                <GlGroupSelect
                  group={group}
                  entities={entities ?? []}
                  representativeIndex={index}
                  onConfirm={async (selectedIndices) => {
                    setAssigningGroup(true)
                    try {
                      await onAssignGroup(group.id, selectedIndices)
                    } finally {
                      setShowGroupSelect(false)
                      setAssigningGroup(false)
                    }
                  }}
                  loading={assigningGroup}
                />
              )}

              {/* Waiting for group config (GlGroupConfigPanel lives at parent level) */}
              {entity.headersConfirmed && !editingHeaders && !showGroupSelect && inGroup && !groupConfigReady && (
                <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 px-4 py-3 text-sm text-indigo-800">
                  Format configuration (partner columns + sign options) is managed in the{' '}
                  <span className="font-semibold">{group?.label ?? 'format group'}</span> panel.
                  Complete it to enable validation for this entity.
                </div>
              )}

              {/* ValidierungStep — suppressed in external grouping mode because
                  GlGroupConfigPanel owns consolidated validation there (F-3).
                  In IngestionPage (inline mode) this is always rendered. */}
              {entity.headersConfirmed && !editingHeaders && !showGroupSelect && groupConfigReady && combinedUploadResult && glProfile && groupingMode !== 'external' && (
                <>
                  <ValidierungStep
                    uploadResult={combinedUploadResult}
                    profile={glProfile}
                    onResult={handleValidationResult}
                    onEntityAssignmentsChange={handleEntityAssignmentsChange}
                    onImportSuccess={noopImportSuccess}
                    stagingMode
                    stage={validationStage}
                  />
                  {validationResult && (
                    <div className={`rounded-lg border px-4 py-3 text-sm ${
                      validationResult.summary.passed
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                        : 'border-amber-200 bg-amber-50 text-amber-800'
                    }`}>
                      {validationResult.summary.passed
                        ? 'Validation passed. This entity is ready to commit.'
                        : 'Validation has warnings. You can still continue — soft warnings are accepted at commit time.'}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// Re-export KontextState so importers of this file can get it transitively if needed
export type { KontextState, OptionsState }
