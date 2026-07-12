/**
 * OPOS snapshot date + Debitor/Kreditor file uploads per row.
 */

import { useCallback, useId, useState } from 'react'
import { Plus, Upload } from 'lucide-react'

export interface OposSideUpload {
  file: File | null
  fileName: string | null
}

export interface OposSnapshotRow {
  id: string
  as_of: string
  debitor: OposSideUpload
  kreditor: OposSideUpload
}

export interface OposSnapshotPair {
  as_of: string
  debitor: { file_id: string; file_path: string; sheet_name?: string }
  kreditor: { file_id: string; file_path: string; sheet_name?: string }
}

interface UploadResult {
  file_id: string
  file_path: string
  sheet_names?: string[]
}

interface Props {
  title?: string
  subtitle?: string
  submitLabel?: string
  disabled?: boolean
  onFileUpload?: (file: File) => Promise<UploadResult | null>
  onSubmit: (snapshots: OposSnapshotPair[]) => void | Promise<void>
}

function newRow(): OposSnapshotRow {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    as_of: '',
    debitor: { file: null, fileName: null },
    kreditor: { file: null, fileName: null },
  }
}

function SnapshotFileDrop({
  rowId,
  side,
  label,
  fileName,
  disabled,
  onFile,
}: {
  rowId: string
  side: 'debitor' | 'kreditor'
  label: string
  fileName: string | null
  disabled?: boolean
  onFile: (file: File) => void
}) {
  const inputId = useId()
  const [dragging, setDragging] = useState(false)

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    if (!disabled) setDragging(true)
  }
  const onDragLeave = () => setDragging(false)
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    if (disabled) return
    const f = e.dataTransfer.files?.[0]
    if (f) onFile(f)
  }

  return (
    <div className="flex flex-col gap-1 flex-1 min-w-0">
      <label className="text-xs font-medium" style={{ color: '#475569' }}>
        {label}
      </label>
      <label
        htmlFor={disabled ? undefined : `${inputId}-${rowId}-${side}`}
        className="rounded-lg flex flex-col items-center justify-center gap-1 py-4 px-2 transition-colors"
        style={{
          border: `1.5px dashed ${dragging ? '#1E3A5F' : '#CBD5E1'}`,
          background: dragging ? 'rgba(30,58,95,0.04)' : '#F8FAFC',
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.55 : 1,
        }}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <Upload size={16} style={{ color: fileName ? '#10B981' : '#94A3B8' }} />
        <p className="text-xs text-center truncate w-full px-1" style={{ color: '#64748B' }}>
          {fileName ?? 'Drop file or browse'}
        </p>
        <input
          id={`${inputId}-${rowId}-${side}`}
          type="file"
          accept=".xlsx,.xls,.xlsm"
          className="hidden"
          disabled={disabled}
          onChange={e => {
            const f = e.target.files?.[0]
            if (f) onFile(f)
          }}
        />
      </label>
    </div>
  )
}

export default function OposSnapshotsCard({
  title = 'OPOS snapshots',
  subtitle,
  submitLabel = 'Continue',
  disabled,
  onFileUpload,
  onSubmit,
}: Props) {
  const [rows, setRows] = useState<OposSnapshotRow[]>(() => [newRow()])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const updateRow = useCallback((id: string, patch: Partial<OposSnapshotRow>) => {
    setRows(prev => prev.map(r => (r.id === id ? { ...r, ...patch } : r)))
  }, [])

  const updateSide = useCallback(
    (id: string, side: 'debitor' | 'kreditor', file: File) => {
      setRows(prev =>
        prev.map(r =>
          r.id === id ? { ...r, [side]: { file, fileName: file.name } } : r,
        ),
      )
    },
    [],
  )

  const addRow = () => setRows(prev => [...prev, newRow()])

  const canSubmit =
    rows.length > 0 &&
    rows.every(
      r =>
        r.as_of.trim() !== '' &&
        r.debitor.file !== null &&
        r.kreditor.file !== null,
    )

  const handleSubmit = async () => {
    if (!canSubmit || submitting || disabled) return
    if (!onFileUpload) {
      setError('File upload is not available.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const out: OposSnapshotPair[] = []
      for (const row of rows) {
        if (!row.debitor.file || !row.kreditor.file) continue
        const debUploaded = await onFileUpload(row.debitor.file)
        if (!debUploaded) {
          setError(`Upload failed for debitor file (${row.debitor.fileName ?? 'file'}).`)
          setSubmitting(false)
          return
        }
        const kredUploaded = await onFileUpload(row.kreditor.file)
        if (!kredUploaded) {
          setError(`Upload failed for kreditor file (${row.kreditor.fileName ?? 'file'}).`)
          setSubmitting(false)
          return
        }
        out.push({
          as_of: row.as_of,
          debitor: {
            file_id: debUploaded.file_id,
            file_path: debUploaded.file_path,
            sheet_name: debUploaded.sheet_names?.[0] ?? '',
          },
          kreditor: {
            file_id: kredUploaded.file_id,
            file_path: kredUploaded.file_path,
            sheet_name: kredUploaded.sheet_names?.[0] ?? '',
          },
        })
      }
      await onSubmit(out)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Submit failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-3"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', maxWidth: 720 }}
    >
      <h3 className="text-sm font-semibold" style={{ color: '#1E293B' }}>{title}</h3>
      {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}

      <div className="flex flex-col gap-3">
        {rows.map((row, idx) => (
          <div key={row.id} className="flex flex-col gap-2">
            <div className="flex flex-col sm:flex-row gap-3 items-stretch">
              <div className="flex flex-col gap-1 sm:w-36 shrink-0">
                <label className="text-xs font-medium" style={{ color: '#475569' }}>
                  Snapshot date{rows.length > 1 ? ` ${idx + 1}` : ''}
                </label>
                <input
                  type="date"
                  value={row.as_of}
                  disabled={disabled || submitting}
                  onChange={e => updateRow(row.id, { as_of: e.target.value })}
                  className="rounded-lg px-3 py-2 text-sm outline-none"
                  style={{ border: '1px solid #E2E8F0', background: '#F8FAFC' }}
                />
              </div>
              <SnapshotFileDrop
                rowId={row.id}
                side="debitor"
                label="Debitor"
                fileName={row.debitor.fileName}
                disabled={disabled || submitting}
                onFile={file => updateSide(row.id, 'debitor', file)}
              />
              <SnapshotFileDrop
                rowId={row.id}
                side="kreditor"
                label="Kreditor"
                fileName={row.kreditor.fileName}
                disabled={disabled || submitting}
                onFile={file => updateSide(row.id, 'kreditor', file)}
              />
            </div>
          </div>
        ))}
      </div>

      <button
        type="button"
        disabled={disabled || submitting}
        onClick={addRow}
        className="flex items-center gap-1.5 text-xs font-medium self-start px-2 py-1 rounded-md"
        style={{ color: '#1E3A5F', background: '#F1F5F9' }}
      >
        <Plus size={14} />
        Add another date
      </button>

      {error && <p className="text-xs text-red-600">{error}</p>}

      <button
        type="button"
        disabled={!canSubmit || disabled || submitting}
        onClick={handleSubmit}
        className="rounded-lg py-2 text-sm font-medium text-white self-end px-6"
        style={{
          background: !canSubmit || disabled || submitting ? '#94A3B8' : '#1E3A5F',
          cursor: !canSubmit || disabled || submitting ? 'not-allowed' : 'pointer',
        }}
      >
        {submitting ? 'Uploading…' : submitLabel}
      </button>
    </div>
  )
}
