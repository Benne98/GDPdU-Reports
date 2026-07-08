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
 *   6  Additional information    (FTE Development — COMPLETE)
 *   7  Review & Finish           (Phase 5 — COMPLETE)
 *
 * Commit sequence (in dependency order):
 *   1. Save config      PUT /projects/default
 *   2. CoA commit       POST /ingest/mapping/commit  (skipped when source=library)
 *      NOTE: library-source skips this step — dim_gl_account won't be pre-populated, so
 *      GL accounts must already exist in the DB when using the library CoA source on a fresh project.
 *   3. GL commit        POST /ingest/commit  (requires dim_gl_account rows — CoA must run first)
 *   4. OB commit        POST /ingest/opening-balance/commit  (skipped when mode=in_data)
 *   5. Partner commit   POST /ingest/partner-master/commit  (skipped when no file staged)
 *   6. FTE Development  POST /api/v1/fdd/run/fte_development  (skipped when no uploads)
 *   7. Rebuild          POST /projects/default/rebuild  (optional checkbox)
 *
 * On mount: prefills from api.getProject('default') where sensible.
 * The /ingestion route is NOT linked here — this wizard is self-contained.
 */

import { type Dispatch, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import PartnerMasterEditor from '../components/masters/PartnerMasterEditor'
import { api, type ProjectConfigResponse, type AccountMappingMode } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { Stepper, StepCard, NavButtons } from '../components/ingest/IngestStepCard'
import EntitySourceSelector, { type EntitySource } from '../components/ingest/EntitySourceSelector'
import AccountColumnMapper, { missingRequiredAccountFields, coaGenericFields } from '../components/ingest/AccountColumnMapper'
import ColumnMapper, { type TargetField } from '../components/ingest/ColumnMapper'
// isBsPlMasterSheets removed — CoaGroupCard now uses per-slot sheet detection inline
import type { KontextState, OptionsState } from '../components/ingest/ingestTypes'
import {
  GlEntityCard,
  defaultGlEntityState,
  suggestGlMapping,
  type GlEntityState,
  type WizardGlState,
  type GlFormatGroup,
  type PartnerColumnsMode,
  type PartnerColumnsSplit,
  type GlYearFileState,
} from '../components/ingest/GlEntityCard'
import GlGroupConfigPanel from '../components/ingest/GlGroupConfigPanel'
import {
  createGroup,
  reindexGroupsAfterRemove,
  applyGroupHeadersToMembers,
  buildGroupsFromAssignments,
} from '../components/ingest/glFormatGroups'
import GlFormatAssignmentStep from '../components/ingest/GlFormatAssignmentStep'
import CoaMappingAssignmentStep from '../components/ingest/CoaMappingAssignmentStep'
import StatementStructureStep from '../components/ingest/StatementStructureStep'
import PerEntityPager from '../components/ingest/PerEntityPager'
import { glFiscalYearLabel } from '../lib/fiscalYear'
import {
  uploadFile,
  combineGlFiles,
  applyHeaders,
  downloadCoaTemplate,
  uploadOpeningBalance,
  uploadPartnerMaster,
  commitIngest,
  commitAccountMapping,
  commitOpeningBalance,
  commitPartnerMaster,
  resetProjectData,
  uploadFddFile,
  runFteDevelopment,
  glUnmappedDetail,
  applyLibraryMapping,
  type ResetProjectDataResponse,
  type Profile,
  type AccountMappingProfile,
  type CommitResponse,
  type Dialect,
  type MappingCommitResponse,
  type ObCommitResponse,
  type PartnerCommitResponse,
  type FteDevelopmentRequest,
  type GlUnmappedDetail,
  type GlUnmappedAccount,
  type ApplyLibraryResponse,
} from '../lib/gdpduApi'
import FteColumnMapper, { type FteMappingPayload } from '../components/fdd-bot/FteColumnMapper'
import FteDimensionPicker, { type FteDimension } from '../components/fdd-bot/FteDimensionPicker'
import FtePexGrid from '../components/fdd-bot/FtePexGrid'
import type { AdaptiveCardInput } from '../components/fdd-bot/useFddBot'
import DatasetSelector from '../components/ingest/DatasetSelector'
import PerYearPager from '../components/ingest/PerYearPager'
import {
  buildInitialCoaAssignment,
  buildCoaItems,
  migrateLegacyCoaGroups,
  coaGroupsCoverAllEntities,
  type CoaItem,
} from '../components/ingest/coaAssignment'
import AnlagenStep, {
  type WizardAnlagenState,
} from '../components/ingest/AnlagenStep'
import OposStep, {
  type WizardOposState,
} from '../components/ingest/OposStep'

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
// Wizard state shape — GL types imported from GlEntityCard
// ---------------------------------------------------------------------------
// PartnerColumnsMode, PartnerColumnsSplit, GlYearFileState, GlEntityState,
// WizardGlState are all imported from '../components/ingest/GlEntityCard'
// and re-exported below for backward compatibility.
export type { PartnerColumnsMode, PartnerColumnsSplit, GlYearFileState, GlEntityState, WizardGlState }

// ---------------------------------------------------------------------------
// Legacy GlInputState — kept for the OLD flat-list path in WizardGlState.inputs
// (only used by the old Finish loop; the new flow uses entities).
// ---------------------------------------------------------------------------

/**
 * @deprecated Use GlEntityState instead.
 * Kept only so the Finish orchestration can still reference .inputs if needed
 * during transition.  New code uses gl.entities exclusively.
 */
export interface GlInputState {
  fileId?: string
  sheet?: string
  columns?: string[]
  sample?: Record<string, unknown>[]
  dialect?: Dialect
  kontext?: KontextState
  partnerColumnsMode?: PartnerColumnsMode
  partnerColumnsSplit?: PartnerColumnsSplit
  mapping?: Record<string, string>
  opts?: OptionsState
  validationOk?: boolean
  entityAssignments?: Record<string, string>
  assembledProfile?: Profile
}

/** Upload slot for one BS or PL file within a CoA mapping group. */
export interface CoaUploadSlot {
  /** file_id from uploadFile() */
  fileId?: string
  /** Sheet names from the uploaded file */
  sheets?: string[]
  /** Detected columns from the uploaded file */
  columns?: string[]
  /** Sample rows */
  sample?: Record<string, unknown>[]
  /**
   * true  → single-sheet master detected (Master_BS or Master_PL sheet present);
   *         commits as format=bs_pl_master, no column mapping needed.
   * false → generic client CoA file; column mapping via AccountColumnMapper (COA_GENERIC_FIELDS).
   */
  isMaster?: boolean
  /** Column mapping for generic (non-master-shaped) client CoA uploads. */
  mapping?: Record<string, string>
}

/** One CoA mapping group — a set of entities sharing the same account mapping source. */
export interface CoaMappingGroup {
  /** Stable key derived from the CoaMappingAssignmentStep output. */
  id: string
  /** Human-readable label, e.g. "Format A". */
  label: string
  /** Entity codes belonging to this group (matches WizardState.entities[i].code). */
  memberEntityCodes: string[]
  /** 'library' = Finssentials built-in; 'upload' = client-provided BS/PL files. */
  method?: 'library' | 'upload'
  /**
   * True when the user actively chose this group's method or membership.
   * migrateLegacyCoaGroups never folds a methodExplicit group into the pivot group,
   * so genuine multi-chart and library+upload-mixed setups are preserved.
   */
  methodExplicit?: boolean
  /** Library variant slug when method='library'. e.g. 'skr03' | 'statutory' | 'past_projects' */
  libraryVariant?: string
  /** Balance-sheet upload slot (method='upload'). */
  bs?: CoaUploadSlot
  /** Profit-and-loss upload slot (method='upload'). */
  pl?: CoaUploadSlot
}

export interface WizardCoaState {
  /** Per-entity-group CoA mapping configuration. */
  groups: CoaMappingGroup[]
  /**
   * How accounts in years/entities NOT covered by the uploaded mapping should be classified.
   * 'library' (default) — fill gaps from the Finssentials library (most-frequent).
   * 'exclusive'         — leave unmatched accounts unmapped (no library fill).
   */
  accountMappingMode: AccountMappingMode
  /**
   * true once the CoaMappingAssignmentStep has been confirmed.
   * Auto-set to true for single-entity projects via useEffect in StepChartOfAccounts.
   */
  assigned?: boolean
  /**
   * true once the legacy-migration useEffect has run its first check with non-empty
   * groups. Prevents re-firing on subsequent group changes (e.g., file uploads in a
   * deliberate multi-CoA flow). Persists in reducer state so it survives component
   * remounts when the user navigates between wizard steps.
   */
  migrationChecked?: boolean
}

export interface WizardObState {
  /** 'in_data' | 'file_first_year' | 'file_all' */
  mode?: 'in_data' | 'file_first_year' | 'file_all'
  /** 'combined' = single file with entity column; 'per_entity' = one file per entity. */
  entitySource?: EntitySource
  /** Combined mode: column in the file that identifies the legal entity. */
  entityCol?: string
  /** Combined mode: whether the entity column holds entity names ('names') or 2-digit prefixes ('prefixes'). Default 'names'. */
  entityColKind?: 'names' | 'prefixes'
  /** Per-entity upload slots keyed by entity code. */
  perEntity?: Record<string, {
    obFileId?: string
    obColumns?: string[]
    obSample?: Record<string, unknown>[]
    obProfile?: { account_col?: string; amount_col?: string; fiscal_year_col?: string }
  }>
  /** Single-file (combined / back-compat) fields. */
  obFileId?: string
  /** Detected columns from the uploaded OB file */
  obColumns?: string[]
  /** Sample rows from the uploaded OB file */
  obSample?: Record<string, unknown>[]
  /** Minimal column profile: account → column, amount → column, fiscal_year → column */
  obProfile?: { account_col?: string; amount_col?: string; fiscal_year_col?: string }
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

export interface PartnerSideState {
  /** 'combined' = single file with entity column; 'per_entity' = one file per entity. */
  entitySource?: EntitySource
  /** Per-entity upload slots keyed by entity code. */
  perEntity?: Record<string, {
    fileId?: string
    columns?: string[]
    sample?: Record<string, unknown>[]
    profile?: PartnerMappingProfile
  }>
  /** Single-file (combined / back-compat) fields. */
  fileId?: string
  /** Detected columns from the uploaded partner master file */
  columns?: string[]
  /** Sample rows from the uploaded partner master file */
  sample?: Record<string, unknown>[]
  /** Full join-key + name/address mapping */
  profile?: PartnerMappingProfile
}

export interface WizardPartnerState {
  sides: Record<'customer' | 'supplier', PartnerSideState>
}

// ---------------------------------------------------------------------------
// FTE / Additional information state
// ---------------------------------------------------------------------------

/** One upload slot — one entity × one FY. Multiple FYs sharing a file_id are separate entries. */
export interface WizardFteUpload {
  entity_index: number
  entity_name: string
  fy_label: string    // e.g. "FY2023" — must contain a year
  file_ids: string[]
}

export interface WizardFteState {
  /** true once the user has provided at least one upload */
  provided: boolean
  /** Wizard-minted ephemeral session_id (crypto.randomUUID() on first render) */
  sessionId: string
  /** 'consolidated' = one file for all entities; 'per_entity' = one file per entity */
  viewMode: 'consolidated' | 'per_entity'
  /** upload_mode forwarded to the backend script */
  uploadMode: string
  /** Flat list: one entry per entity × FY slot (expanded at upload time) */
  uploads: WizardFteUpload[]
  /** file_id used for FteColumnMapper / FteDimensionPicker preview */
  previewFileId: string
  /** FTE tenure mapping payload from FteColumnMapper (mode='fte') */
  fteMapping: Record<string, unknown>
  /** Tenure mode: 'months_col' | 'entry_exit_dates' */
  tenureMode: string
  /** Payroll mapping payload from FteColumnMapper (mode='payroll') */
  payrollMapping: Record<string, unknown>
  /** Payroll mode: 'sum_components' | 'total_col' | 'monthly_col' */
  payrollMode: string
  /** Breakdown dimensions from FteDimensionPicker (max 3) */
  dimensions: Array<{ source_col: string; output_label: string }>
  /** Preset metric keys — see FTE_PRESET_METRIC_OPTIONS */
  presetMetrics: string[]
  /** Custom output columns (source_col → output_label) */
  customMetrics: Array<{ source_col: string; output_label: string }>
  /** PEX view mode: 'consolidated' | 'per_entity' */
  pexViewMode: 'consolidated' | 'per_entity'
  /** Flat cell values: key = "entity{idx+1}_FY{yyyy}", value = EURk */
  pexValues: Record<string, number>
}

// WizardAnlagenState and WizardOposState are defined in and imported from their step components.
// Re-export them so external consumers (e.g. tests) can import from this file as before.
export type { WizardAnlagenUpload, WizardAnlagenState } from '../components/ingest/AnlagenStep'
export type { WizardOposUpload, WizardOposSideState, WizardOposState } from '../components/ingest/OposStep'

export interface WizardState {
  projectName: string
  fyEndMonth: number          // 1–12; UI value (converted to fy_start_month at Finish)
  entities: Array<{ code: string; prefix: string; name: string }>
  gl: WizardGlState
  coa: WizardCoaState
  ob: WizardObState
  partner: WizardPartnerState
  fte: WizardFteState
  /** Which additional-information datasets the user has opted into (all default false). */
  additionalDatasets: { fte: boolean; anlagen: boolean; opos: boolean }
  /** DRAFT provisioning state for Anlagenregister (fixed-asset register). */
  anlagen: WizardAnlagenState
  /** DRAFT provisioning state for OPOS (open-items lists). */
  opos: WizardOposState
}

function defaultState(): WizardState {
  return {
    projectName: '',
    fyEndMonth: 12,
    entities: [{ code: '', prefix: '', name: '' }],
    gl: { years: [], entities: [defaultGlEntityState()], formatGroups: [] },
    coa: { groups: [], accountMappingMode: 'library', assigned: false },
    ob: {},
    partner: { sides: { customer: {}, supplier: {} } },
    fte: {
      provided: false,
      sessionId: crypto.randomUUID(),
      viewMode: 'consolidated',
      uploadMode: 'per_fy_grid',
      uploads: [],
      previewFileId: '',
      fteMapping: {},
      tenureMode: 'months_col',
      payrollMapping: {},
      payrollMode: 'sum_components',
      dimensions: [],
      presetMetrics: ['fte', 'payroll', 'avg_cost_per_fte'],
      customMetrics: [],
      pexViewMode: 'consolidated',
      pexValues: {},
    },
    additionalDatasets: { fte: false, anlagen: false, opos: false },
    anlagen: {
      provided: false,
      viewMode: 'combined',
      uploads: [],
      columnMap: {},
      dimensions: {},
    },
    opos: {
      provided: false,
      debitor:  { viewMode: 'combined', uploads: [], columnMap: {} },
      kreditor: { viewMode: 'combined', uploads: [], columnMap: {} },
    },
  }
}

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

type WizardAction =
  | { type: 'SET_NAME'; value: string }
  | { type: 'SET_FY_END_MONTH'; value: number }
  | { type: 'SET_ENTITIES'; entities: WizardState['entities'] }
  // GL — new entity-based actions
  | { type: 'SET_GL_YEARS'; years: number[] }
  | { type: 'PATCH_GL_ENTITY'; index: number; patch: Partial<GlEntityState> }
  | { type: 'ADD_GL_ENTITY' }
  | { type: 'REMOVE_GL_ENTITY'; index: number }
  | { type: 'CREATE_GL_GROUP'; fromIndex: number; headers: string[]; mapping: Record<string, string> }
  | { type: 'ASSIGN_GL_GROUP'; groupId: string; memberIndices: number[] }
  | { type: 'PATCH_GL_GROUP'; groupId: string; patch: Partial<GlFormatGroup> }
  | { type: 'PATCH_GL_ENTITY_MULTI'; patches: Array<{ index: number; patch: Partial<GlEntityState> }> }
  /**
   * Replaces all format groups with ones derived from GlFormatAssignmentStep's output.
   * assignment: Record<entityIndex, formatKey> — entities sharing a key share a group.
   */
  | { type: 'APPLY_GL_FORMAT_ASSIGNMENTS'; assignment: Record<number, string> }
  /**
   * Clears all combined-file state from every entity and empties formatGroups.
   * Dispatched when the user clicks Back from assign or configure sub-phase,
   * so that glSubPhase falls back to 'collect' and the user can re-upload or
   * add/remove entities before combining again.
   */
  | { type: 'RESET_GL_COMBINE' }
  // CoA
  | { type: 'PATCH_COA'; patch: Partial<WizardCoaState> }
  /**
   * Rebuilds coa.groups from the CoaMappingAssignmentStep output.
   * assignment: Record<entityCode, groupKey> — entities sharing a key form one group.
   * Preserves method/libraryVariant/bs/pl from any existing group whose id matches the key.
   */
  | { type: 'APPLY_COA_ASSIGNMENTS'; assignment: Record<string, string>; explicit?: boolean }
  /** Patch scalar fields (method, libraryVariant, …) of one CoA mapping group by id. */
  | { type: 'PATCH_COA_GROUP'; id: string; patch: Partial<CoaMappingGroup> }
  /** Patch the bs or pl upload slot of a CoA mapping group. */
  | { type: 'PATCH_COA_SLOT'; id: string; statement: 'bs' | 'pl'; patch: Partial<CoaUploadSlot> }
  /**
   * Mark the legacy-migration check as done so it never fires again this session.
   * Dispatched by StepChartOfAccounts after the first migration check with non-empty groups.
   */
  | { type: 'SET_COA_MIGRATION_CHECKED' }
  | { type: 'PATCH_OB'; patch: Partial<WizardObState> }
  | { type: 'PATCH_PARTNER'; side: 'customer' | 'supplier'; patch: Partial<PartnerSideState> }
  | { type: 'PATCH_FTE'; patch: Partial<WizardFteState> }
  | { type: 'PATCH_ANLAGEN'; patch: Partial<WizardAnlagenState> }
  | { type: 'PATCH_OPOS'; patch: Partial<WizardOposState> }
  | { type: 'TOGGLE_DATASET'; dataset: 'fte' | 'anlagen' | 'opos'; value: boolean }
  | { type: 'PREFILL'; partial: Partial<WizardState> }

export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'SET_NAME':         return { ...state, projectName: action.value }
    case 'SET_FY_END_MONTH': return { ...state, fyEndMonth: action.value }
    case 'SET_ENTITIES':     return { ...state, entities: action.entities }

    case 'SET_GL_YEARS': {
      const sorted = [...new Set(action.years)].sort((a, b) => a - b)
      return { ...state, gl: { ...state.gl, years: sorted } }
    }
    case 'PATCH_GL_ENTITY': {
      const entities = state.gl.entities.map((e, i) =>
        i === action.index ? { ...e, ...action.patch } : e
      )
      return { ...state, gl: { ...state.gl, entities } }
    }
    case 'ADD_GL_ENTITY':
      return { ...state, gl: { ...state.gl, entities: [...state.gl.entities, defaultGlEntityState()] } }
    case 'REMOVE_GL_ENTITY': {
      if (state.gl.entities.length <= 1) return state
      const entities = state.gl.entities.filter((_, i) => i !== action.index)
      const formatGroups = reindexGroupsAfterRemove(action.index, state.gl.formatGroups)
      return { ...state, gl: { ...state.gl, entities, formatGroups } }
    }
    case 'CREATE_GL_GROUP': {
      const newGroup = createGroup(action.fromIndex, action.headers, action.mapping, state.gl.formatGroups)
      const entities = state.gl.entities.map((e, i) =>
        i === action.fromIndex ? { ...e, formatGroupId: newGroup.id } : e
      )
      return { ...state, gl: { ...state.gl, formatGroups: [...state.gl.formatGroups, newGroup], entities } }
    }
    case 'ASSIGN_GL_GROUP': {
      // Add new member indices to the group and update their formatGroupId
      const formatGroups = state.gl.formatGroups.map(g =>
        g.id === action.groupId
          ? { ...g, memberIndices: [...new Set([...g.memberIndices, ...action.memberIndices])] }
          : g
      )
      const entities = state.gl.entities.map((e, i) =>
        action.memberIndices.includes(i)
          ? { ...e, formatGroupId: action.groupId, validationOk: undefined, assembledProfile: undefined }
          : e
      )
      return { ...state, gl: { ...state.gl, formatGroups, entities } }
    }
    case 'PATCH_GL_GROUP': {
      const formatGroups = state.gl.formatGroups.map(g =>
        g.id === action.groupId ? { ...g, ...action.patch } : g
      )
      const entities = state.gl.entities.map(e =>
        e.formatGroupId === action.groupId
          ? { ...e, validationOk: undefined, assembledProfile: undefined }
          : e
      )
      return { ...state, gl: { ...state.gl, formatGroups, entities } }
    }
    case 'PATCH_GL_ENTITY_MULTI': {
      let entities = state.gl.entities
      for (const { index, patch } of action.patches) {
        entities = entities.map((e, i) => i === index ? { ...e, ...patch } : e)
      }
      return { ...state, gl: { ...state.gl, entities } }
    }
    case 'APPLY_GL_FORMAT_ASSIGNMENTS': {
      const { groups, entityPatches } = buildGroupsFromAssignments(action.assignment, state.gl.entities)
      let entities = state.gl.entities
      for (const { index, patch } of entityPatches) {
        entities = entities.map((e, i) => i === index ? { ...e, ...patch } : e)
      }
      return { ...state, gl: { ...state.gl, formatGroups: groups, entities } }
    }
    case 'RESET_GL_COMBINE': {
      // Strip all combine-derived state from every entity; clear format groups.
      // glSubPhase re-derives to 'collect' since allCombined becomes false.
      const entities = state.gl.entities.map(e => ({
        ...e,
        combinedFileId: undefined,
        combinedColumns: undefined,
        combinedSample: undefined,
        combinedDialect: undefined,
        combinedSuggestedHeaders: undefined,
        headersConfirmed: undefined,
        combinedColumnWarning: undefined,
        formatGroupId: undefined,
        validationOk: undefined,
        assembledProfile: undefined,
        entityAssignments: undefined,
      }))
      return { ...state, gl: { ...state.gl, entities, formatGroups: [] } }
    }

    case 'PATCH_COA':
      return { ...state, coa: { ...state.coa, ...action.patch } }
    case 'APPLY_COA_ASSIGNMENTS': {
      // Collect distinct group keys in the order they first appear
      const keyOrder: string[] = []
      for (const key of Object.values(action.assignment)) {
        if (!keyOrder.includes(key)) keyOrder.push(key)
      }
      // Preserve method/libraryVariant/bs/pl from any existing group whose id matches the key
      const existingById = new Map(state.coa.groups.map(g => [g.id, g]))
      const groups: CoaMappingGroup[] = keyOrder.map((key, idx) => {
        const memberEntityCodes = Object.entries(action.assignment)
          .filter(([, k]) => k === key)
          .map(([code]) => code)
        const existing = existingById.get(key)
        return {
          id: key,
          label: `Group ${String.fromCharCode(65 + idx)}`,
          memberEntityCodes,
          method: existing?.method,
          // When the user explicitly confirmed the assignment/method, mark all created groups
          // as methodExplicit so migrateLegacyCoaGroups won't fold them. Otherwise preserve
          // whatever flag the existing group already carried (undefined for auto-created groups).
          methodExplicit: action.explicit ? true : existing?.methodExplicit,
          libraryVariant: existing?.libraryVariant,
          bs: existing?.bs,
          pl: existing?.pl,
        }
      })
      return { ...state, coa: { ...state.coa, groups, assigned: true } }
    }
    case 'SET_COA_MIGRATION_CHECKED':
      return { ...state, coa: { ...state.coa, migrationChecked: true } }
    case 'PATCH_COA_GROUP': {
      const groups = state.coa.groups.map(g =>
        g.id === action.id ? { ...g, ...action.patch } : g
      )
      return { ...state, coa: { ...state.coa, groups } }
    }
    case 'PATCH_COA_SLOT': {
      const groups = state.coa.groups.map(g =>
        g.id === action.id
          ? { ...g, [action.statement]: { ...(g[action.statement] ?? {}), ...action.patch } }
          : g
      )
      return { ...state, coa: { ...state.coa, groups } }
    }
    case 'PATCH_OB':         return { ...state, ob: { ...state.ob, ...action.patch } }
    case 'PATCH_PARTNER':
      return { ...state, partner: { ...state.partner, sides: { ...state.partner.sides, [action.side]: { ...state.partner.sides[action.side], ...action.patch } } } }
    case 'PATCH_FTE':
      return { ...state, fte: { ...state.fte, ...action.patch } }
    case 'PATCH_ANLAGEN':
      return { ...state, anlagen: { ...state.anlagen, ...action.patch } }
    case 'PATCH_OPOS': {
      const { debitor, kreditor, ...rest } = action.patch
      return {
        ...state,
        opos: {
          ...state.opos,
          ...rest,
          ...(debitor  !== undefined ? { debitor:  { ...state.opos.debitor,  ...debitor  } } : {}),
          ...(kreditor !== undefined ? { kreditor: { ...state.opos.kreditor, ...kreditor } } : {}),
        },
      }
    }
    case 'TOGGLE_DATASET': {
      // When a dataset is toggled off, clear its upload/mapping state so that submit
      // is skipped cleanly and isNextDisabled() is not affected.
      const nextFte =
        action.dataset === 'fte' && !action.value
          ? { ...state.fte, uploads: [], previewFileId: '', provided: false }
          : state.fte
      const nextAnlagen =
        action.dataset === 'anlagen' && !action.value
          ? { provided: false, viewMode: 'combined' as const, uploads: [], columnMap: {}, dimensions: {} }
          : state.anlagen
      const nextOpos =
        action.dataset === 'opos' && !action.value
          ? {
              provided: false,
              debitor:  { viewMode: 'combined' as const, uploads: [], columnMap: {} },
              kreditor: { viewMode: 'combined' as const, uploads: [], columnMap: {} },
            }
          : state.opos
      return {
        ...state,
        additionalDatasets: { ...state.additionalDatasets, [action.dataset]: action.value },
        fte: nextFte,
        anlagen: nextAnlagen,
        opos: nextOpos,
      }
    }
    case 'PREFILL':          return { ...state, ...action.partial }
    default:                 return state
  }
}

