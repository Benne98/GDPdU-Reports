/**
 * OPOS snapshot date + file upload rows (one snapshot per row).
 */

import { useCallback, useId, useState } from 'react'
import { Plus, Upload } from 'lucide-react'

export interface OposSnapshotRow {
  id: string
  as_of: string
  file: File | null
  fileName: string | null
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
  onSubmit: (snapshots: { as_of: string; file_id: string; file_path: string; sheet_name?: string }[]) => void | Promise<void>
}

function newRow(): OposSnapshotRow {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    as_of: '',
    file: null,
    fileName: null,
  }
}

function SnapshotFileDrop({
  rowId,
  fileName,
  disabled,
  onFile,
}: {
  rowId: string
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
        Open-items file
      </label>
      <label
        htmlFor={disabled ? undefined : `${inputId}-${rowId}`}
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
        <Upload size={16} />
        <span className="text-[11px] text-slate-600 text-center">
          {fileName ? fileName : 'Drop .xlsx here or click to upload'}
        </span>
      </label>
      <input
        id={`${inputId}-${rowId}`}
        type="file"
        accept=".xlsx,.xls"
        className="hidden"
        disabled={disabled}
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) onFile(f)
        }}
      />
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
  const [rows, setRows] = useState<OposSnapshotRow[]>([newRow()])
  const [uploads, setUploads] = useState<Record<string, UploadResult>>({})
  const [submitting, setSubmitting] = useState(false)

  const addRow = () => setRows(prev => [...prev, newRow()])

  const setAsOf = (rowId: string, value: string) => {
    setRows(prev => prev.map(r => (r.id === rowId ? { ...r, as_of: value } : r)))
  }

  const setFile = async (rowId: string, file: File) => {
    setRows(prev => prev.map(r => (r.id === rowId ? { ...r, file, fileName: file.name } : r)))
    if (!onFileUpload) return
    const res = await onFileUpload(file)
    if (!res) return
    setUploads(prev => ({ ...prev, [rowId]: res }))
  }

  const canSubmit = rows.some(r => r.as_of && uploads[r.id]?.file_id)

  const submit = useCallback(async () => {
    if (!canSubmit || submitting || disabled) return
    setSubmitting(true)
    try {
      const snapshots = rows
        .map(r => {
          const up = uploads[r.id]
          if (!r.as_of || !up?.file_id) return null
          return { as_of: r.as_of, file_id: up.file_id, file_path: up.file_path }
        })
        .filter(Boolean) as { as_of: string; file_id: string; file_path: string }[]
      await onSubmit(snapshots)
    } finally {
      setSubmitting(false)
    }
  }, [canSubmit, disabled, onSubmit, rows, submitting, uploads])

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-semibold" style={{ color: '#1E293B' }}>
          {title}
        </h3>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
      </div>

      <div className="flex flex-col gap-3">
        {rows.map(r => (
          <div
            key={r.id}
            className="rounded-xl p-3 flex flex-col gap-2"
            style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
          >
            <div className="flex gap-3 flex-wrap items-end">
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium" style={{ color: '#475569' }}>
                  Snapshot date
                </label>
                <input
                  value={r.as_of}
                  disabled={disabled}
                  onChange={e => setAsOf(r.id, e.target.value)}
                  className="rounded-md border border-slate-200 px-2 py-1 text-xs"
                  type="date"
                />
              </div>
              <SnapshotFileDrop
                rowId={r.id}
                fileName={r.fileName}
                disabled={disabled}
                onFile={f => void setFile(r.id, f)}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={disabled}
          onClick={addRow}
          className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          <Plus size={14} />
          Add snapshot
        </button>

        <button
          type="button"
          disabled={disabled || !canSubmit || submitting}
          onClick={() => void submit()}
          className="ml-auto inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          style={{ background: '#1E3A5F' }}
        >
          {submitLabel}
        </button>
      </div>
    </div>
  )
}

