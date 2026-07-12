/**
 * Shared types for the generic step-by-step column-mapping wizard.
 * Used by StepColumnMapper.tsx and every step-builder module.
 */

export interface NormalizedColumn {
  id: string
  header: string
  sampleValues: string[]
}

export interface NormalizedPreview {
  columns: NormalizedColumn[]
  /** Data rows aligned to columns order (optional context rows, up to ~8). */
  rows?: string[][]
}

export type MapperOption = {
  value: string
  label: string
  hint?: string
}

export type MapperStep =
  | { kind: 'column'; role: string; label: string; required?: boolean; skippable?: boolean }
  | { kind: 'multiColumn'; role: string; label: string; max: number; withLabels?: boolean; skippable?: boolean }
  | {
      kind: 'choice'
      role: string
      label: string
      options: MapperOption[] | ((answers: Answers, p: NormalizedPreview) => MapperOption[])
      /** When true, the step-builder may gate subsequent steps on this answer. */
      gating?: boolean
    }
  | { kind: 'checklist'; role: string; label: string; options: MapperOption[]; skippable?: boolean }
  | { kind: 'custom'; role: string; label: string; component: 'customOutputColumns'; skippable?: boolean }

export type Answer =
  | { t: 'column'; id: string | null }                                // null = skipped
  | { t: 'multiColumn'; items: Array<{ id: string; label?: string }> }
  | { t: 'choice'; value: string }
  | { t: 'checklist'; values: string[] }
  | { t: 'custom'; rows: Array<{ source_col: string; output_label: string }> }

export type Answers = Record<string, Answer>

// ─── Selector helpers — keep step-builder toResult() functions clean ──────────

export function getColumnId(answers: Answers, role: string): string | null {
  const ans = answers[role]
  if (!ans || ans.t !== 'column') return null
  return ans.id
}

export function getChoice(answers: Answers, role: string): string | undefined {
  const ans = answers[role]
  if (!ans || ans.t !== 'choice') return undefined
  return ans.value
}

export function getMulti(answers: Answers, role: string): Array<{ id: string; label?: string }> {
  const ans = answers[role]
  if (!ans || ans.t !== 'multiColumn') return []
  return ans.items
}

export function getChecklist(answers: Answers, role: string): string[] {
  const ans = answers[role]
  if (!ans || ans.t !== 'checklist') return []
  return ans.values
}

export function getCustom(
  answers: Answers,
  role: string,
): Array<{ source_col: string; output_label: string }> {
  const ans = answers[role]
  if (!ans || ans.t !== 'custom') return []
  return ans.rows
}
