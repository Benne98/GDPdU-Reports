/**
 * Step-builder and result-converter for the OPOS / Aging column mapper.
 * Used by OposStep.tsx (both OposSidePanel and OposCombinedSidesPanel)
 * with StepColumnMapper.
 */

import type { Answers, MapperStep, NormalizedPreview } from './stepMapperTypes'
import { getColumnId, getChoice, getChecklist } from './stepMapperTypes'

/**
 * Canonical target-field set for the OPOS open-items mapping.
 * Kept in sync with backend _TARGET_FIELDS (app/routers/opos.py) and the fields
 * consumed by opos_aging.py.
 *
 * Ordered by IMPORTANCE with generic, source-system-neutral labels + hints so the
 * mapper reads on arbitrary DATEV / SAP exports (not just the reference dataset):
 *   ESSENTIAL (required): partner_no, konto, amount_hauswaehrung, net_due_date,
 *                         posting_date, belegart
 *   OPTIONAL  (end)     : beleg_no
 *
 * partner_no label is OVERRIDDEN per-side in buildOposSteps — the value here is
 * the generic fallback used when no side context is available.
 *
 * Dropped vs. old set: satzart, buchungskreis (not needed for the aging read),
 * referenz (not consumed by opos_aging.py).  belegart is now REQUIRED — it seeds
 * the FIFO invoice pool, so a missing document type mis-buckets the aging.
 */
const OPOS_TARGET_FIELDS: Array<{ key: string; label: string; hint?: string; required: boolean }> = [
  {
    key: 'partner_no',
    label: 'Customer/Supplier account (partner ID)',
    hint: 'The debtor/creditor the open item belongs to — aging groups by partner.',
    required: true,
  },
  {
    key: 'konto',
    label: 'Reconciliation / control account',
    hint: 'The AR/AP control account (SAP HKONT, DATEV Sammelkonto).',
    required: true,
  },
  {
    key: 'amount_hauswaehrung',
    label: 'Open amount (local currency, signed)',
    required: true,
  },
  {
    key: 'net_due_date',
    label: 'Net due date',
    hint: 'Drives the aging buckets.',
    required: true,
  },
  {
    key: 'posting_date',
    label: 'Posting date',
    required: true,
  },
  {
    key: 'belegart',
    label: 'Document type',
    hint: 'Invoice vs payment/carry-forward — needed for correct buckets.',
    required: true,
  },
  {
    key: 'beleg_no',
    label: 'Document number',
    required: false,
  },
]

export interface OposResult {
  columnMap: Record<string, string>
  entityColumn?: string
  sideColumn?: string
  debitorValue?: string
  kreditorValue?: string
  /** Belegart sample values the user marked as "invoice" (FIFO pool seed). */
  invoiceDoctypes?: string[]
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
 *      Required: partner_no, konto, amount_hauswaehrung, net_due_date,
 *                posting_date, belegart.  Skippable: beleg_no.
 *   2. A skippable `checklist` step letting the user mark which mapped belegart
 *      sample values mean "invoice" (FIFO pool seed).  Options come from the
 *      mapped belegart column; skipping keeps the backend default ("RV","RG").
 *   3. IF viewMode === 'combined': a skippable `column` step for the entity column.
 *   4. IF loadMode === 'combined_sides':
 *        – required `column` step for the side discriminator column.
 *        – `choice` step for the Debitor value (options from sample).
 *        – `choice` step for the Kreditor value (options from sample).
 *
 * @param side - When provided, the partner_no step label is made side-aware:
 *   'debitor'  → "Customer account (partner ID)"
 *   'kreditor' → "Supplier account (partner ID)"
 *   undefined  → "Customer/Supplier account (partner ID)"  (combined-sides / unknown)
 *
 * Total steps (7 target fields + invoice_doctypes):
 *   per_side + per_entity      :  8
 *   per_side + combined        :  9
 *   combined_sides + per_entity: 11
 *   combined_sides + combined  : 12
 */
export function buildOposSteps(
  loadMode: string,
  viewMode: string,
  side?: 'debitor' | 'kreditor',
): MapperStep[] {
  const partnerLabel =
    side === 'debitor'  ? 'Customer account (partner ID)'          :
    side === 'kreditor' ? 'Supplier account (partner ID)'          :
                          'Customer/Supplier account (partner ID)'

  const steps: MapperStep[] = []

  // Target-field steps
  for (const field of OPOS_TARGET_FIELDS) {
    steps.push({
      kind: 'column',
      role: field.key,
      label: field.key === 'partner_no' ? partnerLabel : field.label,
      hint: field.hint,
      required: field.required,
      skippable: !field.required,
    })
  }

  // Optional invoice-doctype picker: which belegart values seed the FIFO pool.
  // Options derive from the mapped belegart column; skip keeps default ("RV","RG").
  steps.push({
    kind: 'checklist',
    role: 'invoice_doctypes',
    label: 'Which document types are invoices?',
    hint: 'Seeds the FIFO invoice pool. Leave empty to keep the default (RV, RG).',
    skippable: true,
    options: (answers: Answers, preview: NormalizedPreview) => {
      const belegartColId = getColumnId(answers, 'belegart')
      if (!belegartColId) return []
      return distinctSampleValues(belegartColId, preview)
    },
  })

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
 *   columnMap      — { target_field: source_column } (nulls/skipped excluded)
 *   entityColumn   — source column for entity identification (combined entity mode)
 *   sideColumn     — discriminator column (combined_sides only)
 *   debitorValue   — value meaning Debitor/AR in sideColumn (distinct from kreditorValue)
 *   kreditorValue  — value meaning Kreditor/AP in sideColumn (distinct from debitorValue)
 *   invoiceDoctypes — belegart values marked as "invoice" (undefined when none picked;
 *                     the backend then keeps its default ("RV","RG"))
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

  const doctypes = getChecklist(answers, 'invoice_doctypes')

  return {
    columnMap,
    entityColumn,
    sideColumn,
    debitorValue:  distinct ? rawDebitor  : undefined,
    kreditorValue: distinct ? rawKreditor : undefined,
    invoiceDoctypes: doctypes.length > 0 ? doctypes : undefined,
  }
}
