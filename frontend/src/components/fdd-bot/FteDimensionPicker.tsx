/** Pick up to 3 breakdown dimensions with output labels. */

import { useEffect, useState } from 'react'
import {
  fetchPersonaltablePreview,
  headerNameForColumn,
  idxFromLetter,
  type PreviewData,
} from './fteMapperUtils'

export interface FteDimension {
  source_col: string
  output_label: string
  source_letter?: string
}

interface Props {
  sessionId: string
  previewFileId: string
  disabled?: boolean
  onSubmit: (dimensions: FteDimension[]) => void | Promise<void>
}

export default function FteDimensionPicker({
  sessionId,
  previewFileId,
  disabled,
  onSubmit,
}: Props) {
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dims, setDims] = useState<FteDimension[]>([])
  const [pendingLetter, setPendingLetter] = useState<string | null>(null)
  const [labelDraft, setLabelDraft] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      try {
        const data = await fetchPersonaltablePreview(sessionId, previewFileId)
        if (!cancelled) setPreview(data)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Preview failed')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sessionId, previewFileId])

  const clickCol = (letter: string) => {
    if (disabled || dims.length >= 3) return
    setPendingLetter(letter)
    const name = preview ? headerNameForColumn(preview, letter) : letter
    setLabelDraft(name)
  }

  const addDimension = () => {
    if (!pendingLetter || !labelDraft.trim() || dims.length >= 3) return
    const name = preview ? headerNameForColumn(preview, pendingLetter) : pendingLetter
    if (dims.some(d => d.source_col === name)) return
    setDims([...dims, { source_col: name, output_label: labelDraft.trim(), source_letter: pendingLetter }])
    setPendingLetter(null)
    setLabelDraft('')
  }

  const removeDim = (idx: number) => {
    setDims(dims.filter((_, i) => i !== idx))
  }

  if (loading) return <p className="text-sm text-slate-500">Loading preview…</p>
  if (error) return <p className="text-sm text-red-600">{error}</p>
  if (!preview) return null

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-slate-600">
        Click a column, set an output label, then Add. Maximum 3 dimensions (Level 1 → 3).
      </p>

      {dims.map((d, i) => (
        <div key={i} className="flex items-center justify-between text-sm bg-slate-50 rounded px-2 py-1">
          <span>
            L{i + 1}: <strong>{d.output_label}</strong> ← {d.source_col}
          </span>
          <button type="button" className="text-red-600 text-xs" onClick={() => removeDim(i)}>
            Remove
          </button>
        </div>
      ))}

      {pendingLetter && (
        <div className="flex gap-2 items-end">
          <div className="flex-1">
            <label className="text-xs text-slate-600">Output label for {pendingLetter}</label>
            <input
              className="w-full border rounded px-2 py-1 text-sm"
              value={labelDraft}
              onChange={e => setLabelDraft(e.target.value)}
            />
          </div>
          <button type="button" className="px-3 py-1 text-sm bg-slate-200 rounded" onClick={addDimension}>
            Add
          </button>
        </div>
      )}

      <div className="overflow-x-auto border rounded-lg max-h-48">
        <table className="text-xs border-collapse">
          <thead>
            <tr>
              {preview.columns.map(col => {
                const hr = preview.header_row_index
                const header = preview.rows[hr]?.[idxFromLetter(col.letter)] ?? col.letter
                const sel = pendingLetter === col.letter
                return (
                  <th
                    key={col.letter}
                    className="border px-1 py-1 cursor-pointer"
                    style={{ background: sel ? '#DBEAFE' : '#F8FAFC' }}
                    onClick={() => clickCol(col.letter)}
                  >
                    {col.letter}
                    <div className="font-normal truncate max-w-[90px]">{header}</div>
                  </th>
                )
              })}
            </tr>
          </thead>
        </table>
      </div>

      <button
        type="button"
        disabled={disabled || submitting || dims.length < 1}
        onClick={async () => {
          setSubmitting(true)
          try {
            await onSubmit(dims)
          } finally {
            setSubmitting(false)
          }
        }}
        className="self-end px-4 py-2 text-sm font-semibold rounded-lg text-white"
        style={{ background: dims.length < 1 ? '#94A3B8' : '#1E3A5F' }}
      >
        Continue
      </button>
    </div>
  )
}
