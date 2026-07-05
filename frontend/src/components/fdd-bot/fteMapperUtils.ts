/** Excel column letter helpers for FTE preview mappers. */

export function idxFromLetter(letter: string): number {
  let n = 0
  for (const ch of letter.toUpperCase()) {
    n = n * 26 + (ch.charCodeAt(0) - 64)
  }
  return n - 1
}

export function letterFromIdx(idx: number): string {
  let n = idx + 1
  let s = ''
  while (n > 0) {
    const rem = (n - 1) % 26
    s = String.fromCharCode(65 + rem) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

export interface PreviewColumn {
  letter: string
  sample_values: string[]
}

export interface PreviewData {
  header_row_index: number
  columns: PreviewColumn[]
  rows: string[][]
}

export async function fetchPersonaltablePreview(
  sessionId: string,
  previewFileId: string,
): Promise<PreviewData> {
  const base = (await import('../../lib/api')).getApiBaseUrl()
  const url = `${base}/api/v1/fdd/personaltable/preview?session_id=${encodeURIComponent(sessionId)}&file_id=${encodeURIComponent(previewFileId)}`
  const resp = await fetch(url)
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    const detail = (err as { detail?: string }).detail
    throw new Error(typeof detail === 'string' ? detail : `Preview failed (${resp.status})`)
  }
  return (await resp.json()) as PreviewData
}

export function headerNameForColumn(preview: PreviewData, letter: string): string {
  const idx = idxFromLetter(letter)
  const hr = preview.header_row_index
  const row = preview.rows[hr]
  if (!row || idx < 0 || idx >= row.length) return letter
  return String(row[idx] ?? letter).trim() || letter
}
