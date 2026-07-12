/**
 * Step builder and result converter for the unified FTE + Payroll column mapping flow.
 *
 * Used by StepColumnMapper inside ProjectSetupWizard's Additional Information step.
 * Produces payloads byte-for-byte compatible with the old FteColumnMapper buildPayload()
 * (src/components/fdd-bot/FteColumnMapper.tsx).
 *
 * Key contract: column header (name) → *_col fields; column id (Excel letter) → column_names.
 */

import type { Answers, MapperStep, NormalizedPreview } from './stepMapperTypes'
import { getChoice, getChecklist, getColumnId, getCustom, getMulti } from './stepMapperTypes'

// ── Preset metric options (kept in sync with ProjectSetupWizard.tsx constant) ──

const FTE_PRESET_METRIC_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'fte',              label: 'Average FTEs # (whole numbers)' },
  { value: 'payroll',          label: 'Payroll accounting (EURk)' },
  { value: 'avg_cost_per_fte', label: 'Average cost per FTE (EURk)' },
]

// ── Step builder ───────────────────────────────────────────────────────────────

/**
 * Build the ordered step list for the unified FTE + Payroll mapper.
 *
 * Called on every StepColumnMapper render — gating is implemented by deriving
 * subsequent steps from current answers so the list contracts/expands automatically
 * as the user navigates backward or forward.
 *
 * @param answers    Current answers map (may be empty on the initial render).
 * @param uploadMode Value from WizardFteState.uploadMode (e.g. 'per_fy_grid').
 *                   When 'single_combined_file', an extra fiscal year column step appears.
 */
export function buildFteSteps(answers: Answers, uploadMode: string): MapperStep[] {
  const steps: MapperStep[] = []

  // 1. Tenure basis — gating choice (determines which columns appear in step 3)
  steps.push({
    kind: 'choice',
    role: 'tenure_basis',
    label: 'FTE tenure basis',
    options: [
      { value: 'months_col',       label: 'Column with months employed in the year' },
      { value: 'entry_exit_dates', label: 'Entry / exit date columns' },
    ],
    gating: true,
  })

  // 2. Employment % — required for every tenure mode
  steps.push({
    kind: 'column',
    role: 'employment_pct',
    label: 'Employment % column',
    required: true,
  })

  // 3. Tenure columns — conditional on tenure_basis answer (default: months_col)
  const tenureBasis = getChoice(answers, 'tenure_basis') ?? 'months_col'
  if (tenureBasis === 'months_col') {
    steps.push({ kind: 'column', role: 'months', label: 'Months employed in FY', required: true })
  } else {
    steps.push({ kind: 'column', role: 'entry', label: 'Entry date column',  required: true })
    steps.push({ kind: 'column', role: 'exit',  label: 'Exit date column',   skippable: true })
  }

  // 4. Fiscal year column — only for single-file-per-combined-year upload mode
  if (uploadMode === 'single_combined_file') {
    steps.push({ kind: 'column', role: 'year', label: 'Fiscal year column' })
  }

  // 5. Payroll basis — gating choice (determines which columns appear in step 6)
  steps.push({
    kind: 'choice',
    role: 'payroll_basis',
    label: 'Payroll cost basis',
    options: [
      { value: 'sum_components', label: 'Sum of cost components' },
      { value: 'total_col',      label: 'Total personnel cost column' },
      { value: 'monthly_col',    label: 'Monthly personnel cost column' },
    ],
    gating: true,
  })

  // 6. Payroll columns — conditional on payroll_basis answer (default: sum_components)
  const payrollBasis = getChoice(answers, 'payroll_basis') ?? 'sum_components'
  if (payrollBasis === 'sum_components') {
    steps.push({
      kind: 'multiColumn',
      role: 'component_cols',
      label: 'Cost component columns',
      max: 8,
    })
  } else if (payrollBasis === 'total_col') {
    steps.push({ kind: 'column', role: 'total',  label: 'Total personnel cost', required: true })
    steps.push({ kind: 'column', role: 'social', label: 'Social security cost', skippable: true })
  } else {
    // monthly_col
    steps.push({ kind: 'column', role: 'monthly', label: 'Monthly personnel cost', required: true })
    steps.push({ kind: 'column', role: 'social',  label: 'Social security cost',   skippable: true })
  }

  // 7. Breakdown dimensions — optional, max 3
  steps.push({
    kind: 'multiColumn',
    role: 'dimensions',
    label: 'Breakdown dimensions (add up to 3)',
    max: 3,
    withLabels: true,
    skippable: true,
  })

  // 8. Preset metrics — optional checklist
  steps.push({
    kind: 'checklist',
    role: 'preset_metrics',
    label: 'Preset output metrics',
    options: FTE_PRESET_METRIC_OPTIONS,
    skippable: true,
  })

  // 9. Custom output columns — optional
  steps.push({
    kind: 'custom',
    role: 'custom_columns',
    label: 'Custom output columns',
    component: 'customOutputColumns',
    skippable: true,
  })

  return steps
}

// ── Result type ────────────────────────────────────────────────────────────────

