/**
 * Step-builder and result-converter for the Fixed-Asset Register column mapper.
 * Used by AnlagenStep.tsx with StepColumnMapper.
 */

import type { Answers, MapperStep } from './stepMapperTypes'
import { getColumnId } from './stepMapperTypes'

// Keep in sync with ANLAGEN_TARGET_FIELDS in AnlagenStep.tsx
const ANLAGEN_TARGET_FIELDS: Array<{ key: string; label: string; required: boolean }> = [
  { key: 'asset_id',            label: 'Anlage (asset ID)',           required: true  },
  { key: 'asset_label',         label: 'Anlagenbezeichnung',          required: false },
  { key: 'asset_sub_no',        label: 'Asset sub-number',            required: false },
  { key: 'asset_class',         label: 'Asset class',                 required: false },
  { key: 'segment',             label: 'Segment',                     required: false },
  { key: 'bilanzposition',      label: 'Balance sheet line',          required: false },
  { key: 'capitalization_date', label: 'Capitalization date',         required: false },
  { key: 'opening_cost_ahk',    label: 'Opening cost (AHK)',          required: false },
  { key: 'additions_zugang',    label: 'Additions',                   required: false },
  { key: 'disposals_abgang',    label: 'Disposals',                   required: false },
  { key: 'transfers_umbuchung', label: 'Transfers',                   required: false },
  { key: 'depreciation',        label: 'Depreciation',                required: false },
  { key: 'nbv',                 label: 'Net book value (NBV)',         required: false },
]

export interface AnlagenResult {
  columnMap: Record<string, string>
  entityColumn?: string
  dimensions: {
    segmentCol?: string
    assetClassCol?: string
    bilanzpositionCol?: string
  }
}

/**
 * Builds the ordered list of mapping steps for the fixed-asset register.
 *
 * Step order:
 *   1. One `column` step per ANLAGEN_TARGET_FIELDS entry (asset_id required, rest skippable).
 *   2. IF viewMode === 'combined': a required `column` step for the entity column.
 *   3. Three skippable `column` steps for drill-down dimensions.
 *
 * Total: 16 steps (per_entity) or 17 steps (combined).
 */
export function buildAnlagenSteps(viewMode: string): MapperStep[] {
  const steps: MapperStep[] = []

  // Target-field steps
  for (const field of ANLAGEN_TARGET_FIELDS) {
    steps.push({
      kind: 'column',
      role: field.key,
      label: field.label,
      required: field.required,
      skippable: !field.required,
    })
  }

  // Entity column (combined entity mode only)
  if (viewMode === 'combined') {
    steps.push({
      kind: 'column',
      role: 'entity_column',
      label: 'Entity column',
      required: true,
      skippable: false,
    })
  }

  // Breakdown-dimension steps (always shown, always skippable)
  steps.push(
    { kind: 'column', role: 'segmentCol',        label: 'Segment column',            required: false, skippable: true },
    { kind: 'column', role: 'assetClassCol',     label: 'Asset class column',         required: false, skippable: true },
    { kind: 'column', role: 'bilanzpositionCol', label: 'Balance-sheet line column',  required: false, skippable: true },
  )

  return steps
}

/**
 * Converts the mapper's Answers into the payload consumed by commitAnlagen /
 * stored in WizardAnlagenState:
 *   columnMap     — { target_field: source_column } (nulls/skipped excluded)
 *   entityColumn  — source column for entity identification (combined mode)
 *   dimensions    — breakdown column assignments for drill-downs
 */
export function toAnlagenResult(answers: Answers): AnlagenResult {
  const columnMap: Record<string, string> = {}
  for (const field of ANLAGEN_TARGET_FIELDS) {
    const id = getColumnId(answers, field.key)
    if (id !== null) columnMap[field.key] = id
  }

  const entityColId = getColumnId(answers, 'entity_column')
  const entityColumn = entityColId ?? undefined

  const dimensions = {
    segmentCol:        getColumnId(answers, 'segmentCol')        ?? undefined,
    assetClassCol:     getColumnId(answers, 'assetClassCol')     ?? undefined,
    bilanzpositionCol: getColumnId(answers, 'bilanzpositionCol') ?? undefined,
  }

  return { columnMap, entityColumn, dimensions }
}