// ---------------------------------------------------------------------------
// Entity-sync helpers — exported for unit-testing
// ---------------------------------------------------------------------------

/**
 * Derive the canonical project entity list from the GL entity cards.
 * prefix === code === entityCode so that CoA key joins line up with GL keys.
 * Returns only entries where entityCode is non-empty after trimming.
 */
export function deriveProjectEntities(
  glEntities: ReadonlyArray<Pick<GlEntityState, 'entityCode' | 'entityLabel'>>
): Array<{ code: string; prefix: string; name: string }> {
  return glEntities
    .map(e => ({ code: e.entityCode.trim(), entityLabel: e.entityLabel }))
    .filter(e => e.code.length > 0)
    .map(e => ({ code: e.code, prefix: e.code, name: (e.entityLabel?.trim() || e.code) }))
}

/**
 * Structural equality for the project entity list.
 * Returns true only when length and every { code, prefix, name } triplet match.
 */
export function projectEntitiesEqual(
  a: ReadonlyArray<{ code: string; prefix: string; name: string }>,
  b: ReadonlyArray<{ code: string; prefix: string; name: string }>
): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i].code !== b[i].code || a[i].prefix !== b[i].prefix || a[i].name !== b[i].name) {
      return false
    }
  }
  return true
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
  'Project name',           // 0
  'Fiscal year',            // 1
  'GL bookings',            // 2
  'Chart of accounts',      // 3
  'Opening balances',       // 4
  'Partner master',         // 5
  'Additional information', // 6 — optional FTE/personnel-cost provisioning
  'Statement structure',    // 7 — unknown CoA position classification
  'Review & Finish',        // 8
] as const

/** Preset metric options for FTE Development — exact string values from rasa/actions/fte_flow.py. */
const FTE_PRESET_METRIC_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'fte',              label: 'Average FTEs # (whole numbers)' },
  { value: 'payroll',          label: 'Payroll accounting (EURk)' },
  { value: 'avg_cost_per_fte', label: 'Average cost per FTE (EURk)' },
]

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
            placeholder="your project"
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

        <InfoBox>
          This setting is saved as part of your project configuration and applies to every data
          pipeline run. It can be changed later but will require a full rebuild to take effect.
        </InfoBox>
      </div>
    </StepCard>
  )
}

// GlYearSlot and GlEntityCard are now in ../components/ingest/GlEntityCard.tsx

// ---------------------------------------------------------------------------
// GlYearChipPicker — clickable year chips with "Prior years" expansion
// ---------------------------------------------------------------------------

const DEFAULT_CHIP_TOP = 2026
const DEFAULT_CHIP_BOTTOM = 2020
const PRIOR_BLOCK_SIZE = 5

/**
 * Returns the sorted list of available calendar years given how many prior
 * blocks have been revealed. Default range is 2020–2026; each prior block
 * prepends 5 more years going back.
 */
function availableChipYears(priorBlocks: number): number[] {
  const bottom = DEFAULT_CHIP_BOTTOM - priorBlocks * PRIOR_BLOCK_SIZE
  const years: number[] = []
  for (let y = bottom; y <= DEFAULT_CHIP_TOP; y++) years.push(y)
  return years
}

