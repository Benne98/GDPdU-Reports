/**
 * ProjectSetupWizard.tsx — self-contained onboarding wizard for first-time project setup.
 *
 * Phase 5 complete: Finish orchestration — sequential collect-then-commit with
 *   per-step progress checklist, stop-on-first-error, skip logic for library CoA
 *   and missing files, and optional full rebuild.
 *
 * Steps:
 *   0  Project name
 *   1  Fiscal year end month
 *   2  Upload GL bookings        (Phase 2 — COMPLETE)
 *   3  Chart of accounts         (Phase 3 — COMPLETE)
 *   4  Opening balances          (Phase 4 — COMPLETE)
 *   5  Partner master            (Phase 4 — COMPLETE)
 *   6  Review & Finish           (Phase 5 — COMPLETE)
 *
 * Commit sequence (in dependency order):
 *   1. Save config    PUT /projects/default
 *   2. GL commit      POST /ingest/commit
 *   3. CoA commit     POST /ingest/mapping/commit  (skipped when source=library)
 *   4. OB commit      POST /ingest/opening-balance/commit  (skipped when mode=in_data)
 *   5. Partner commit POST /ingest/partner-master/commit  (skipped when no file staged)
 *   6. Rebuild        POST /projects/default/rebuild  (optional checkbox)
 *
 * On mount: prefills from api.getProject('default') where sensible.
 * The /ingestion route is NOT linked here — this wizard is self-contained.
 */

import { type Dispatch, useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api, type ProjectConfigResponse } from '../lib/api'
import { Stepper, StepCard, NavButtons } from '../components/ingest/IngestStepCard'
import UploadStep from '../components/ingest/UploadStep'
import KontextStep from '../components/ingest/KontextStep'
import ColumnMapper, { missingRequiredFields } from '../components/ingest/ColumnMapper'
import OptionsStep from '../components/ingest/OptionsStep'
import ValidierungStep from '../components/ingest/ValidierungStep'
import AccountColumnMapper, { missingRequiredAccountFields } from '../components/ingest/AccountColumnMapper'
import { isBsPlMasterSheets } from '../components/ingest/UploadStep'
import type { KontextState, OptionsState } from '../components/ingest/ingestTypes'
import {
  uploadFile,
  downloadCoaTemplate,
  uploadOpeningBalance,
  uploadPartnerMaster,
  commitIngest,
  commitAccountMapping,
  commitOpeningBalance,
  commitPartnerMaster,
  type Profile,
  type UploadResponse,
  type ValidationResponse,
  type CommitResponse,
  type Dialect,
  type MappingCommitResponse,
  type ObCommitResponse,
  type PartnerCommitResponse,
} from '../lib/gdpduApi'

// ---------------------------------------------------------------------------
// FY helper — exported for reuse in Phase 5 Finish orchestration
// ---------------------------------------------------------------------------

/** Convert a fiscal year end month (1–12) to the corresponding start month. */
export function fyStartFromEndMonth(end: number): number {
  return (end % 12) + 1
}

/** Derive the fiscal year end month from a stored fy_start_month. */
export function fyEndFromStartMonth(start: number): number {
  // end = ((start - 2 + 12) % 12) + 1
  // start=1 → end=12, start=4 → end=3, start=7 → end=6
  return ((start - 2 + 12) % 12) + 1
}

// ---------------------------------------------------------------------------
// Wizard state shape — GL partner-columns toggle + full GL sub-wizard state
// ---------------------------------------------------------------------------

/**
 * Describes how creditor / debtor / fixed-asset numbers appear in the GL file.
 *
 * 'single' (default, fully functional): one "source_no" column holds the
 *   partner number; a companion "source_type" column distinguishes creditor /
 *   debtor / fixed-asset. The backend's apply_profile handles this natively.
 *
 * 'split': three separate columns (creditor_no, debtor_no, fixed_asset_no).
 *   The column-names below are stored in the profile under partner_columns.
 *   NOTE: backend consumption of the split case is a documented follow-up
 *   (Phase 4 / backend-engineer); the single-column path is the functional
 *   default for the current backend.
 */
export type PartnerColumnsMode = 'single' | 'split'

export interface PartnerColumnsSplit {
  creditorNoCol: string
  debtorNoCol: string
  fixedAssetNoCol: string
}

export interface WizardGlState {
  // --- Upload sub-step ---
  fileId?: string
  sheet?: string
  columns?: string[]
  sample?: Record<string, unknown>[]
  dialect?: Dialect

  // --- Entity (KontextStep) sub-step ---
  kontext?: KontextState

  // --- Partner columns toggle ---
  partnerColumnsMode?: PartnerColumnsMode
  partnerColumnsSplit?: PartnerColumnsSplit

  // --- Column mapping sub-step ---
  mapping?: Record<string, string>

  // --- Options (amount/date/linking) sub-step ---
  opts?: OptionsState

  // --- Validation sub-step ---
  validationOk?: boolean
  entityAssignments?: Record<string, string>

  /**
   * The fully assembled Profile (built from kontext + mapping + opts at
   * validation time). Stored here so Phase 5 Finish can use it for the commit
   * without rebuilding from scratch.
   */
  assembledProfile?: Profile
}

export interface WizardCoaState {
  /** 'library' = Finssentials standard; 'upload' = client master upload */
  source?: 'library' | 'upload'
  /** e.g. 'skr03' | 'statutory' | 'past_projects' — populated when source='library' */
  libraryVariant?: string
  /** file_id from uploadFile(), populated when source='upload' */
  masterFileId?: string
  /** sheet name(s) from the uploaded file */
  masterSheets?: string[]
  /** detected columns from the uploaded master file */
  masterColumns?: string[]
  /** sample rows from the uploaded master file */
  masterSample?: Record<string, unknown>[]
  /**
   * Whether the uploaded file is already in Master_BS/Master_PL shape.
   * true  → commits as format=bs_pl_master (no column mapping needed)
   * false → generic client CoA, column mapping via AccountColumnMapper
   */
  isBsPlMaster?: boolean
  /** Column mapping for generic (non-master-shaped) client CoA uploads */
  mapping?: Record<string, string>
}

export interface WizardObState {
  /** 'in_data' | 'file_first_year' | 'file_all' */
  mode?: 'in_data' | 'file_first_year' | 'file_all'
  obFileId?: string
  /** Detected columns from the uploaded OB file */
  obColumns?: string[]
  /** Sample rows from the uploaded OB file */
  obSample?: Record<string, unknown>[]
  /** Minimal column profile: account → column, amount → column */
  obProfile?: { account_col?: string; amount_col?: string }
}

/**
 * PartnerMappingProfile — stored in the wizard and forwarded verbatim to the
 * /ingest/partner-master/commit call in Phase 5 Finish.
 *
 * side:      'customer' | 'supplier' — which dimension table to populate
 * entity:    how the entity/legal entity is identified (fixed value or from a column)
 * join_key:  the column that holds the debtor/creditor number (used as PK for the upsert)
 * columns:   name and address column mappings (name_line_1 is required)
 */
export interface PartnerMappingProfile {
  side: 'customer' | 'supplier'
  entity: { mode: 'fixed' | 'column'; value: string }
  join_key: { column: string }
  columns: {
    name_line_1: string
    name_line_2?: string
    country_code?: string
    city?: string
    postal_code?: string
  }
}

export interface WizardPartnerState {
  fileId?: string
  /** Detected columns from the uploaded partner master file */
  columns?: string[]
  /** Sample rows from the uploaded partner master file */
  sample?: Record<string, unknown>[]
  /** Full join-key + name/address mapping */
  profile?: PartnerMappingProfile
}

export interface WizardState {
  projectName: string
  fyEndMonth: number          // 1–12; UI value (converted to fy_start_month at Finish)
  entities: Array<{ code: string; prefix: string; name: string }>
  gl: WizardGlState
  coa: WizardCoaState
  ob: WizardObState
  partner: WizardPartnerState
}

function defaultState(): WizardState {
  return {
    projectName: '',
    fyEndMonth: 12,
    entities: [{ code: '', prefix: '', name: '' }],
    gl: {},
    coa: {},
    ob: {},
    partner: {},
  }
}

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

type WizardAction =
  | { type: 'SET_NAME'; value: string }
  | { type: 'SET_FY_END_MONTH'; value: number }
  | { type: 'SET_ENTITIES'; entities: WizardState['entities'] }
  | { type: 'PATCH_GL'; patch: Partial<WizardGlState> }
  | { type: 'PATCH_COA'; patch: Partial<WizardCoaState> }
  | { type: 'PATCH_OB'; patch: Partial<WizardObState> }
  | { type: 'PATCH_PARTNER'; patch: Partial<WizardPartnerState> }
  | { type: 'PREFILL'; partial: Partial<WizardState> }

function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'SET_NAME':         return { ...state, projectName: action.value }
    case 'SET_FY_END_MONTH': return { ...state, fyEndMonth: action.value }
    case 'SET_ENTITIES':     return { ...state, entities: action.entities }
    case 'PATCH_GL':         return { ...state, gl: { ...state.gl, ...action.patch } }
    case 'PATCH_COA':        return { ...state, coa: { ...state.coa, ...action.patch } }
    case 'PATCH_OB':         return { ...state, ob: { ...state.ob, ...action.patch } }
    case 'PATCH_PARTNER':    return { ...state, partner: { ...state.partner, ...action.patch } }
    case 'PREFILL':          return { ...state, ...action.partial }
    default:                 return state
  }
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PROJECT_ID = 'default'

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
] as const

const WIZARD_STEPS = [
  'Project name',
  'Fiscal year',
  'GL bookings',
  'Chart of accounts',
  'Opening balances',
  'Partner master',
  'Review & Finish',
] as const

// ---------------------------------------------------------------------------
// Shared primitives
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

