/**
 * Step-builder and result-converter for the Fixed-Asset Register column mapper.
 * Used by AnlagenStep.tsx with StepColumnMapper.
 *
 * Fields are importance-ordered with generic, system-agnostic labels/hints:
 * the six ESSENTIAL rollforward inputs first, then (combined mode) the entity
 * column, then a few OPTIONAL breakdown/description columns at the end.
 */

import type { Answers, MapperStep } from './stepMapperTypes'
import { getColumnId } from './stepMapperTypes'

interface AnlagenField {
  key: string
  label: string
  hint?: string
  required?: boolean
}

// Essential rollforward inputs — asset_id required, the rest skippable.
// Order = importance (opening NBV → movements → closing NBV).
const ESSENTIAL_FIELDS: AnlagenField[] = [
  { key: 'asset_id',         label: 'Asset number',                        hint: 'Unique identifier of each fixed asset.',        required: true },
  { key: 'opening_nbv',      label: 'Opening net book value',              hint: 'Carrying amount at the start of the fiscal year.' },
  { key: 'additions_zugang', label: 'Additions / CapEx',                   hint: 'Capital additions during the period.' },
  { key: 'disposals_abgang', label: 'Disposals / retirements' },
  { key: 'depreciation',     label: 'Depreciation & amortization (period)' },
  { key: 'nbv',              label: 'Closing net book value',              hint: 'Carrying amount at period end.' },
]

// Optional description / breakdown columns — shown last, always skippable.
const OPTIONAL_FIELDS: AnlagenField[] = [
  { key: 'asset_label',    label: 'Asset description' },
  { key: 'segment',        label: 'Business segment / cost center', hint: 'Optional breakdown dimension for drill-downs.' },
  { key: 'bilanzposition', label: 'Balance-sheet line',             hint: 'Optional breakdown dimension for drill-downs.' },
]

// Every target field the commit column_map may carry (order-independent).
const ANLAGEN_TARGET_FIELDS: AnlagenField[] = [...ESSENTIAL_FIELDS, ...OPTIONAL_FIELDS]

export interface AnlagenResult {
  columnMap: Record<string, string>
  entityColumn?: string
}

/**
 * Builds the ordered list of mapping steps for the fixed-asset register.
 *
 * Step order:
 *   1. Six ESSENTIAL `column` steps (asset_id required, rest skippable).
 *   2. IF viewMode === 'combined': a required `column` step for the entity column.
 *   3. Three OPTIONAL, skippable `column` steps (description + breakdown dims).
 *
 * Total: 9 steps (per_entity) or 10 steps (combined).
 */
export function buildAnlagenSteps(viewMode: string): MapperStep[] {
  const steps: MapperStep[] = []

  for (const field of ESSENTIAL_FIELDS) {
    steps.push({
      kind: 'column',
      role: field.key,
      label: field.label,
      hint: field.hint,
      required: field.required ?? false,
      skippable: !field.required,
    })
  }

  // Entity column (combined entity mode only)
  if (viewMode === 'combined') {
    steps.push({
      kind: 'column',
      role: 'entity_column',
      label: 'Entity column',
      hint: 'Column that identifies the legal entity for each row.',
      required: true,
      skippable: false,
    })
  }

  for (const field of OPTIONAL_FIELDS) {
    steps.push({
      kind: 'column',
      role: field.key,
      label: field.label,
      hint: field.hint,
      required: false,
      skippable: true,
    })
  }

  return steps
}

/**
 * Converts the mapper's Answers into the payload consumed by commitAnlagen /
 * stored in WizardAnlagenState:
 *   columnMap     — { target_field: source_column } (nulls/skipped excluded)
 *   entityColumn  — source column for entity identification (combined mode)
 */
export function toAnlagenResult(answers: Answers): AnlagenResult {
  const columnMap: Record<string, string> = {}
  for (const field of ANLAGEN_TARGET_FIELDS) {
    const id = getColumnId(answers, field.key)
    if (id !== null) columnMap[field.key] = id
  }

  const entityColId = getColumnId(answers, 'entity_column')
  const entityColumn = entityColId ?? undefined

  return { columnMap, entityColumn }
}