function GlYearChipPicker({
  selected,
  fyEndMonth,
  onToggle,
  readOnly = false,
}: {
  selected: number[]
  fyEndMonth: number
  onToggle: (year: number) => void
  readOnly?: boolean
}) {
  const [priorBlocks, setPriorBlocks] = useState(0)
  const chips = availableChipYears(priorBlocks)
  const selectedSet = new Set(selected)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {chips.map(y => {
          const isSelected = selectedSet.has(y)
          return (
            <button
              key={y}
              type="button"
              onClick={() => onToggle(y)}
              disabled={readOnly}
              className={`rounded-full px-3 py-1 text-sm font-semibold border transition focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-blue-500 ${
                isSelected
                  ? 'text-white border-transparent'
                  : 'bg-white border-slate-300 text-slate-700 hover:border-blue-400 hover:text-blue-700'
              } ${readOnly ? 'cursor-default' : ''}`}
              style={isSelected ? { backgroundColor: '#1E3A5F', borderColor: '#1E3A5F' } : undefined}
              aria-pressed={isSelected}
              aria-label={glFiscalYearLabel(y, fyEndMonth)}
            >
              {glFiscalYearLabel(y, fyEndMonth)}
            </button>
          )
        })}
      </div>
      {!readOnly && (
        <button
          type="button"
          onClick={() => setPriorBlocks(b => b + 1)}
          className="text-xs text-slate-500 underline underline-offset-2 hover:text-blue-600 transition"
        >
          + Prior years ({DEFAULT_CHIP_BOTTOM - priorBlocks * PRIOR_BLOCK_SIZE - PRIOR_BLOCK_SIZE}–{DEFAULT_CHIP_BOTTOM - priorBlocks * PRIOR_BLOCK_SIZE - 1})
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 2 — GL bookings: years-first selector + entity cards
// ---------------------------------------------------------------------------

function StepGlBookings({
  gl,
  fyEndMonth,
  dispatch,
  batchCombining,
  batchCombineErrors,
}: {
  gl: WizardGlState
  fyEndMonth: number
  dispatch: Dispatch<WizardAction>
  /** True while the parent's batchCombineGlEntities is running. */
  batchCombining: boolean
  /** Per-entity errors from the last batchCombineGlEntities call. */
  batchCombineErrors: Array<{ index: number; entityCode: string; error: string }>
}) {
  function toggleYear(y: number) {
    const next = gl.years.includes(y)
      ? gl.years.filter(v => v !== y)
      : [...new Set([...gl.years, y])].sort((a, b) => a - b)
    dispatch({ type: 'SET_GL_YEARS', years: next })
  }

  const validatedCount = gl.entities.filter(e => e.validationOk !== undefined).length

  // ── Sub-phase: derived from gl state so re-uploads drop back automatically ──
  // 'collect'  — upload + combine all entities (no groups yet)
  // 'assign'   — GlFormatAssignmentStep (multi-entity only, all combined)
  // 'configure' — GlGroupConfigPanel + per-entity header confirm + validation
  const allCombined =
    gl.years.length > 0 &&
    gl.entities.length > 0 &&
    gl.entities.every(e => !!e.combinedFileId)

  const allAssigned = gl.entities.every(e => !!e.formatGroupId)

  const glSubPhase: 'collect' | 'assign' | 'configure' = (() => {
    if (!allCombined) return 'collect'
    if (gl.entities.length === 1 || allAssigned) return 'configure'
    return 'assign'
  })()

  // Single-entity: auto-create one singleton group and skip 'assign'
  useEffect(() => {
    if (allCombined && gl.entities.length === 1 && !gl.entities[0].formatGroupId) {
      dispatch({ type: 'APPLY_GL_FORMAT_ASSIGNMENTS', assignment: { 0: 'grp-0' } })
    }
  }, [allCombined, gl.entities, dispatch])

  // ── Async onPatchGroup: dispatches PATCH_GL_GROUP then propagates headers ──
  // Called by GlEntityCard when representative confirms headers (external mode).
  const handlePatchGroup = useCallback(async (groupId: string, patch: Partial<GlFormatGroup>) => {
    dispatch({ type: 'PATCH_GL_GROUP', groupId, patch })
    if (patch.headers && patch.headers.length > 0) {
      const group = gl.formatGroups.find(g => g.id === groupId)
      if (group) {
        const updatedGroup = { ...group, ...patch }
        const nonRepIndices = group.memberIndices.filter(i => i !== group.representativeIndex)
        if (nonRepIndices.length > 0) {
          const { patches } = await applyGroupHeadersToMembers(updatedGroup, nonRepIndices, gl.entities)
          if (patches.length > 0) dispatch({ type: 'PATCH_GL_ENTITY_MULTI', patches })
        }
      }
    }
  }, [gl.formatGroups, gl.entities, dispatch])

  // ── Group-level header confirm: called from GlGroupConfigPanel (wizard mode) ──
  // Applies headers to the representative's combined file, derives the mapping,
  // patches both the representative entity and the group, then propagates headers
  // positionally to all non-representative members.
  const handleConfirmGroupHeaders = useCallback(async (groupId: string, headers: string[]) => {
    // Snapshot entities at call-time so the rep lookup and applyGroupHeadersToMembers
    // both read the same consistent array, regardless of the two dispatches that follow.
    const entitiesAtStart = gl.entities
    const group = gl.formatGroups.find(g => g.id === groupId)
    if (!group) return
    const rep = entitiesAtStart[group.representativeIndex]
    if (!rep?.combinedFileId) return

    // Apply header labels to the representative's staged file
    const result = await applyHeaders(rep.combinedFileId, headers)
    const mapping = suggestGlMapping(result.columns.filter(c => c !== 'fiscal_year'))

    // Patch the representative entity
    dispatch({
      type: 'PATCH_GL_ENTITY',
      index: group.representativeIndex,
      patch: {
        combinedFileId: result.file_id,
        combinedColumns: result.columns,
        combinedSample: result.sample,
        combinedSuggestedHeaders: undefined,
        headersConfirmed: true,
        validationOk: undefined,
        assembledProfile: undefined,
        entityAssignments: undefined,
      },
    })

    // Patch the group with confirmed headers + derived mapping
    dispatch({
      type: 'PATCH_GL_GROUP',
      groupId,
      patch: {
        headers: result.columns,
        mapping,
        columnCount: result.columns.length,
        headersConfirmed: true,
      },
    })

    // Propagate headers positionally to non-representative members.
    // entitiesAtStart is used intentionally: the two dispatches above only mutate
    // the rep entity and the group; non-rep members' combinedFileId/combinedColumns
    // are unchanged, so reading them from the pre-dispatch snapshot is correct.
    const updatedGroup: GlFormatGroup = {
      ...group,
      headers: result.columns,
      columnCount: result.columns.length,
      headersConfirmed: true,
    }
    const nonRepIndices = group.memberIndices.filter(i => i !== group.representativeIndex)
    if (nonRepIndices.length > 0) {
      const { patches } = await applyGroupHeadersToMembers(updatedGroup, nonRepIndices, entitiesAtStart)
      if (patches.length > 0) dispatch({ type: 'PATCH_GL_ENTITY_MULTI', patches })
    }
  }, [gl.formatGroups, gl.entities, dispatch])

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="rounded-xl border border-slate-200 bg-white px-6 pt-5 pb-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Upload GL bookings</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          First select the fiscal years you are loading. Then upload one file per year for each
          entity. The files are concatenated with a <span className="font-mono text-xs bg-slate-100 rounded px-1">fiscal_year</span> column
          and you map columns and validate once per entity.
          Nothing is written to the database until the final Finish step.
        </p>
        {gl.entities.length > 1 && (
          <p className="mt-2 text-xs text-blue-700">
            {validatedCount} of {gl.entities.length} entities validated.
            {glSubPhase === 'collect' && ' — combine all entities to proceed.'}
            {glSubPhase === 'assign' && ' — assign formats to proceed.'}
          </p>
        )}
      </div>

      {/* ── COLLECT phase: year picker + entity upload cards ── */}
      {(glSubPhase === 'collect' || glSubPhase === 'configure') && (
        <>
          {/* Years selector */}
          <div className="rounded-xl border border-slate-200 bg-white px-6 py-5 shadow-sm space-y-4">
            <div>
              <h3 className="text-base font-semibold text-slate-900">Which fiscal years are you loading?</h3>
              <p className="mt-0.5 text-xs text-slate-500">
                Click years to select them. Each entity card will show one upload slot per year.
              </p>
            </div>

            <GlYearChipPicker
              selected={gl.years}
              fyEndMonth={fyEndMonth}
              onToggle={toggleYear}
              readOnly={glSubPhase !== 'collect'}
            />

            {gl.years.length === 0 && (
              <WarnBox>Select at least one fiscal year before uploading GL files.</WarnBox>
            )}
          </div>

          {/* Format group config panels — rendered in configure phase only */}
          {glSubPhase === 'configure' && gl.formatGroups.map(group => (
            <GlGroupConfigPanel
              key={group.id}
              group={group}
              entities={gl.entities}
              onPatchGroup={(groupId, patch) => { void handlePatchGroup(groupId, patch) }}
              onConfirmGroupHeaders={handleConfirmGroupHeaders}
              // F-3: consolidated validation — stage='gl' prevents R1-R4/M1 regression
              validationStage="gl"
              onMemberValidated={(entityIndex, result, assembledProfile, excludedLineIds) => {
                dispatch({
                  type: 'PATCH_GL_ENTITY',
                  index: entityIndex,
                  patch: {
                    validationOk: result.summary.passed,
                    assembledProfile,
                    excludedLineIds,
                  },
                })
              }}
            />
          ))}

          {/* Entity cards — external grouping mode, footer combine mode, GL validation stage */}
          {gl.entities.map((entity, index) => (
            <GlEntityCard
              key={index}
              entity={entity}
              index={index}
              total={gl.entities.length}
              years={gl.years}
              entityMode="create"
              fyEndMonth={fyEndMonth}
              groupingMode="external"
              combineMode="footer"
              validationStage="gl"
              onPatch={p => dispatch({ type: 'PATCH_GL_ENTITY', index, patch: p })}
              onRemove={
                glSubPhase === 'collect' && gl.entities.length > 1
                  ? () => dispatch({ type: 'REMOVE_GL_ENTITY', index })
                  : undefined
              }
              group={gl.formatGroups.find(g => g.id === entity.formatGroupId)}
              groups={gl.formatGroups}
              entities={gl.entities}
              onCreateGroup={(fromIndex, headers, mapping) =>
                dispatch({ type: 'CREATE_GL_GROUP', fromIndex, headers, mapping })
              }
              onAssignGroup={async (groupId, selectedIndices) => {
                const group = gl.formatGroups.find(g => g.id === groupId)
                if (!group) return
                const { patches } = await applyGroupHeadersToMembers(
                  group,
                  selectedIndices,
                  gl.entities,
                )
                dispatch({ type: 'ASSIGN_GL_GROUP', groupId, memberIndices: selectedIndices })
                if (patches.length > 0) dispatch({ type: 'PATCH_GL_ENTITY_MULTI', patches })
              }}
              onPatchGroup={(groupId, patch) => { void handlePatchGroup(groupId, patch) }}
            />
          ))}

          {/* Add another entity — only in collect phase */}
          {glSubPhase === 'collect' && (
            <button
              type="button"
              onClick={() => dispatch({ type: 'ADD_GL_ENTITY' })}
              className="flex items-center gap-2 rounded-xl border-2 border-dashed border-slate-300 bg-white px-5 py-3 text-sm font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 transition w-full justify-center"
            >
              <span className="text-lg leading-none">+</span>
              Add another entity
            </button>
          )}

          {/* Lock hint — shown only in collect phase, near the footer */}
          {glSubPhase === 'collect' && (
            <p className="text-xs text-slate-500 text-center">
              Add or remove entities now — combining locks the list. Use Back to make changes after combining.
            </p>
          )}

          {/* Per-entity combine errors from the last batch combine attempt */}
          {batchCombineErrors.length > 0 && glSubPhase === 'collect' && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 space-y-1.5">
              <p className="text-sm font-semibold text-red-800">
                Some entities failed to combine. Fix the issues above and try again.
              </p>
              {batchCombineErrors.map(err => (
                <p key={err.index} className="text-xs text-red-700">
                  Entity {err.index + 1} ({err.entityCode || `Entity ${err.index + 1}`}): {err.error}
                </p>
              ))}
            </div>
          )}

          {/* Combining progress indicator */}
          {batchCombining && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
              Combining files for all entities — please wait…
            </div>
          )}

          {/* In configure phase: note that Back resets to uploads */}
          {glSubPhase === 'configure' && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
              Need to add or remove entities? Use the Back button below to return to the upload step.
              All combined files and format groups will be cleared so you can start fresh.
            </div>
          )}
        </>
      )}

      {/* ── ASSIGN phase: forced format-assignment screen (multi-entity only) ── */}
      {glSubPhase === 'assign' && (
        <>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
            Need to add or remove entities? Use the Back button below to return to the upload step.
            All combined files and format groups will be cleared so you can start fresh.
          </div>
          <GlFormatAssignmentStep
            entities={gl.entities}
            onConfirm={assignment => dispatch({ type: 'APPLY_GL_FORMAT_ASSIGNMENTS', assignment })}
          />
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

// ---------------------------------------------------------------------------
// CoaGroupCard — per-group mapping configuration card (BS + PL upload slots)
// ---------------------------------------------------------------------------

function CoaGroupCard({
  group,
  entities,
  dispatch,
}: {
  group: CoaMappingGroup
  entities: WizardState['entities']
  dispatch: Dispatch<WizardAction>
}) {
  const [expanded, setExpanded] = useState(true)

  // BS slot state
  const [bsUploading, setBsUploading] = useState(false)
  const [bsUploadError, setBsUploadError] = useState<string | null>(null)
  const [bsDownloading, setBsDownloading] = useState(false)
  const [bsDownloadError, setBsDownloadError] = useState<string | null>(null)
  const bsFileRef = useRef<HTMLInputElement>(null)
  const [bsMapping, setBsMapping] = useState<Record<string, string>>(group.bs?.mapping ?? {})

  // PL slot state
  const [plUploading, setPlUploading] = useState(false)
  const [plUploadError, setPlUploadError] = useState<string | null>(null)
  const [plDownloading, setPlDownloading] = useState(false)
  const [plDownloadError, setPlDownloadError] = useState<string | null>(null)
  const plFileRef = useRef<HTMLInputElement>(null)
  const [plMapping, setPlMapping] = useState<Record<string, string>>(group.pl?.mapping ?? {})

  const patchGroup = useCallback(
    (patch: Partial<CoaMappingGroup>) => dispatch({ type: 'PATCH_COA_GROUP', id: group.id, patch }),
    [dispatch, group.id],
  )
  const patchSlot = useCallback(
    (stmt: 'bs' | 'pl', patch: Partial<CoaUploadSlot>) =>
      dispatch({ type: 'PATCH_COA_SLOT', id: group.id, statement: stmt, patch }),
    [dispatch, group.id],
  )

  async function handleDownload(stmt: 'bs' | 'pl') {
    if (stmt === 'bs') { setBsDownloading(true); setBsDownloadError(null) }
    else { setPlDownloading(true); setPlDownloadError(null) }
    try {
      await downloadCoaTemplate({ statement: stmt, library: group.libraryVariant })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Download failed'
      if (stmt === 'bs') setBsDownloadError(msg)
      else setPlDownloadError(msg)
    } finally {
      if (stmt === 'bs') setBsDownloading(false)
      else setPlDownloading(false)
    }
  }

  async function handleFileSelected(file: File, stmt: 'bs' | 'pl') {
    if (stmt === 'bs') { setBsUploading(true); setBsUploadError(null) }
    else { setPlUploading(true); setPlUploadError(null) }
    try {
      const res = await uploadFile(file)
      // Detect single-sheet master via sheet name
      const isMaster = stmt === 'bs'
        ? res.sheets.includes('Master_BS')
        : res.sheets.includes('Master_PL')
      patchSlot(stmt, {
        fileId: res.file_id,
        sheets: res.sheets,
        columns: res.columns,
        sample: res.sample,
        isMaster,
        mapping: undefined,
      })
      if (stmt === 'bs') setBsMapping({})
      else setPlMapping({})
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Upload failed'
      if (stmt === 'bs') setBsUploadError(msg)
      else setPlUploadError(msg)
    } finally {
      if (stmt === 'bs') setBsUploading(false)
      else setPlUploading(false)
    }
  }

  function handleMappingChange(stmt: 'bs' | 'pl', m: Record<string, string>) {
    if (stmt === 'bs') { setBsMapping(m); patchSlot('bs', { mapping: m }) }
    else { setPlMapping(m); patchSlot('pl', { mapping: m }) }
  }

  const method = group.method
  const bsMissing = group.bs?.columns && !group.bs?.isMaster
    ? missingRequiredAccountFields(bsMapping, coaGenericFields('bs')) : []
  const plMissing = group.pl?.columns && !group.pl?.isMaster
    ? missingRequiredAccountFields(plMapping, coaGenericFields('pl')) : []

  const memberNames = group.memberEntityCodes.map(code => {
    const e = entities.find(en => en.code === code)
    return e ? (e.name || e.prefix || code || '—') : (code || '—')
  })

  const isConfigured = !method || method === 'library'
    || (method === 'upload' && (group.bs?.fileId || group.pl?.fileId))
  const badge = isConfigured
    ? <span className="text-emerald-600 font-medium text-xs">Configured</span>
    : <span className="text-slate-400 text-xs">Not configured</span>

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Card header */}
      <div
        className="flex items-center justify-between px-5 py-3 cursor-pointer hover:bg-slate-50 transition"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-400 shrink-0">
            {group.label}
          </span>
          <span className="text-sm text-slate-500 truncate">{memberNames.join(', ')}</span>
          <span className="text-slate-300 shrink-0">·</span>
          {badge}
        </div>
        <span className="text-slate-400 text-xs shrink-0">{expanded ? '▲' : '▼'}</span>
      </div>

      {/* Expandable body */}
      {expanded && (
        <div className="border-t border-slate-100 px-5 pt-4 pb-5 space-y-5">
          {/* Member entities */}
          <div className="rounded-lg border border-slate-100 bg-slate-50 px-4 py-3">
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
              Entities in this group
            </p>
            <div className="flex flex-wrap gap-2">
              {group.memberEntityCodes.map(code => {
                const e = entities.find(en => en.code === code)
                return (
                  <span
                    key={code}
                    className="inline-flex items-center gap-1.5 rounded-full bg-slate-200 px-2.5 py-0.5 text-xs font-medium text-slate-700"
                  >
                    {e?.name || e?.prefix || code || '—'}
                    {e?.prefix && <span className="text-slate-400">({e.prefix})</span>}
                  </span>
                )
              })}
            </div>
          </div>

          {/* Method selection */}
          <div className="space-y-2">
            <p className="text-sm font-medium text-slate-700">Mapping method for this group</p>
            <label className={`flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
              !method || method === 'library'
                ? 'border-blue-400 bg-blue-50' : 'border-slate-200 bg-white hover:bg-slate-50'
            }`}>
              <input
                type="radio"
                name={`method-${group.id}`}
                value="library"
                checked={!method || method === 'library'}
                onChange={() => patchGroup({ method: 'library', methodExplicit: true })}
                className="mt-0.5 accent-blue-600"
              />
              <div>
                <p className="text-sm font-semibold text-slate-800">Finssentials library</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Auto-classify accounts using the built-in mapping library. No file upload required.
                </p>
              </div>
            </label>
            <label className={`flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
              method === 'upload'
                ? 'border-blue-400 bg-blue-50' : 'border-slate-200 bg-white hover:bg-slate-50'
            }`}>
              <input
                type="radio"
                name={`method-${group.id}`}
                value="upload"
                checked={method === 'upload'}
                onChange={() => patchGroup({ method: 'upload', methodExplicit: true })}
                className="mt-0.5 accent-blue-600"
              />
              <div>
                <p className="text-sm font-semibold text-slate-800">Upload client chart of accounts</p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Download the BS and PL templates, fill in account numbers and hierarchy,
                  then re-upload separately for Balance Sheet and Profit &amp; Loss.
                </p>
              </div>
            </label>
          </div>

          {/* Library variant sub-select */}
          {(!method || method === 'library') && (
            <div className="ml-7 space-y-3">
              {LIBRARY_VARIANTS.map(v => (
                <label
                  key={v.value}
                  className={`flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
                    (group.libraryVariant ?? 'skr03') === v.value
                      ? 'border-blue-400 bg-blue-50'
                      : 'border-slate-200 bg-white hover:bg-slate-50'
                  }`}
                >
                  <input
                    type="radio"
                    name={`variant-${group.id}`}
                    value={v.value}
                    checked={(group.libraryVariant ?? 'skr03') === v.value}
                    onChange={() => patchGroup({ libraryVariant: v.value })}
                    className="mt-0.5 accent-blue-600"
                  />
                  <div>
                    <p className="text-sm font-semibold text-slate-800">{v.label}</p>
                    <p className="text-xs text-slate-500 mt-0.5">{v.description}</p>
                  </div>
                </label>
              ))}
            </div>
          )}

          {/* Upload slots: BS + PL */}
          {method === 'upload' && (
            <div className="space-y-4">
              {/* Balance Sheet slot */}
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 space-y-3">
                <p className="text-sm font-semibold text-slate-700">Balance Sheet (BS)</p>
                <button
                  type="button"
                  onClick={() => void handleDownload('bs')}
                  disabled={bsDownloading}
                  className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition"
                >
                  {bsDownloading ? 'Downloading…' : 'Download BS template (.xlsx)'}
                </button>
                {bsDownloadError && <p className="text-xs text-red-600">{bsDownloadError}</p>}
                <div
                  onClick={() => bsFileRef.current?.click()}
                  onDragOver={e => e.preventDefault()}
                  onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) void handleFileSelected(f, 'bs') }}
                  className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-6 cursor-pointer transition ${
                    bsUploading ? 'border-blue-400 bg-blue-50'
                    : group.bs?.fileId ? 'border-emerald-300 bg-emerald-50'
                    : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
                  }`}
                >
                  <input ref={bsFileRef} type="file" accept=".xlsx,.xls,.csv,.txt" className="hidden"
                    onChange={e => { const f = e.target.files?.[0]; if (f) void handleFileSelected(f, 'bs') }} />
                  {bsUploading ? (
                    <p className="text-sm font-medium text-blue-600">Processing…</p>
                  ) : group.bs?.fileId ? (
                    <div className="text-center">
                      <p className="text-sm font-semibold text-emerald-700">BS file staged</p>
                      {group.bs.isMaster && (
                        <p className="text-xs font-medium text-emerald-600 mt-1">Master_BS detected — no mapping needed</p>
                      )}
                      <p className="text-xs text-slate-400 mt-1 italic">Click or drop to replace</p>
                    </div>
                  ) : (
                    <>
                      <p className="text-sm font-semibold text-slate-700">Drop BS file here or click to browse</p>
                      <p className="mt-1 text-xs text-slate-400">XLSX · XLS · CSV</p>
                    </>
                  )}
                </div>
                {bsUploadError && (
                  <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{bsUploadError}</div>
                )}
                {group.bs?.fileId && !group.bs.isMaster && group.bs.columns && group.bs.sample && (
                  <div className="space-y-2">
                    {bsMissing.length > 0 && (
                      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                        <span className="font-medium">Missing required fields:</span>{' '}{bsMissing.join(', ')}
                      </div>
                    )}
                    <AccountColumnMapper
                      key={group.bs.fileId}
                      sourceColumns={group.bs.columns}
                      sample={group.bs.sample}
                      mapping={bsMapping}
                      onChange={m => handleMappingChange('bs', m)}
                      fields={coaGenericFields('bs')}
                    />
                  </div>
                )}
              </div>

              {/* Profit & Loss slot */}
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-4 space-y-3">
                <p className="text-sm font-semibold text-slate-700">Profit &amp; Loss (PL)</p>
                <button
                  type="button"
                  onClick={() => void handleDownload('pl')}
                  disabled={plDownloading}
                  className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition"
                >
                  {plDownloading ? 'Downloading…' : 'Download PL template (.xlsx)'}
                </button>
                {plDownloadError && <p className="text-xs text-red-600">{plDownloadError}</p>}
                <div
                  onClick={() => plFileRef.current?.click()}
                  onDragOver={e => e.preventDefault()}
                  onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) void handleFileSelected(f, 'pl') }}
                  className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-6 cursor-pointer transition ${
                    plUploading ? 'border-blue-400 bg-blue-50'
                    : group.pl?.fileId ? 'border-emerald-300 bg-emerald-50'
                    : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
                  }`}
                >
                  <input ref={plFileRef} type="file" accept=".xlsx,.xls,.csv,.txt" className="hidden"
                    onChange={e => { const f = e.target.files?.[0]; if (f) void handleFileSelected(f, 'pl') }} />
                  {plUploading ? (
                    <p className="text-sm font-medium text-blue-600">Processing…</p>
                  ) : group.pl?.fileId ? (
                    <div className="text-center">
                      <p className="text-sm font-semibold text-emerald-700">PL file staged</p>
                      {group.pl.isMaster && (
                        <p className="text-xs font-medium text-emerald-600 mt-1">Master_PL detected — no mapping needed</p>
                      )}
                      <p className="text-xs text-slate-400 mt-1 italic">Click or drop to replace</p>
                    </div>
                  ) : (
                    <>
                      <p className="text-sm font-semibold text-slate-700">Drop PL file here or click to browse</p>
                      <p className="mt-1 text-xs text-slate-400">XLSX · XLS · CSV</p>
                    </>
                  )}
                </div>
                {plUploadError && (
                  <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{plUploadError}</div>
                )}
                {group.pl?.fileId && !group.pl.isMaster && group.pl.columns && group.pl.sample && (
                  <div className="space-y-2">
                    {plMissing.length > 0 && (
                      <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
                        <span className="font-medium">Missing required fields:</span>{' '}{plMissing.join(', ')}
                      </div>
                    )}
                    <AccountColumnMapper
                      key={group.pl.fileId}
                      sourceColumns={group.pl.columns}
                      sample={group.pl.sample}
                      mapping={plMapping}
                      onChange={m => handleMappingChange('pl', m)}
                      fields={coaGenericFields('pl')}
                    />
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StepChartOfAccounts({
  coa,
  gl,
  dispatch,
  entities,
}: {
  coa: WizardCoaState
  gl: WizardGlState
  dispatch: Dispatch<WizardAction>
  entities: WizardState['entities']
}) {
  // Auto-singleton: for single-entity projects skip the assignment UI and auto-create one group.
  // Mirrors the GL single-entity auto-group logic (~line 654 in StepGlBookings).
  useEffect(() => {
    if (entities.length === 1 && !coa.assigned) {
      const entityCode = entities[0].code
      // Default the group key via the shared helper (same logic as the multi-entity branch).
      const glEntity = gl.entities.find(e => e.entityCode === entityCode)
      const formatGroupIdByEntity: Record<string, string | undefined> = {
        [entityCode]: glEntity?.formatGroupId,
      }
      const assignment = buildInitialCoaAssignment([entityCode], formatGroupIdByEntity)
      dispatch({ type: 'APPLY_COA_ASSIGNMENTS', assignment })
    }
  }, [entities, coa.assigned, gl.entities, dispatch])

  // Auto-migrate legacy per-format CoA groups (Retry / resume path only).
  //
  // OLD buildInitialCoaAssignment keyed CoA groups by GL formatGroupId, so a two-entity
  // project with different column layouts landed in two separate CoA groups. When the user
  // uploaded ONE shared CoA file, only the first group got a fileId — the second group's
  // slot was never filled → entity 02 produced zero commitAccountMapping rows.
  //
  // GUARD: the migration must NOT fire during a legitimate deliberate multi-CoA upload
  // where the user intentionally has 2+ groups and is mid-upload (has uploaded to group A
  // but not yet to group B). To prevent that, we gate on two conditions:
  //   1. coa.assigned — the assignment step was already confirmed (resume/Retry path).
  //   2. !coa.migrationChecked — the check has not run yet this session.
  //
  // coa.migrationChecked is set to true (via SET_COA_MIGRATION_CHECKED) after the first
  // check against non-empty groups. Because it lives in the reducer — not in a local ref —
  // it survives component remounts (user navigating away from step 3 and back).
  //
  // Sequence:
  //   Resume / Retry with legacy groups → assigned=true, migrationChecked=false at mount
  //     → migration runs → collapses groups → SET_COA_MIGRATION_CHECKED
  //   New config: user confirms 2-group assignment (no files) → assigned=true,
  //     migrationChecked=false → migration returns null (no files) → SET_COA_MIGRATION_CHECKED
  //     → user uploads first file → migrationChecked=true → effect skips → NO premature merge.
  useEffect(() => {
    if (!coa.assigned || coa.migrationChecked) return
    const migration = migrateLegacyCoaGroups(coa.groups)
    if (migration !== null) {
      dispatch({ type: 'APPLY_COA_ASSIGNMENTS', assignment: migration })
    }
    // Mark checked once groups are non-empty. If groups haven't been created yet
    // (state still settling), hold off so we retry on the next change.
    if (coa.groups.length > 0) {
      dispatch({ type: 'SET_COA_MIGRATION_CHECKED' })
    }
  }, [coa.groups, coa.assigned, coa.migrationChecked, dispatch])

  // Build the initial assignment from current coa.groups (re-assignment) or from GL groups (first time).
  const initialAssignment: Record<string, string> = (() => {
    if (coa.groups.length > 0) {
      const result: Record<string, string> = {}
      entities.forEach(e => {
        const g = coa.groups.find(g => g.memberEntityCodes.includes(e.code))
        if (g) result[e.code] = g.id
      })
      // Entities NOT found in any existing group get a fallback key so they are never silently dropped.
      // Prefer the id of the single non-empty group when exactly one exists (pre-fills them into it),
      // otherwise fall back to the generic shared key so the user sees one merged group by default.
      const nonEmptyGroups = coa.groups.filter(g => g.memberEntityCodes.length > 0)
      const fallbackKey = nonEmptyGroups.length === 1 ? nonEmptyGroups[0].id : 'coa-shared'
      entities.forEach(e => {
        if (result[e.code] === undefined) result[e.code] = fallbackKey
      })
      return result
    }
    // First visit: default via shared helper (all entities share one group key).
    const formatGroupIdByEntity: Record<string, string | undefined> = {}
    entities.forEach(e => {
      const glEntity = gl.entities.find(ge => ge.entityCode === e.code)
      formatGroupIdByEntity[e.code] = glEntity?.formatGroupId
    })
    return buildInitialCoaAssignment(entities.map(e => e.code), formatGroupIdByEntity)
  })()

  // Show the assignment step for multi-entity projects that haven't confirmed yet,
  // OR whose stale coa.groups don't cover all current entities (coverage-completeness gate).
  const showAssignment =
    entities.length > 1 &&
    (!coa.assigned || !coaGroupsCoverAllEntities(coa.groups, entities.map(e => e.code)))

  function handleMappingModeChange(mode: AccountMappingMode) {
    dispatch({ type: 'PATCH_COA', patch: { accountMappingMode: mode } })
  }

  return (
    <StepCard
      title="Chart of accounts"
      subtitle="Configure how accounts are classified into the P&L and Balance Sheet hierarchy."
    >
      <div className="space-y-6">

        {/* ------------------------------------------------------------------ */}
        {/* Assignment step (multi-entity only, hides once assigned)            */}
        {/* ------------------------------------------------------------------ */}
        {showAssignment && (
          <>
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">
              Assign each entity to a CoA mapping group. Entities sharing the same account
              structure can share one mapping. Defaults are suggested from your GL format groups.
            </div>
            <CoaMappingAssignmentStep
              entities={entities}
              initialAssignment={initialAssignment}
              onConfirm={assignment =>
                dispatch({ type: 'APPLY_COA_ASSIGNMENTS', assignment, explicit: true })
              }
            />
          </>
        )}

        {/* ------------------------------------------------------------------ */}
        {/* Per-group configuration cards                                        */}
        {/* ------------------------------------------------------------------ */}
        {!showAssignment && coa.groups.length > 0 && (
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-200 bg-white px-6 pt-5 pb-4 shadow-sm">
              <h3 className="text-base font-semibold text-slate-900">Mapping group configuration</h3>
              <p className="mt-0.5 text-sm text-slate-500">
                Configure the account mapping method for each group. Nothing is written to the
                database until the Finish step.
              </p>
            </div>
            {coa.groups.map(group => (
              <CoaGroupCard
                key={group.id}
                group={group}
                entities={entities}
                dispatch={dispatch}
              />
            ))}
          </div>
        )}

        {/* ------------------------------------------------------------------ */}
        {/* Fallback mode for accounts not covered by any group mapping         */}
        {/* ------------------------------------------------------------------ */}
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-3">
          <p className="text-sm font-semibold text-slate-700">
            How should accounts NOT covered by your mapping be classified?
          </p>
          <div className="space-y-2">
            <label className={`flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
              coa.accountMappingMode === 'library'
                ? 'border-blue-400 bg-blue-50'
                : 'border-slate-200 bg-white hover:bg-slate-50'
            }`}>
              <input
                type="radio"
                name="accountMappingMode"
                value="library"
                checked={coa.accountMappingMode === 'library'}
                onChange={() => handleMappingModeChange('library')}
                className="mt-0.5 accent-blue-600"
              />
              <div>
                <p className="text-sm font-semibold text-slate-800">
                  Use the Finssentials library to fill them in (most-frequent)
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Accounts not in your mapping file are auto-classified using the built-in library.
                  Recommended for projects with standard German chart-of-accounts numbering (SKR03, GKR).
                </p>
              </div>
            </label>
            <label className={`flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
              coa.accountMappingMode === 'exclusive'
                ? 'border-amber-400 bg-amber-50'
                : 'border-slate-200 bg-white hover:bg-slate-50'
            }`}>
              <input
                type="radio"
                name="accountMappingMode"
                value="exclusive"
                checked={coa.accountMappingMode === 'exclusive'}
                onChange={() => handleMappingModeChange('exclusive')}
                className="mt-0.5 accent-blue-600"
              />
              <div>
                <p className="text-sm font-semibold text-slate-800">
                  Use ONLY the mapping I provide (leave the rest unmapped)
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  Accounts not present in any uploaded mapping file remain unclassified.
                  Use this when the client has a fully custom chart of accounts.
                </p>
              </div>
            </label>
          </div>
          {coa.accountMappingMode === 'exclusive' && (
            <WarnBox>
              Exclusive mode: accounts not covered by your mapping files will appear as "unmapped"
              in P&amp;L and Balance Sheet views until reclassified in the CoA Editor.
            </WarnBox>
          )}
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* CoA Editor hint                                                     */}
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
            ) lets you remap individual accounts, adjust hierarchy nodes (L2 / L3 / L4),
            and re-run classification at any time.
          </p>
          <p className="text-xs text-blue-700">
            Because this wizard uses a <strong>collect-then-commit</strong> model,
            the classified account dimension table (
            <span className="font-mono text-xs bg-white border border-blue-200 rounded px-1">
              dim_gl_account
            </span>
            ) does not exist until the Finish step commits the GL data and CoA mapping.
            The Editor link will become active in the navigation after you complete the wizard.
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
  entities,
}: {
  ob: WizardObState
  dispatch: Dispatch<WizardAction>
  entities: WizardState['entities']
}) {
  const validEntities = entities.filter(e => e.code.trim())
  const defaultEntitySource: EntitySource = validEntities.length > 1 ? 'per_entity' : 'combined'

  const [mode, setMode] = useState<WizardObState['mode']>(ob.mode ?? 'in_data')
  const [entitySource, setEntitySource] = useState<EntitySource>(ob.entitySource ?? defaultEntitySource)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Per-entity refs and upload state
  const perEntityInputRefs = useRef<Record<string, HTMLInputElement | null>>({})
  const [perEntityUploading, setPerEntityUploading] = useState<Record<string, boolean>>({})
  const [perEntityErrors, setPerEntityErrors] = useState<Record<string, string | null>>({})

  // Local profile for combined account/amount column mapping
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

  function handleEntitySourceChange(val: EntitySource) {
    setEntitySource(val)
    dispatch({ type: 'PATCH_OB', patch: { entitySource: val } })
  }

  function handleEntityColChange(col: string) {
    dispatch({ type: 'PATCH_OB', patch: { entityCol: col } })
  }

  function handleEntityColKindChange(val: 'names' | 'prefixes') {
    dispatch({ type: 'PATCH_OB', patch: { entityColKind: val } })
  }

  async function handlePerEntityFileSelected(entityCode: string, file: File) {
    setPerEntityUploading(prev => ({ ...prev, [entityCode]: true }))
    setPerEntityErrors(prev => ({ ...prev, [entityCode]: null }))
    try {
      const res = await uploadOpeningBalance(file)
      const cols = res.columns
      const accountGuess = cols.find(c => /account|konto/i.test(c)) ?? ''
      const amountGuess = cols.find(c => /amount|betrag/i.test(c)) ?? ''
      const suggested = { account_col: accountGuess, amount_col: amountGuess }
      dispatch({
        type: 'PATCH_OB',
        patch: {
          perEntity: {
            ...ob.perEntity,
            [entityCode]: {
              ...(ob.perEntity?.[entityCode] ?? {}),
              obFileId: res.file_id,
              obColumns: cols,
              obSample: res.sample,
              obProfile: suggested,
            },
          },
        },
      })
    } catch (e) {
      setPerEntityErrors(prev => ({
        ...prev,
        [entityCode]: e instanceof Error ? e.message : 'Upload failed',
      }))
    } finally {
      setPerEntityUploading(prev => ({ ...prev, [entityCode]: false }))
    }
  }

  function handlePerEntityProfileChange(
    entityCode: string,
    field: 'account_col' | 'amount_col' | 'fiscal_year_col',
    value: string,
  ) {
    const existing = ob.perEntity?.[entityCode]?.obProfile ?? {}
    const updated = { ...existing, [field]: value }
    dispatch({
      type: 'PATCH_OB',
      patch: {
        perEntity: {
          ...ob.perEntity,
          [entityCode]: {
            ...(ob.perEntity?.[entityCode] ?? {}),
            obProfile: updated,
          },
        },
      },
    })
  }

  const columns = ob.obColumns ?? []
  const needsFile = mode === 'file_first_year' || mode === 'file_all'

  // ---------------------------------------------------------------------------
  // OB column mapper — manifest + mapping bridge
  // ---------------------------------------------------------------------------

  // Build OB target-field manifest; mode/entity conditionals determine which
  // fields are included. Groups render in first-seen order (single group here).
  const obFields = useMemo<TargetField[]>(() => {
    const fs: TargetField[] = []
    if (validEntities.length > 0) {
      fs.push({
        key: 'entity',
        label: 'Entity',
        group: 'Opening balances',
        required: true,
        hint: 'Column identifying the legal entity (name or prefix) for each row',
      })
    }
    fs.push({
      key: 'account_number',
      label: 'Account Number',
      group: 'Opening balances',
      required: true,
      hint: 'The account number — combined with the entity to build the account group for the join',
    })
    fs.push({ key: 'amount', label: 'Opening Balance Amount', group: 'Opening balances', required: true })
    if (mode === 'file_all') {
      fs.push({
        key: 'fiscal_year',
        label: 'Fiscal Year',
        group: 'Opening balances',
        required: true,
        hint: 'Which fiscal year each opening-balance row belongs to',
      })
    }
    return fs
  }, [validEntities.length, mode])

  // Current mapping derived from state — empty strings are omitted so ColumnMapper
  // correctly treats them as unmapped drop-zones.
  const obMapping: Record<string, string> = {}
  if (ob.entityCol) obMapping.entity = ob.entityCol
  if (obProfile?.account_col) obMapping.account_number = obProfile.account_col
  if (obProfile?.amount_col) obMapping.amount = obProfile.amount_col
  if (obProfile?.fiscal_year_col) obMapping.fiscal_year = obProfile.fiscal_year_col

  // Translate the mapper's full-next-mapping output back to existing state
  // handlers. Only fires a handler when the value actually changed, avoiding
  // redundant dispatches.
  function handleObMappingChange(next: Record<string, string>) {
    if ((next.entity ?? '') !== (ob.entityCol ?? '')) handleEntityColChange(next.entity ?? '')
    if ((next.account_number ?? '') !== (obProfile?.account_col ?? '')) handleProfileChange('account_col', next.account_number ?? '')
    if ((next.amount ?? '') !== (obProfile?.amount_col ?? '')) handleProfileChange('amount_col', next.amount ?? '')
    if ((next.fiscal_year ?? '') !== (obProfile?.fiscal_year_col ?? '')) handleProfileChange('fiscal_year_col', next.fiscal_year ?? '')
  }

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
        {/* Entity source selector — how OB data is split across entities       */}
        {/* ------------------------------------------------------------------ */}
        {needsFile && validEntities.length > 0 && (
          <EntitySourceSelector
            value={entitySource}
            onChange={handleEntitySourceChange}
            dataLabel="opening balances"
          />
        )}

        {/* ------------------------------------------------------------------ */}
        {/* File upload — shown for first_year and all modes (combined path)   */}
        {/* ------------------------------------------------------------------ */}
        {needsFile && (!validEntities.length || entitySource === 'combined') && (
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

            {/* Column mapping — drag-and-drop */}
            {ob.obFileId && columns.length > 0 && (
              <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-4">
                <p className="text-sm font-semibold text-slate-700">Map columns to target fields</p>
                <p className="text-xs text-slate-500">
                  Drag a column chip from the left panel and drop it onto the target field.
                  Required fields are highlighted in amber until mapped.
                </p>

                {/* Drag-and-drop column mapper */}
                <ColumnMapper
                  sourceColumns={columns}
                  sample={ob.obSample ?? []}
                  mapping={obMapping}
                  onChange={handleObMappingChange}
                  fields={obFields}
                />

                {/* Entity column content type — shown when the project has entities (combined mode) */}
                {validEntities.length > 0 && entitySource === 'combined' && (
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-slate-700">The Entity column contains:</p>
                    <div className="flex gap-4">
                      {([
                        ['names',    'Entity names (e.g. Atlas, Calypto)'],
                        ['prefixes', 'Entity prefixes (e.g. 01, 02)'],
                      ] as const).map(([val, label]) => (
                        <label key={val} className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
                          <input
                            type="radio"
                            name="entityColKind"
                            value={val}
                            checked={(ob.entityColKind ?? 'names') === val}
                            onChange={() => handleEntityColKindChange(val)}
                            className="accent-blue-600"
                          />
                          {label}
                        </label>
                      ))}
                    </div>
                  </div>
                )}

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
            {ob.obFileId && obProfile?.account_col && obProfile?.amount_col &&
              (mode !== 'file_all' || !!obProfile?.fiscal_year_col) && (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                <strong>Opening balance file staged.</strong>{' '}
                Account column: <span className="font-mono">{obProfile.account_col}</span>,
                amount column: <span className="font-mono">{obProfile.amount_col}</span>
                {mode === 'file_all' && obProfile.fiscal_year_col && (
                  <>, fiscal year column: <span className="font-mono">{obProfile.fiscal_year_col}</span></>
                )}.
                The file will be committed with scope "{mode === 'file_first_year' ? 'first_year' : 'all'}" at the Finish step.
              </div>
            )}
            {ob.obFileId && obProfile?.account_col && obProfile?.amount_col &&
              mode === 'file_all' && !obProfile?.fiscal_year_col && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                <strong>Fiscal year column not mapped.</strong>{' '}
                Select a fiscal year column above to complete the mapping for multi-year opening balances.
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

        {/* ------------------------------------------------------------------ */}
        {/* Per-entity upload blocks — one per legal entity                    */}
        {/* ------------------------------------------------------------------ */}
        {needsFile && entitySource === 'per_entity' && validEntities.length > 0 && (
          <div className="space-y-4">
            <p className="text-xs text-slate-500">
              Upload one opening balance file per legal entity. The entity prefix is injected
              automatically at commit time — no entity column is needed in these files.
            </p>
            <PerEntityPager
              entities={validEntities}
              stagedCount={validEntities.filter(e => !!ob.perEntity?.[e.code]?.obFileId).length}
              renderEntity={entity => {
                const code = entity.code
                const perState = ob.perEntity?.[code]
                const perCols = perState?.obColumns ?? []
                const isUploading = perEntityUploading[code] ?? false
                const uploadErr = perEntityErrors[code] ?? null
                return (
                  <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-3">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-slate-700">{entity.name || entity.code}</p>
                      <span className="font-mono text-xs text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">
                        prefix: {entity.prefix || entity.code}
                      </span>
                    </div>

                    <div
                      onClick={() => perEntityInputRefs.current[code]?.click()}
                      onDragOver={e => e.preventDefault()}
                      onDrop={e => {
                        e.preventDefault()
                        const f = e.dataTransfer.files[0]
                        if (f) void handlePerEntityFileSelected(code, f)
                      }}
                      className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 cursor-pointer transition ${
                        isUploading
                          ? 'border-blue-400 bg-blue-50'
                          : perState?.obFileId
                          ? 'border-emerald-300 bg-emerald-50'
                          : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-slate-50'
                      }`}
                    >
                      <input
                        ref={el => { perEntityInputRefs.current[code] = el }}
                        type="file"
                        accept=".xlsx,.xls,.csv"
                        className="hidden"
                        onChange={e => {
                          const f = e.target.files?.[0]
                          if (f) void handlePerEntityFileSelected(code, f)
                        }}
                      />
                      {isUploading ? (
                        <p className="text-sm font-medium text-blue-600">Processing file…</p>
                      ) : perState?.obFileId ? (
                        <div className="text-center">
                          <p className="text-sm font-semibold text-emerald-700">File staged</p>
                          <p className="text-xs text-slate-500 mt-0.5 font-mono">{perState.obFileId}</p>
                          {perCols.length > 0 && (
                            <p className="text-xs text-slate-400 mt-0.5">{perCols.length} columns detected</p>
                          )}
                          <p className="text-xs text-slate-400 mt-1 italic">Click or drop to replace</p>
                        </div>
                      ) : (
                        <>
                          <div className="text-3xl text-slate-300 mb-2">&#128196;</div>
                          <p className="text-sm font-semibold text-slate-700">Drop a file here or click to browse</p>
                          <p className="mt-1 text-xs text-slate-400">XLSX · XLS · CSV</p>
                        </>
                      )}
                    </div>

                    {uploadErr && (
                      <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                        {uploadErr}
                      </div>
                    )}

                    {perState?.obFileId && perCols.length > 0 && (
                      <div className="space-y-2">
                        <p className="text-xs font-semibold text-slate-700">Map account and amount columns</p>
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
                                value={perState?.obProfile?.[field] ?? ''}
                                onChange={e => handlePerEntityProfileChange(code, field, e.target.value)}
                                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                              >
                                <option value="">-- Select column --</option>
                                {perCols.map(c => (
                                  <option key={c} value={c}>{c}</option>
                                ))}
                              </select>
                            </div>
                          ))}
                        </div>
                        {/* Fiscal year column — required for multi-year OB files */}
                        {mode === 'file_all' && (
                          <div className="space-y-1">
                            <label className="text-xs font-medium text-slate-700">
                              Fiscal year column
                              <span className="ml-0.5 text-red-500" aria-hidden>*</span>
                            </label>
                            <select
                              value={perState?.obProfile?.fiscal_year_col ?? ''}
                              onChange={e => handlePerEntityProfileChange(code, 'fiscal_year_col', e.target.value)}
                              className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                              <option value="">-- Select column --</option>
                              {perCols.map(c => (
                                <option key={c} value={c}>{c}</option>
                              ))}
                            </select>
                            <p className="text-xs text-slate-400">
                              Required for multi-year opening balances; maps which fiscal year each opening row belongs to.
                            </p>
                          </div>
                        )}
                        {perState.obProfile?.account_col && perState.obProfile?.amount_col &&
                          (mode !== 'file_all' || !!perState.obProfile?.fiscal_year_col) && (
                          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
                            <strong>Staged.</strong>{' '}
                            Account: <span className="font-mono">{perState.obProfile.account_col}</span>{' '}
                            · Amount: <span className="font-mono">{perState.obProfile.amount_col}</span>
                            {mode === 'file_all' && perState.obProfile.fiscal_year_col && (
                              <>{' '}· Fiscal year: <span className="font-mono">{perState.obProfile.fiscal_year_col}</span></>
                            )}.
                          </div>
                        )}
                        {perState.obProfile?.account_col && perState.obProfile?.amount_col &&
                          mode === 'file_all' && !perState.obProfile?.fiscal_year_col && (
                          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                            <strong>Fiscal year column not mapped.</strong>{' '}
                            Select a fiscal year column above to complete the mapping.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              }}
            />
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

/**
 * PerEntityPartnerBlock — upload + column-mapping panel for one legal entity
 * in per-entity mode. Does NOT render the "Entity assignment" sub-control;
 * the entity prefix is injected automatically at Finish time.
 */
function PerEntityPartnerBlock({
  entityCode,
  entityName,
  entityPrefix,
  side,
  perState,
  allPerEntity,
  dispatch,
}: {
  entityCode: string
  entityName: string
  entityPrefix: string
  side: 'customer' | 'supplier'
  perState: NonNullable<PartnerSideState['perEntity']>[string] | undefined
  allPerEntity: PartnerSideState['perEntity'] | undefined
  dispatch: Dispatch<WizardAction>
}) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [joinKeyCol, setJoinKeyCol] = useState(perState?.profile?.join_key?.column ?? '')
  const [nameCol, setNameCol] = useState(perState?.profile?.columns?.name_line_1 ?? '')
  const [nameLine2Col, setNameLine2Col] = useState(perState?.profile?.columns?.name_line_2 ?? '')
  const [countryCol, setCountryCol] = useState(perState?.profile?.columns?.country_code ?? '')
  const [cityCol, setCityCol] = useState(perState?.profile?.columns?.city ?? '')
  const [postalCol, setPostalCol] = useState(perState?.profile?.columns?.postal_code ?? '')

  const columns = perState?.columns ?? []

  function persistProfile(overrides: Partial<{
    joinKeyCol: string; nameCol: string; nameLine2Col: string
    countryCol: string; cityCol: string; postalCol: string
  }> = {}) {
    const jk = overrides.joinKeyCol ?? joinKeyCol
    const nc = overrides.nameCol ?? nameCol
    if (!jk || !nc) return
    const nl2 = overrides.nameLine2Col ?? nameLine2Col
    const cc = overrides.countryCol ?? countryCol
    const ci = overrides.cityCol ?? cityCol
    const po = overrides.postalCol ?? postalCol
    const profile: PartnerMappingProfile = {
      side,
      entity: { mode: 'fixed', value: entityPrefix || entityCode }, // overridden at Finish
      join_key: { column: jk },
      columns: {
        name_line_1: nc,
        ...(nl2 ? { name_line_2: nl2 } : {}),
        ...(cc ? { country_code: cc } : {}),
        ...(ci ? { city: ci } : {}),
        ...(po ? { postal_code: po } : {}),
      },
    }
    dispatch({
      type: 'PATCH_PARTNER',
      side,
      patch: {
        perEntity: {
          ...allPerEntity,
          [entityCode]: { ...(allPerEntity?.[entityCode] ?? {}), profile },
        },
      },
    })
  }

  async function handleFileSelected(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const res = await uploadPartnerMaster(file)
      const cols = res.columns
      const joinGuess = cols.find(c => /debtor|creditor|kred|deb|partner.?no|kunden.?nr|lief.?nr|number/i.test(c)) ?? ''
      const nameGuess = cols.find(c => /name|firma|bezeichnung/i.test(c)) ?? ''
      setJoinKeyCol(joinGuess)
      setNameCol(nameGuess)
      dispatch({
        type: 'PATCH_PARTNER',
        side,
        patch: {
          perEntity: {
            ...allPerEntity,
            [entityCode]: {
              ...(allPerEntity?.[entityCode] ?? {}),
              fileId: res.file_id,
              columns: res.columns,
              sample: res.sample,
              profile: undefined,
            },
          },
        },
      })
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-4 space-y-4">
      {/* Entity header */}
      <div className="flex items-center gap-2">
        <p className="text-sm font-semibold text-slate-700">{entityName || entityCode}</p>
        <span className="font-mono text-xs text-slate-400 bg-slate-100 rounded px-1.5 py-0.5">
          prefix: {entityPrefix || entityCode}
        </span>
      </div>

      {/* Dropzone */}
      <div
        onClick={() => fileInputRef.current?.click()}
        onDragOver={e => e.preventDefault()}
        onDrop={e => {
          e.preventDefault()
          const f = e.dataTransfer.files[0]
          if (f) void handleFileSelected(f)
        }}
        className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 cursor-pointer transition ${
          uploading
            ? 'border-blue-400 bg-blue-50'
            : perState?.fileId
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
        ) : perState?.fileId ? (
          <div className="text-center">
            <p className="text-sm font-semibold text-emerald-700">File staged</p>
            <p className="text-xs text-slate-500 mt-0.5 font-mono">{perState.fileId}</p>
            {columns.length > 0 && (
              <p className="text-xs text-slate-400 mt-0.5">{columns.length} columns detected</p>
            )}
            <p className="text-xs text-slate-400 mt-1 italic">Click or drop to replace</p>
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

      {/* Column mapping */}
      {perState?.fileId && columns.length > 0 && (
        <div className="space-y-3">
          <p className="text-sm font-semibold text-slate-700">Column mapping</p>

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
                onChange={e => { setJoinKeyCol(e.target.value); persistProfile({ joinKeyCol: e.target.value }) }}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">-- Select column --</option>
                {columns.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-700">
                Primary name column
                <span className="ml-0.5 text-red-500" aria-hidden>*</span>
              </label>
              <select
                value={nameCol}
                onChange={e => { setNameCol(e.target.value); persistProfile({ nameCol: e.target.value }) }}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">-- Select column --</option>
                {columns.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-xs font-medium text-slate-600">Optional address fields</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-xs text-slate-600">Name line 2</label>
                <select
                  value={nameLine2Col}
                  onChange={e => { setNameLine2Col(e.target.value); persistProfile({ nameLine2Col: e.target.value }) }}
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Not mapped --</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-slate-600">Country code</label>
                <select
                  value={countryCol}
                  onChange={e => { setCountryCol(e.target.value); persistProfile({ countryCol: e.target.value }) }}
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Not mapped --</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-slate-600">City</label>
                <select
                  value={cityCol}
                  onChange={e => { setCityCol(e.target.value); persistProfile({ cityCol: e.target.value }) }}
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Not mapped --</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <div className="space-y-1">
                <label className="text-xs text-slate-600">Postal code</label>
                <select
                  value={postalCol}
                  onChange={e => { setPostalCol(e.target.value); persistProfile({ postalCol: e.target.value }) }}
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">-- Not mapped --</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
            </div>
          </div>

          {perState.profile && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
              <strong>Staged.</strong>{' '}
              Join key: <span className="font-mono">{joinKeyCol}</span> ·
              Name: <span className="font-mono">{nameCol}</span>.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * CombinedPartnerBlock — upload + column-mapping panel for a single side
 * (customer or supplier) in combined (non-per-entity) mode.
 * Rendered with key={activeSide} so it remounts on side switch, picking up
 * fresh local state from sideState.
 */
function CombinedPartnerBlock({
  side,
  sideState,
  dispatch,
}: {
  side: 'customer' | 'supplier'
  sideState: PartnerSideState
  dispatch: Dispatch<WizardAction>
}) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Local form state — initialised from persisted side state
  const [entityMode, setEntityMode] = useState<'fixed' | 'column'>(
    sideState.profile?.entity.mode ?? 'fixed',
  )
  const [entityValue, setEntityValue] = useState<string>(
    sideState.profile?.entity.value ?? '',
  )
  const [joinKeyCol, setJoinKeyCol] = useState<string>(
    sideState.profile?.join_key.column ?? '',
  )
  const [nameCol, setNameCol] = useState<string>(
    sideState.profile?.columns.name_line_1 ?? '',
  )
  const [nameLine2Col, setNameLine2Col] = useState<string>(
    sideState.profile?.columns.name_line_2 ?? '',
  )
  const [countryCol, setCountryCol] = useState<string>(
    sideState.profile?.columns.country_code ?? '',
  )
  const [cityCol, setCityCol] = useState<string>(
    sideState.profile?.columns.city ?? '',
  )
  const [postalCol, setPostalCol] = useState<string>(
    sideState.profile?.columns.postal_code ?? '',
  )

  const columns = sideState.columns ?? []

  function persistProfile(overrides: Partial<{
    entityMode: 'fixed' | 'column'
    entityValue: string
    joinKeyCol: string
    nameCol: string
    nameLine2Col: string
    countryCol: string
    cityCol: string
    postalCol: string
  }> = {}) {
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
      side,
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
    dispatch({ type: 'PATCH_PARTNER', side, patch: { profile } })
  }

  async function handleFileSelected(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const res = await uploadPartnerMaster(file)
      const cols = res.columns
      const joinGuess = cols.find(c => /debtor|creditor|kred|deb|partner.?no|kunden.?nr|lief.?nr|number/i.test(c)) ?? ''
      const nameGuess = cols.find(c => /name|firma|bezeichnung/i.test(c)) ?? ''
      setJoinKeyCol(joinGuess)
      setNameCol(nameGuess)
      dispatch({
        type: 'PATCH_PARTNER',
        side,
        patch: {
          fileId: res.file_id,
          columns: res.columns,
          sample: res.sample,
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
    <>
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
              : sideState.fileId
              ? 'border-emerald-300 bg-emerald-50'
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
              if (f) void handleFileSelected(f)
            }}
          />
          {uploading ? (
            <p className="text-sm font-medium text-blue-600">Processing file…</p>
          ) : sideState.fileId ? (
            <div className="text-center">
              <p className="text-sm font-semibold text-emerald-700">File staged</p>
              <p className="text-xs text-slate-500 mt-0.5">
                File ID: <span className="font-mono">{sideState.fileId}</span>
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
      {sideState.fileId && columns.length > 0 && (
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
                  name={`entityMode-${side}`}
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
                  name={`entityMode-${side}`}
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
          {sideState.sample && sideState.sample.length > 0 && (
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
                  {sideState.sample.slice(0, 3).map((row, i) => (
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
    </>
  )
}

function StepPartnerMaster({
  partner,
  dispatch,
  entities,
}: {
  partner: WizardPartnerState
  dispatch: Dispatch<WizardAction>
  entities: WizardState['entities']
}) {
  const validEntities = entities.filter(e => e.code.trim())
  const defaultEntitySource: EntitySource = validEntities.length > 1 ? 'per_entity' : 'combined'
  const [activeSide, setActiveSide] = useState<'customer' | 'supplier'>('customer')
  const [editorTab, setEditorTab] = useState<'customers' | 'suppliers'>('customers')

  const sideState = partner.sides[activeSide]
  const entitySource = sideState.entitySource ?? defaultEntitySource

  function handleEntitySourceChange(val: EntitySource) {
    dispatch({ type: 'PATCH_PARTNER', side: activeSide, patch: { entitySource: val } })
  }

  return (
    <StepCard
      title="Partner master"
      subtitle="Upload customer or supplier master data and map the join key and name fields."
    >
      <div className="space-y-6">

        {/* ------------------------------------------------------------------ */}
        {/* Entity source selector — shown always as a pre-step                 */}
        {/* ------------------------------------------------------------------ */}
        <EntitySourceSelector
          value={entitySource}
          onChange={handleEntitySourceChange}
          dataLabel="partner masters"
        />

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
                  activeSide === val
                    ? 'border-blue-400 bg-blue-50'
                    : 'border-slate-200 bg-white hover:bg-slate-50'
                }`}
              >
                <input
                  type="radio"
                  name="partnerSide"
                  value={val}
                  checked={activeSide === val}
                  onChange={() => setActiveSide(val)}
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
            Upload and map both customers and suppliers here — switching sides keeps each
            side's file and mapping. Both are committed at Finish.
          </p>
        </div>

        {/* ------------------------------------------------------------------ */}
        {/* Combined upload + mapping block (remounts on side switch via key)  */}
        {/* ------------------------------------------------------------------ */}
        {entitySource !== 'per_entity' && (
          <CombinedPartnerBlock
            key={activeSide}
            side={activeSide}
            sideState={sideState}
            dispatch={dispatch}
          />
        )}

        {/* ------------------------------------------------------------------ */}
        {/* Per-entity upload blocks — one per legal entity                    */}
        {/* ------------------------------------------------------------------ */}
        {entitySource === 'per_entity' && validEntities.length > 0 && (
          <div className="space-y-4">
            <p className="text-xs text-slate-500">
              Upload one partner master file per legal entity. The entity prefix is injected
              automatically at commit time — no entity column is needed in these files.
            </p>
            <PerEntityPager
              entities={validEntities}
              stagedCount={validEntities.filter(e => !!sideState.perEntity?.[e.code]?.profile).length}
              renderEntity={(entity) => (
                <PerEntityPartnerBlock
                  key={`${activeSide}-${entity.code}`}
                  entityCode={entity.code}
                  entityName={entity.name}
                  entityPrefix={entity.prefix}
                  side={activeSide}
                  perState={sideState.perEntity?.[entity.code]}
                  allPerEntity={sideState.perEntity}
                  dispatch={dispatch}
                />
              )}
            />
          </div>
        )}

        {/* ------------------------------------------------------------------ */}
        {/* Inline editor — manage records directly without a file              */}
        {/* ------------------------------------------------------------------ */}
        <div className="space-y-3 pt-2">
          <div className="flex items-center gap-3">
            <div className="h-px flex-1 bg-slate-200" />
            <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
              or manage records inline
            </span>
            <div className="h-px flex-1 bg-slate-200" />
          </div>

          <p className="text-xs text-slate-500">
            Add, edit, or delete individual customer and supplier records directly —
            no file needed. Records saved here are immediately visible in drill-downs.
          </p>

          {/* Customer / Supplier tab toggle */}
          <div className="flex gap-2">
            {(['customers', 'suppliers'] as const).map(t => (
              <button
                key={t}
                type="button"
                onClick={() => setEditorTab(t)}
                className={`rounded-md px-4 py-1.5 text-xs font-semibold transition ${
                  editorTab === t
                    ? 'text-white'
                    : 'border border-slate-300 text-slate-600 hover:bg-slate-50'
                }`}
                style={editorTab === t ? { backgroundColor: '#1E3A5F' } : undefined}
              >
                {t === 'customers' ? 'Customers' : 'Suppliers'}
              </button>
            ))}
          </div>

          <PartnerMasterEditor side={editorTab} compact showBulkImport={false} />
        </div>

      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 6 — Additional information (optional FTE / personnel-cost provisioning)
// ---------------------------------------------------------------------------

function StepAdditionalInformation({
  fte,
  anlagen,
  opos,
  entities,
  glYears,
  fyEndMonth,
  additionalDatasets,
  dispatch,
}: {
  fte: WizardFteState
  anlagen: WizardAnlagenState
  opos: WizardOposState
  entities: WizardState['entities']
  glYears: number[]
  fyEndMonth: number
  additionalDatasets: WizardState['additionalDatasets']
  dispatch: Dispatch<WizardAction>
}) {
  const validEntities = entities.filter(e => e.code.trim())
  const [uploading, setUploading] = useState<Record<string, boolean>>({})
  const [uploadError, setUploadError] = useState<Record<string, string>>({})
  const [customDraft, setCustomDraft] = useState({ source_col: '', output_label: '' })

  // Derive FY labels from GL years using the fiscal-year label helper (mirrors GL step).
  // Fall back to a 4-year default range when no GL years are selected yet.
  const fyLabels = glYears.length > 0
    ? glYears.map(y => glFiscalYearLabel(y, fyEndMonth))
    : ['FY2022', 'FY2023', 'FY2024', 'FY2025']

  const patch = (p: Partial<WizardFteState>) =>
    dispatch({ type: 'PATCH_FTE', patch: p })

  /** Get the upload slot for a specific {entity_index, fy_label} combination. */
  const getSlotUpload = (entityIndex: number, fyLabel: string) =>
    fte.uploads.find(u => u.entity_index === entityIndex && u.fy_label === fyLabel)

  /** True when at least one FY slot for the given entity has a staged file. */
  const isEntityStaged = (entityIndex: number) =>
    fte.uploads.some(u => u.entity_index === entityIndex && u.file_ids.length > 0)

  const hasUploads = fte.uploads.some(u => u.file_ids.length > 0)

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
      const result = await uploadFddFile(fte.sessionId, file)
      const newEntry: WizardFteUpload = {
        entity_index: entityIndex,
        entity_name: entityName,
        fy_label: fyLabel,
        file_ids: [result.file_id],
      }
      // Replace the existing entry for this exact {entity_index, fy_label} slot.
      const rest = fte.uploads.filter(
        u => !(u.entity_index === entityIndex && u.fy_label === fyLabel),
      )
      const allUploads = [...rest, newEntry]
      const previewFileId =
        [...allUploads].sort((a, b) => a.entity_index - b.entity_index)[0]?.file_ids[0] ??
        result.file_id
      patch({ uploads: allUploads, previewFileId, provided: true })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Upload failed'
      setUploadError(prev => ({ ...prev, [slotKey]: msg }))
    } finally {
      setUploading(prev => ({ ...prev, [slotKey]: false }))
    }
  }

  /** Render a single upload drop-zone for one {entity_index, fy_label} slot. */
  function renderUploadZone(entityIndex: number, entityName: string, fyLabel: string) {
    const slotKey = `${entityIndex}__${fyLabel}`
    const existing = getSlotUpload(entityIndex, fyLabel)
    const isUploading = uploading[slotKey] ?? false
    const err = uploadError[slotKey] ?? ''
    return (
      <div key={slotKey} className="space-y-1.5">
        <label
          className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 cursor-pointer transition ${
            existing
              ? 'border-emerald-300 bg-emerald-50'
              : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-blue-50/50'
          }`}
        >
          <input
            type="file"
            accept=".xlsx"
            className="sr-only"
            disabled={isUploading}
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) void handleFileUpload(entityIndex, entityName, fyLabel, f)
              e.target.value = ''
            }}
          />
          {isUploading ? (
            <span className="text-sm text-blue-600">Uploading…</span>
          ) : existing ? (
            <>
              <svg className="h-5 w-5 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              <span className="text-sm text-emerald-700 font-medium">File uploaded — {fyLabel}</span>
              <span className="text-xs text-slate-500">Click to replace</span>
            </>
          ) : (
            <>
              <svg className="h-6 w-6 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
              </svg>
              <span className="text-sm text-slate-600">Drop .xlsx or click to browse</span>
            </>
          )}
        </label>
        {err && <p className="text-xs text-red-600">{err}</p>}
      </div>
    )
  }

  // AdaptiveCardInput shape required by FtePexGrid
  const pexInput: AdaptiveCardInput = {
    id: 'fte_pex_values',
    type: 'fte_pex_grid',
    label: 'Personnel expenses (EURk)',
    options: fyLabels.map(fy => ({ label: fy, value: fy })),
    entity_count:
      fte.pexViewMode === 'per_entity' ? Math.max(1, validEntities.length) : 1,
    default_entity_names:
      fte.pexViewMode === 'per_entity'
        ? validEntities.map(e => e.name || e.code)
        : [validEntities[0]?.name || validEntities[0]?.code || 'Entity 1'],
  }

  return (
    <StepCard
      title="Additional information"
      subtitle="Select which additional datasets to provide. All datasets are optional — skip this step by clicking Next."
    >
      <div className="space-y-8">

        {/* ------------------------------------------------------------------ */}
        {/* Dataset selector                                                    */}
        {/* ------------------------------------------------------------------ */}
        <DatasetSelector
          selection={additionalDatasets}
          onToggle={(dataset, value) => dispatch({ type: 'TOGGLE_DATASET', dataset, value })}
        />

        {/* ------------------------------------------------------------------ */}
        {/* FTE provisioning — visible only when additionalDatasets.fte is true */}
        {/* ------------------------------------------------------------------ */}
        {additionalDatasets.fte && (
          <div className="space-y-8">

            {/* a. Intro */}
            <InfoBox>
              <strong>FTE and payroll data is optional.</strong> If you provide a personnel file,
              a formula-linked FTE Development workbook will be generated at the end of setup.
            </InfoBox>

            {/* b. Data layout: consolidated vs per-entity */}
            <div className="space-y-3">
              <p className="text-sm font-medium text-slate-700">Data layout</p>
              <div className="flex gap-3 flex-wrap">
                {(
                  [
                    ['consolidated', 'All entities combined',  'Upload one file per fiscal year — the file covers all entities'] as const,
                    ['per_entity',   'One file per entity',    'Upload one file per entity per fiscal year']                     as const,
                  ] as const
                ).map(([val, label, hint]) => (
                  <label
                    key={val}
                    className={`flex-1 min-w-[200px] flex items-start gap-3 cursor-pointer rounded-lg border p-3.5 transition ${
                      fte.viewMode === val
                        ? 'border-blue-400 bg-blue-50'
                        : 'border-slate-200 bg-white hover:bg-slate-50'
                    }`}
                  >
                    <input
                      type="radio"
                      name="fteViewMode"
                      value={val}
                      checked={fte.viewMode === val}
                      onChange={() => patch({ viewMode: val, uploads: [], previewFileId: '' })}
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
                FY range auto-derived from GL step: {fyLabels.join(', ')}.
                {validEntities.length > 0 && (
                  <> Entities: {validEntities.map(e => e.name || e.code).join(', ')}.</>
                )}
              </p>
            </div>

            {/* c. File upload zone(s) — one per FY (combined) or per entity×FY (per_entity) */}
            <div className="space-y-3">
              <p className="text-sm font-medium text-slate-700">
                Upload personnel data file{fyLabels.length > 1 ? 's' : ''}
              </p>
              <p className="text-xs text-slate-500">
                Excel (.xlsx) file with one row per employee. Upload one file per fiscal year
                {fte.viewMode === 'per_entity' ? ' per entity' : ''}.
                {fyLabels.length > 0 && ` FY range: ${fyLabels[0]}–${fyLabels[fyLabels.length - 1]}.`}
              </p>

              {/* Combined mode: year pager — one upload slot per FY for all entities */}
              {fte.viewMode === 'consolidated' && (
                <PerYearPager
                  fyLabels={fyLabels}
                  stagedCount={fyLabels.filter(fy => Boolean(getSlotUpload(0, fy))).length}
                  renderYear={(fyLabel) => renderUploadZone(0, 'All entities (combined)', fyLabel)}
                />
              )}

              {/* Per-entity mode: entity pager wrapping a year pager per entity */}
              {fte.viewMode === 'per_entity' && (
                <PerEntityPager
                  entities={validEntities}
                  stagedCount={validEntities.filter((_, i) => isEntityStaged(i)).length}
                  renderEntity={(entity, idx) => (
                    <PerYearPager
                      fyLabels={fyLabels}
                      stagedCount={fyLabels.filter(fy => Boolean(getSlotUpload(idx, fy))).length}
                      renderYear={(fyLabel) => renderUploadZone(idx, entity.name || entity.code, fyLabel)}
                    />
                  )}
                />
              )}
            </div>

            {/* Sections d–h — visible only after at least one file is uploaded */}
            {hasUploads && fte.previewFileId && (
              <>
                {/* d. FTE column mapping */}
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-slate-200" />
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                      FTE column mapping
                    </span>
                    <div className="h-px flex-1 bg-slate-200" />
                  </div>
                  <p className="text-xs text-slate-500">
                    Map source columns for FTE tenure calculation (headcount months or entry/exit dates).
                  </p>
                  <FteColumnMapper
                    sessionId={fte.sessionId}
                    previewFileId={fte.previewFileId}
                    mode="fte"
                    uploadMode={fte.uploadMode}
                    initialTenureMode={fte.tenureMode}
                    onSubmit={(mapping: FteMappingPayload) => {
                      patch({
                        fteMapping: mapping as Record<string, unknown>,
                        tenureMode: mapping.tenure_mode ?? fte.tenureMode,
                        provided: true,
                      })
                    }}
                  />
                </div>

                {/* e. Payroll column mapping */}
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-slate-200" />
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                      Payroll column mapping
                    </span>
                    <div className="h-px flex-1 bg-slate-200" />
                  </div>
                  <p className="text-xs text-slate-500">
                    Map source columns for personnel cost (payroll) per employee.
                  </p>
                  <FteColumnMapper
                    sessionId={fte.sessionId}
                    previewFileId={fte.previewFileId}
                    mode="payroll"
                    uploadMode={fte.uploadMode}
                    initialPayrollMode={fte.payrollMode}
                    onSubmit={(mapping: FteMappingPayload) => {
                      patch({
                        payrollMapping: mapping as Record<string, unknown>,
                        payrollMode: mapping.payroll_mode ?? fte.payrollMode,
                        provided: true,
                      })
                    }}
                  />
                </div>

                {/* f. Breakdown dimensions */}
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-slate-200" />
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                      Breakdown dimensions (optional, max 3)
                    </span>
                    <div className="h-px flex-1 bg-slate-200" />
                  </div>
                  <p className="text-xs text-slate-500">
                    Select up to 3 columns to use as breakdown dimensions in the output workbook.
                  </p>
                  <FteDimensionPicker
                    sessionId={fte.sessionId}
                    previewFileId={fte.previewFileId}
                    onSubmit={(dims: FteDimension[]) => {
                      patch({ dimensions: dims })
                    }}
                  />
                </div>

                {/* g. Output metrics */}
                <div className="space-y-4">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-slate-200" />
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                      Output metrics
                    </span>
                    <div className="h-px flex-1 bg-slate-200" />
                  </div>

                  {/* Preset metrics */}
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-slate-600">Preset metrics</p>
                    {FTE_PRESET_METRIC_OPTIONS.map(opt => (
                      <label key={opt.value} className="flex items-center gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          className="accent-blue-600 h-4 w-4"
                          checked={fte.presetMetrics.includes(opt.value)}
                          onChange={e => {
                            const next = e.target.checked
                              ? [...fte.presetMetrics, opt.value]
                              : fte.presetMetrics.filter(m => m !== opt.value)
                            patch({ presetMetrics: next })
                          }}
                        />
                        <span className="text-sm text-slate-800">{opt.label}</span>
                      </label>
                    ))}
                  </div>

                  {/* Custom output columns */}
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-slate-600">
                      Custom output columns (source column → output label)
                    </p>
                    {fte.customMetrics.map((m, i) => (
                      <div key={m.source_col || i} className="flex items-center gap-2 text-sm">
                        <span className="font-mono bg-slate-100 rounded px-2 py-0.5 text-xs text-slate-700">
                          {m.source_col}
                        </span>
                        <span className="text-slate-400">→</span>
                        <span className="text-slate-800">{m.output_label}</span>
                        <button
                          type="button"
                          onClick={() =>
                            patch({ customMetrics: fte.customMetrics.filter((_, j) => j !== i) })
                          }
                          className="ml-auto text-xs text-red-500 hover:text-red-700 transition"
                        >
                          Remove
                        </button>
                      </div>
                    ))}
                    <div className="flex gap-2">
                      <input
                        type="text"
                        placeholder="Source column name"
                        value={customDraft.source_col}
                        onChange={e => setCustomDraft(d => ({ ...d, source_col: e.target.value }))}
                        className="flex-1 rounded border border-slate-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                      />
                      <input
                        type="text"
                        placeholder="Output label"
                        value={customDraft.output_label}
                        onChange={e => setCustomDraft(d => ({ ...d, output_label: e.target.value }))}
                        className="flex-1 rounded border border-slate-300 px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                      />
                      <button
                        type="button"
                        disabled={!customDraft.source_col.trim() || !customDraft.output_label.trim()}
                        onClick={() => {
                          if (!customDraft.source_col.trim() || !customDraft.output_label.trim()) return
                          patch({ customMetrics: [...fte.customMetrics, { ...customDraft }] })
                          setCustomDraft({ source_col: '', output_label: '' })
                        }}
                        className="rounded border border-blue-300 bg-blue-50 px-3 py-1.5 text-xs text-blue-700 hover:bg-blue-100 disabled:opacity-40 transition"
                      >
                        Add
                      </button>
                    </div>
                  </div>
                </div>

                {/* h. PEX — GL personnel expenses grid */}
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <div className="h-px flex-1 bg-slate-200" />
                    <span className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
                      Personnel expenses from GL (PEX) — optional
                    </span>
                    <div className="h-px flex-1 bg-slate-200" />
                  </div>
                  <p className="text-xs text-slate-500">
                    Enter GL personnel expense totals in EURk per entity and fiscal year.
                    Used for variance analysis between GL and payroll data. Leave blank if unavailable.
                  </p>
                  <div className="flex gap-4 mb-2">
                    {(
                      [['consolidated', 'Consolidated'], ['per_entity', 'Per entity']] as const
                    ).map(([val, label]) => (
                      <label key={val} className="flex items-center gap-2 cursor-pointer text-sm">
                        <input
                          type="radio"
                          name="pexViewMode"
                          value={val}
                          checked={fte.pexViewMode === val}
                          onChange={() => patch({ pexViewMode: val })}
                          className="accent-blue-600"
                        />
                        <span className="text-slate-700">{label}</span>
                      </label>
                    ))}
                  </div>
                  <FtePexGrid
                    input={pexInput}
                    values={{ fte_pex_values: fte.pexValues }}
                    onChange={(vals: Record<string, number | string>) => {
                      const numVals: Record<string, number> = {}
                      for (const [k, v] of Object.entries(vals)) {
                        if (v !== '' && !Number.isNaN(Number(v))) numVals[k] = Number(v)
                      }
                      patch({ pexValues: numVals })
                    }}
                  />
                </div>
              </>
            )}
          </div>
        )}

        {/* Anlagen provisioning — visible only when additionalDatasets.anlagen is true */}
        {additionalDatasets.anlagen && (
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-slate-200" />
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Fixed-Asset Register
              </span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
            <AnlagenStep
              anlagen={anlagen}
              entities={entities}
              glYears={glYears}
              fyEndMonth={fyEndMonth}
              onPatch={patch => dispatch({ type: 'PATCH_ANLAGEN', patch })}
            />
          </div>
        )}

        {/* OPOS provisioning — visible only when additionalDatasets.opos is true */}
        {additionalDatasets.opos && (
          <div className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-slate-200" />
              <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                Open-Items Lists (OPOS)
              </span>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
            <OposStep
              opos={opos}
              entities={entities}
              glYears={glYears}
              fyEndMonth={fyEndMonth}
              onPatch={patch => dispatch({ type: 'PATCH_OPOS', patch })}
            />
          </div>
        )}

      </div>
    </StepCard>
  )
}

// ---------------------------------------------------------------------------
// Step 7 — Review & Finish (Phase 5 — full orchestration)
// ---------------------------------------------------------------------------

/** Status of one commit step in the Finish sequence. */
type StepStatus = 'pending' | 'running' | 'done' | 'skipped' | 'failed'

interface CommitStepState {
  id: string
  label: string
  status: StepStatus
  detail?: string        // counts / reason for skip / error message
  unmapped?: GlUnmappedDetail  // present when GL commit returns gl_accounts_unmapped 422
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

/** Returns true when the wizard partner state has at least one file+profile pair ready to commit. */
function hasPartnerData(state: WizardState): boolean {
  const validEntities = state.entities.filter(e => e.code.trim())
  const defaultEntitySource: EntitySource = validEntities.length > 1 ? 'per_entity' : 'combined'
  for (const side of ['customer', 'supplier'] as const) {
    const sideState = state.partner.sides[side]
    const entitySource = sideState.entitySource ?? defaultEntitySource
    if (entitySource === 'per_entity') {
      for (const e of validEntities) {
        const perState = sideState.perEntity?.[e.code]
        if (perState?.fileId && perState?.profile) return true
      }
    } else if (sideState.fileId && sideState.profile) {
      return true
    }
  }
  return false
}

/** Returns true when the wizard has FTE data staged and ready to commit. */
function hasFteData(state: WizardState): boolean {
  return (
    state.additionalDatasets.fte &&
    state.fte.uploads.length > 0 &&
    state.fte.uploads.some(u => u.file_ids.length > 0)
  )
}

/**
 * Build the commit-step list for the Finish sequence, conditionally including
 * 'partner' and 'fte' only when the relevant data has been staged.
 * patchStep() is .map-based, so omitted step ids are harmless no-ops.
 */
function buildInitialCommitSteps(state: WizardState): CommitStepState[] {
  const steps: CommitStepState[] = [
    { id: 'config',  label: 'Save project config',      status: 'pending' },
    { id: 'coa',     label: 'Commit Chart of Accounts', status: 'pending' },
    { id: 'gl',      label: 'Commit GL bookings',       status: 'pending' },
    { id: 'ob',      label: 'Commit opening balances',  status: 'pending' },
  ]
  if (hasPartnerData(state)) {
    steps.push({ id: 'partner', label: 'Commit partner master',       status: 'pending' })
  }
  if (hasFteData(state)) {
    steps.push({ id: 'fte',     label: 'Build FTE Development table', status: 'pending' })
  }
  steps.push({ id: 'rebuild', label: 'Full rebuild', status: 'pending' })
  return steps
}

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
    if (state.gl.years.length === 0) return 'No fiscal years selected'
    const yearsLabel = state.gl.years.map(y => `FY${y}`).join(', ')
    const entitySummaries = state.gl.entities.map((e, i) => {
      const name = e.entityCode.trim() || `Entity ${i + 1}`
      const filesUploaded = state.gl.years.filter(y => e.yearFiles[y] !== undefined).length
      const combined = e.combinedFileId ? ' · combined' : ''
      const val = e.validationOk === true ? 'validated' : e.validationOk === false ? 'warnings' : 'not validated'
      return `${name} (${filesUploaded}/${state.gl.years.length} files${combined} · ${val})`
    })
    return `Years: ${yearsLabel}  |  ${entitySummaries.join('  |  ')}`
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
        const { groups, accountMappingMode, assigned } = state.coa
        if (groups.length === 0 || !assigned) return 'Not yet configured'
        const uploadGroups = groups.filter(g => g.method === 'upload')
        if (uploadGroups.length === 0) {
          // All groups use library
          const variants = [...new Set(groups.map(g => {
            const vl = LIBRARY_VARIANTS.find(v => v.value === (g.libraryVariant ?? 'skr03'))?.label
            return vl ?? (g.libraryVariant ?? 'skr03')
          }))]
          return `Library (${variants.join(', ')}) · fill mode: ${accountMappingMode}`
        }
        const stagedSlots = uploadGroups.flatMap(g => [
          g.bs?.fileId ? `${g.label} BS (${g.bs.isMaster ? 'master' : 'generic'})` : null,
          g.pl?.fileId ? `${g.label} PL (${g.pl.isMaster ? 'master' : 'generic'})` : null,
        ]).filter(Boolean)
        if (stagedSlots.length === 0) return 'Upload — no files staged yet'
        return `Upload — ${stagedSlots.join(', ')} · fill mode: ${accountMappingMode}`
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
        const validEntities = state.entities.filter(e => e.code.trim())
        const defaultEntitySource: EntitySource = validEntities.length > 1 ? 'per_entity' : 'combined'
        const parts: string[] = []
        for (const side of ['customer', 'supplier'] as const) {
          const ss = state.partner.sides[side]
          const label = side === 'customer' ? 'Customers' : 'Suppliers'
          const entitySource = ss.entitySource ?? defaultEntitySource
          if (entitySource === 'per_entity') {
            const staged = validEntities.filter(
              e => ss.perEntity?.[e.code]?.fileId && ss.perEntity?.[e.code]?.profile
            ).length
            if (staged > 0) {
              parts.push(`${label} — ${staged} ${staged === 1 ? 'entity' : 'entities'} staged (per-entity)`)
            }
          } else if (ss.profile && ss.fileId) {
            parts.push(`${label} staged · Join key: ${ss.profile.join_key.column}`)
          } else if (ss.fileId) {
            parts.push(`${label} file staged — mapping incomplete`)
          }
        }
        return parts.length ? parts.join(' | ') : 'Not uploaded (optional)'
      })(),
    },
    {
      label: 'Additional information',
      value: (() => {
        const { uploads, presetMetrics, customMetrics, pexValues } = state.fte
        if (uploads.length === 0 || !uploads.some(u => u.file_ids.length > 0)) {
          return 'Not provided (optional)'
        }
        const entityCount = new Set(uploads.map(u => u.entity_index)).size
        const pexCount = Object.values(pexValues).filter(v => v > 0).length
        const metricLabels = [
          ...presetMetrics.map(m =>
            m === 'avg_cost_per_fte' ? 'Avg cost per FTE' : m === 'fte' ? 'FTE' : 'Payroll'
          ),
          ...customMetrics.map(m => m.output_label),
        ]
        const fySet = new Set(uploads.map(u => u.fy_label))
        return (
          `${entityCount} entity slot(s) · ${fySet.size} FY labels` +
          ` · Metrics: ${metricLabels.join(', ') || 'none'}` +
          (pexCount > 0 ? ` · PEX entries: ${pexCount}` : '')
        )
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

/** Compact table for unmapped GL accounts (Account / Year / Entity / Label columns). */
function UnmappedAccountsTable({ rows }: { rows: GlUnmappedAccount[] }) {
  return (
    <div className="rounded border border-red-200 overflow-x-auto">
      <table className="w-full text-xs text-left">
        <thead className="bg-red-100 text-red-700">
          <tr>
            <th className="px-3 py-2 font-semibold">Account</th>
            <th className="px-3 py-2 font-semibold">Year</th>
            <th className="px-3 py-2 font-semibold">Entity</th>
            <th className="px-3 py-2 font-semibold">Label</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-red-100 bg-white">
          {rows.map((u, i) => (
            <tr key={i} className="odd:bg-white even:bg-red-50/30">
              <td className="px-3 py-1.5 font-mono">{u.account}</td>
              <td className="px-3 py-1.5">{u.fiscal_year}</td>
              <td className="px-3 py-1.5">{u.entity_prefix}</td>
              <td className="px-3 py-1.5 text-slate-600">{u.line_note ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function FinishPanel({
  steps,
  done,
  errorStepId,
  onRetry,
  onBack,
  coaGroups,
  onApplyLibrary,
  onRerun,
}: {
  steps: CommitStepState[]
  done: boolean
  errorStepId: string | null
  onRetry: () => void
  /** Navigate back to StepReview so the user can fix the issue in an earlier step. */
  onBack: () => void
  coaGroups: CoaMappingGroup[]
  onApplyLibrary: (
    library: string,
    keys: Array<{ account_number_group: string; fiscal_year: number }>,
  ) => Promise<ApplyLibraryResponse>
  onRerun: () => void
}) {
  const allSucceeded = done && errorStepId === null

  // Local state for the library-apply recovery flow
  const [applyBusy, setApplyBusy] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [applyUnresolved, setApplyUnresolved] = useState<GlUnmappedAccount[] | null>(null)

  // ---------------------------------------------------------------------------
  // Elapsed-time tracker — one interval per running step, stored in a ref so
  // interval ids survive re-renders without being part of React state.
  // Does NOT modify CommitStepState; purely cosmetic / local to FinishPanel.
  // ---------------------------------------------------------------------------
  const [elapsedByStepId, setElapsedByStepId] = useState<Record<string, number>>({})
  const elapsedIntervalsRef = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  // Start / stop intervals as step statuses change.
  useEffect(() => {
    for (const s of steps) {
      if (s.status === 'running' && !elapsedIntervalsRef.current[s.id]) {
        // Initialise counter to 0 (non-destructive — don't reset if already counting)
        setElapsedByStepId(prev => ({ ...prev, [s.id]: prev[s.id] ?? 0 }))
        elapsedIntervalsRef.current[s.id] = setInterval(() => {
          setElapsedByStepId(prev => ({ ...prev, [s.id]: (prev[s.id] ?? 0) + 1 }))
        }, 1000)
      } else if (s.status !== 'running' && elapsedIntervalsRef.current[s.id]) {
        clearInterval(elapsedIntervalsRef.current[s.id])
        delete elapsedIntervalsRef.current[s.id]
      }
    }
  }, [steps])

  // Clear ALL remaining intervals on unmount to prevent setState-after-unmount warnings.
  useEffect(() => {
    return () => {
      for (const id of Object.keys(elapsedIntervalsRef.current)) {
        clearInterval(elapsedIntervalsRef.current[id])
      }
      elapsedIntervalsRef.current = {}
    }
  }, [])

  // Find failed step and its unmapped detail (if any)
  const failedStep = errorStepId !== null ? steps.find(s => s.id === errorStepId) : null
  const unmappedDetail = failedStep?.unmapped ?? null

  // Library detection: any group that is NOT upload-based uses a library variant
  const libraryGroup = coaGroups.find(g => g.method !== 'upload')
  const hasLibrary = libraryGroup != null
  const libraryVariant = libraryGroup?.libraryVariant ?? 'skr03'

  async function handleApplyClick() {
    if (!unmappedDetail) return
    setApplyBusy(true)
    setApplyError(null)
    setApplyUnresolved(null)
    try {
      const keys = unmappedDetail.unmapped.map(u => ({
        account_number_group: u.account_number_group,
        fiscal_year: u.fiscal_year,
      }))
      const result = await onApplyLibrary(libraryVariant, keys)
      if (result.unresolved.length > 0) {
        setApplyUnresolved(result.unresolved)
      } else {
        // All accounts resolved — re-run the full setup automatically
        onRerun()
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setApplyError(`Could not apply library mapping: ${msg}`)
    } finally {
      setApplyBusy(false)
    }
  }

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
                {(() => {
                  // When running, append elapsed time to whatever sub-label is in s.detail.
                  // When done/failed/skipped, render s.detail as-is.
                  let displayDetail: string | undefined
                  if (s.status === 'running') {
                    const secs = elapsedByStepId[s.id] ?? 0
                    const m = Math.floor(secs / 60)
                    const sec = secs % 60
                    const timeStr = `${m}:${String(sec).padStart(2, '0')} elapsed`
                    displayDetail = s.detail ? `${s.detail} — ${timeStr}` : timeStr
                  } else {
                    displayDetail = s.detail
                  }
                  return displayDetail ? (
                    <p className={`text-xs mt-0.5 ${s.status === 'failed' ? 'text-red-600' : 'text-slate-500'}`}>
                      {displayDetail}
                    </p>
                  ) : null
                })()}
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

            {/* Unmapped-accounts recovery panel — shown for GL 422 gl_accounts_unmapped */}
            {unmappedDetail && (
              <div className="mt-4 space-y-3">
                <p className="text-xs font-semibold text-red-800 uppercase tracking-wide">
                  Unmapped accounts ({unmappedDetail.total_unmapped} total)
                </p>

                <UnmappedAccountsTable rows={unmappedDetail.unmapped} />

                {unmappedDetail.truncated && (
                  <p className="text-xs text-red-600">
                    Showing 50 of {unmappedDetail.total_unmapped} unmapped accounts.
                  </p>
                )}

                {/* Library apply button — only when at least one CoA group uses a library */}
                {hasLibrary && applyUnresolved === null && (
                  <button
                    type="button"
                    disabled={applyBusy}
                    onClick={handleApplyClick}
                    className="inline-flex items-center gap-2 rounded-md border border-red-300 bg-white px-4 py-1.5 text-sm font-semibold text-red-700 hover:bg-red-50 transition disabled:opacity-60 disabled:cursor-not-allowed"
                  >
                    {applyBusy && <SpinnerIcon />}
                    Apply library mapping to these accounts
                  </button>
                )}
                {applyError && (
                  <p className="text-xs text-red-700">{applyError}</p>
                )}

                {/* Accounts that the library could not resolve */}
                {applyUnresolved && applyUnresolved.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-xs font-semibold text-red-800 uppercase tracking-wide">
                      Accounts not found in the library ({applyUnresolved.length})
                    </p>
                    <UnmappedAccountsTable rows={applyUnresolved} />
                    <p className="text-xs text-red-700">
                      These accounts are not covered by the selected library. Go back to the
                      Chart of Accounts step and upload a mapping file that includes these
                      account numbers, then retry.
                    </p>
                  </div>
                )}

              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={onRetry}
                className="rounded-md border border-red-300 bg-white px-4 py-1.5 text-sm font-semibold text-red-700 hover:bg-red-50 transition"
              >
                Retry from failed step
              </button>
              <button
                type="button"
                onClick={onBack}
                className="rounded-md border border-slate-300 bg-white px-4 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition"
              >
                Back to steps
              </button>
            </div>
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
// Danger Zone — Reset all ingested data (admin-only, non-live stacks only)
// ---------------------------------------------------------------------------

/**
 * ResetConfirmModal — two-factor confirmation:
 *   1. Checkbox "I understand this deletes all ingested data"
 *   2. Red "Reset all data" button (enabled only when checkbox is checked)
 *
 * Calls onConfirm() when the user proceeds.
 */
function ResetConfirmModal({
  onConfirm,
  onCancel,
  busy,
}: {
  onConfirm: () => void
  onCancel: () => void
  busy: boolean
}) {
  const [understood, setUnderstood] = useState(false)

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      aria-modal="true"
      role="dialog"
      aria-labelledby="reset-modal-title"
    >
      <div className="w-full max-w-lg rounded-xl border border-red-200 bg-white shadow-xl">
        {/* Header */}
        <div className="flex items-start gap-3 border-b border-red-100 bg-red-50 px-6 py-4 rounded-t-xl">
          <svg
            className="h-5 w-5 text-red-600 mt-0.5 shrink-0"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v3m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
            />
          </svg>
          <div>
            <h2
              id="reset-modal-title"
              className="text-base font-semibold text-red-800"
            >
              Reset all ingested data
            </h2>
            <p className="text-xs text-red-700 mt-0.5">
              This action is permanent and cannot be undone.
            </p>
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          <p className="text-sm text-slate-700">
            This will <strong>permanently delete</strong> all ingested data from this project,
            including:
          </p>
          <ul className="list-disc pl-5 text-sm text-slate-700 space-y-1">
            <li>All GL booking lines and journal entries</li>
            <li>Account mappings (Chart of Accounts)</li>
            <li>Legal entity definitions</li>
            <li>Customer and supplier partner masters</li>
            <li>Opening balance rows</li>
            <li>Version history snapshots</li>
          </ul>
          <p className="text-sm text-slate-700">
            <strong>Preserved:</strong> libraries, system structure, users and roles,
            and project configuration settings.
          </p>
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            After the reset the project returns to a blank baseline. You will need to
            re-run the full setup wizard to reload data.
          </div>

          {/* Explicit confirm checkbox */}
          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white px-4 py-3 hover:bg-slate-50 transition select-none">
            <input
              type="checkbox"
              checked={understood}
              onChange={e => setUnderstood(e.target.checked)}
              disabled={busy}
              className="mt-0.5 h-4 w-4 accent-red-600 shrink-0"
            />
            <span className="text-sm text-slate-800">
              I understand this permanently deletes all ingested data and cannot be undone.
            </span>
          </label>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 border-t border-slate-100 px-6 py-4">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40 transition"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!understood || busy}
            className="inline-flex items-center gap-2 rounded-md px-5 py-2 text-sm font-semibold text-white transition disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ backgroundColor: '#B91C1C' }}
          >
            {busy ? (
              <>
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                Resetting…
              </>
            ) : (
              'Reset all data'
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * DangerZone — rendered below the wizard when data_reset_allowed === true
 * (non-live stack) AND the current user is an admin.
 *
 * On live stacks (data_reset_allowed === false) this component is never mounted,
 * so there is zero risk of accidental exposure.
 */
function DangerZone({
  projectId,
  onResetComplete,
}: {
  projectId: string
  onResetComplete: () => void
}) {
  const [showModal, setShowModal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ResetProjectDataResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function handleConfirm() {
    setBusy(true)
    setError(null)
    try {
      const r = await resetProjectData(projectId)
      setResult(r)
      setShowModal(false)
      onResetComplete()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      // Surface friendly messages for known status codes
      if (/403|forbidden|not allowed/i.test(msg)) {
        setError('Access denied — only admins can reset project data, and this endpoint is disabled on live stacks.')
      } else if (/422|confirm/i.test(msg)) {
        setError('The server rejected the request (422) — confirm payload missing. Please try again.')
      } else {
        setError(msg)
      }
      setShowModal(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {showModal && (
        <ResetConfirmModal
          onConfirm={handleConfirm}
          onCancel={() => setShowModal(false)}
          busy={busy}
        />
      )}

      {/* Danger zone card */}
      <div className="mt-10 rounded-xl border-2 border-red-200 bg-white shadow-sm overflow-hidden">
        {/* Header stripe */}
        <div className="flex items-center gap-3 border-b border-red-100 bg-red-50 px-6 py-3">
          <svg
            className="h-4 w-4 text-red-600 shrink-0"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v3m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
            />
          </svg>
          <span className="text-xs font-bold uppercase tracking-wider text-red-700">
            Administrative actions
          </span>
          <span className="ml-auto rounded-full border border-red-200 bg-white px-2 py-0.5 text-xs font-semibold text-red-600">
            Admin only
          </span>
        </div>

        {/* Body */}
        <div className="px-6 py-5 space-y-4">
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div className="space-y-1 min-w-0">
              <p className="text-sm font-semibold text-slate-900">
                Reset all ingested data
              </p>
              <p className="text-xs text-slate-500 max-w-lg">
                Permanently deletes all project-related data. This action cannot be undone.
              </p>
            </div>
            <button
              type="button"
              onClick={() => { setResult(null); setError(null); setShowModal(true) }}
              disabled={busy}
              className="shrink-0 inline-flex items-center gap-2 rounded-md border-2 border-red-300 bg-white px-5 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <svg
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7h6m-7 0a2 2 0 012-2h4a2 2 0 012 2m-9 0H5m14 0h-2"
                />
              </svg>
              Reset all ingested data
            </button>
          </div>

          {/* Success toast */}
          {result && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
              <p className="font-semibold">Reset complete.</p>
            </div>
          )}

          {/* Error panel */}
          {error && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              <p className="font-semibold">Reset failed.</p>
              <p className="mt-1 text-xs">{error}</p>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// GL combine helper — mirrors hasSyntheticHeaders in GlEntityCard (not exported)
// ---------------------------------------------------------------------------

/**
 * Returns true when every non-fiscal_year column in the combined file is a
 * synthetic positional label ("Column 1", "Column 2", …), indicating the source
 * files had no header row and the user must assign GoBD labels manually.
 */
function glHasSyntheticHeaders(columns: string[]): boolean {
  const dataColumns = columns.filter(c => c !== 'fiscal_year')
  if (dataColumns.length === 0) return false
  return dataColumns.every(c => /^Column \d+$/.test(c))
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
  const { isAdmin } = useAuth()
  const [step, setStep] = useState(0)
  const [state, dispatch] = useReducer(wizardReducer, undefined, defaultState)
  const [loading, setLoading] = useState(true)
  /** Mirrors data_reset_allowed from GET /projects/{id}. Only true on non-live stacks. */
  const [dataResetAllowed, setDataResetAllowed] = useState(false)
  /** True once the Statement Structure step has resolved (auto-pass or explicit apply). */
  const [structureResolved, setStructureResolved] = useState(false)

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

        // Capture data_reset_allowed — only render Danger Zone when the backend
        // explicitly signals this stack allows resets.
        setDataResetAllowed(data.data_reset_allowed === true)

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
        setDataResetAllowed(false)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Keep state.entities in sync with the GL entity cards.
  // Once the user has typed at least one real entityCode the GL-derived list is
  // authoritative. Guard with projectEntitiesEqual to avoid infinite dispatch.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const derived = deriveProjectEntities(state.gl.entities)
    if (derived.length === 0) return   // no real GL entities yet — preserve PREFILL/default
    if (projectEntitiesEqual(derived, state.entities)) return
    dispatch({ type: 'SET_ENTITIES', entities: derived })
  }, [state.gl.entities, state.entities])

  // ---------------------------------------------------------------------------
  // GL sub-phase — mirrors the computation inside StepGlBookings so the
  // NavButtons can vary their behaviour per sub-phase without prop drilling.
  // ---------------------------------------------------------------------------
  const glAllCombined =
    state.gl.years.length > 0 &&
    state.gl.entities.length > 0 &&
    state.gl.entities.every(e => !!e.combinedFileId)
  const glAllAssigned = state.gl.entities.every(e => !!e.formatGroupId)
  const glSubPhase: 'collect' | 'assign' | 'configure' = !glAllCombined
    ? 'collect'
    : state.gl.entities.length === 1 || glAllAssigned
    ? 'configure'
    : 'assign'

  /** True when every entity has at least one year-slot filled (gaps in years are allowed). */
  const allEntitiesReadyToCombine =
    state.gl.years.length > 0 &&
    state.gl.entities.length > 0 &&
    state.gl.entities.every(e =>
      state.gl.years.some(y => e.yearFiles[y] !== undefined)
    )

  // ---------------------------------------------------------------------------
  // Batch combine state — driven from the footer Next button in collect phase
  // ---------------------------------------------------------------------------
  const [batchCombining, setBatchCombining] = useState(false)
  const [batchCombineErrors, setBatchCombineErrors] = useState<
    Array<{ index: number; entityCode: string; error: string }>
  >([])

  /**
   * Combine all entities in one pass.  Replicates the groupingMode==='external'
   * branch of GlEntityCard.handleCombine for each entity that has all year-slots
   * filled.  Dispatches PATCH_GL_ENTITY for each success; collects failures and
   * returns false if any entity failed (so the footer does not advance).
   */
  const batchCombineGlEntities = useCallback(async (): Promise<boolean> => {
    setBatchCombineErrors([])
    setBatchCombining(true)
    const errors: Array<{ index: number; entityCode: string; error: string }> = []

    const { years, entities } = state.gl
    for (let i = 0; i < entities.length; i++) {
      const entity = entities[i]

      try {
        const inputs = years
          .filter(y => entity.yearFiles[y] !== undefined)
          .map(y => ({ file_id: entity.yearFiles[y]!.fileId, fiscal_year: y }))
        // Guard: should never happen because Next is gated on ≥1 file per entity,
        // but skip safely if an entity somehow has no filled years.
        if (inputs.length === 0) continue
        const result = await combineGlFiles({ inputs })

        const isSynthetic = glHasSyntheticHeaders(result.columns)
        const colWarning = result.column_warning ?? undefined

        let patch: Partial<GlEntityState>
        if (isSynthetic) {
          // Headerless path — user will assign GoBD labels in the configure phase
          patch = {
            combinedFileId: result.file_id,
            combinedColumns: result.columns,
            combinedSample: result.sample,
            combinedDialect: result.dialect,
            combinedColumnWarning: colWarning,
            combinedSuggestedHeaders: result.suggested_headers ?? {},
            headersConfirmed: false,
            validationOk: undefined,
            assembledProfile: undefined,
            entityAssignments: undefined,
          }
        } else {
          // Headered path in external (wizard) mode: never auto-confirm; defer header
          // confirmation to the configure phase where the group representative does it.
          const preFill = Object.fromEntries(
            result.columns.filter(c => c !== 'fiscal_year').map(c => [c, c])
          )
          patch = {
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
          }
        }

        dispatch({ type: 'PATCH_GL_ENTITY', index: i, patch })
      } catch (e) {
        errors.push({
          index: i,
          entityCode: entity.entityCode || `Entity ${i + 1}`,
          error: e instanceof Error ? e.message : 'Combine failed',
        })
      }
    }

    setBatchCombining(false)
    setBatchCombineErrors(errors)
    return errors.length === 0
  }, [state.gl, dispatch])

  // ---------------------------------------------------------------------------
  // Per-step Next-disabled validation
  // ---------------------------------------------------------------------------
  function isNextDisabled(): boolean {
    if (step === 0) return state.projectName.trim().length === 0
    if (step === 1) return state.fyEndMonth < 1 || state.fyEndMonth > 12
    // Step 2 (GL): sub-phase-aware gate.
    if (step === 2) {
      if (state.gl.years.length === 0) return true
      // collect — Next triggers batch combine; enabled once every entity has all year-slots filled
      if (glSubPhase === 'collect') return !allEntitiesReadyToCombine || batchCombining
      // assign — user must complete GlFormatAssignmentStep before advancing
      if (glSubPhase === 'assign') return true
      // configure — all entities must pass validation
      return state.gl.entities.some(e => e.validationOk === undefined)
    }
    // Step 3 (CoA): blocked only for multi-entity projects that haven't confirmed
    // the group assignment yet. Once assigned (or single entity), the step is always
    // passable — per-group config warnings show inline but don't hard-block.
    if (step === 3) {
      const configurableEntities = state.entities.filter(e => e.code.trim())
      if (configurableEntities.length > 1 && !state.coa.assigned) return true
      return false
    }
    // Step 4 (OB): in_data → always ok. file_first_year/file_all → ok even without a
    // file staged (warn shown inline; user can proceed and upload later).
    if (step === 4) {
      return !state.ob.mode   // block only if mode not yet chosen (default 'in_data' so always passes)
    }
    // Step 5 (Partner): entirely optional step — never blocks Next.
    if (step === 5) return false
    // Step 6 (Additional information): optional — allow Next when no uploads provided.
    // If the user started uploading, require at least one upload with a file_id + both mappings.
    if (step === 6) {
      const { uploads, fteMapping, payrollMapping } = state.fte
      const hasUpload = uploads.some(u => u.file_ids.length > 0)
      if (!hasUpload) return false  // nothing provided — always allow Next
      const hasFteMapping    = Object.keys(fteMapping).length > 0
      const hasPayrollMapping = Object.keys(payrollMapping).length > 0
      return !(hasFteMapping && hasPayrollMapping)
    }
    // Step 7 (Statement structure): blocked until the step self-resolves (auto-pass or explicit apply).
    if (step === 7) return !structureResolved
    return false
  }

  // ---------------------------------------------------------------------------
  // Phase 5 — Finish orchestration state
  // ---------------------------------------------------------------------------
  const [commitSteps, setCommitSteps] = useState<CommitStepState[]>(() => buildInitialCommitSteps(state))
  const [finishDone, setFinishDone] = useState(false)
  const [finishErrorStepId, setFinishErrorStepId] = useState<string | null>(null)
  /** true once the user has clicked "Run Setup" — switches Review to FinishPanel */
  const [finishStarted, setFinishStarted] = useState(false)
  /** Captures the runRebuild choice so recovery re-runs can repeat the same flag. */
  const runRebuildRef = useRef(false)

  /** Mutate one step in the checklist by id. */
  const patchStep = useCallback((id: string, patch: Partial<CommitStepState>) => {
    setCommitSteps(prev => prev.map(s => s.id === id ? { ...s, ...patch } : s))
  }, [])

  // ---------------------------------------------------------------------------
  // Finish handler — sequential COLLECT-THEN-COMMIT orchestration
  // ---------------------------------------------------------------------------
  const handleRunSetup = useCallback(async (runRebuild: boolean) => {
    runRebuildRef.current = runRebuild
    setFinishStarted(true)
    setFinishDone(false)
    setFinishErrorStepId(null)
    // Reset all steps to pending so a retry starts fresh from step 1
    setCommitSteps(buildInitialCommitSteps(state).map(s =>
      s.id === 'rebuild' ? { ...s, status: runRebuild ? 'pending' : 'skipped', detail: runRebuild ? undefined : 'Rebuild checkbox not selected' } : s
    ))

    const { gl, coa, ob, partner, fte, projectName, fyEndMonth, entities: wizardEntities } = state
    const fyStart = fyStartFromEndMonth(fyEndMonth)

    // ---- Helper: run one async step, mark done/failed, return false on failure ----
    // Pass an optional `humanize` function to translate raw error messages into
    // plain English before storing them in the step detail. GL/OB/Partner steps
    // leave this unset and receive the raw message as before; CoA opts in below.
    async function runStep<T>(
      id: string,
      fn: () => Promise<T>,
      formatDetail: (result: T) => string,
      humanize?: (rawMessage: string) => string,
    ): Promise<T | null> {
      patchStep(id, { status: 'running' })
      try {
        const result = await fn()
        patchStep(id, { status: 'done', detail: formatDetail(result) })
        return result
      } catch (err) {
        const rawMsg = err instanceof Error ? err.message : String(err)
        const msg = humanize ? humanize(rawMsg) : rawMsg
        const unmapped = glUnmappedDetail(err)
        patchStep(id, { status: 'failed', detail: msg, ...(unmapped ? { unmapped } : {}) })
        setFinishErrorStepId(id)
        setFinishDone(true)
        return null
      }
    }

    // -------------------------------------------------------------------------
    // Plain-language error translator (finish flow only).
    // Ensures non-programmers never see raw Python exception text in the UI.
    // All finish steps (CoA, GL, OB, Partner, FTE, Rebuild) pass their caught
    // error through this function before storing it in the step detail.
    //
    // Rules (checked in order):
    //  1. "have no account number" — GL backend contract: preserve + prepend entity.
    //  2. "invalid literal for int()" — fiscal year not set.
    //  3. column not found / KeyError — column mapping problem.
    //  4. Traceback / psycopg2 / sqlalchemy / transaction text — per-step generic.
    //  5. Other Python exception names — per-step generic.
    //  6. Otherwise pass through (backend already returned plain English).
    // -------------------------------------------------------------------------
    function friendlyFinishError(
      raw: string,
      opts?: {
        /** Descriptive label for this step, e.g. "GL bookings", "opening balances". */
        stepLabel?: string
        /** Entity code or display name shown in the message (e.g. "ENT1"). */
        entityCode?: string
      },
    ): string {
      const { stepLabel = 'data', entityCode } = opts ?? {}
      const entityPart = entityCode ? ` for entity ${entityCode}` : ''

      // 1. Backend contract: GL 422 "have no account number" — preserve and prepend entity.
      if (/have no account number/i.test(raw)) {
        return entityCode ? `Entity ${entityCode}: ${raw}` : raw
      }

      // 2. Fiscal year missing (Python int('') → ValueError: invalid literal for int()).
      if (/invalid literal.*int|int\(.*\).*base 10/i.test(raw)) {
        return (
          `Couldn't import the ${stepLabel}${entityPart}: no fiscal year was set for the project. ` +
          `Go back to the Fiscal Year step and add at least one year.`
        )
      }

      // 3a-new. Humanized backend wording (2026-06+):
      //   GoBD header columns end with "…in the GL header step, then re-validate."
      //   Sign/amount columns end with "…Select it in the Options step, then re-validate."
      // The captured label is already friendly — use it directly, no key lookup needed.
      {
        const m = /The '([^']+)' column is not mapped/i.exec(raw)
        if (m) {
          const label = m[1]
          if (/Options step/i.test(raw)) {
            return (
              `The '${label}' column isn't mapped${entityPart}. ` +
              `Select it in the Options step, then re-validate.`
            )
          }
          return (
            `The '${label}' column isn't mapped${entityPart}. ` +
            `Go back to the GL header step and assign a column as '${label}', then re-validate.`
          )
        }
      }

      // 3a. GoBD required column missing — backend apply_profile 422 (legacy wording):
      //   "posting_date is required; map it in profile.columns['posting_date']"
      // Kept for backward compatibility; translates snake_case key to a friendly label.
      {
        const GOBD_FIELD_LABELS: Record<string, string> = {
          posting_date: 'Posting date',
          journal_entry_number: 'Transaction/journal number',
          account_number: 'Account number',
          amount: 'Amount',
        }
        const m = /^(\w+) is required; map it in profile\.columns/i.exec(raw)
        if (m) {
          const fieldKey = m[1].toLowerCase()
          const label = GOBD_FIELD_LABELS[fieldKey] ?? fieldKey
          return (
            `The '${label}' column isn't mapped${entityPart}. ` +
            `Go back to the GL header step and assign a source column as '${label}', then re-validate.`
          )
        }
      }

      // 3b. Sign/amount column missing — backend ValueError:
      //   "signAmount column is required — select it in the Options step"
      if (/column is required.*Options step/i.test(raw)) {
        return (
          `A required amount column isn't configured${entityPart}. ` +
          `Go back to the GL configuration step and set the amount sign options, then re-validate.`
        )
      }

      // 3. Column not found or required field missing (generic fallback).
      if (/column not found|missing.*required|required.*field|missing.*column|column.*missing|\bKeyError\b/i.test(raw)) {
        return `A required column couldn't be found${entityPart} — check the column mapping for this step and try again.`
      }

      // 4. Python/SQL internals — never show these raw.
      if (/Traceback|psycopg2|NotNullViolation|sqlalchemy|transaction rolled back|Load failed/i.test(raw)) {
        return (
          `Something went wrong importing the ${stepLabel} data${entityPart} — ` +
          `please check the validation step and try again.`
        )
      }

      // 5. Other Python exception class names.
      if (/\bValueError\b|\bTypeError\b|\bAttributeError\b/i.test(raw)) {
        return (
          `Something went wrong importing the ${stepLabel} data${entityPart} — ` +
          `please check the validation step and try again.`
        )
      }

      // Backend already returned plain English; pass through unchanged.
      return raw
    }

    // ---- Step 1: Save config (also creates the project's legal entities) ----
    // The PUT /projects backend upserts `entities` into dim_legal_entity up front,
    // so the Step-2 CoA (bs_pl_master) commit can resolve entity references on a
    // fresh project (order: config → entities → CoA → GL).
    const configResult = await runStep(
      'config',
      () => api.putProject(PROJECT_ID, {
        name: projectName,
        fy_start_month: fyStart,
        entities: wizardEntities.filter(e => e.code.trim() !== ''),
        // Map the wizard's 3 OB states to the 3 backend rebuild modes:
        //   in_data         → 'in_data'       (OBs already in the GL)
        //   file_first_year → 'carry_forward' (first-year OB file loaded, later years
        //                     synthesised from prior-year closings — exactly what the
        //                     file_first_year UI promises: "subsequent years carry forward")
        //   file_all        → 'file'          (every year's OB loaded from file, no synthesis)
        opening_balance_mode:
          ob.mode === 'in_data'
            ? 'in_data'
            : ob.mode === 'file_first_year'
              ? 'carry_forward'
              : 'file',
        mapping_source: coa.groups.some(g => g.method === 'upload') ? 'client_coa' : 'library',
        partner_master_source: 'files',
        net_profit_source: 'report_inject',
        sales_label: 'Sales',
        cost_label: 'Cost of materials',
        account_mapping_mode: coa.accountMappingMode,
      }),
      () => `Project "${projectName}" saved (fy_start_month=${fyStart}, account_mapping_mode=${coa.accountMappingMode})`,
    )
    if (configResult === null) return

    // ---- Step 2: CoA commit loop (must run BEFORE GL so dim_gl_account is populated) ----
    // NOTE: library groups are skipped here — library classification runs at rebuild.
    // Upload groups commit one call per (group × statement × entity) triple.
    // replace_mode: first commit overall = 'replace' (wipes prior CoA scope);
    // all subsequent commits = 'append'.
    const uploadGroups = coa.groups.filter(g => g.method === 'upload')
    if (uploadGroups.length === 0) {
      const libraryInfo = [
        ...new Set(coa.groups.map(g => g.libraryVariant ?? 'skr03')),
      ].join(', ')
      patchStep('coa', {
        status: 'skipped',
        detail: `Library classification (${libraryInfo || 'skr03'}) — applied at rebuild; no file to commit`,
      })
    } else {
      // Flatten into ordered (group × statement × entity) items.
      // buildCoaItems is an exported pure helper (coaAssignment.ts) so it can be
      // unit-tested at the commit-emission level without JSX.
      const coaItems: CoaItem[] = buildCoaItems(coa.groups, wizardEntities)

      if (coaItems.length === 0) {
        patchStep('coa', { status: 'skipped', detail: 'No CoA file staged — skipped' })
      } else {
        // Expand the single 'coa' placeholder into per-item checklist entries
        if (coaItems.length > 1) {
          setCommitSteps(prev => prev.map(s => s.id === 'coa'
            ? { id: 'coa_0', label: `CoA — ${coaItems[0].group.label} ${coaItems[0].stmt.toUpperCase()}`, status: 'pending' as const }
            : s
          ))
        }

        // Guard: CoA commits are replicated across all project fiscal years.
        // If no years have been configured yet the backend would receive an empty
        // fiscal_year value and crash (Python int('') → ValueError). Fail fast with
        // a plain-English message so the user knows exactly what to fix.
        if (state.gl.years.length === 0) {
          const noYearsMsg = 'Select at least one fiscal year before finishing.'
          const firstStepId = coaItems.length === 1 ? 'coa' : 'coa_0'
          patchStep(firstStepId, { status: 'failed', detail: noYearsMsg })
          setFinishErrorStepId(firstStepId)
          setFinishDone(true)
          return
        }

        let totalCoaAccounts = 0
        for (let i = 0; i < coaItems.length; i++) {
          const item = coaItems[i]
          const stepId = coaItems.length === 1 ? 'coa' : `coa_${i}`
          // entity_prefixes covers all member entities; fall back to group label when none set.
          const entityTag = item.entityPrefixes.length > 0 ? item.entityPrefixes.join(', ') : 'all'
          const stepLabel = `CoA — ${item.group.label} ${item.stmt.toUpperCase()} (${entityTag})`

          if (i > 0) {
            setCommitSteps(prev => {
              const insertIdx = prev.findIndex(s => s.id === `coa_${i - 1}`)
              const next = [...prev]
              next.splice(insertIdx + 1, 0, { id: stepId, label: stepLabel, status: 'pending' as const })
              return next
            })
          } else if (coaItems.length > 1) {
            setCommitSteps(prev => prev.map(s => s.id === 'coa_0' ? { ...s, label: stepLabel } : s))
          }

          // Always replace: one atomic call per group×stmt covers all member entities.
          const replace_mode = 'replace' as const

          // For generic (non-master) slots attach the column mapping profile.
          // CoA files do not have a fiscal_year column; entity is scoped via entity_prefixes.
          // fiscal_year value is the first project FY (never empty after the guard above);
          // the backend overrides it per-year when fiscal_years[] is supplied.
          // Use the first member entity code as the fixed entity value (the profile is for
          // file parsing; scoping is handled by entity_prefixes on the commit call).
          const firstMemberCode = item.group.memberEntityCodes[0] ?? ''
          const profile: AccountMappingProfile | undefined =
            !item.slot.isMaster && item.slot.mapping && Object.keys(item.slot.mapping).length > 0
              ? {
                  entity: { mode: 'fixed' as const, value: firstMemberCode },
                  fiscal_year: { mode: 'fixed' as const, value: String(state.gl.years[0] ?? '') },
                  columns: item.slot.mapping,
                }
              : undefined

          // Build display strings used by the CoA error humanizer below.
          const humanEntityTag = entityTag
          const stmtLabel = item.stmt === 'bs' ? 'Balance Sheet' : 'Profit & Loss'

          const coaResult = await runStep(
            stepId,
            () => commitAccountMapping({
              file_id: item.slot.fileId!,
              format: item.slot.isMaster ? 'bs_pl_master' : 'generic',
              statement: item.stmt,
              entity_prefixes: item.entityPrefixes.length > 0 ? item.entityPrefixes : undefined,
              replace_mode,
              // Replicate this account mapping across all project fiscal years.
              fiscal_years: state.gl.years,
              ...(profile ? { profile } : {}),
            }),
            (r: MappingCommitResponse) => {
              totalCoaAccounts += r.accounts
              return `${r.accounts} accounts (NA: ${r.na ?? 0}, CF: ${r.cf ?? 0}, mode: ${replace_mode})`
            },
            // CoA humanizer: non-programmers must never see raw exception text.
            (raw) => friendlyFinishError(raw, { stepLabel: `${stmtLabel} account mapping`, entityCode: humanEntityTag }),
          )
          if (coaResult === null) return
        }

        if (coaItems.length > 1) {
          const lastId = `coa_${coaItems.length - 1}`
          setCommitSteps(prev => prev.map(s =>
            s.id === lastId
              ? { ...s, detail: (s.detail ?? '') + ` | Total: ${totalCoaAccounts} accounts` }
              : s
          ))
        }
      }
    }

    // ---- Step 3: GL commit loop (one call per validated entity) ----
    // CoA (dim_gl_account) must be committed first — the backend 422s otherwise.
    // Each entity uses its combined file_id (all years concatenated with fiscal_year column).
    // Entity profiles always use entity: fixed + fiscal_year: column — so each entity is a
    // distinct scope; first occurrence → replace, subsequent → append (shouldn't happen in
    // normal flow but guarded anyway).
    const stagedGlEntities = gl.entities.filter(e => e.combinedFileId && e.assembledProfile)
    if (stagedGlEntities.length === 0) {
      patchStep('gl', { status: 'skipped', detail: 'No GL entity staged — skipped' })
    } else {
      const committedScopes = new Set<string>()
      let totalEntries = 0, totalLines = 0

      for (let i = 0; i < stagedGlEntities.length; i++) {
        const entity = stagedGlEntities[i]
        const entityCode = entity.entityCode.trim() || `entity_${i}`
        // Show the entity NAME the user entered (with the code in parens) rather than
        // just the bare code/prefix; fall back to the code when no name was given.
        const entityName = entity.entityLabel?.trim()
        const entityDisplay =
          entityName && entityName !== entityCode ? `${entityName} (${entityCode})` : entityCode
        const commit_mode: 'replace' | 'append' = committedScopes.has(entityCode) ? 'append' : 'replace'
        committedScopes.add(entityCode)

        const stepId = `gl_${i}`
        const stepLabel = `GL — ${entityDisplay} · entity ${i + 1} of ${stagedGlEntities.length}`

        setCommitSteps(prev => {
          if (i === 0) {
            return prev.map(s => s.id === 'gl'
              ? { id: stepId, label: stepLabel, status: 'pending' as const }
              : s
            )
          }
          const insertIdx = prev.findIndex(s => s.id === `gl_${i - 1}`)
          const next = [...prev]
          next.splice(insertIdx + 1, 0, { id: stepId, label: stepLabel, status: 'pending' as const })
          return next
        })

        // Optimistic sub-status labels — purely advisory (the POST is atomic).
        // These are cleared immediately when runStep resolves, so they never
        // overwrite the final done/failed detail.
        patchStep(stepId, { detail: 'Parsing & validating…' })
        const glSubTimers: ReturnType<typeof setTimeout>[] = [
          setTimeout(() => patchStep(stepId, { detail: 'Writing GL bookings…' }), 3000),
          setTimeout(() => patchStep(stepId, { detail: 'Deriving AR / AP / sales…' }), 8000),
        ]

        const glResult = await runStep(
          stepId,
          () => commitIngest({
            file_id: entity.combinedFileId!,
            sheet: undefined,
            profile: entity.assembledProfile!,
            dataset: 'gl',
            confirm_soft: true,
            // F-1 fix: propagate exclusions confirmed during validation so the
            // excluded rows are not written to the DB at commit time.
            exclude_line_ids: entity.excludedLineIds ?? [],
            commit_mode,
          }),
          (r: CommitResponse) => {
            totalEntries += r.entries
            totalLines += r.lines
            if (r.unchanged === true) {
              return 'Already up to date — no changes written'
            }
            return `${r.entries} entries, ${r.lines} lines (mode: ${commit_mode}, AR: ${r.ar}, AP: ${r.ap}, skipped: ${r.skipped})`
          },
          (raw) => friendlyFinishError(raw, { stepLabel: 'GL bookings', entityCode: entityDisplay }),
        )
        glSubTimers.forEach(clearTimeout)
        if (glResult === null) return
      }

      if (stagedGlEntities.length > 1) {
        const lastId = `gl_${stagedGlEntities.length - 1}`
        setCommitSteps(prev => prev.map(s =>
          s.id === lastId
            ? { ...s, detail: (s.detail ?? '') + ` | Total: ${totalEntries} entries, ${totalLines} lines` }
            : s
        ))
      }
    }

    // ---- Step 4: Opening balances commit ----
    if (ob.mode === 'in_data') {
      patchStep('ob', {
        status: 'skipped',
        detail: 'Opening balances are included in the GL data — no separate file needed',
      })
    } else if (ob.mode === 'file_first_year' && state.gl.years.length === 0) {
      // Guard: first-year scope requires at least one GL fiscal year to be configured
      patchStep('ob', {
        status: 'skipped',
        detail: 'No GL fiscal years configured — OB first-year commit requires at least one GL year; skipped',
      })
    } else if (ob.entitySource === 'per_entity') {
      // Per-entity mode: commit one file per entity
      const perObItems = wizardEntities
        .filter(e => e.code.trim())
        .map(e => ({ entity: e, perState: ob.perEntity?.[e.code] }))
        .filter(({ perState }) =>
          perState?.obFileId && perState?.obProfile?.account_col && perState?.obProfile?.amount_col &&
          (ob.mode !== 'file_all' || !!perState?.obProfile?.fiscal_year_col)
        )

      if (perObItems.length === 0) {
        patchStep('ob', {
          status: 'skipped',
          detail: ob.mode === 'file_all'
            ? 'OB all-years needs a fiscal-year column mapping — skipped'
            : 'No per-entity OB files staged with complete mapping — skipped',
        })
      } else {
        // Expand single 'ob' checklist item into 'ob_0', 'ob_1', … like the CoA loop
        if (perObItems.length > 1) {
          setCommitSteps(prev => prev.map(s => s.id === 'ob'
            ? { id: 'ob_0', label: `OB — ${perObItems[0].entity.name || perObItems[0].entity.code}`, status: 'pending' as const }
            : s
          ))
        }
        for (let i = 0; i < perObItems.length; i++) {
          const { entity: e, perState } = perObItems[i]
          const stepId = perObItems.length === 1 ? 'ob' : `ob_${i}`
          const stepLabel = `OB — ${e.name || e.code} (${e.prefix || e.code})`
          if (i > 0) {
            setCommitSteps(prev => {
              const insertIdx = prev.findIndex(s => s.id === `ob_${i - 1}`)
              const next = [...prev]
              next.splice(insertIdx + 1, 0, { id: stepId, label: stepLabel, status: 'pending' as const })
              return next
            })
          } else if (perObItems.length > 1) {
            setCommitSteps(prev => prev.map(s => s.id === 'ob_0' ? { ...s, label: stepLabel } : s))
          }

          const entityValue = e.prefix || e.code
          const perObProfile = {
            entity: { mode: 'fixed' as const, value: entityValue },
            fiscal_year: ob.mode === 'file_first_year'
              ? { mode: 'fixed' as const, value: String(state.gl.years[0] ?? '') }
              : { mode: 'column' as const, value: perState!.obProfile!.fiscal_year_col! },
            sign: { mode: 'signed' as const, amount: perState!.obProfile!.amount_col! },
            date_dayfirst: true,
            columns: {
              account_number: perState!.obProfile!.account_col!,
              amount: perState!.obProfile!.amount_col!,
            },
            linking_strategy: 'none' as const,
            entry_type: 'actual' as const,
          }
          const obResult = await runStep(
            stepId,
            () => commitOpeningBalance({
              file_id: perState!.obFileId!,
              profile: perObProfile,
              scope: ob.mode === 'file_first_year' ? 'first_year' : 'all',
            }),
            (r: ObCommitResponse) =>
              `${r.lines} lines loaded, scope="${r.scope}", fiscal years: ${r.fiscal_years.join(', ') || 'n/a'}`,
            (raw) => friendlyFinishError(raw, { stepLabel: 'opening balances', entityCode: e.code }),
          )
          if (obResult === null) return
        }
      }
    } else {
      // Combined or undefined (back-compat): single-file behavior
      if (!ob.obFileId || !ob.obProfile?.account_col || !ob.obProfile?.amount_col) {
        patchStep('ob', {
          status: 'skipped',
          detail: ob.obFileId
            ? 'OB file staged but column mapping incomplete — skipped'
            : 'No OB file staged — skipped',
        })
      } else if (ob.mode === 'file_all' && !ob.obProfile?.fiscal_year_col) {
        patchStep('ob', {
          status: 'skipped',
          detail: 'OB all-years needs a fiscal-year column mapping — skipped',
        })
      } else {
        // entity mode: column when combined+entityCol provided, else fixed to first entity (back-compat)
        const entityConfig = (ob.entitySource === 'combined' && ob.entityCol)
          ? { mode: 'column' as const, value: ob.entityCol }
          : { mode: 'fixed' as const, value: wizardEntities[0]?.code ?? '' }
        // Build label→prefix map so the backend resolves both entity names and codes
        const entityAssignments = Object.fromEntries(
          wizardEntities.filter(e => e.code.trim()).flatMap(e => {
            const pfx = e.prefix || e.code
            return [[e.name || e.code, pfx], [e.code, pfx]]
          })
        )
        const obProfile = {
          entity: entityConfig,
          entity_assignments: entityAssignments,
          fiscal_year: ob.mode === 'file_first_year'
            ? { mode: 'fixed' as const, value: String(state.gl.years[0] ?? '') }
            : { mode: 'column' as const, value: ob.obProfile.fiscal_year_col! },
          sign: { mode: 'signed' as const, amount: ob.obProfile.amount_col },
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
          (raw) => friendlyFinishError(raw, { stepLabel: 'opening balances' }),
        )
        if (obResult === null) return
      }
    }

    // ---- Step 5: Partner master commit ----
    // Build a flat list across both sides (each side can be combined or per-entity independently)
    type _PartnerItem =
      | { kind: 'combined'; side: 'customer' | 'supplier'; fileId: string; profile: PartnerMappingProfile }
      | { kind: 'per_entity'; side: 'customer' | 'supplier'; entity: { code: string; name: string; prefix: string }; fileId: string; profile: PartnerMappingProfile }
    const partnerItems: _PartnerItem[] = []
    const _validEntities = wizardEntities.filter(e => e.code.trim())
    const _defaultEntitySource = _validEntities.length > 1 ? 'per_entity' : 'combined'
    for (const side of ['customer', 'supplier'] as const) {
      const sideState = partner.sides[side]
      const entitySource = sideState.entitySource ?? _defaultEntitySource
      if (entitySource === 'per_entity') {
        for (const e of _validEntities) {
          const perState = sideState.perEntity?.[e.code]
          if (perState?.fileId && perState?.profile) {
            partnerItems.push({ kind: 'per_entity', side, entity: e, fileId: perState.fileId, profile: perState.profile })
          }
        }
      } else if (sideState.fileId && sideState.profile) {
        partnerItems.push({ kind: 'combined', side, fileId: sideState.fileId, profile: sideState.profile })
      }
    }
    if (partnerItems.length === 0) {
      patchStep('partner', {
        status: 'skipped',
        detail: 'No partner master files staged with complete mapping — skipped (optional)',
      })
    } else {
      if (partnerItems.length > 1) {
        const first = partnerItems[0]
        const firstLabel = first.kind === 'per_entity'
          ? `Partner — ${first.side} / ${first.entity.name || first.entity.code}`
          : `Partner — ${first.side}`
        setCommitSteps(prev => prev.map(s => s.id === 'partner'
          ? { id: 'partner_0', label: firstLabel, status: 'pending' as const }
          : s
        ))
      }
      for (let i = 0; i < partnerItems.length; i++) {
        const item = partnerItems[i]
        const stepId = partnerItems.length === 1 ? 'partner' : `partner_${i}`
        const stepLabel = item.kind === 'per_entity'
          ? `Partner — ${item.side} / ${item.entity.name || item.entity.code} (${item.entity.prefix || item.entity.code})`
          : `Partner — ${item.side}`
        if (i > 0) {
          setCommitSteps(prev => {
            const insertIdx = prev.findIndex(s => s.id === `partner_${i - 1}`)
            const next = [...prev]
            next.splice(insertIdx + 1, 0, { id: stepId, label: stepLabel, status: 'pending' as const })
            return next
          })
        } else if (partnerItems.length > 1) {
          setCommitSteps(prev => prev.map(s => s.id === 'partner_0' ? { ...s, label: stepLabel } : s))
        }
        const commitProfile = item.kind === 'per_entity'
          ? { ...item.profile, entity: { mode: 'fixed' as const, value: item.entity.prefix || item.entity.code } }
          : item.profile
        const partnerEntityCode = item.kind === 'per_entity' ? (item.entity.prefix || item.entity.code) : undefined
        const partnerResult = await runStep(
          stepId,
          () => commitPartnerMaster({ file_id: item.fileId, profile: commitProfile }),
          (r: PartnerCommitResponse) =>
            `${r.upserted} ${r.side === 'customer' ? 'customers' : 'suppliers'} upserted into dim_${r.side}`,
          (raw) => friendlyFinishError(raw, { stepLabel: `partner master (${item.side})`, entityCode: partnerEntityCode }),
        )
        if (partnerResult === null) return
      }
    }

    // ---- Step 6: FTE Development (optional — non-blocking on failure) ----
    if (!state.additionalDatasets.fte || fte.uploads.length === 0 || !fte.uploads.some(u => u.file_ids.length > 0)) {
      patchStep('fte', { status: 'skipped', detail: 'No FTE data provided — skipped (optional)' })
    } else {
      const glYears = gl.years
      const firstFy = glYears.length > 0 ? Math.min(...glYears) : 2022
      const lastFy  = glYears.length > 0 ? Math.max(...glYears) : 2025
      const entityNames = wizardEntities.filter(e => e.code.trim()).map(e => e.name || e.code)

      // fte.uploads already has one entry per entity×FY (expanded at upload time in
      // StepAdditionalInformation.handleFileUpload), so they map directly to entity_year_files.
      const entity_year_files: FteDevelopmentRequest['entity_year_files'] = fte.uploads

      const fteReq: FteDevelopmentRequest = {
        session_id:      fte.sessionId,
        entity_year_files,
        entity_names:    entityNames,
        output_folder:   '',
        view_mode:       fte.viewMode,
        upload_mode:     fte.uploadMode,
        first_fy:        firstFy,
        last_fy:         lastFy,
        fte_mapping:     fte.fteMapping,
        fte_tenure_mode: fte.tenureMode,
        payroll_mapping: fte.payrollMapping,
        fte_payroll_mode: fte.payrollMode,
        dimensions:      fte.dimensions,
        preset_metrics:  fte.presetMetrics,
        custom_metrics:  fte.customMetrics,
        pex_view_mode:   fte.pexViewMode,
        pex_values:      fte.pexValues,
        fy_end_month:    fyEndMonth,
        fy_end_day:      31,
        formula_mode:    true,
      }

      patchStep('fte', { status: 'running' })
      try {
        const r = await runFteDevelopment(fteReq)
        // Resolve filename: prefer output_filename, fall back to output_file, then construct.
        const filename =
          r.output_filename ??
          r.output_file ??
          `${fte.sessionId}_FTE_Development.xlsx`
        patchStep('fte', {
          status: r.success ? 'done' : 'failed',
          detail: r.success
            ? `FTE Development workbook built: ${filename}`
            : `Run completed with errors: ${r.message}`,
        })
        // A non-success response is surfaced as 'failed' detail but does NOT abort Finish.
      } catch (err) {
        const rawMsg = err instanceof Error ? err.message : String(err)
        patchStep('fte', { status: 'failed', detail: friendlyFinishError(rawMsg, { stepLabel: 'FTE development' }) })
        // Intentionally do NOT set finishErrorStepId here — FTE failure is non-blocking.
        // The Finish sequence continues to rebuild.
      }
    }

    // ---- Step 7: Rebuild (optional) ----
    if (!runRebuild) {
      // already marked skipped in initial reset above
    } else {
      patchStep('rebuild', { status: 'running' })
      try {
        await api.rebuildProject(PROJECT_ID)
        patchStep('rebuild', { status: 'done', detail: 'Rebuild triggered successfully' })
      } catch (err) {
        const rawMsg = err instanceof Error ? err.message : String(err)
        // 503 = rebuild_on_commit disabled — treat as info, not hard failure
        if (/503|rebuild_on_commit|disabled/i.test(rawMsg)) {
          patchStep('rebuild', {
            status: 'skipped',
            detail: 'Rebuild endpoint returned 503 — rebuild_on_commit is disabled on this server. Trigger a rebuild manually from the Admin panel.',
          })
        } else {
          patchStep('rebuild', { status: 'failed', detail: friendlyFinishError(rawMsg, { stepLabel: 'rebuild' }) })
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
    setCommitSteps(buildInitialCommitSteps(state))
  }, [state])

  /**
   * Return from FinishPanel to StepReview so the user can navigate back to an
   * earlier step, fix the issue (e.g. exclude rows in GL validation), and
   * return.  Intentionally does NOT reset commitSteps / finishDone — a fresh
   * "Run Setup" call from StepReview resets those when needed.  Wizard reducer
   * state (entities, gl.excludedLineIds, CoA mapping, etc.) is never touched
   * here so all collected data survives the round-trip.
   */
  const handleGoBack = useCallback(() => {
    setFinishStarted(false)
  }, [])

  /** Forward the apply-library call from FinishPanel to the backend. */
  const handleApplyLibrary = useCallback(async (
    library: string,
    keys: Array<{ account_number_group: string; fiscal_year: number }>,
  ): Promise<ApplyLibraryResponse> => {
    return applyLibraryMapping({ library, keys })
  }, [])

  /**
   * Re-run the full setup after a successful library-mapping apply.
   * Repeats the same rebuild flag that the user originally chose.
   */
  const handleRerunAfterApply = useCallback(() => {
    handleRunSetup(runRebuildRef.current)
  }, [handleRunSetup])

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

        {/* Stepper — completed steps are clickable for back-navigation.
            Navigation is blocked while a finish run is actively in progress
            (finishStarted && !finishDone and no error yet). */}
        <Stepper
          current={step}
          steps={WIZARD_STEPS}
          onStepClick={(idx) => {
            // Block mid-run navigation; allow once done or stopped on error.
            const isRunning = finishStarted && !finishDone && finishErrorStepId === null
            if (isRunning) return
            setFinishStarted(false)
            setStep(idx)
          }}
        />

        {/* Step content */}
        <div className="mt-2">
          {step === 0 && <StepProjectName state={state} dispatch={dispatch} />}
          {step === 1 && <StepFiscalYear state={state} dispatch={dispatch} />}
          {step === 2 && (
            <StepGlBookings
              gl={state.gl}
              fyEndMonth={state.fyEndMonth}
              dispatch={dispatch}
              batchCombining={batchCombining}
              batchCombineErrors={batchCombineErrors}
            />
          )}
          {step === 3 && (
            <StepChartOfAccounts
              coa={state.coa}
              gl={state.gl}
              dispatch={dispatch}
              entities={state.entities}
            />
          )}
          {step === 4 && <StepOpeningBalances ob={state.ob} dispatch={dispatch} entities={state.entities} />}
          {step === 5 && <StepPartnerMaster partner={state.partner} dispatch={dispatch} entities={state.entities} />}
          {step === 6 && (
            <StepAdditionalInformation
              fte={state.fte}
              anlagen={state.anlagen}
              opos={state.opos}
              entities={state.entities}
              glYears={state.gl.years}
              fyEndMonth={state.fyEndMonth}
              additionalDatasets={state.additionalDatasets}
              dispatch={dispatch}
            />
          )}
          {step === 7 && (
            <StatementStructureStep
              projectId={PROJECT_ID}
              fiscalYears={state.gl.years}
              entityPrefixes={state.entities.filter(e => e.code.trim()).map(e => e.prefix || e.code)}
              onResolvedChange={setStructureResolved}
            />
          )}
          {step === 8 && !finishStarted && (
            <StepReview state={state} onRunSetup={handleRunSetup} />
          )}
          {step === 8 && finishStarted && (
            <FinishPanel
              steps={commitSteps}
              done={finishDone}
              errorStepId={finishErrorStepId}
              onRetry={handleRetry}
              onBack={handleGoBack}
              coaGroups={state.coa.groups}
              onApplyLibrary={handleApplyLibrary}
              onRerun={handleRerunAfterApply}
            />
          )}
        </div>

        {/* Navigation — hide Back/Next while finish is running or done */}
        {!(step === 8 && finishStarted) && (
          <NavButtons
            step={step}
            totalSteps={totalSteps}
            onBack={() => {
              // From GL assign or configure sub-phase: Back resets combined state and
              // returns to collect so the user can add/remove entities or re-upload.
              if (step === 2 && (glSubPhase === 'assign' || glSubPhase === 'configure')) {
                if (window.confirm(
                  'Going back will clear all combined files and format groups. ' +
                  'You can re-upload or add/remove entities and combine again. Continue?'
                )) {
                  dispatch({ type: 'RESET_GL_COMBINE' })
                  setBatchCombineErrors([])
                }
                // Stay on step 2 regardless — don't go to step 1
                return
              }
              setStep(s => Math.max(0, s - 1))
            }}
            onNext={() => {
              // GL collect phase: Next triggers batch combine instead of advancing the step.
              // The phase transition (collect → assign/configure) happens automatically via
              // derived glSubPhase once entities get their combinedFileId set.
              if (step === 2 && glSubPhase === 'collect') {
                void batchCombineGlEntities()
                return
              }
              setStep(s => Math.min(totalSteps - 1, s + 1))
            }}
            nextDisabled={isNextDisabled()}
            nextLoading={step === 2 && glSubPhase === 'collect' && batchCombining}
            nextLabel={
              step === 2 && glSubPhase === 'collect'
                ? 'Combine files & continue'
                : step === 2 && glSubPhase === 'assign'
                ? 'Complete format assignment above'
                : step === totalSteps - 2
                ? 'Review'
                : 'Next'
            }
          />
        )}

        {/* Danger Zone — only on non-live stacks (data_reset_allowed=true) and for admins.
            The backend enforces admin + ALLOW_DATA_RESET server-side too; this is a UI gate only.
            On the live 5176 stack data_reset_allowed is always false, so this never renders. */}
        {step === 0 && dataResetAllowed && isAdmin && (
          <DangerZone
            projectId={PROJECT_ID}
            onResetComplete={() => {
              // Reset wizard local state back to the blank default so the UI
              // reflects the now-empty project without requiring a page reload.
              dispatch({ type: 'PREFILL', partial: defaultState() })
              setStep(0)
              setFinishStarted(false)
              setFinishDone(false)
              setFinishErrorStepId(null)
              setCommitSteps(buildInitialCommitSteps(defaultState()))
            }}
          />
        )}
      </div>
    </div>
  )
}