function Field({
  label,
  required,
  hint,
  children,
}: {
  label: string
  required?: boolean
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <span className="text-sm font-medium text-slate-700">
        {label}
        {required && <span className="ml-0.5 text-red-500" aria-hidden>*</span>}
      </span>
      {children}
      {hint && <p className="text-xs text-slate-400">{hint}</p>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 0 — Project name
// ---------------------------------------------------------------------------

function StepProjectName({
  state,
  dispatch,
}: {
  state: WizardState
  dispatch: Dispatch<WizardAction>
}) {
  const valid = state.projectName.trim().length > 0

  return (
    <StepCard
      title="Project name"
      subtitle="Give this project a recognisable name. It will appear in all reports and exports."
    >
      <div className="space-y-5">
        <Field label="Project name" required>
          <input
            type="text"
            value={state.projectName}
            onChange={e => dispatch({ type: 'SET_NAME', value: e.target.value })}
            placeholder="e.g. Acme GmbH FDD 2024"
            className="rounded-md border border-slate-300 px-3 py-2 text-sm w-full max-w-md focus:outline-none focus:ring-2 focus:ring-blue-500"
            autoFocus
          />
        </Field>
        {!valid && (
          <WarnBox>Enter a project name to continue.</WarnBox>
        )}
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 1 — Fiscal year end month
// ---------------------------------------------------------------------------

function StepFiscalYear({
  state,
  dispatch,
}: {
  state: WizardState
  dispatch: Dispatch<WizardAction>
}) {
  const startMonth = fyStartFromEndMonth(state.fyEndMonth)

  return (
    <StepCard
      title="Fiscal year end month"
      subtitle="Select the last month of the fiscal year. The start month is derived automatically."
    >
      <div className="space-y-5">
        <Field
          label="Fiscal year end month"
          required
          hint="e.g. for a December fiscal year end select December; for a March year end select March."
        >
          <select
            value={state.fyEndMonth}
            onChange={e =>
              dispatch({ type: 'SET_FY_END_MONTH', value: Number(e.target.value) })
            }
            className="rounded-md border border-slate-300 px-3 py-2 text-sm w-52 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {MONTH_NAMES.map((name, i) => (
              <option key={i + 1} value={i + 1}>
                {name}
              </option>
            ))}
          </select>
        </Field>

        <div className="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3 text-sm text-slate-600 space-y-1">
          <p>
            <span className="font-medium text-slate-700">Fiscal year start: </span>
            {MONTH_NAMES[startMonth - 1]}
          </p>
          <p>
            <span className="font-medium text-slate-700">Fiscal year end: </span>
            {MONTH_NAMES[state.fyEndMonth - 1]}
          </p>
          <p className="text-xs text-slate-400 mt-1">
            Stored internally as <code className="bg-white border border-slate-200 rounded px-1">fy_start_month = {startMonth}</code>.
            This setting drives all period buckets and FY/YTD labels throughout the platform.
          </p>
        </div>

        <InfoBox>
          This setting is saved as part of your project configuration and applies to every data
          pipeline run. It can be changed later but will require a full rebuild to take effect.
        </InfoBox>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// GL sub-wizard helpers
// ---------------------------------------------------------------------------

/**
 * Default GoBD column mapping when source headers match a standard DATEV/Decidra export.
 * Mirrors the same table in IngestionPage so suggestions are consistent.
 */
const GOBD_GL_DEFAULTS: Record<string, string> = {
  journal_entry_number: 'Transaction number',
  account_number: 'Account number',
  posting_date: 'Posting date',
  document_date: 'Document date',
  document_type: 'Document type',
  reference_document_number: 'Document number',
  amount: 'Amount',
  vat_amount: 'VAT amount',
  line_note: 'Booking text',
  posting_type: 'Posting type',
  source_type: 'Source type',
  source_no: 'Source No.',
}

function suggestGlMapping(columns: string[]): Record<string, string> {
  const colSet = new Set(columns)
  const out: Record<string, string> = {}
  for (const [target, source] of Object.entries(GOBD_GL_DEFAULTS)) {
    if (colSet.has(source)) out[target] = source
  }
  return out
}

/** Default OptionsState for the GL sub-wizard. */
function defaultGlOpts(): OptionsState {
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

/** Default KontextState for the GL sub-wizard (entity only — FY derived from date). */
function defaultGlKontext(): KontextState {
  return {
    entityMode: 'fixed',
    entityValue: '',
    fiscalYearMode: 'fixed',
    fiscalYearValue: '',
  }
}

/**
 * Assemble a GL Profile from the sub-wizard pieces.
 * Replicates buildProfile() from IngestionPage exactly so the same backend
 * logic is exercised.
 */
function buildGlProfile(
  kontext: KontextState,
  mapping: Record<string, string>,
  opts: OptionsState,
  dialect: Dialect,
  entityAssignments: Record<string, string> = {},
): Profile {
  const signConfig = (() => {
    if (opts.signMode === 'signed') return { mode: 'signed' as const, amount: opts.signAmount }
    if (opts.signMode === 'soll_haben')
      return { mode: 'soll_haben' as const, soll: opts.signSoll, haben: opts.signHaben }
    return {
      mode: 'amount_dc' as const,
      amount: opts.signAmount,
      dc_flag: opts.signDcFlag,
      debit_value: opts.signDebitValue,
    }
  })()

  return {
    entity: { mode: kontext.entityMode, value: kontext.entityValue },
    fiscal_year: { mode: 'from_date' as const, value: '' },
    sign: signConfig,
    decimal: opts.decimal || dialect.decimal || ',',
    thousands: opts.thousands,
    date_dayfirst: opts.dateDayfirst,
    columns: mapping,
    linking_strategy: opts.linking,
    entry_type: 'actual',
    ...(Object.keys(entityAssignments).length > 0 ? { entity_assignments: entityAssignments } : {}),
  }
}

// ---------------------------------------------------------------------------
// GL sub-stepper labels
// ---------------------------------------------------------------------------

const GL_SUB_STEPS = [
  'Upload',
  'Entity',
  'Partner columns',
  'Column mapping',
  'Options',
  'Validation',
] as const

// ---------------------------------------------------------------------------
// Step 2 — GL bookings (Phase 2 — full inline flow)
// ---------------------------------------------------------------------------

function StepGlBookings({
  gl,
  dispatch,
}: {
  gl: WizardGlState
  dispatch: Dispatch<WizardAction>
}) {
  // Local sub-step index (0–5 matching GL_SUB_STEPS)
  const [subStep, setSubStep] = useState<number>(() => {
    // Restore to the furthest reached sub-step on re-entry
    if (gl.validationOk !== undefined) return 5
    if (gl.opts) return 5      // jump to validation if options done
    if (gl.mapping) return 4
    if (gl.partnerColumnsMode) return 3
    if (gl.kontext?.entityValue) return 2
    if (gl.fileId) return 1
    return 0
  })

  // Local upload result (not persisted globally — we only persist the extracted fields)
  const [uploadResult, setUploadResult] = useState<UploadResponse | null>(() => {
    if (gl.fileId && gl.columns && gl.sample && gl.dialect) {
      return {
        file_id: gl.fileId,
        filename: '',
        sheets: gl.sheet ? [gl.sheet] : [],
        columns: gl.columns,
        sample: gl.sample,
        dialect: gl.dialect,
      }
    }
    return null
  })

  // Local opts (initialised from persisted state or defaults)
  const [opts, setOpts] = useState<OptionsState>(() => gl.opts ?? defaultGlOpts())

  // Local kontext (initialised from persisted state or defaults)
  const [kontext, setKontext] = useState<KontextState>(() => gl.kontext ?? defaultGlKontext())

  // Partner columns mode
  const [partnerMode, setPartnerMode] = useState<PartnerColumnsMode>(
    () => gl.partnerColumnsMode ?? 'single',
  )
  const [partnerSplit, setPartnerSplit] = useState<PartnerColumnsSplit>(
    () =>
      gl.partnerColumnsSplit ?? {
        creditorNoCol: '',
        debtorNoCol: '',
        fixedAssetNoCol: '',
      },
  )

  // Column mapping
  const [mapping, setMapping] = useState<Record<string, string>>(() => gl.mapping ?? {})

  // Validation result (staging — no DB write)
  const [validationResult, setValidationResult] = useState<ValidationResponse | null>(null)
  const [entityAssignments, setEntityAssignments] = useState<Record<string, string>>(
    () => gl.entityAssignments ?? {},
  )

  // Sync opts decimal/thousands when dialect is detected
  useEffect(() => {
    if (uploadResult?.dialect) {
      setOpts(prev => ({
        ...prev,
        decimal: uploadResult.dialect.decimal || ',',
        thousands: uploadResult.dialect.thousands || '.',
      }))
    }
  }, [uploadResult?.dialect])

  // Sync local opts/kontext back into wizard state when they change (so Back→Next restores)
  useEffect(() => {
    dispatch({ type: 'PATCH_GL', patch: { opts } })
  }, [opts, dispatch])

  useEffect(() => {
    dispatch({ type: 'PATCH_GL', patch: { kontext } })
  }, [kontext, dispatch])

  // -------------------------------------------------------------------------
  // Upload handler
  // -------------------------------------------------------------------------

  function handleUploaded(result: UploadResponse) {
    setUploadResult(result)
    const suggested = suggestGlMapping(result.columns)
    const newMapping = Object.keys(suggested).length > 0 ? suggested : {}
    setMapping(newMapping)

    // Auto-detect entity column
    const cols = result.columns
    const newKontext: KontextState = { ...kontext }
    if (cols.includes('Entity No')) {
      newKontext.entityMode = 'column'
      newKontext.entityValue = 'Entity No'
    } else if (cols.includes('Entity')) {
      newKontext.entityMode = 'column'
      newKontext.entityValue = 'Entity'
    }
    setKontext(newKontext)

    // Auto-detect amount column
    if (cols.includes('Amount')) {
      setOpts(prev => ({ ...prev, signMode: 'signed', signAmount: 'Amount' }))
    }

    dispatch({
      type: 'PATCH_GL',
      patch: {
        fileId: result.file_id,
        sheet: result.sheets[0] ?? undefined,
        columns: result.columns,
        sample: result.sample,
        dialect: result.dialect,
        mapping: newMapping,
        kontext: newKontext,
        // Reset downstream state on new upload
        validationOk: undefined,
        assembledProfile: undefined,
        entityAssignments: undefined,
      },
    })
    setSubStep(1)
  }

  // -------------------------------------------------------------------------
  // Validation result handler (staging — no commit)
  // -------------------------------------------------------------------------

  function handleValidationResult(r: ValidationResponse) {
    setValidationResult(r)
    const ok = r.summary.passed
    if (uploadResult) {
      const profile = buildGlProfile(kontext, mapping, opts, uploadResult.dialect, entityAssignments)
      dispatch({
        type: 'PATCH_GL',
        patch: {
          validationOk: ok,
          entityAssignments,
          assembledProfile: profile,
        },
      })
    }
  }

  function handleEntityAssignmentsChange(ea: Record<string, string>) {
    setEntityAssignments(ea)
  }

  // onImportSuccess is never called because stagingMode=true hides the commit button.
  // Typed as required by ValidierungStep; safe no-op here.
  const noopImportSuccess = useCallback((_r: CommitResponse) => {
    // staging mode — commit happens in Phase 5 Finish, not here
  }, [])

  // -------------------------------------------------------------------------
  // Sub-step navigation helpers
  // -------------------------------------------------------------------------

  function goSubNext() {
    setSubStep(s => Math.min(GL_SUB_STEPS.length - 1, s + 1))
  }
  function goSubBack() {
    setSubStep(s => Math.max(0, s - 1))
  }

  const columns = uploadResult?.columns ?? gl.columns ?? []
  const sample = uploadResult?.sample ?? gl.sample ?? []
  const dialect = uploadResult?.dialect ?? gl.dialect ?? { decimal: ',', thousands: '.', delimiter: ';', encoding: 'utf-8' }

  const missing = missingRequiredFields(mapping)
  const kontextValid = kontext.entityValue.trim() !== ''

  // Assembled profile (live — used by ValidierungStep)
  const glProfile = uploadResult
    ? buildGlProfile(kontext, mapping, opts, uploadResult.dialect, entityAssignments)
    : null

  // -------------------------------------------------------------------------
  // Sub-step content
  // -------------------------------------------------------------------------

  return (
    <div className="space-y-4">
      {/* GL sub-stepper */}
      <div className="rounded-xl border border-slate-200 bg-white px-6 pt-5 pb-3 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Upload GL bookings</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          Upload your GDPdU general ledger export and map it to the Finssentials schema.
          Nothing is written to the database until the final Finish step.
        </p>

        {/* Inline progress dots */}
        <div className="mt-4 flex items-center gap-1.5 flex-wrap">
          {GL_SUB_STEPS.map((label, idx) => {
            const done = idx < subStep
            const active = idx === subStep
            const reachable = (
              idx === 0 ||
              (idx === 1 && Boolean(gl.fileId)) ||
              (idx === 2 && kontextValid && Boolean(gl.fileId)) ||
              (idx === 3 && Boolean(gl.partnerColumnsMode) && kontextValid && Boolean(gl.fileId)) ||
              (idx === 4 && missing.length === 0 && kontextValid && Boolean(gl.fileId)) ||
              (idx === 5 && Boolean(gl.fileId) && kontextValid && missing.length === 0)
            )
            return (
              <button
                key={label}
                type="button"
                disabled={!reachable && !done && !active}
                onClick={() => { if (done || active || reachable) setSubStep(idx) }}
                className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition ${
                  active
                    ? 'bg-blue-600 text-white'
                    : done
                    ? 'bg-blue-100 text-blue-700 hover:bg-blue-200'
                    : reachable
                    ? 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    : 'bg-slate-50 text-slate-300 cursor-not-allowed'
                }`}
              >
                {done ? <span aria-hidden>&#10003;</span> : <span>{idx + 1}</span>}
                {label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Sub-step 0: Upload */}
      {subStep === 0 && (
        <UploadStep onUploaded={handleUploaded} />
      )}

      {/* Sub-step 1: Entity */}
      {subStep === 1 && columns.length > 0 && (
        <>
          <KontextStep
            sourceColumns={columns}
            state={kontext}
            onChange={k => {
              setKontext(k)
              dispatch({ type: 'PATCH_GL', patch: { kontext: k } })
            }}
            variant="gl"
          />
          <div className="flex justify-between mt-4">
            <button type="button" onClick={goSubBack}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Back
            </button>
            <button type="button" onClick={goSubNext} disabled={!kontextValid}
              className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40">
              Next
            </button>
          </div>
        </>
      )}

      {/* Sub-step 2: Partner columns question */}
      {subStep === 2 && (
        <>
          <StepCard
            title="Partner column layout"
            subtitle="Tell us how creditor, debtor, and fixed-asset numbers are stored in your GL file."
          >
            <div className="space-y-5">
              <div className="space-y-3">
                <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
                  <input
                    type="radio"
                    name="partnerMode"
                    value="single"
                    checked={partnerMode === 'single'}
                    onChange={() => setPartnerMode('single')}
                    className="mt-0.5 accent-blue-600"
                  />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">
                      One combined column (recommended)
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      A single "Source No." column holds the partner number, and a companion
                      "Source Type" column contains a code that distinguishes creditor, debtor,
                      or fixed asset. This is the standard DATEV / GoBD layout and is fully
                      supported by the backend.
                    </p>
                    <p className="text-xs text-slate-400 mt-1 italic">
                      Example: Source No. = "01100", Source Type = "K" (Kreditor)
                    </p>
                  </div>
                </label>

                <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
                  <input
                    type="radio"
                    name="partnerMode"
                    value="split"
                    checked={partnerMode === 'split'}
                    onChange={() => setPartnerMode('split')}
                    className="mt-0.5 accent-blue-600"
                  />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">
                      Three separate columns
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Creditor number, debtor number, and fixed-asset number each have their own
                      column. Map them in the next step. The mapping is stored in the profile;
                      full backend processing of this layout is a planned follow-up (Phase 4).
                    </p>
                    <p className="text-xs text-slate-400 mt-1 italic">
                      Example: "Kred. No." = "01100", "Deb. No." = "" (empty on this line)
                    </p>
                  </div>
                </label>
              </div>

              {partnerMode === 'split' && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 space-y-3">
                  <p className="text-xs font-semibold text-amber-800">
                    Map the three partner columns
                  </p>
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
                        onChange={e =>
                          setPartnerSplit(prev => ({ ...prev, [field]: e.target.value }))
                        }
                        className="rounded-md border border-slate-300 px-2 py-1.5 text-sm flex-1 focus:outline-none focus:ring-2 focus:ring-blue-500"
                      >
                        <option value="">-- Select column --</option>
                        {columns.map(c => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                  ))}
                  <p className="text-xs text-amber-700">
                    Note: backend consumption of the three-separate-column layout is a
                    documented follow-up (Phase 4 / backend-engineer). The profile is stored
                    correctly; the single-column path is the active processing default.
                  </p>
                </div>
              )}

              <InfoBox>
                If you are unsure, choose "One combined column" — it covers the vast majority
                of DATEV, Lexware, and GoBD-compliant exports.
              </InfoBox>
            </div>
          </StepCard>

          <div className="flex justify-between mt-4">
            <button type="button" onClick={goSubBack}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Back
            </button>
            <button
              type="button"
              onClick={() => {
                dispatch({
                  type: 'PATCH_GL',
                  patch: {
                    partnerColumnsMode: partnerMode,
                    partnerColumnsSplit: partnerMode === 'split' ? partnerSplit : undefined,
                  },
                })
                goSubNext()
              }}
              className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700"
            >
              Next
            </button>
          </div>
        </>
      )}

      {/* Sub-step 3: Column mapping */}
      {subStep === 3 && columns.length > 0 && (
        <>
          <StepCard
            title="Column mapping"
            subtitle="Drag source columns onto target fields. Required fields (*) must be mapped before you can continue."
          >
            {missing.length > 0 && (
              <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                <span className="font-medium">Missing required fields:</span>{' '}
                {missing.join(', ')}
              </div>
            )}

            {/* Partner-column hint */}
            {partnerMode === 'single' && (
              <div className="mb-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                <span className="font-semibold">Partner columns (single-column layout):</span>{' '}
                Map "Source Type" and "Source No." in the Partner group below to enable
                creditor / debtor assignment during validation.
              </div>
            )}
            {partnerMode === 'split' && (
              <div className="mb-4 rounded-md border border-amber-100 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <span className="font-semibold">Three-column layout selected.</span>{' '}
                The separate creditor / debtor / fixed-asset columns you chose are stored in
                the profile. Map any remaining required fields below.
              </div>
            )}

            <ColumnMapper
              sourceColumns={columns}
              sample={sample}
              mapping={mapping}
              onChange={m => {
                setMapping(m)
                dispatch({ type: 'PATCH_GL', patch: { mapping: m } })
              }}
            />
          </StepCard>

          <div className="flex justify-between mt-4">
            <button type="button" onClick={goSubBack}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Back
            </button>
            <button
              type="button"
              onClick={goSubNext}
              disabled={missing.length > 0}
              className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {missing.length > 0 ? `Next (${missing.length} required fields missing)` : 'Next'}
            </button>
          </div>
        </>
      )}

      {/* Sub-step 4: Options (amount logic, separators, date, AR/AP linking) */}
      {subStep === 4 && columns.length > 0 && (
        <>
          {/* AR/AP linking explanation — shown ABOVE the OptionsStep */}
          <div className="rounded-xl border border-blue-200 bg-blue-50/70 px-5 py-4 shadow-sm">
            <h3 className="text-sm font-semibold text-blue-900 mb-2">
              AR/AP linking — how should a customer or supplier be attached to the matching
              revenue or cost line?
            </h3>
            <p className="text-xs text-blue-800 mb-3">
              In a double-entry GL, an invoice booking typically has two lines: one on a
              receivable / payable account (which carries the partner number) and one on the
              revenue / cost account (which does not). The linking strategy controls whether and
              how the partner is copied onto the revenue / cost line so that drill-downs by
              customer or supplier work correctly.
            </p>
            <div className="space-y-3 text-xs text-blue-900">
              <div className="rounded-md border border-blue-200 bg-white px-3 py-2.5">
                <p className="font-semibold mb-0.5">
                  A — By transaction (GoBD)
                </p>
                <p className="text-blue-700">
                  Within one journal entry all lines share a transaction number. The partner
                  from the receivable / payable line is propagated to every other line in the
                  same entry.
                </p>
                <p className="mt-1.5 text-blue-600 italic">
                  Example: entry #1001 — line 1: Receivables 1,000 (customer 01100); line 2:
                  Revenue 1,000 (no partner). After linking: Revenue line also gets customer
                  01100. If more than one unique partner exists in the entry the lines remain
                  unlinked (ambiguous).
                </p>
              </div>
              <div className="rounded-md border border-blue-200 bg-white px-3 py-2.5">
                <p className="font-semibold mb-0.5">
                  B — By counter-account (DATEV / Gegenkonto)
                </p>
                <p className="text-blue-700">
                  The counter-account is stored in the same row. If the counter-account is a
                  receivable / payable account, the partner associated with that account is
                  assigned to this line. Already-set partner IDs are not overwritten.
                </p>
                <p className="mt-1.5 text-blue-600 italic">
                  Example: Revenue line, counter-account = 10000 (Receivables) → system looks
                  up the partner on account 10000 and attaches it to the Revenue line.
                </p>
              </div>
              <div className="rounded-md border border-blue-200 bg-white px-3 py-2.5">
                <p className="font-semibold mb-0.5">No automatic attachment</p>
                <p className="text-blue-700">
                  Partner numbers are used only where they already appear in the source file.
                  Choose this if your GL already contains partner IDs on every relevant line,
                  or if AR/AP drill-downs are not needed.
                </p>
              </div>
            </div>
            <p className="mt-3 text-xs text-blue-700">
              Select your preferred strategy in the options panel below. You can change it
              later and re-run validation without re-uploading.
            </p>
          </div>

          <OptionsStep
            sourceColumns={columns}
            dialect={dialect}
            state={opts}
            onChange={o => {
              setOpts(o)
              dispatch({ type: 'PATCH_GL', patch: { opts: o } })
            }}
            onSaveProfile={() => { /* profile save is optional; wired in full Data Update flow */ }}
            savingProfile={false}
            profileSaved={false}
          />

          <div className="flex justify-between mt-4">
            <button type="button" onClick={goSubBack}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Back
            </button>
            <button type="button" onClick={goSubNext}
              className="rounded-md bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700">
              Run validation
            </button>
          </div>
        </>
      )}

      {/* Sub-step 5: Validation (staging — no commit) */}
      {subStep === 5 && uploadResult && glProfile && (
        <>
          <ValidierungStep
            uploadResult={uploadResult}
            profile={glProfile}
            onResult={handleValidationResult}
            onEntityAssignmentsChange={handleEntityAssignmentsChange}
            onImportSuccess={noopImportSuccess}
            stagingMode
          />

          {/* Wizard-level status after validation */}
          {validationResult && (
            <div className={`rounded-lg border px-4 py-3 text-sm ${
              validationResult.summary.passed
                ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
                : 'border-amber-200 bg-amber-50 text-amber-800'
            }`}>
              {validationResult.summary.passed
                ? 'Validation passed. Click "Next" on the outer wizard to continue to the Chart of Accounts step.'
                : 'Validation has warnings or errors. You can still continue — the GL will be committed in the Finish step once all issues are resolved, or you can accept soft warnings.'}
            </div>
          )}

          <div className="flex justify-between mt-4">
            <button type="button" onClick={goSubBack}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              Back to options
            </button>
            {gl.validationOk !== undefined && (
              <div className={`flex items-center gap-2 text-sm font-medium ${
                gl.validationOk ? 'text-emerald-700' : 'text-amber-700'
              }`}>
                {gl.validationOk ? '&#10003; Validation passed' : 'Warnings present'}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 3 — Chart of accounts (Phase 3 — COMPLETE)
// ---------------------------------------------------------------------------

/** Library variant options shown in the sub-select. */
const LIBRARY_VARIANTS: Array<{ value: string; label: string; description: string }> = [
  {
    value: 'statutory',
    label: 'Statutory chart of accounts',
    description:
      'The German statutory Gemeinschaftskontenrahmen (GKR) — broad industry coverage. ' +
      'Accounts are auto-classified using the Finssentials standard mapping library.',
  },
  {
    value: 'skr03',
    label: 'SKR03',
    description:
      'DATEV SKR03 (process-oriented). Standard for the majority of German SMEs. ' +
      'High auto-classification rate out of the box.',
  },
  {
    value: 'past_projects',
    label: 'Mapping library from past projects',
    description:
      'Reuses account mappings from previously onboarded projects in this Finssentials ' +
      'instance. Best when client accounts follow a known custom numbering scheme.',
  },
]

function StepChartOfAccounts({
  coa,
  dispatch,
  fyEndMonth,
  entities,
}: {
  coa: WizardCoaState
  dispatch: Dispatch<WizardAction>
  fyEndMonth: number
  entities: WizardState['entities']
}) {
  // Local source choice (controlled by radio)
  const [source, setSource] = useState<'library' | 'upload'>(coa.source ?? 'library')
  const [libraryVariant, setLibraryVariant] = useState<string>(
    coa.libraryVariant ?? 'skr03',
  )

  // Upload path state
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Mapping state for generic client CoA (non-master-shaped uploads)
  const [mapping, setMapping] = useState<Record<string, string>>(coa.mapping ?? {})

  // Sync source choice into wizard state
  function handleSourceChange(val: 'library' | 'upload') {
    setSource(val)
    dispatch({ type: 'PATCH_COA', patch: { source: val } })
  }

  // Sync library variant into wizard state
  function handleVariantChange(val: string) {
    setLibraryVariant(val)
    dispatch({ type: 'PATCH_COA', patch: { libraryVariant: val } })
  }

  // Template download
  async function handleDownloadTemplate() {
    setDownloading(true)
    setDownloadError(null)
    try {
      const firstEntity = entities.find(e => e.code.trim() !== '')
      await downloadCoaTemplate({
        entity: firstEntity?.code ?? undefined,
        fiscalYear: fyEndMonth,   // pass end-month as a proxy; backend interprets
        library: libraryVariant,
      })
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'Download failed')
    } finally {
      setDownloading(false)
    }
  }

  // File upload handler
  async function handleFileSelected(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const res = await uploadFile(file)
      const isMaster = isBsPlMasterSheets(res.sheets)
      dispatch({
        type: 'PATCH_COA',
        patch: {
          source: 'upload',
          masterFileId: res.file_id,
          masterSheets: res.sheets,
          masterColumns: res.columns,
          masterSample: res.sample,
          isBsPlMaster: isMaster,
          // reset mapping on new upload
          mapping: undefined,
        },
      })
      setMapping({})
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  function handleMappingChange(m: Record<string, string>) {
    setMapping(m)
    dispatch({ type: 'PATCH_COA', patch: { mapping: m } })
  }

  const missingAccountFields = coa.masterColumns
    ? missingRequiredAccountFields(mapping)
    : []

  const uploadStagedOk =
    Boolean(coa.masterFileId) &&
    (coa.isBsPlMaster || missingAccountFields.length === 0)

  return (
    <StepCard
      title="Chart of accounts"
      subtitle="Choose how accounts are classified into the P&L and Balance Sheet hierarchy."
    >
      <div className="space-y-6">

        {/* ------------------------------------------------------------------ */}
        {/* Source choice — radio group                                         */}
        {/* ------------------------------------------------------------------ */}
        <div className="space-y-3">
          <p className="text-sm font-medium text-slate-700">
            Account classification source
          </p>

          {/* Option A: Finssentials library */}
          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
            <input
              type="radio"
              name="coaSource"
              value="library"
              checked={source === 'library'}
              onChange={() => handleSourceChange('library')}
              className="mt-0.5 accent-blue-600"
            />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-slate-800">
                Finssentials library
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                Accounts are auto-classified using a built-in mapping library.
                Unmatched accounts can be fine-tuned in the CoA Editor after the
                initial commit.
              </p>
            </div>
          </label>

          {/* Library variant sub-select */}
          {source === 'library' && (
            <div className="ml-7 space-y-3">
              {LIBRARY_VARIANTS.map(v => (
                <label
                  key={v.value}
                  className={`flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
                    libraryVariant === v.value
                      ? 'border-blue-400 bg-blue-50'
                      : 'border-slate-200 bg-white hover:bg-slate-50'
                  }`}
                >
                  <input
                    type="radio"
                    name="libraryVariant"
                    value={v.value}
                    checked={libraryVariant === v.value}
                    onChange={() => handleVariantChange(v.value)}
                    className="mt-0.5 accent-blue-600"
                  />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">{v.label}</p>
                    <p className="text-xs text-slate-500 mt-0.5">{v.description}</p>
                  </div>
                </label>
              ))}

              <InfoBox>
                <strong>How library classification works:</strong> after the Finish
                step commits the GL data and CoA mapping, the platform runs
                account auto-classification against the chosen library. Any account
                that could not be matched automatically will appear in the CoA
                Editor under{' '}
                <span className="font-mono text-xs bg-white border border-blue-200 rounded px-1">
                  /mapping-editor
                </span>{' '}
                where you can assign it to the correct P&amp;L or Balance Sheet
                hierarchy node. The Editor is only available after the initial
                commit because it works directly on the classified account dimension
                table.
              </InfoBox>
            </div>
          )}

          {/* Option B: Upload */}
          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
            <input
              type="radio"
              name="coaSource"
              value="upload"
              checked={source === 'upload'}
              onChange={() => handleSourceChange('upload')}
              className="mt-0.5 accent-blue-600"
            />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-slate-800">
                Upload client chart of accounts
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                Download the Master template (Master_BS + Master_PL sheets, L1–L4
                hierarchy), fill in your account numbers and hierarchy assignments,
                and re-upload. Or upload an existing client CoA and map columns
                manually.
              </p>
            </div>
          </label>
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* Upload path                                                          */}
        {/* ------------------------------------------------------------------ */}
        {source === 'upload' && (
          <div className="space-y-5">

            {/* Download Master template button */}
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 space-y-3">
              <p className="text-sm font-semibold text-slate-700">
                Step 1 — Download the Master template
              </p>
              <p className="text-xs text-slate-500">
                The template contains two sheets: <strong>Master_BS</strong> (Balance
                Sheet) and <strong>Master_PL</strong> (P&amp;L). Each row maps an
                account number to a hierarchy position (L1 through L4). Fill in your
                client&apos;s account numbers and hierarchy labels, then upload the
                completed file below.
              </p>
              <button
                type="button"
                onClick={handleDownloadTemplate}
                disabled={downloading}
                className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition"
              >
                {downloading ? (
                  <>
                    <svg className="animate-spin h-4 w-4 text-slate-500" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    Downloading…
                  </>
                ) : (
                  <>
                    <svg className="h-4 w-4 text-slate-500" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3" />
                    </svg>
                    Download Master template (.xlsx)
                  </>
                )}
              </button>
              {downloadError && (
                <p className="text-xs text-red-600">{downloadError}</p>
              )}
            </div>

            {/* Upload area */}
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 space-y-3">
              <p className="text-sm font-semibold text-slate-700">
                Step 2 — Upload the completed CoA file
              </p>
              <p className="text-xs text-slate-500">
                If you upload the filled-in Master template (Master_BS + Master_PL
                sheets), no column mapping is needed — it commits directly as{' '}
                <span className="font-mono text-xs bg-white border border-slate-200 rounded px-1">
                  format=bs_pl_master
                </span>
                . If you upload a generic client CoA file, you will map its columns
                to the required target fields below.
              </p>

              {/* File drop / click area */}
              <div
                onClick={() => fileInputRef.current?.click()}
                onDragOver={e => e.preventDefault()}
                onDrop={e => {
                  e.preventDefault()
                  const f = e.dataTransfer.files[0]
                  if (f) handleFileSelected(f)
                }}
                className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-10 cursor-pointer transition ${
                  uploading
                    ? 'border-blue-400 bg-blue-50'
                    : coa.masterFileId
                    ? 'border-emerald-300 bg-emerald-50'
                    : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".xlsx,.xls,.csv"
                  className="hidden"
                  onChange={e => {
                    const f = e.target.files?.[0]
                    if (f) handleFileSelected(f)
                  }}
                />
                {uploading ? (
                  <p className="text-sm font-medium text-blue-600">Processing file…</p>
                ) : coa.masterFileId ? (
                  <div className="text-center">
                    <p className="text-sm font-semibold text-emerald-700">
                      File staged
                    </p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      File ID: <span className="font-mono">{coa.masterFileId}</span>
                    </p>
                    {coa.masterSheets && coa.masterSheets.length > 0 && (
                      <p className="text-xs text-slate-400 mt-0.5">
                        Sheets: {coa.masterSheets.join(', ')}
                      </p>
                    )}
                    {coa.isBsPlMaster && (
                      <p className="text-xs font-medium text-emerald-600 mt-1">
                        Master_BS + Master_PL detected — no column mapping needed
                      </p>
                    )}
                    <p className="text-xs text-slate-400 mt-1.5 italic">
                      Click or drop to replace
                    </p>
                  </div>
                ) : (
                  <>
                    <div className="text-3xl text-slate-300 mb-2">&#128196;</div>
                    <p className="text-sm font-semibold text-slate-700">
                      Drop a file here or click to browse
                    </p>
                    <p className="mt-1 text-xs text-slate-400">XLSX · XLS · CSV</p>
                  </>
                )}
              </div>

              {uploadError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {uploadError}
                </div>
              )}
            </div>

            {/* Column mapper — only shown for generic (non-master-shaped) uploads */}
            {coa.masterFileId && !coa.isBsPlMaster && coa.masterColumns && coa.masterSample && (
              <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-3">
                <p className="text-sm font-semibold text-slate-700">
                  Step 3 — Map source columns to account target fields
                </p>
                <p className="text-xs text-slate-500">
                  This file does not have the standard Master_BS / Master_PL sheet
                  layout. Drag source columns onto the required target fields so the
                  backend can classify accounts correctly.
                </p>
                {missingAccountFields.length > 0 && (
                  <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                    <span className="font-medium">Missing required fields:</span>{' '}
                    {missingAccountFields.join(', ')}
                  </div>
                )}
                <AccountColumnMapper
                  sourceColumns={coa.masterColumns}
                  sample={coa.masterSample}
                  mapping={mapping}
                  onChange={handleMappingChange}
                />
              </div>
            )}

            {/* Staged status summary */}
            {uploadStagedOk && (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <strong>CoA file staged.</strong>{' '}
                {coa.isBsPlMaster
                  ? 'The Master template will be committed as bs_pl_master format at the Finish step.'
                  : 'Column mapping complete. The file will be committed with the mapped column profile at the Finish step.'}
              </div>
            )}

            {coa.masterFileId && !uploadStagedOk && !coa.isBsPlMaster && (
              <WarnBox>
                Map all required account fields above before continuing. You can
                also proceed and return here, or continue past this step with a
                note — the CoA can be uploaded separately after project creation.
              </WarnBox>
            )}
          </div>
        )}

        {/* ------------------------------------------------------------------ */}
        {/* CoA Editor hint (collect-then-commit — editor available post-Finish) */}
        {/* ------------------------------------------------------------------ */}
        <div className="rounded-lg border border-blue-200 bg-blue-50/70 px-4 py-4 space-y-2">
          <p className="text-sm font-semibold text-blue-900">
            CoA Editor — available after the initial commit
          </p>
          <p className="text-xs text-blue-800">
            The Chart of Accounts Editor (
            <span className="font-mono text-xs bg-white border border-blue-200 rounded px-1">
              /mapping-editor
            </span>
            ) lets you remap individual accounts, adjust hierarchy nodes (L2 / L3
            / L4), and re-run classification at any time.
          </p>
          <p className="text-xs text-blue-700">
            Because this wizard uses a <strong>collect-then-commit</strong> model,
            the classified account dimension table (
            <span className="font-mono text-xs bg-white border border-blue-200 rounded px-1">
              dim_gl_account
            </span>
            ) does not exist until the Finish step commits the GL data and CoA
            mapping. The Editor link will become active in the navigation after
            you complete the wizard.
          </p>
        </div>

      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 4 — Opening balances (Phase 4 — COMPLETE)
// ---------------------------------------------------------------------------

function StepOpeningBalances({
  ob,
  dispatch,
}: {
  ob: WizardObState
  dispatch: Dispatch<WizardAction>
}) {
  const [mode, setMode] = useState<WizardObState['mode']>(ob.mode ?? 'in_data')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Local profile for account/amount column mapping
  const [obProfile, setObProfile] = useState<WizardObState['obProfile']>(
    ob.obProfile ?? {},
  )

  function handleModeChange(val: WizardObState['mode']) {
    setMode(val)
    dispatch({ type: 'PATCH_OB', patch: { mode: val } })
  }

  async function handleFileSelected(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const res = await uploadOpeningBalance(file)
      const cols = res.columns
      // Auto-suggest account and amount columns
      const accountGuess = cols.find(c =>
        /account|konto/i.test(c),
      ) ?? ''
      const amountGuess = cols.find(c =>
        /amount|betrag/i.test(c),
      ) ?? ''
      const suggested: WizardObState['obProfile'] = {
        account_col: accountGuess,
        amount_col: amountGuess,
      }
      setObProfile(suggested)
      dispatch({
        type: 'PATCH_OB',
        patch: {
          obFileId: res.file_id,
          obColumns: res.columns,
          obSample: res.sample,
          obProfile: suggested,
        },
      })
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  function handleProfileChange(field: keyof NonNullable<WizardObState['obProfile']>, value: string) {
    const updated = { ...obProfile, [field]: value }
    setObProfile(updated)
    dispatch({ type: 'PATCH_OB', patch: { obProfile: updated } })
  }

  const columns = ob.obColumns ?? []
  const needsFile = mode === 'file_first_year' || mode === 'file_all'

  return (
    <StepCard
      title="Opening balances"
      subtitle="Tell the pipeline how balance sheet opening balances are supplied in your data."
    >
      <div className="space-y-6">

        {/* ------------------------------------------------------------------ */}
        {/* Option radio group                                                   */}
        {/* ------------------------------------------------------------------ */}
        <div className="space-y-3">
          <p className="text-sm font-medium text-slate-700">How are opening balances provided?</p>

          {/* Option A — already in GL data */}
          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
            <input
              type="radio"
              name="obMode"
              value="in_data"
              checked={mode === 'in_data'}
              onChange={() => handleModeChange('in_data')}
              className="mt-0.5 accent-blue-600"
            />
            <div>
              <p className="text-sm font-semibold text-slate-800">
                Already included in GL data
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                Opening balance rows are embedded in your GL export, typically tagged with entry
                type "EB" (Eröffnungsbilanz) or fiscal period 0. No extra file needed — the
                pipeline will detect and tag them automatically using the existing GL column
                mapping.
              </p>
              <p className="text-xs text-slate-400 mt-1 italic">
                Most DATEV and GoBD-compliant exports include opening balances in the main file.
              </p>
            </div>
          </label>

          {/* Option B — first-year separate file */}
          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
            <input
              type="radio"
              name="obMode"
              value="file_first_year"
              checked={mode === 'file_first_year'}
              onChange={() => handleModeChange('file_first_year')}
              className="mt-0.5 accent-blue-600"
            />
            <div>
              <p className="text-sm font-semibold text-slate-800">
                First-year opening balances as a separate file
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                Upload a file containing opening balances for the <strong>first fiscal year</strong>{' '}
                only. These rows will be ingested with{' '}
                <span className="font-mono text-xs bg-slate-100 rounded px-1">entry_type=opening_balance</span>{' '}
                and <span className="font-mono text-xs bg-slate-100 rounded px-1">fiscal_period=0</span>.
                Subsequent years carry forward from the closing balance of the prior year.
              </p>
              <p className="text-xs text-slate-400 mt-1 italic">
                Use this when migrating from a prior system that exported only the migration
                year's opening balances separately.
              </p>
            </div>
          </label>

          {/* Option C — all years separate file */}
          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white p-4 hover:bg-slate-50 transition">
            <input
              type="radio"
              name="obMode"
              value="file_all"
              checked={mode === 'file_all'}
              onChange={() => handleModeChange('file_all')}
              className="mt-0.5 accent-blue-600"
            />
            <div>
              <p className="text-sm font-semibold text-slate-800">
                All opening balances as a separate file
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                Upload a file containing opening balances for <strong>all fiscal years</strong>.
                Each row is stored with{' '}
                <span className="font-mono text-xs bg-slate-100 rounded px-1">entry_type=opening_balance</span>{' '}
                and <span className="font-mono text-xs bg-slate-100 rounded px-1">fiscal_period=0</span>.
                The pipeline uses these as authoritative opening balances across all years, rather
                than deriving them from prior-year closing balances.
              </p>
              <p className="text-xs text-slate-400 mt-1 italic">
                Use this when your source system exports a separate OB file per year, or when
                carry-forward logic is not reliable in the source data.
              </p>
            </div>
          </label>
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* File upload — shown for first_year and all modes                    */}
        {/* ------------------------------------------------------------------ */}
        {needsFile && (
          <div className="space-y-4">
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 space-y-3">
              <p className="text-sm font-semibold text-slate-700">
                Upload the opening balance file
              </p>
              <p className="text-xs text-slate-500">
                The file should contain at minimum an account number column and an amount column.
                XLSX, XLS, and CSV formats are accepted. After upload you can map the account
                and amount columns below.
              </p>

              <div
                onClick={() => fileInputRef.current?.click()}
                onDragOver={e => e.preventDefault()}
                onDrop={e => {
                  e.preventDefault()
                  const f = e.dataTransfer.files[0]
                  if (f) void handleFileSelected(f)
                }}
                className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-10 cursor-pointer transition ${
                  uploading
                    ? 'border-blue-400 bg-blue-50'
                    : ob.obFileId
                    ? 'border-emerald-300 bg-emerald-50'
                    : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".xlsx,.xls,.csv"
                  className="hidden"
                  onChange={e => {
                    const f = e.target.files?.[0]
                    if (f) void handleFileSelected(f)
                  }}
                />
                {uploading ? (
                  <p className="text-sm font-medium text-blue-600">Processing file…</p>
                ) : ob.obFileId ? (
                  <div className="text-center">
                    <p className="text-sm font-semibold text-emerald-700">File staged</p>
                    <p className="text-xs text-slate-500 mt-0.5">
                      File ID: <span className="font-mono">{ob.obFileId}</span>
                    </p>
                    {ob.obColumns && ob.obColumns.length > 0 && (
                      <p className="text-xs text-slate-400 mt-0.5">
                        {ob.obColumns.length} columns detected
                      </p>
                    )}
                    <p className="text-xs text-slate-400 mt-1.5 italic">Click or drop to replace</p>
                  </div>
                ) : (
                  <>
                    <div className="text-3xl text-slate-300 mb-2">&#128196;</div>
                    <p className="text-sm font-semibold text-slate-700">Drop a file here or click to browse</p>
                    <p className="mt-1 text-xs text-slate-400">XLSX · XLS · CSV</p>
                  </>
                )}
              </div>

              {uploadError && (
                <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {uploadError}
                </div>
              )}
            </div>

            {/* Column mapping for account + amount */}
            {ob.obFileId && columns.length > 0 && (
              <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-3">
                <p className="text-sm font-semibold text-slate-700">Map account and amount columns</p>
                <p className="text-xs text-slate-500">
                  Select which column holds the account number and which holds the opening balance
                  amount. These two fields are the minimum required for ingestion.
                </p>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {(
                    [
                      ['account_col', 'Account number column', true],
                      ['amount_col', 'Amount column', true],
                    ] as [keyof NonNullable<WizardObState['obProfile']>, string, boolean][]
                  ).map(([field, label, required]) => (
                    <div key={field} className="space-y-1">
                      <label className="text-xs font-medium text-slate-700">
                        {label}
                        {required && <span className="ml-0.5 text-red-500" aria-hidden>*</span>}
                      </label>
                      <select
                        value={obProfile?.[field] ?? ''}
                        onChange={e => handleProfileChange(field, e.target.value)}
                        className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      >
                        <option value="">-- Select column --</option>
                        {columns.map(c => (
                          <option key={c} value={c}>{c}</option>
                        ))}
                      </select>
                    </div>
                  ))}
                </div>

                {/* Sample preview */}
                {ob.obSample && ob.obSample.length > 0 && (
                  <div className="mt-2 overflow-x-auto rounded-md border border-slate-100">
                    <table className="min-w-full text-xs">
                      <thead className="bg-slate-50">
                        <tr>
                          {columns.slice(0, 6).map(c => (
                            <th key={c} className="px-2 py-1.5 text-left font-medium text-slate-600 whitespace-nowrap">
                              {c}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-100">
                        {ob.obSample.slice(0, 3).map((row, i) => (
                          <tr key={i} className="even:bg-slate-50/50">
                            {columns.slice(0, 6).map(c => (
                              <td key={c} className="px-2 py-1.5 text-slate-700 whitespace-nowrap">
                                {String(row[c] ?? '')}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Staged status */}
            {ob.obFileId && obProfile?.account_col && obProfile?.amount_col && (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <strong>Opening balance file staged.</strong>{' '}
                Account column: <span className="font-mono">{obProfile.account_col}</span>,
                amount column: <span className="font-mono">{obProfile.amount_col}</span>.
                The file will be committed with scope "{mode === 'file_first_year' ? 'first_year' : 'all'}" at the Finish step.
              </div>
            )}

            {needsFile && !ob.obFileId && (
              <WarnBox>
                Upload an opening balance file, or go back and select "Already included in GL
                data" if opening balance rows are in the main GL export. You can also proceed
                without a file — it can be added later.
              </WarnBox>
            )}
          </div>
        )}

        {/* Mode-specific informational note */}
        {mode === 'in_data' && (
          <InfoBox>
            No extra file needed. The pipeline will detect opening balance rows in the GL data
            using the entry type mapping from your column profile (e.g. rows where Source Type
            or Posting Type indicates an opening balance entry).
          </InfoBox>
        )}

      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 5 — Partner master (Phase 4 — COMPLETE)
// ---------------------------------------------------------------------------

function StepPartnerMaster({
  partner,
  dispatch,
}: {
  partner: WizardPartnerState
  dispatch: Dispatch<WizardAction>
}) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Local profile — initialise from persisted state
  const [side, setSide] = useState<'customer' | 'supplier'>(
    partner.profile?.side ?? 'customer',
  )
  const [entityMode, setEntityMode] = useState<'fixed' | 'column'>(
    partner.profile?.entity.mode ?? 'fixed',
  )
  const [entityValue, setEntityValue] = useState<string>(
    partner.profile?.entity.value ?? '',
  )
  const [joinKeyCol, setJoinKeyCol] = useState<string>(
    partner.profile?.join_key.column ?? '',
  )
  const [nameCol, setNameCol] = useState<string>(
    partner.profile?.columns.name_line_1 ?? '',
  )
  const [nameLine2Col, setNameLine2Col] = useState<string>(
    partner.profile?.columns.name_line_2 ?? '',
  )
  const [countryCol, setCountryCol] = useState<string>(
    partner.profile?.columns.country_code ?? '',
  )
  const [cityCol, setCityCol] = useState<string>(
    partner.profile?.columns.city ?? '',
  )
  const [postalCol, setPostalCol] = useState<string>(
    partner.profile?.columns.postal_code ?? '',
  )

  const columns = partner.columns ?? []

  // Build and persist profile whenever any mapping field changes
  function persistProfile(overrides: Partial<{
    side: 'customer' | 'supplier'
    entityMode: 'fixed' | 'column'
    entityValue: string
    joinKeyCol: string
    nameCol: string
    nameLine2Col: string
    countryCol: string
    cityCol: string
    postalCol: string
  }> = {}) {
    const s = overrides.side ?? side
    const em = overrides.entityMode ?? entityMode
    const ev = overrides.entityValue ?? entityValue
    const jk = overrides.joinKeyCol ?? joinKeyCol
    const nc = overrides.nameCol ?? nameCol
    const nl2 = overrides.nameLine2Col ?? nameLine2Col
    const cc = overrides.countryCol ?? countryCol
    const ci = overrides.cityCol ?? cityCol
    const po = overrides.postalCol ?? postalCol

    if (!jk || !nc) return   // don't persist partial required-field state

    const profile: PartnerMappingProfile = {
      side: s,
      entity: { mode: em, value: ev },
      join_key: { column: jk },
      columns: {
        name_line_1: nc,
        ...(nl2 ? { name_line_2: nl2 } : {}),
        ...(cc ? { country_code: cc } : {}),
        ...(ci ? { city: ci } : {}),
        ...(po ? { postal_code: po } : {}),
      },
    }
    dispatch({ type: 'PATCH_PARTNER', patch: { profile } })
  }

  async function handleFileSelected(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const res = await uploadPartnerMaster(file)
      const cols = res.columns
      // Auto-suggest join key and name columns
      const joinGuess = cols.find(c => /debtor|creditor|kred|deb|partner.?no|kunden.?nr|lief.?nr|number/i.test(c)) ?? ''
      const nameGuess = cols.find(c => /name|firma|bezeichnung/i.test(c)) ?? ''
      setJoinKeyCol(joinGuess)
      setNameCol(nameGuess)
      dispatch({
        type: 'PATCH_PARTNER',
        patch: {
          fileId: res.file_id,
          columns: res.columns,
          sample: res.sample,
          // reset profile on new upload so stale column refs are cleared
          profile: undefined,
        },
      })
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const profileComplete = Boolean(joinKeyCol && nameCol)

  return (
    <StepCard
      title="Partner master"
      subtitle="Upload customer or supplier master data and map the join key and name fields."
    >
      <div className="space-y-6">

        {/* ------------------------------------------------------------------ */}
        {/* Explanation box                                                      */}
        {/* ------------------------------------------------------------------ */}
        <InfoBox>
          <strong>What is partner master data?</strong> Partner master files contain
          the name, address, and identifier for each customer (debtor) or supplier (creditor).
          Uploading this data populates{' '}
          <span className="font-mono text-xs bg-white border border-blue-200 rounded px-1">
            dim_customer
          </span>{' '}
          and{' '}
          <span className="font-mono text-xs bg-white border border-blue-200 rounded px-1">
            dim_supplier
          </span>
          , enabling customer- and supplier-level drill-downs across all Financials
          and Sales views. This step is optional — drill-downs will show partner IDs
          instead of names until a master is loaded.
        </InfoBox>

        {/* ------------------------------------------------------------------ */}
        {/* Side toggle — customer vs supplier                                  */}
        {/* ------------------------------------------------------------------ */}
        <div className="space-y-2">
          <p className="text-sm font-medium text-slate-700">Which dimension does this file populate?</p>
          <div className="flex gap-3 flex-wrap">
            {(
              [
                ['customer', 'Customers (dim_customer)', 'Debtor numbers / Kundennummern'],
                ['supplier', 'Suppliers (dim_supplier)', 'Creditor numbers / Kreditorennummern'],
              ] as ['customer' | 'supplier', string, string][]
            ).map(([val, label, hint]) => (
              <label
                key={val}
                className={`flex-1 min-w-[200px] flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
                  side === val
                    ? 'border-blue-400 bg-blue-50'
                    : 'border-slate-200 bg-white hover:bg-slate-50'
                }`}
              >
                <input
                  type="radio"
                  name="partnerSide"
                  value={val}
                  checked={side === val}
                  onChange={() => {
                    setSide(val)
                    persistProfile({ side: val })
                  }}
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
            To load both customers and suppliers, complete this step for one side, then return
            and re-upload for the other side before the Finish step.
          </p>
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* File upload                                                          */}
        {/* ------------------------------------------------------------------ */}
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 space-y-3">
          <p className="text-sm font-semibold text-slate-700">
            Upload the {side === 'customer' ? 'customer' : 'supplier'} master file
          </p>
          <p className="text-xs text-slate-500">
            The file must contain at minimum the partner number (join key) and a name column.
            Address fields (country, city, postal code) are optional but improve reporting quality.
          </p>

          <div
            onClick={() => fileInputRef.current?.click()}
            onDragOver={e => e.preventDefault()}
            onDrop={e => {
              e.preventDefault()
              const f = e.dataTransfer.files[0]
              if (f) void handleFileSelected(f)
            }}
            className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-8 py-10 cursor-pointer transition ${
              uploading
                ? 'border-blue-400 bg-blue-50'
                : partner.fileId
                ? 'border-emerald-300 bg-emerald-50'
                : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls,.csv"
              className="hidden"
              onChange={e => {
                const f = e.target.files?.[0]
                if (f) void handleFileSelected(f)
              }}
            />
            {uploading ? (
              <p className="text-sm font-medium text-blue-600">Processing file…</p>
            ) : partner.fileId ? (
              <div className="text-center">
                <p className="text-sm font-semibold text-emerald-700">File staged</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  File ID: <span className="font-mono">{partner.fileId}</span>
                </p>
                {columns.length > 0 && (
                  <p className="text-xs text-slate-400 mt-0.5">
                    {columns.length} columns detected
                  </p>
                )}
                <p className="text-xs text-slate-400 mt-1.5 italic">Click or drop to replace</p>
              </div>
            ) : (
              <>
                <div className="text-3xl text-slate-300 mb-2">&#128196;</div>
                <p className="text-sm font-semibold text-slate-700">Drop a file here or click to browse</p>
                <p className="mt-1 text-xs text-slate-400">XLSX · XLS · CSV</p>
              </>
            )}
          </div>

          {uploadError && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {uploadError}
            </div>
          )}
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* Column mapping — shown once a file is staged                        */}
        {/* ------------------------------------------------------------------ */}
        {partner.fileId && columns.length > 0 && (
          <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-4">
            <p className="text-sm font-semibold text-slate-700">Column mapping</p>
            <p className="text-xs text-slate-500">
              Map the columns from your file to the standard target fields.
              The join key and primary name are required; all other fields are optional.
            </p>

            {/* Entity assignment */}
            <div className="rounded-md border border-slate-100 bg-slate-50 px-3 py-3 space-y-3">
              <p className="text-xs font-semibold text-slate-600">Entity assignment</p>
              <div className="flex gap-3 flex-wrap">
                <label className={`flex items-center gap-2 cursor-pointer rounded-md border px-3 py-2 text-xs transition ${
                  entityMode === 'fixed' ? 'border-blue-400 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                }`}>
                  <input
                    type="radio"
                    name="entityMode"
                    value="fixed"
                    checked={entityMode === 'fixed'}
                    onChange={() => {
                      setEntityMode('fixed')
                      persistProfile({ entityMode: 'fixed' })
                    }}
                    className="accent-blue-600"
                  />
                  Fixed entity code
                </label>
                <label className={`flex items-center gap-2 cursor-pointer rounded-md border px-3 py-2 text-xs transition ${
                  entityMode === 'column' ? 'border-blue-400 bg-blue-50 text-blue-800' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
                }`}>
                  <input
                    type="radio"
                    name="entityMode"
                    value="column"
                    checked={entityMode === 'column'}
                    onChange={() => {
                      setEntityMode('column')
                      persistProfile({ entityMode: 'column' })
                    }}
                    className="accent-blue-600"
                  />
                  From column
                </label>
              </div>
              {entityMode === 'fixed' ? (
                <div className="space-y-1">
                  <label className="text-xs text-slate-600">Entity code (e.g. "DE" or "01")</label>
                  <input
                    type="text"
                    value={entityValue}
                    onChange={e => {
                      setEntityValue(e.target.value)
                      persistProfile({ entityValue: e.target.value })
                    }}
                    placeholder="e.g. DE"
                    className="rounded-md border border-slate-300 px-2 py-1.5 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              ) : (
                <div className="space-y-1">
                  <label className="text-xs text-slate-600">Entity column</label>
                  <select
                    value={entityValue}
                    onChange={e => {
                      setEntityValue(e.target.value)
                      persistProfile({ entityValue: e.target.value })
                    }}
                    className="rounded-md border border-slate-300 px-2 py-1.5 text-sm w-56 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">-- Select column --</option>
                    {columns.map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              )}
            </div>

            {/* Required fields: join key + name */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-700">
                  Join key column
                  <span className="ml-0.5 text-red-500" aria-hidden>*</span>
                  <span className="ml-1 font-normal text-slate-400">
                    ({side === 'customer' ? 'debtor' : 'creditor'} number)
                  </span>
                </label>
                <select
                  value={joinKeyCol}
                  onChange={e => {
                    setJoinKeyCol(e.target.value)
                    persistProfile({ joinKeyCol: e.target.value })
                  }}
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Select column --</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <p className="text-xs text-slate-400">
                  Must match the {side === 'customer' ? 'debtor' : 'creditor'} number
                  stored in your GL data.
                </p>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-700">
                  Primary name column
                  <span className="ml-0.5 text-red-500" aria-hidden>*</span>
                </label>
                <select
                  value={nameCol}
                  onChange={e => {
                    setNameCol(e.target.value)
                    persistProfile({ nameCol: e.target.value })
                  }}
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Select column --</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <p className="text-xs text-slate-400">
                  Company name or first name — displayed in drill-downs and exports.
                </p>
              </div>
            </div>

            {/* Optional fields */}
            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-600">Optional address fields</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {(
                  [
                    ['nameLine2Col', setNameLine2Col, nameLine2Col, 'Name line 2', 'name_line_2'],
                    ['countryCol', setCountryCol, countryCol, 'Country code', 'country_code'],
                    ['cityCol', setCityCol, cityCol, 'City', 'city'],
                    ['postalCol', setPostalCol, postalCol, 'Postal code', 'postal_code'],
                  ] as [string, (v: string) => void, string, string, string][]
                ).map(([key, setter, val, label]) => (
                  <div key={key} className="space-y-1">
                    <label className="text-xs text-slate-600">{label}</label>
                    <select
                      value={val}
                      onChange={e => {
                        setter(e.target.value)
                        persistProfile({ [key]: e.target.value } as Parameters<typeof persistProfile>[0])
                      }}
                      className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">-- Not mapped --</option>
                      {columns.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                ))}
              </div>
            </div>

            {/* Sample preview */}
            {partner.sample && partner.sample.length > 0 && (
              <div className="overflow-x-auto rounded-md border border-slate-100">
                <table className="min-w-full text-xs">
                  <thead className="bg-slate-50">
                    <tr>
                      {columns.slice(0, 6).map(c => (
                        <th key={c} className="px-2 py-1.5 text-left font-medium text-slate-600 whitespace-nowrap">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {partner.sample.slice(0, 3).map((row, i) => (
                      <tr key={i} className="even:bg-slate-50/50">
                        {columns.slice(0, 6).map(c => (
                          <td key={c} className="px-2 py-1.5 text-slate-700 whitespace-nowrap">
                            {String(row[c] ?? '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Staged status */}
            {profileComplete && (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <strong>Partner master staged.</strong>{' '}
                Side: {side} · Join key: <span className="font-mono">{joinKeyCol}</span> ·
                Name: <span className="font-mono">{nameCol}</span>.
                Will populate{' '}
                <span className="font-mono text-xs">
                  {side === 'customer' ? 'dim_customer' : 'dim_supplier'}
                </span>{' '}
                at the Finish step.
              </div>
            )}

            {!profileComplete && (
              <WarnBox>
                Select the join key column and primary name column to complete the mapping.
                You can proceed without completing the mapping — the partner master can be
                uploaded separately after the initial project commit.
              </WarnBox>
            )}
          </div>
        )}

      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 6 — Review & Finish (Phase 5 — full orchestration)
// ---------------------------------------------------------------------------

/** Status of one commit step in the Finish sequence. */
type StepStatus = 'pending' | 'running' | 'done' | 'skipped' | 'failed'

interface CommitStepState {
  id: string
  label: string
  status: StepStatus
  detail?: string   // counts / reason for skip / error message
}

/** Spinner SVG — inline to avoid any icon-library dep. */
function SpinnerIcon() {
  return (
    <svg className="animate-spin h-4 w-4 text-blue-600" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

function StepStatusIcon({ status }: { status: StepStatus }) {
  if (status === 'pending')
    return <span className="inline-block h-4 w-4 rounded-full border-2 border-slate-300 bg-white" />
  if (status === 'running') return <SpinnerIcon />
  if (status === 'done')
    return <span className="inline-block h-4 w-4 rounded-full bg-emerald-500 text-white flex items-center justify-center text-[10px]">&#10003;</span>
  if (status === 'skipped')
    return <span className="inline-block h-4 w-4 rounded-full bg-slate-200 text-slate-500 flex items-center justify-center text-[10px]">&#8212;</span>
  // failed
  return <span className="inline-block h-4 w-4 rounded-full bg-red-500 text-white flex items-center justify-center text-[10px]">&#10007;</span>
}

const INITIAL_COMMIT_STEPS: CommitStepState[] = [
  { id: 'config',  label: 'Save project config',   status: 'pending' },
  { id: 'gl',      label: 'Commit GL bookings',     status: 'pending' },
  { id: 'coa',     label: 'Commit Chart of Accounts', status: 'pending' },
  { id: 'ob',      label: 'Commit opening balances', status: 'pending' },
  { id: 'partner', label: 'Commit partner master',  status: 'pending' },
  { id: 'rebuild', label: 'Full rebuild',            status: 'pending' },
]

function StepReview({
  state,
  onRunSetup,
}: {
  state: WizardState
  onRunSetup: (runRebuild: boolean) => void
}) {
  const startMonth = fyStartFromEndMonth(state.fyEndMonth)
  const [runRebuild, setRunRebuild] = useState(false)

  const glSummary = (() => {
    if (!state.gl.fileId) return 'Not yet uploaded'
    const parts: string[] = [`File ID: ${state.gl.fileId}`]
    if (state.gl.kontext?.entityMode === 'fixed' && state.gl.kontext.entityValue)
      parts.push(`Entity: ${state.gl.kontext.entityValue}`)
    if (state.gl.kontext?.entityMode === 'column' && state.gl.kontext.entityValue)
      parts.push(`Entity column: ${state.gl.kontext.entityValue}`)
    if (state.gl.partnerColumnsMode)
      parts.push(`Partner columns: ${state.gl.partnerColumnsMode}`)
    if (state.gl.opts?.linking)
      parts.push(`AR/AP linking: ${state.gl.opts.linking}`)
    if (state.gl.validationOk === true)
      parts.push('Validation: passed')
    else if (state.gl.validationOk === false)
      parts.push('Validation: warnings present')
    return parts.join(' · ')
  })()

  const rows: Array<{ label: string; value: string }> = [
    { label: 'Project name',    value: state.projectName || '—' },
    {
      label: 'Fiscal year',
      value: `${MONTH_NAMES[startMonth - 1]} – ${MONTH_NAMES[state.fyEndMonth - 1]} (fy_start_month = ${startMonth})`,
    },
    { label: 'GL bookings',     value: glSummary },
    {
      label: 'Chart of accounts',
      value: (() => {
        if (!state.coa.source) return 'Not yet configured'
        if (state.coa.source === 'library') {
          const variantLabel =
            LIBRARY_VARIANTS.find(v => v.value === state.coa.libraryVariant)?.label ??
            state.coa.libraryVariant ??
            '—'
          return `Library — ${variantLabel}`
        }
        if (state.coa.source === 'upload') {
          if (!state.coa.masterFileId) return 'Upload — no file staged yet'
          const fmt = state.coa.isBsPlMaster ? 'bs_pl_master format' : 'generic CoA (column-mapped)'
          return `Upload — File ID: ${state.coa.masterFileId} · ${fmt}`
        }
        return state.coa.source
      })(),
    },
    {
      label: 'Opening balances',
      value: (() => {
        if (!state.ob.mode) return 'Not yet configured'
        if (state.ob.mode === 'in_data') return 'Already included in GL data'
        const scope = state.ob.mode === 'file_first_year' ? 'first-year file' : 'all-years file'
        if (!state.ob.obFileId) return `Separate ${scope} — no file staged yet`
        const profile = state.ob.obProfile
        const cols = profile?.account_col && profile?.amount_col
          ? ` · Account: ${profile.account_col}, Amount: ${profile.amount_col}`
          : ' · column mapping incomplete'
        return `Separate ${scope} — File ID: ${state.ob.obFileId}${cols}`
      })(),
    },
    {
      label: 'Partner master',
      value: (() => {
        if (!state.partner.fileId) return 'Not uploaded (optional)'
        if (!state.partner.profile) return `File staged (ID: ${state.partner.fileId}) — mapping incomplete`
        const p = state.partner.profile
        return `${p.side === 'customer' ? 'Customers' : 'Suppliers'} · Join key: ${p.join_key.column} · Name: ${p.columns.name_line_1}`
      })(),
    },
  ]

  return (
    <StepCard
      title="Review & Finish"
      subtitle="Review the collected configuration. Click Run Setup to commit everything in order."
    >
      <div className="space-y-5">
        {/* Summary table */}
        <div className="rounded-lg border border-slate-200 overflow-hidden">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-slate-100">
              {rows.map(r => (
                <tr key={r.label} className="even:bg-slate-50/50">
                  <td className="px-4 py-2.5 font-medium text-slate-600 w-48 flex-shrink-0">
                    {r.label}
                  </td>
                  <td className="px-4 py-2.5 text-slate-900">{r.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Rebuild checkbox */}
        <label className="flex items-center gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white px-4 py-3 hover:bg-slate-50 transition">
          <input
            type="checkbox"
            checked={runRebuild}
            onChange={e => setRunRebuild(e.target.checked)}
            className="accent-blue-600 h-4 w-4"
          />
          <div>
            <p className="text-sm font-semibold text-slate-800">Run full rebuild now</p>
            <p className="text-xs text-slate-500 mt-0.5">
              After committing all data, trigger a full rebuild of all derived tables
              (P&amp;L, BS, WC, CF, dimension tables). Takes 1–3 minutes depending on data volume.
              If rebuild_on_commit is disabled on the server the rebuild step will be skipped
              gracefully (no error).
            </p>
          </div>
        </label>

        <InfoBox>
          <strong>Collect-then-commit model:</strong> nothing has been written to the database yet.
          Clicking Run Setup will execute the sequence below in order. Each step is idempotent
          (uses replace mode) — you can re-run safely after a failure.
        </InfoBox>

        <button
          type="button"
          onClick={() => onRunSetup(runRebuild)}
          className="rounded-md px-6 py-2.5 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed"
          style={{ backgroundColor: '#1E3A5F' }}
        >
          Run Setup
        </button>
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 6 — Finish panel (shown after "Run Setup" is clicked)
// ---------------------------------------------------------------------------

function FinishPanel({
  steps,
  done,
  errorStepId,
  onRetry,
}: {
  steps: CommitStepState[]
  done: boolean
  errorStepId: string | null
  onRetry: () => void
}) {
  const allSucceeded = done && errorStepId === null

  return (
    <StepCard
      title="Setup in progress"
      subtitle="Committing your project configuration and data in sequence. Do not close this tab."
    >
      <div className="space-y-4">
        {/* Checklist */}
        <div className="rounded-lg border border-slate-200 bg-white divide-y divide-slate-100 overflow-hidden">
          {steps.map(s => (
            <div key={s.id} className="flex items-start gap-3 px-4 py-3">
              <div className="mt-0.5 shrink-0">
                <StepStatusIcon status={s.status} />
              </div>
              <div className="min-w-0 flex-1">
                <p className={`text-sm font-medium ${
                  s.status === 'failed'   ? 'text-red-700'
                  : s.status === 'done'  ? 'text-emerald-700'
                  : s.status === 'skipped' ? 'text-slate-400'
                  : s.status === 'running' ? 'text-blue-700'
                  : 'text-slate-600'
                }`}>
                  {s.label}
                  {s.status === 'skipped' && <span className="ml-1 font-normal text-slate-400">(skipped)</span>}
                </p>
                {s.detail && (
                  <p className={`text-xs mt-0.5 ${s.status === 'failed' ? 'text-red-600' : 'text-slate-500'}`}>
                    {s.detail}
                  </p>
                )}
              </div>
              <div className="shrink-0 text-xs font-medium uppercase tracking-wide">
                {s.status === 'pending' && <span className="text-slate-300">Pending</span>}
                {s.status === 'running' && <span className="text-blue-600">Running…</span>}
                {s.status === 'done'    && <span className="text-emerald-600">Done</span>}
                {s.status === 'skipped' && <span className="text-slate-400">Skipped</span>}
                {s.status === 'failed'  && <span className="text-red-600">Failed</span>}
              </div>
            </div>
          ))}
        </div>

        {/* Final status banner */}
        {allSucceeded && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 font-medium">
            Setup complete. All steps committed successfully. You can now navigate to the
            Financials or Mapping Editor to verify your data.
          </div>
        )}
        {errorStepId !== null && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            <p className="font-semibold">Setup stopped at a failed step.</p>
            <p className="mt-1 text-red-700 text-xs">
              Fix the underlying issue and click Retry. All commit operations are idempotent —
              re-running will not duplicate data.
            </p>
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 rounded-md border border-red-300 bg-white px-4 py-1.5 text-sm font-semibold text-red-700 hover:bg-red-50 transition"
            >
              Retry from failed step
            </button>
          </div>
        )}
        {!done && errorStepId === null && (
          <div className="text-xs text-slate-400 text-center">
            Please wait — do not navigate away or close this tab while the setup is running.
          </div>
        )}
      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Loading spinner
// ---------------------------------------------------------------------------

function LoadingScreen() {
  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="mx-auto w-full max-w-[1680px] px-6 py-8">
        <div className="flex items-center justify-center py-20 gap-3 text-slate-500">
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
          <span className="text-sm">Loading project configuration...</span>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ProjectSetupWizard() {
  const [step, setStep] = useState(0)
  const [state, dispatch] = useReducer(wizardReducer, undefined, defaultState)
  const [loading, setLoading] = useState(true)

  const totalSteps = WIZARD_STEPS.length

  // ---------------------------------------------------------------------------
  // Prefill from backend on mount
  // ---------------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api
      .getProject(PROJECT_ID)
      .then((data: ProjectConfigResponse) => {
        if (cancelled) return
        const cfg = data.config ?? {}
        const fyStartMonth = data.fy_start_month ?? cfg.fy_start_month ?? 1
        const fyEnd = fyEndFromStartMonth(fyStartMonth)

        dispatch({
          type: 'PREFILL',
          partial: {
            projectName: data.name ?? cfg.name ?? '',
            fyEndMonth: fyEnd,
            entities:
              Array.isArray(cfg.entities) && cfg.entities.length > 0
                ? cfg.entities
                : [{ code: '', prefix: '', name: '' }],
          },
        })
      })
      .catch(() => {
        // Backend not yet live or 404 — start with defaults, no error shown
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Per-step Next-disabled validation
  // ---------------------------------------------------------------------------
  function isNextDisabled(): boolean {
    if (step === 0) return state.projectName.trim().length === 0
    if (step === 1) return state.fyEndMonth < 1 || state.fyEndMonth > 12
    // Step 2 (GL): block Next until validation has run (passed or soft-confirmed).
    // validationOk is set to true (hard pass) or false (soft warnings present but
    // user has seen results). undefined means validation has not yet been attempted.
    if (step === 2) return state.gl.validationOk === undefined
    // Step 3 (CoA): library path always ok once a variant is chosen (default 'skr03').
    // Upload path: ok once a file is staged (column mapping issues show a warning but
    // don't block — user can proceed and fix post-commit in the CoA Editor).
    if (step === 3) {
      if (!state.coa.source) return true   // source not yet chosen
      if (state.coa.source === 'library') return !state.coa.libraryVariant
      if (state.coa.source === 'upload') return false  // allow proceed even without file
    }
    // Step 4 (OB): in_data → always ok. file_first_year/file_all → ok even without a
    // file staged (warn shown inline; user can proceed and upload later).
    if (step === 4) {
      return !state.ob.mode   // block only if mode not yet chosen (default 'in_data' so always passes)
    }
    // Step 5 (Partner): entirely optional step — never blocks Next.
    if (step === 5) return false
    return false
  }

  // ---------------------------------------------------------------------------
  // Phase 5 — Finish orchestration state
  // ---------------------------------------------------------------------------
  const [commitSteps, setCommitSteps] = useState<CommitStepState[]>(INITIAL_COMMIT_STEPS)
  const [finishDone, setFinishDone] = useState(false)
  const [finishErrorStepId, setFinishErrorStepId] = useState<string | null>(null)
  /** true once the user has clicked "Run Setup" — switches Review to FinishPanel */
  const [finishStarted, setFinishStarted] = useState(false)

  /** Mutate one step in the checklist by id. */
  const patchStep = useCallback((id: string, patch: Partial<CommitStepState>) => {
    setCommitSteps(prev => prev.map(s => s.id === id ? { ...s, ...patch } : s))
  }, [])

  // ---------------------------------------------------------------------------
  // Finish handler — sequential COLLECT-THEN-COMMIT orchestration
  // ---------------------------------------------------------------------------
  const handleRunSetup = useCallback(async (runRebuild: boolean) => {
    setFinishStarted(true)
    setFinishDone(false)
    setFinishErrorStepId(null)
    // Reset all steps to pending so a retry starts fresh from step 1
    setCommitSteps(INITIAL_COMMIT_STEPS.map(s =>
      s.id === 'rebuild' ? { ...s, status: runRebuild ? 'pending' : 'skipped', detail: runRebuild ? undefined : 'Rebuild checkbox not selected' } : s
    ))

    const { gl, coa, ob, partner, projectName, fyEndMonth, entities } = state
    const fyStart = fyStartFromEndMonth(fyEndMonth)

    // ---- Helper: run one async step, mark done/failed, return false on failure ----
    async function runStep<T>(
      id: string,
      fn: () => Promise<T>,
      formatDetail: (result: T) => string,
    ): Promise<T | null> {
      patchStep(id, { status: 'running' })
      try {
        const result = await fn()
        patchStep(id, { status: 'done', detail: formatDetail(result) })
        return result
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        patchStep(id, { status: 'failed', detail: msg })
        setFinishErrorStepId(id)
        setFinishDone(true)
        return null
      }
    }

    // ---- Step 1: Save config ----
    const configResult = await runStep(
      'config',
      () => api.putProject(PROJECT_ID, {
        name: projectName,
        fy_start_month: fyStart,
        entities: entities.filter(e => e.code.trim() !== ''),
        opening_balance_mode: ob.mode === 'in_data' ? 'in_data' : 'file',
        mapping_source: coa.source === 'library' ? 'library' : 'client_coa',
        partner_master_source: 'files',
        net_profit_source: 'report_inject',
        sales_label: 'Sales',
        cost_label: 'Cost of materials',
      }),
      () => `Project "${projectName}" saved (fy_start_month=${fyStart})`,
    )
    if (configResult === null) return

    // ---- Step 2: GL commit ----
    if (!gl.fileId || !gl.assembledProfile) {
      patchStep('gl', { status: 'skipped', detail: 'No GL file staged — skipped' })
    } else {
      const glResult = await runStep(
        'gl',
        () => commitIngest({
          file_id: gl.fileId!,
          sheet: gl.sheet,
          profile: gl.assembledProfile!,
          dataset: 'gl',
          confirm_soft: true,
          exclude_line_ids: [],
          commit_mode: 'replace',
        }),
        (r: CommitResponse) =>
          `${r.entries} entries, ${r.lines} lines loaded (AR: ${r.ar}, AP: ${r.ap}, skipped: ${r.skipped})`,
      )
      if (glResult === null) return
    }

    // ---- Step 3: CoA commit ----
    if (coa.source === 'library') {
      patchStep('coa', {
        status: 'skipped',
        detail: `Library classification (${coa.libraryVariant ?? 'skr03'}) — applied at rebuild; no file to commit`,
      })
    } else if (!coa.masterFileId) {
      patchStep('coa', { status: 'skipped', detail: 'No CoA file staged — skipped' })
    } else {
      const coaResult = await runStep(
        'coa',
        () => commitAccountMapping({
          file_id: coa.masterFileId!,
          format: coa.isBsPlMaster ? 'bs_pl_master' : 'generic',
          replace_mode: 'replace',
          ...(coa.isBsPlMaster ? {} : { profile: undefined }),
        }),
        (r: MappingCommitResponse) =>
          `${r.accounts} accounts loaded (NA: ${r.na ?? 0}, CF: ${r.cf ?? 0})`,
      )
      if (coaResult === null) return
    }

    // ---- Step 4: Opening balances commit ----
    if (ob.mode === 'in_data') {
      patchStep('ob', {
        status: 'skipped',
        detail: 'Opening balances are included in the GL data — no separate file needed',
      })
    } else if (!ob.obFileId || !ob.obProfile?.account_col || !ob.obProfile?.amount_col) {
      patchStep('ob', {
        status: 'skipped',
        detail: ob.obFileId
          ? 'OB file staged but column mapping incomplete — skipped'
          : 'No OB file staged — skipped',
      })
    } else {
      // Build a minimal GL-style profile for the OB commit (only account + amount columns needed)
      const obProfile = {
        entity: { mode: 'fixed' as const, value: entities[0]?.code ?? '' },
        fiscal_year: { mode: 'from_date' as const, value: '' },
        sign: { mode: 'signed' as const, amount: ob.obProfile.amount_col },
        decimal: ',',
        thousands: '.',
        date_dayfirst: true,
        columns: {
          account_number: ob.obProfile.account_col,
          amount: ob.obProfile.amount_col,
        },
        linking_strategy: 'none' as const,
        entry_type: 'actual' as const,
      }
      const obResult = await runStep(
        'ob',
        () => commitOpeningBalance({
          file_id: ob.obFileId!,
          profile: obProfile,
          scope: ob.mode === 'file_first_year' ? 'first_year' : 'all',
        }),
        (r: ObCommitResponse) =>
          `${r.lines} lines loaded, scope="${r.scope}", fiscal years: ${r.fiscal_years.join(', ') || 'n/a'}`,
      )
      if (obResult === null) return
    }

    // ---- Step 5: Partner master commit ----
    if (!partner.fileId || !partner.profile) {
      patchStep('partner', {
        status: 'skipped',
        detail: partner.fileId
          ? 'Partner file staged but mapping incomplete — skipped. Upload separately after initial commit.'
          : 'No partner master file staged (optional step) — skipped',
      })
    } else {
      const partnerResult = await runStep(
        'partner',
        () => commitPartnerMaster({
          file_id: partner.fileId!,
          profile: partner.profile!,
        }),
        (r: PartnerCommitResponse) =>
          `${r.upserted} ${r.side === 'customer' ? 'customers' : 'suppliers'} upserted into dim_${r.side}`,
      )
      if (partnerResult === null) return
    }

    // ---- Step 6: Rebuild (optional) ----
    if (!runRebuild) {
      // already marked skipped in initial reset above
    } else {
      patchStep('rebuild', { status: 'running' })
      try {
        await api.rebuildProject(PROJECT_ID)
        patchStep('rebuild', { status: 'done', detail: 'Rebuild triggered successfully' })
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        // 503 = rebuild_on_commit disabled — treat as info, not hard failure
        if (/503|rebuild_on_commit|disabled/i.test(msg)) {
          patchStep('rebuild', {
            status: 'skipped',
            detail: 'Rebuild endpoint returned 503 — rebuild_on_commit is disabled on this server. Trigger a rebuild manually from the Admin panel.',
          })
        } else {
          patchStep('rebuild', { status: 'failed', detail: msg })
          setFinishErrorStepId('rebuild')
          setFinishDone(true)
          return
        }
      }
    }

    setFinishDone(true)
  }, [state, patchStep])

  /** Retry: reset state and re-run from scratch (idempotent replace commits). */
  const handleRetry = useCallback(() => {
    setFinishStarted(false)
    setFinishDone(false)
    setFinishErrorStepId(null)
    setCommitSteps(INITIAL_COMMIT_STEPS)
  }, [])

  if (loading) {
    return <LoadingScreen />
  }

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="mx-auto w-full max-w-[1680px] px-6 py-8">
        {/* Page header */}
        <div className="mb-8">
          <p
            className="text-xs font-semibold uppercase tracking-widest mb-2"
            style={{ color: '#1E3A5F' }}
          >
            Admin
          </p>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
            Project Setup
          </h1>
          <p className="text-sm mt-1.5" style={{ color: '#94A3B8' }}>
            Walk through the one-time setup to configure your project, upload data and map accounts.
            Monthly data updates use the Data Update flow — no need to re-run this wizard.
          </p>
        </div>

        {/* Stepper */}
        <Stepper current={step} steps={WIZARD_STEPS} />

        {/* Step content */}
        <div className="mt-2">
          {step === 0 && <StepProjectName state={state} dispatch={dispatch} />}
          {step === 1 && <StepFiscalYear state={state} dispatch={dispatch} />}
          {step === 2 && <StepGlBookings gl={state.gl} dispatch={dispatch} />}
          {step === 3 && (
            <StepChartOfAccounts
              coa={state.coa}
              dispatch={dispatch}
              fyEndMonth={state.fyEndMonth}
              entities={state.entities}
            />
          )}
          {step === 4 && <StepOpeningBalances ob={state.ob} dispatch={dispatch} />}
          {step === 5 && <StepPartnerMaster partner={state.partner} dispatch={dispatch} />}
          {step === 6 && !finishStarted && (
            <StepReview state={state} onRunSetup={handleRunSetup} />
          )}
          {step === 6 && finishStarted && (
            <FinishPanel
              steps={commitSteps}
              done={finishDone}
              errorStepId={finishErrorStepId}
              onRetry={handleRetry}
            />
          )}
        </div>

        {/* Navigation — hide Back/Next while finish is running or done */}
        {!(step === 6 && finishStarted) && (
          <NavButtons
            step={step}
            totalSteps={totalSteps}
            onBack={() => setStep(s => Math.max(0, s - 1))}
            onNext={() => setStep(s => Math.min(totalSteps - 1, s + 1))}
            nextDisabled={isNextDisabled()}
            nextLabel={step === totalSteps - 2 ? 'Review' : 'Next'}
          />
        )}
      </div>
    </div>
  )
}