export interface FteStepResult {
  /** FTE tenure mapping — mirrors FteColumnMapper buildPayload(mode='fte'). */
  fteMapping: Record<string, unknown>
  /** Payroll mapping — mirrors FteColumnMapper buildPayload(mode='payroll'). */
  payrollMapping: Record<string, unknown>
  /** Resolved tenure mode choice. */
  tenureMode: string
  /** Resolved payroll mode choice. */
  payrollMode: string
  /**
   * Breakdown dimensions.
   * source_col = header name (not letter), output_label = user label,
   * source_letter = Excel column letter id — matches FteDimension shape.
   */
  dimensions: Array<{ source_col: string; output_label: string; source_letter?: string }>
  /** Selected preset metric keys. */
  presetMetrics: string[]
  /** Custom output column pairs. */
  customMetrics: Array<{ source_col: string; output_label: string }>
}

// ── Internal helper ────────────────────────────────────────────────────────────

/**
 * Resolve a column's header name (display string) from its id in a NormalizedPreview.
 * Returns undefined when id is falsy — callers guard with `if (id) { … }`.
 */
function headerById(preview: NormalizedPreview, id: string | null | undefined): string | undefined {
  if (!id) return undefined
  return preview.columns.find(c => c.id === id)?.header
}

// ── Result converter ───────────────────────────────────────────────────────────

/**
 * Convert StepColumnMapper answers to the payload shapes expected by the backend.
 *
 * Contract (mirrors FteColumnMapper.buildPayload()):
 * - *_col fields  → column header (name) from preview.columns[i].header
 * - column_names  → column id (Excel letter) from preview.columns[i].id
 *
 * The two identities MUST NOT be mixed — fromLetterPreview() sets id=letter, header=name.
 *
 * @param answers  Confirmed answers from StepColumnMapper.
 * @param preview  NormalizedPreview produced by fromLetterPreview().
 */
export function toFteResult(answers: Answers, preview: NormalizedPreview): FteStepResult {
  const tenureMode  = getChoice(answers, 'tenure_basis')  ?? 'months_col'
  const payrollMode = getChoice(answers, 'payroll_basis') ?? 'sum_components'

  // ── fteMapping — mirrors buildPayload() with mode='fte' ────────────────────
  const fteColNames: Record<string, string> = {}
  const fteMapping: Record<string, unknown> = {
    tenure_mode:  tenureMode,
    payroll_mode: payrollMode,
    column_names: fteColNames,
  }

  const empPctId = getColumnId(answers, 'employment_pct')
  if (empPctId) {
    fteMapping.employment_pct_col = headerById(preview, empPctId)
    fteColNames.employment_pct    = empPctId
  }

  if (tenureMode === 'months_col') {
    const mId = getColumnId(answers, 'months')
    if (mId) {
      fteMapping.months_col = headerById(preview, mId)
      fteColNames.months    = mId
    }
  } else {
    const enId = getColumnId(answers, 'entry')
    const exId = getColumnId(answers, 'exit')
    if (enId) {
      fteMapping.entry_col = headerById(preview, enId)
      fteColNames.entry    = enId
    }
    if (exId) {
      fteMapping.exit_col = headerById(preview, exId)
      fteColNames.exit    = exId
    }
  }

  const yrId = getColumnId(answers, 'year')
  if (yrId) {
    fteMapping.year_col  = headerById(preview, yrId)
    fteColNames.year     = yrId
  }

  // ── payrollMapping — mirrors buildPayload() with mode='payroll' ────────────
  const payColNames: Record<string, string> = {}
  const payrollMapping: Record<string, unknown> = {
    tenure_mode:  tenureMode,
    payroll_mode: payrollMode,
    column_names: payColNames,
  }

  if (payrollMode === 'sum_components') {
    const compItems = getMulti(answers, 'component_cols')
    payrollMapping.component_cols = compItems.map(i => headerById(preview, i.id) ?? i.id)
    payColNames.components        = compItems.map(i => i.id).join(',')
  } else if (payrollMode === 'monthly_col') {
    const mcId = getColumnId(answers, 'monthly')
    if (mcId) {
      payrollMapping.monthly_col = headerById(preview, mcId)
      payColNames.monthly        = mcId
    }
  } else {
    // total_col
    const tcId = getColumnId(answers, 'total')
    if (tcId) {
      payrollMapping.total_col = headerById(preview, tcId)
      payColNames.total        = tcId
    }
  }

  const scId = getColumnId(answers, 'social')
  if (scId) {
    payrollMapping.social_col = headerById(preview, scId)
    payColNames.social        = scId
  }

  // ── Dimensions ─────────────────────────────────────────────────────────────
  // source_col = resolved header name (matching FteDimensionPicker output)
  const dimItems  = getMulti(answers, 'dimensions')
  const dimensions = dimItems.map(item => ({
    source_col:    headerById(preview, item.id) ?? item.id,
    output_label:  item.label ?? (headerById(preview, item.id) ?? item.id),
    source_letter: item.id,
  }))

  // ── Preset metrics ─────────────────────────────────────────────────────────
  const presetMetrics = getChecklist(answers, 'preset_metrics')

  // ── Custom metrics ─────────────────────────────────────────────────────────
  // In StepColumnMapper the custom step stores column IDs (letters) in source_col.
  // Convert to header names to match the backend's expected format.
  const customRows    = getCustom(answers, 'custom_columns')
  const customMetrics = customRows.map(r => ({
    source_col:   headerById(preview, r.source_col) ?? r.source_col,
    output_label: r.output_label,
  }))

  return {
    fteMapping,
    payrollMapping,
    tenureMode,
    payrollMode,
    dimensions,
    presetMetrics,
    customMetrics,
  }
}
