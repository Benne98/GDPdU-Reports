/**
 * Step-builder and result-converter for the OPOS / Aging column mapper.
 * Used by OposStep.tsx (both OposSidePanel and OposCombinedSidesPanel)
 * with StepColumnMapper.
 */

import type { Answers, MapperStep, NormalizedPreview } from './stepMapperTypes'
import { getColumnId, getChoice } from './stepMapperTypes'

/**
 * Canonical target-field set for the OPOS open-items mapping.
 * Kept in sync with backend _TARGET_FIELDS (app/routers/opos.py).
 *
 * partner_no label is OVERRIDDEN per-side in buildOposSteps — the value here
 * is the generic fallback used when no side context is available.
 *
 * Fields removed vs. old set: referenz (not consumed by opos_aging.py).
 * Fields added:  partner_no (critical — NULL partner_key drops all aging rows),
 *                satzart, buchungskreis (display / entity resolution).
 */
const OPOS_TARGET_FIELDS: Array<{ key: string; label: string; required: boolean }> = [
  { key: 'partner_no',          label: 'Debitor / Kreditor number (partner)',                                           required: true  },
  { key: 'konto',               label: 'G/L reconciliation account (LuL trade account)',                                required: true  },
  { key: 'amount_hauswaehrung', label: 'Open amount in house currency (signed)',                                         required: true  },
  { key: 'net_due_date',        label: 'Net due date',                                                                  required: true  },
  { key: 'posting_date',        label: 'Posting date',                                                                  required: true  },
  { key: 'belegart',            label: 'Document type (needed for correct aging buckets)',                               required: false },
  { key: 'beleg_no',            label: 'Document number',                                                               required: false },
  { key: 'satzart',             label: 'Record type',                                                                   required: false },
  { key: 'buchungskreis',       label: 'Company code (Buchungskreis) — identifies the entity per row',             required: false },
]

export interface OposResult {
  columnMap: Record<string, string>
  entityColumn?: string
  sideColumn?: string
  debitorValue?: string
  kreditorValue?: string
}

/** Extracts up to 20 distinct non-empty sample values for a column from the preview. */
function distinctSampleValues(
  colId: string,
  preview: NormalizedPreview,
): Array<{ value: string; label: string }> {
  const col = preview.columns.find(c => c.id === colId)
  if (!col) return []
  const uniq = [...new Set(col.sampleValues.filter(v => v !== ''))]
  return uniq.slice(0, 20).map(v => ({ value: v, label: v }))
}

/**
 * Builds the ordered list of mapping steps for the OPOS open-items list.
 *
 * Step order:
 *   1. One `column` step per OPOS_TARGET_FIELDS entry.
 *      Required: partner_no, konto, amount_hauswaehrung, net_due_date, posting_date.
 *      Skippable: belegart, beleg_no, satzart, buchungskreis.
 *   2. IF viewMode === 'combined': a skippable `column` step for the entity column.
 *   3. IF loadMode === 'combined_sides':
 *        – required `column` step for the side discriminator column.
 *        – `choice` step for the Debitor value (options from sample).
 *        – `choice` step for the Kreditor value (options from sample).
 *
 * @param side - When provided, the partner_no step label is made side-aware:
 *   'debitor'  → "Debitor number (customer)"
 *   'kreditor' → "Kreditor number (supplier)"
 *   undefined  → "Debitor / Kreditor number (partner)"  (combined-sides / unknown)
 *
 * Total steps (9 target fields):
 *   per_side + per_entity      :  9
 *   per_side + combined        : 10
 *   combined_sides + per_entity: 12
 *   combined_sides + combined  : 13
 */
export function buildOposSteps(
  loadMode: string,
  viewMode: string,
  side?: 'debitor' | 'kreditor',
): MapperStep[] {
  const partnerLabel =
    side === 'debitor'  ? 'Debitor number (customer)'          :
    side === 'kreditor' ? 'Kreditor number (supplier)'         :
                          'Debitor / Kreditor number (partner)'

  const steps: MapperStep[] = []

  // Target-field steps
  for (const field of OPOS_TARGET_FIELDS) {
    steps.push({
      kind: 'column',
      role: field.key,
      label: field.key === 'partner_no' ? partnerLabel : field.label,
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
      required: false,
      skippable: true,
    })
  }

  // Combined-sides: side discriminator + debitor/kreditor value pickers
  if (loadMode === 'combined_sides') {
    steps.push({
      kind: 'column',
      role: 'side_column',
      label: 'Side column (Debitor/Kreditor discriminator)',
      required: true,
      skippable: false,
    })

    steps.push({
      kind: 'choice',
      role: 'debitor_value',
      label: 'Value that means Debitor (AR)',
      options: (answers: Answers, preview: NormalizedPreview) => {
        const sideColId = getColumnId(answers, 'side_column')
        if (!sideColId) return []
        return distinctSampleValues(sideColId, preview)
      },
    })

    steps.push({
      kind: 'choice',
      role: 'kreditor_value',
      label: 'Value that means Kreditor (AP)',
      options: (answers: Answers, preview: NormalizedPreview) => {
        const sideColId = getColumnId(answers, 'side_column')
        if (!sideColId) return []
        return distinctSampleValues(sideColId, preview)
      },
    })
  }

  return steps
}

/**
 * Converts mapper Answers into the payload consumed by commitOpos /
 * commitOposCombined / stored in WizardOposSideState or
 * WizardOposCombinedSidesState:
 *   columnMap    — { target_field: source_column } (nulls/skipped excluded)
 *   entityColumn — source column for entity identification (combined entity mode)
 *   sideColumn   — discriminator column (combined_sides only)
 *   debitorValue — value meaning Debitor/AR in sideColumn (distinct from kreditorValue)
 *   kreditorValue — value meaning Kreditor/AP in sideColumn (distinct from debitorValue)
 *
 * If debitorValue === kreditorValue, both are returned as undefined so the commit
 * gate (canCommit) stays false.
 */
export function toOposResult(answers: Answers): OposResult {
  const columnMap: Record<string, string> = {}
  for (const field of OPOS_TARGET_FIELDS) {
    const id = getColumnId(answers, field.key)
    if (id !== null) columnMap[field.key] = id
  }

  const entityColId = getColumnId(answers, 'entity_column')
  const entityColumn = entityColId ?? undefined

  const sideColId = getColumnId(answers, 'side_column')
  const sideColumn = sideColId ?? undefined

  const rawDebitor  = getChoice(answers, 'debitor_value')
  const rawKreditor = getChoice(answers, 'kreditor_value')

  // Enforce distinct values — leave invalid as undefined so commit stays gated
  const distinct =
    rawDebitor !== undefined &&
    rawKreditor !== undefined &&
    rawDebitor !== rawKreditor

  return {
    columnMap,
    entityColumn,
    sideColumn,
    debitorValue:  distinct ? rawDebitor  : undefined,
    kreditorValue: distinct ? rawKreditor : undefined,
  }
}
