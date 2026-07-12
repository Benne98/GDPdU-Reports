/**
 * Adapters that turn upload-specific preview shapes into the generic
 * NormalizedPreview used by StepColumnMapper.
 */

import { headerNameForColumn, idxFromLetter } from '../fdd-bot/fteMapperUtils'
import type { NormalizedPreview } from './stepMapperTypes'

interface LetterPreviewInput {
  header_row_index: number
  columns: { letter: string; sample_values: string[] }[]
  rows: string[][]
}

/**
 * Normalize an Excel letter-column preview (SuSa / FTE upload flow).
 *
 * - id   = Excel column letter (e.g. "A", "AB")
 * - header = text from the header row cell
 * - rows = up to 8 data rows following the header row, aligned to columns order
 */
export function fromLetterPreview(p: LetterPreviewInput): NormalizedPreview {
  const columns = p.columns.map(col => ({
    id: col.letter,
    header: headerNameForColumn(p, col.letter),
    sampleValues: col.sample_values,
  }))

  const rows = p.rows
    .slice(p.header_row_index + 1, p.header_row_index + 9)
    .map(r => p.columns.map(c => r[idxFromLetter(c.letter)] ?? ''))

  return { columns, rows }
}

/**
 * Normalize a named-column preview (GL / named-header upload flow).
 *
 * - id = header = column name string
 * - sampleValues = up to 6 values from sample rows
 * - rows = up to 3 sample rows aligned to columns order
 */
export function fromNamedPreview(
  columns: string[],
  sample: Record<string, unknown>[],
): NormalizedPreview {
  const normalizedColumns = columns.map(col => ({
    id: col,
    header: col,
    sampleValues: sample.slice(0, 6).map(r => String(r[col] ?? '')),
  }))

  const rows = sample
    .slice(0, 3)
    .map(r => columns.map(c => String(r[c] ?? '')))

  return { columns: normalizedColumns, rows }
}
