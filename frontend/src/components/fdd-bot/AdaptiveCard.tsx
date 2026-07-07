/**
 * AdaptiveCard — renders a structured bot message as an interactive form.
 *
 * Supports input types:
 *   text, dropdown, radio, multi_select, number, month_picker, day_picker,
 *   file_drop, date_picker, susa_grid
 *
 * On submit, calls onSubmit(cardName, values) which the parent hook posts to Rasa.
 */

import { useState, useRef, type DragEvent } from 'react'
import { Upload, ChevronDown, Check, FileSpreadsheet, Download } from 'lucide-react'
import { getApiBaseUrl } from '../../lib/api'
import type { AdaptiveCardPayload, AdaptiveCardInput } from './useFddBot'
import SusaColumnMapper, { type SusaColumnMappingPayload } from './SusaColumnMapper'
import FteColumnMapper, { type FteMappingPayload } from './FteColumnMapper'
import FteDimensionPicker from './FteDimensionPicker'
import FtePexGrid from './FtePexGrid'

interface Props {
  payload: AdaptiveCardPayload
  onSubmit: (
    cardName: string,
    values: Record<string, unknown>,
    meta?: { submitLabel?: string },
  ) => void | Promise<void>
  onFileUpload?: (
    file: File,
  ) => Promise<{
    file_id: string
    file_path: string
    headers: string[]
    sheet_names?: string[]
    session_id: string
  } | null>
  disabled?: boolean
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** Consecutive inputs sharing `rowGroup` render on one horizontal row. */
function chunkInputRows(inputs: AdaptiveCardInput[]): AdaptiveCardInput[][] {
  const rows: AdaptiveCardInput[][] = []
  let buf: AdaptiveCardInput[] = []
  let bufKey: string | undefined

  const flushBuf = () => {
    if (buf.length) rows.push(buf)
    buf = []
    bufKey = undefined
  }

  for (const inp of inputs) {
    const k = inp.rowGroup
    if (k) {
      if (bufKey === undefined) {
        bufKey = k
        buf = [inp]
      } else if (bufKey === k) {
        buf.push(inp)
      } else {
        flushBuf()
        bufKey = k
        buf = [inp]
      }
    } else {
      flushBuf()
      rows.push([inp])
    }
  }
  flushBuf()
  return rows
}

// ─── Individual input renderers ───────────────────────────────────────────────

function FolderPickerInput({
  input,
  value,
  onChange,
  dense,
}: {
  input: AdaptiveCardInput
  value: string
  onChange: (v: string) => void
  dense?: boolean
}) {
  const [picking, setPicking] = useState(false)

  const browse = async () => {
    setPicking(true)
    try {
      const resp = await fetch(`${getApiBaseUrl()}/api/v1/fdd/pick-folder`, { method: 'POST' })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error((err as { detail?: string }).detail || `Picker failed (${resp.status})`)
      }
      const data = (await resp.json()) as { path?: string; cancelled?: boolean }
      if (!data.cancelled && data.path) onChange(data.path)
    } catch (e) {
      console.error(e)
    } finally {
      setPicking(false)
    }
  }

  return (
    <div className={dense ? 'flex flex-col gap-0.5' : 'flex flex-col gap-1'}>
      <label className={dense ? 'text-[12px] font-medium' : 'text-xs font-medium'} style={{ color: '#475569' }}>
        {input.label}
        {input.required !== false && <span style={{ color: '#EF4444' }}> *</span>}
      </label>
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          placeholder={input.placeholder ?? ''}
          onChange={e => onChange(e.target.value)}
          className={
            dense
              ? 'flex-1 rounded-md px-2 py-1 text-xs outline-none min-h-[32px]'
              : 'flex-1 rounded-lg px-3 py-2 text-sm outline-none'
          }
          style={{
            border: '1px solid #E2E8F0',
            background: '#F8FAFC',
            color: '#1E293B',
          }}
        />
        <button
          type="button"
          onClick={() => void browse()}
          disabled={picking}
          className={
            dense
              ? 'shrink-0 rounded-md px-2.5 py-1 text-[12px] font-semibold'
              : 'shrink-0 rounded-lg px-3 py-2 text-xs font-semibold'
          }
          style={{
            background: '#1E3A5F',
            color: '#FFFFFF',
            opacity: picking ? 0.6 : 1,
          }}
        >
          {picking ? '…' : 'Browse…'}
        </button>
      </div>
    </div>
  )
}

function FileAttachmentCard({ payload }: { payload: AdaptiveCardPayload }) {
  const filename = payload.filename ?? 'Output.xlsx'
  const rel = payload.download_url ?? ''
  const href = rel.startsWith('http') ? rel : `${getApiBaseUrl()}${rel}`
  const compact = Boolean(payload.notification)

  const onDragStart = (e: DragEvent) => {
    if (compact) return
    e.dataTransfer.effectAllowed = 'copy'
    e.dataTransfer.setData(
      'DownloadURL',
      `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet:${filename}:${href}`,
    )
    e.dataTransfer.setData('text/uri-list', href)
  }

  if (compact) {
    return (
      <div
        className="rounded-lg mb-2 px-3 py-2 flex items-center gap-2"
        style={{
          background: 'rgba(30,58,95,0.06)',
          border: '1px solid rgba(30,58,95,0.15)',
          maxWidth: 340,
        }}
      >
        <FileSpreadsheet size={18} style={{ color: '#1E3A5F', flexShrink: 0 }} />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold truncate" style={{ color: '#1E3A5F' }}>
            {payload.title || 'Output ready'}
          </p>
          <p className="text-[12px] truncate" style={{ color: '#64748B' }}>
            {filename}
          </p>
        </div>
        <a
          href={href}
          download={filename}
          className="text-[12px] font-semibold rounded-md px-2 py-1 shrink-0"
          style={{ background: '#1E3A5F', color: '#FFFFFF' }}
        >
          Download
        </a>
      </div>
    )
  }

  return (
    <div
      className="rounded-xl mb-3 overflow-hidden px-4 py-3"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
        maxWidth: 380,
      }}
      draggable
      onDragStart={onDragStart}
      title="Drag to Desktop or use Download"
    >
      <div className="flex items-start gap-3">
        <FileSpreadsheet size={28} style={{ color: '#1E3A5F', flexShrink: 0 }} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold" style={{ color: '#111827' }}>
            {payload.title || 'Output file'}
          </p>
          <p className="text-xs mt-0.5 truncate" style={{ color: '#64748B' }}>
            {filename}
          </p>
          <p className="text-[12px] mt-1" style={{ color: '#94A3B8' }}>
            Drag this card to your Desktop, or download below.
          </p>
          <a
            href={href}
            download={filename}
            className="inline-flex items-center gap-1.5 mt-2 text-xs font-semibold rounded-lg px-3 py-1.5"
            style={{ background: '#1E3A5F', color: '#FFFFFF' }}
            onClick={e => e.stopPropagation()}
          >
            <Download size={14} />
            Download
          </a>
        </div>
      </div>
    </div>
  )
}

function TextInput({ input, value, onChange, dense }: {
  input: AdaptiveCardInput
  value: string
  onChange: (v: string) => void
  dense?: boolean
}) {
  return (
    <div className={dense ? 'flex flex-col gap-0.5' : 'flex flex-col gap-1'}>
      <label className={dense ? 'text-[12px] font-medium' : 'text-xs font-medium'} style={{ color: '#475569' }}>
        {input.label}
        {input.required !== false && <span style={{ color: '#EF4444' }}> *</span>}
      </label>
      <input
        type={input.type === 'number' ? 'number' : 'text'}
        value={value}
        placeholder={input.placeholder ?? ''}
        min={input.type === 'number' && input.min != null ? input.min : undefined}
        max={input.type === 'number' && input.max != null ? input.max : undefined}
        onChange={e => onChange(e.target.value)}
        className={dense ? 'rounded-md px-2 py-1 text-xs outline-none min-h-[32px]' : 'rounded-lg px-3 py-2 text-sm outline-none'}
        style={{
          border: '1px solid #E2E8F0',
          background: '#F8FAFC',
          color: '#1E293B',
        }}
      />
    </div>
  )
}


function DropdownInput({ input, value, onChange, dense }: {
  input: AdaptiveCardInput
  value: string
  onChange: (v: string) => void
  dense?: boolean
}) {
  const baseOpts = input.options ?? []
  const options =
    value && !baseOpts.some(o => o.value === value)
      ? [{ label: value, value }, ...baseOpts]
      : baseOpts
  return (
    <div className={dense ? 'flex flex-col gap-0.5' : 'flex flex-col gap-1'}>
      <label className={dense ? 'text-[12px] font-medium' : 'text-xs font-medium'} style={{ color: '#475569' }}>
        {input.label}
      </label>
      <div className="relative">
        <select
          value={value}
          onChange={e => onChange(e.target.value)}
          className={dense ? 'w-full rounded-md px-2 py-1 text-xs appearance-none outline-none pr-7 min-h-[32px]' : 'w-full rounded-lg px-3 py-2 text-sm appearance-none outline-none pr-8'}
          style={{
            border: '1px solid #E2E8F0',
            background: '#F8FAFC',
            color: value ? '#1E293B' : '#94A3B8',
          }}
        >
          <option value="">Select...</option>
          {options.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        <ChevronDown
          size={14}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
          style={{ color: '#94A3B8' }}
        />
      </div>
    </div>
  )
}

function RadioInput({ input, value, onChange, dense }: {
  input: AdaptiveCardInput
  value: string
  onChange: (v: string) => void
  dense?: boolean
}) {
  const split = input.layout === 'split'
  if (split) {
    return (
      <div className="flex flex-row gap-2 items-start w-full min-w-0">
        <span
          className={dense ? 'text-[12px] font-medium shrink-0 pt-1' : 'text-xs font-medium shrink-0 pt-1.5'}
          style={{ color: '#475569', maxWidth: '38%' }}
        >
          {input.label}
        </span>
        <div className="flex flex-wrap gap-1 flex-1 min-w-0 justify-end">
          {input.options?.map(opt => (
            <label
              key={opt.value}
              className="flex items-center gap-1 cursor-pointer rounded-md px-2 py-1 text-[12px] transition-colors"
              style={{
                border: `1px solid ${value === opt.value ? '#1E3A5F' : '#E2E8F0'}`,
                background: value === opt.value ? 'rgba(30,58,95,0.05)' : '#F8FAFC',
                color: '#1E293B',
              }}
            >
              <input
                type="radio"
                className="hidden"
                value={opt.value}
                checked={value === opt.value}
                onChange={() => onChange(opt.value)}
              />
              {opt.label}
            </label>
          ))}
        </div>
      </div>
    )
  }
  return (
    <div className={dense ? 'flex flex-col gap-1' : 'flex flex-col gap-1.5'}>
      <label className={dense ? 'text-[12px] font-medium' : 'text-xs font-medium'} style={{ color: '#475569' }}>
        {input.label}
      </label>
      <div className="flex flex-col gap-1.5">
        {input.options?.map(opt => (
          <label
            key={opt.value}
            className="flex items-center gap-2 cursor-pointer rounded-lg px-3 py-2 text-xs transition-colors"
            style={{
              border: `1px solid ${value === opt.value ? '#1E3A5F' : '#E2E8F0'}`,
              background: value === opt.value ? 'rgba(30,58,95,0.05)' : '#F8FAFC',
              color: '#1E293B',
            }}
          >
            <div
              className="rounded-full shrink-0 flex items-center justify-center"
              style={{
                width: 14,
                height: 14,
                border: `2px solid ${value === opt.value ? '#1E3A5F' : '#CBD5E1'}`,
                background: value === opt.value ? '#1E3A5F' : 'transparent',
              }}
            >
              {value === opt.value && (
                <div style={{ width: 5, height: 5, borderRadius: '50%', background: '#fff' }} />
              )}
            </div>
            <input
              type="radio"
              className="hidden"
              value={opt.value}
              checked={value === opt.value}
              onChange={() => onChange(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </div>
    </div>
  )
}

function MultiSelectInput({ input, value, onChange }: {
  input: AdaptiveCardInput
  value: string[]
  onChange: (v: string[]) => void
}) {
  const toggle = (v: string) => {
    onChange(value.includes(v) ? value.filter(x => x !== v) : [...value, v])
  }
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium" style={{ color: '#475569' }}>
        {input.label}
      </label>
      <div className="flex flex-wrap gap-2">
        {input.options?.map(opt => {
          const selected = value.includes(opt.value)
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => toggle(opt.value)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                border: `1px solid ${selected ? '#1E3A5F' : '#E2E8F0'}`,
                background: selected ? 'rgba(30,58,95,0.08)' : '#F8FAFC',
                color: selected ? '#1E3A5F' : '#64748B',
              }}
            >
              {selected && <Check size={10} />}
              {opt.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function MonthPickerInput({ input, value, onChange, dense }: {
  input: AdaptiveCardInput
  value: string
  onChange: (v: string) => void
  dense?: boolean
}) {
  const parseYearMonth = (v: string) => {
    const parts = v.trim().split('-')
    if (parts.length >= 2) {
      const y = parseInt(parts[0], 10)
      const m = parseInt(parts[1], 10)
      if (Number.isFinite(y) && Number.isFinite(m) && m >= 1 && m <= 12) {
        return { year: y, month: m - 1 }
      }
    }
    return null
  }

  const initial = value ? parseYearMonth(value) : null
  const [year, setYear] = useState(() => initial?.year ?? new Date().getFullYear())
  const [month, setMonth] = useState<number | null>(initial?.month ?? null)

  const emit = (y: number, m: number) => {
    onChange(`${y}-${String(m + 1).padStart(2, '0')}`)
  }

  const select = (m: number) => {
    setMonth(m)
    emit(year, m)
  }

  const bumpYear = (delta: number) => {
    setYear(prev => {
      const next = prev + delta
      if (month !== null) {
        emit(next, month)
      }
      return next
    })
  }

  return (
    <div className={dense ? 'flex flex-col gap-1' : 'flex flex-col gap-1.5'}>
      <label className={dense ? 'text-[12px] font-medium' : 'text-xs font-medium'} style={{ color: '#475569' }}>{input.label}</label>
      <div className={`flex items-center justify-between ${dense ? 'mb-0.5' : 'mb-1'}`}>
        <button type="button" onClick={() => bumpYear(-1)}
          className={dense ? 'text-[12px] px-1.5 py-0.5 rounded' : 'text-xs px-2 py-1 rounded'} style={{ color: '#64748B' }}>‹</button>
        <span className={dense ? 'text-[12px] font-semibold' : 'text-xs font-semibold'} style={{ color: '#1E293B' }}>{year}</span>
        <button type="button" onClick={() => bumpYear(1)}
          className={dense ? 'text-[12px] px-1.5 py-0.5 rounded' : 'text-xs px-2 py-1 rounded'} style={{ color: '#64748B' }}>›</button>
      </div>
      <div className={dense ? 'grid grid-cols-3 gap-0.5' : 'grid grid-cols-3 gap-1'}>
        {MONTHS.map((name, i) => (
          <button
            key={name}
            type="button"
            onClick={() => select(i)}
            className={dense ? 'text-[12px] py-1 rounded-md transition-colors' : 'text-xs py-1.5 rounded-lg transition-colors'}
            style={{
              background: month === i ? '#1E3A5F' : '#F1F5F9',
              color: month === i ? '#fff' : '#475569',
              border: 'none',
            }}
          >
            {name.slice(0, 3)}
          </button>
        ))}
      </div>
    </div>
  )
}

function FileDropInput({ input, onFileSelect }: {
  input: AdaptiveCardInput
  onFileSelect: (f: File) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)

  const handle = (file: File) => {
    setFileName(file.name)
    onFileSelect(file)
  }

  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium" style={{ color: '#475569' }}>{input.label}</label>
      <div
        className="rounded-lg flex flex-col items-center justify-center gap-2 py-5 cursor-pointer transition-colors"
        style={{
          border: `1.5px dashed ${dragging ? '#1E3A5F' : '#CBD5E1'}`,
          background: dragging ? 'rgba(30,58,95,0.04)' : '#F8FAFC',
        }}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => {
          e.preventDefault()
          setDragging(false)
          const f = e.dataTransfer.files[0]
          if (f) handle(f)
        }}
        onClick={() => ref.current?.click()}
      >
        <Upload size={18} style={{ color: fileName ? '#10B981' : '#94A3B8' }} />
        <p className="text-xs" style={{ color: '#64748B' }}>
          {fileName ?? 'Drop file here or click to browse'}
        </p>
        {input.accept && (
          <p className="text-xs" style={{ color: '#94A3B8' }}>{input.accept}</p>
        )}
      </div>
      <input
        ref={ref}
        type="file"
        accept={input.accept}
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0]
          if (f) handle(f)
        }}
      />
    </div>
  )
}

function FolderDropInput({ input, onFilesSelect }: {
  input: AdaptiveCardInput
  onFilesSelect: (files: File[]) => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [count, setCount] = useState(0)

  const handleFiles = (list: FileList | null) => {
    if (!list?.length) return
    const pdfs = Array.from(list).filter(f => f.name.toLowerCase().endsWith('.pdf'))
    if (!pdfs.length) return
    setCount(pdfs.length)
    onFilesSelect(pdfs)
  }

  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium" style={{ color: '#475569' }}>{input.label}</label>
      <div
        className="rounded-lg flex flex-col items-center justify-center gap-2 py-5 cursor-pointer transition-colors"
        style={{
          border: `1.5px dashed ${dragging ? '#1E3A5F' : '#CBD5E1'}`,
          background: dragging ? 'rgba(30,58,95,0.04)' : '#F8FAFC',
        }}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => {
          e.preventDefault()
          setDragging(false)
          handleFiles(e.dataTransfer.files)
        }}
        onClick={() => ref.current?.click()}
      >
        <Upload size={18} style={{ color: count > 0 ? '#10B981' : '#94A3B8' }} />
        <p className="text-xs" style={{ color: '#64748B' }}>
          {count > 0 ? `${count} PDF file(s) selected` : 'Drop folder here or click to browse'}
        </p>
        {input.accept && (
          <p className="text-xs" style={{ color: '#94A3B8' }}>{input.accept}</p>
        )}
      </div>
      <input
        ref={ref}
        type="file"
        accept={input.accept ?? '.pdf'}
        multiple
        {...({ webkitDirectory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
        className="hidden"
        onChange={e => handleFiles(e.target.files)}
      />
    </div>
  )
}

function SortableListInput({ input, value, onChange }: {
  input: AdaptiveCardInput
  value: string[]
  onChange: (v: string[]) => void
}) {
  const [order, setOrder] = useState<string[]>(() => {
    if (value.length) return value
    return input.options?.map(o => o.value) ?? []
  })
  const [dragIdx, setDragIdx] = useState<number | null>(null)

  const move = (from: number, to: number) => {
    if (from === to) return
    const next = [...order]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    setOrder(next)
    onChange(next)
  }

  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium" style={{ color: '#475569' }}>{input.label}</label>
      {order.map((item, idx) => (
        <div
          key={item}
          draggable
          onDragStart={() => setDragIdx(idx)}
          onDragOver={e => e.preventDefault()}
          onDrop={() => {
            if (dragIdx !== null) move(dragIdx, idx)
            setDragIdx(null)
          }}
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs cursor-grab active:cursor-grabbing"
          style={{
            border: '1px solid #E2E8F0',
            background: dragIdx === idx ? 'rgba(30,58,95,0.08)' : '#F8FAFC',
            color: '#1E293B',
          }}
        >
          <span className="font-semibold w-5 text-center" style={{ color: '#64748B' }}>{idx + 1}</span>
          <span className="flex-1">{item}</span>
        </div>
      ))}
    </div>
  )
}

function DatePickerInput({ input, value, onChange }: {
  input: AdaptiveCardInput
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium" style={{ color: '#475569' }}>
        {input.label}
        {input.required !== false && <span style={{ color: '#EF4444' }}> *</span>}
      </label>
      <input
        type="date"
        value={value}
        onChange={e => onChange(e.target.value)}
        className="rounded-lg px-3 py-2 text-sm outline-none"
        style={{
          border: '1px solid #E2E8F0',
          background: '#F8FAFC',
          color: value ? '#1E293B' : '#94A3B8',
        }}
      />
    </div>
  )
}

function SusaGridInput({ input, onCellFiles, fileStatuses, onEntityNameChange }: {
  input: AdaptiveCardInput
  onCellFiles: (entityIdx: number, year: string, files: File[]) => void
  fileStatuses: Record<string, string | null>
  onEntityNameChange: (entityIdx: number, name: string) => void
}) {
  const entityCount = input.entity_count ?? 5
  const years = input.options?.map(o => o.value) ?? []
  const gridMode = input.grid_mode ?? 'single_file'
  const fileRefs = useRef<Record<string, HTMLInputElement | null>>({})
  const [entityNames, setEntityNames] = useState<string[]>(() =>
    Array.from({ length: entityCount }, (_, i) => `Entity ${i + 1}`),
  )

  const cellKey = (entityIdx: number, year: string) => `entity_${entityIdx + 1}_${year}`

  const pickXlsx = (files: FileList | File[]) =>
    Array.from(files).filter(f => /\.xlsx$/i.test(f.name) && !f.name.startsWith('~$'))

  const triggerBrowse = (key: string) => {
    fileRefs.current[key]?.click()
  }

  return (
    <div className="flex flex-col gap-3">
      <label className="text-xs font-medium" style={{ color: '#475569' }}>{input.label}</label>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', minWidth: '100%', fontSize: 12 }}>
          <thead>
            <tr>
              <th className="text-left pr-3 pb-2" style={{ color: '#64748B', fontWeight: 600, whiteSpace: 'nowrap' }}>
                Entity
              </th>
              {years.map(yr => (
                <th key={yr} className="pb-2 px-2 text-center" style={{ color: '#64748B', fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {yr}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: entityCount }, (_, ei) => (
              <tr key={ei} style={{ borderTop: '1px solid #F1F5F9' }}>
                <td className="pr-3 py-2">
                  <input
                    type="text"
                    value={entityNames[ei]}
                    onChange={e => {
                      const updated = [...entityNames]
                      updated[ei] = e.target.value
                      setEntityNames(updated)
                      onEntityNameChange(ei, e.target.value)
                    }}
                    className="rounded px-2 py-1 text-xs outline-none"
                    style={{ border: '1px solid #E2E8F0', background: '#F8FAFC', color: '#1E293B', width: 90 }}
                  />
                </td>
                {years.map(yr => {
                  const key = cellKey(ei, yr)
                  const uploaded = fileStatuses[key]
                  return (
                    <td key={yr} className="px-2 py-2 text-center">
                      <div
                        className="rounded-lg flex flex-col items-center justify-center gap-1 cursor-pointer transition-colors"
                        style={{
                          border: `1.5px dashed ${uploaded ? '#10B981' : '#CBD5E1'}`,
                          background: uploaded ? 'rgba(16,185,129,0.04)' : '#F8FAFC',
                          padding: '6px 8px',
                          minWidth: 70,
                        }}
                        onClick={() => triggerBrowse(key)}
                        onDragOver={e => e.preventDefault()}
                        onDrop={e => {
                          e.preventDefault()
                          const raw = pickXlsx(e.dataTransfer.files)
                          if (gridMode === 'folder') {
                            if (raw.length) onCellFiles(ei, yr, raw)
                          } else {
                            const f = raw[0]
                            if (f) onCellFiles(ei, yr, [f])
                          }
                        }}
                      >
                        <Upload size={12} style={{ color: uploaded ? '#10B981' : '#94A3B8' }} />
                        <span
                          className="text-center"
                          style={{
                            color: uploaded ? '#10B981' : '#94A3B8',
                            fontSize: 11,
                            whiteSpace: 'nowrap',
                            maxWidth: 92,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                        >
                          {uploaded ?? (gridMode === 'folder' ? 'Drop folder / click' : 'Drop / click')}
                        </span>
                      </div>
                      <input
                        ref={el => {
                          fileRefs.current[key] = el
                        }}
                        type="file"
                        className="hidden"
                        multiple={gridMode === 'folder'}
                        {...(gridMode === 'folder'
                          ? ({ webkitDirectory: '' } as React.InputHTMLAttributes<HTMLInputElement>)
                          : { accept: '.xlsx' })}
                        onChange={e => {
                          const list = e.target.files
                          if (!list?.length) return
                          const raw = pickXlsx(list)
                          if (gridMode === 'folder') {
                            if (raw.length) onCellFiles(ei, yr, raw)
                          } else {
                            const f = raw[0]
                            if (f) onCellFiles(ei, yr, [f])
                          }
                          e.target.value = ''
                        }}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function inputVisible(inp: AdaptiveCardInput, vals: Record<string, unknown>): boolean {
  if (inp.hidden) return false
  const w = inp.showWhen
  if (!w) return true
  return String(vals[w.field] ?? '') === w.value
}

// ─── AdaptiveCard ─────────────────────────────────────────────────────────────

export default function AdaptiveCard(props: Props) {
  if (props.payload.card === 'file_attachment') {
    return <FileAttachmentCard payload={props.payload} />
  }
  if (props.payload.card === 'databook_susa_column_mapper') {
    return <SusaColumnMapperCard {...props} />
  }
  if (
    props.payload.card === 'fte_fte_mapping' ||
    props.payload.card === 'fte_payroll_mapping'
  ) {
    return <FteMapperCard {...props} />
  }
  if (props.payload.card === 'fte_dimensions') {
    return <FteDimensionsCard {...props} />
  }
  return <AdaptiveCardForm {...props} />
}

function normalizeMapperMeta(value: unknown, fallback: string): string {
  const trimmed = String(value ?? '').trim()
  return trimmed || fallback
}

function FteMapperCard({ payload, onSubmit, disabled }: Props) {
  const meta = payload.mapper_meta ?? {}
  const sessionId = String(meta.session_id ?? '')
  const previewFileId = String(meta.preview_file_id ?? '')
  const uploadMode = String(meta.upload_mode ?? 'per_fy_grid')
  const mode = payload.card === 'fte_payroll_mapping' ? 'payroll' : 'fte'

  const handleMapping = async (mapping: FteMappingPayload) => {
    if (mode === 'payroll') {
      await onSubmit(payload.card, { fte_payroll_mapping: mapping, mapping })
    } else {
      await onSubmit(payload.card, {
        fte_mapping: mapping,
        mapping,
        fte_tenure_mode: mapping.tenure_mode,
      })
    }
  }

  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-3 relative"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
    >
      <h3 className="text-sm font-semibold" style={{ color: '#1E293B' }}>
        {payload.title}
      </h3>
      {payload.subtitle && (
        <p className="text-xs text-slate-500">{payload.subtitle}</p>
      )}
      <FteColumnMapper
        sessionId={sessionId}
        previewFileId={previewFileId}
        mode={mode}
        uploadMode={uploadMode}
        disabled={disabled}
        onSubmit={handleMapping}
      />
    </div>
  )
}

function FteDimensionsCard({ payload, onSubmit, disabled }: Props) {
  const meta = payload.mapper_meta ?? {}
  const sessionId = String(meta.session_id ?? '')
  const previewFileId = String(meta.preview_file_id ?? '')

  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-3"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
    >
      <h3 className="text-sm font-semibold" style={{ color: '#1E293B' }}>
        {payload.title}
      </h3>
      {payload.subtitle && (
        <p className="text-xs text-slate-500">{payload.subtitle}</p>
      )}
      <FteDimensionPicker
        sessionId={sessionId}
        previewFileId={previewFileId}
        disabled={disabled}
        onSubmit={async dimensions => {
          await onSubmit(payload.card, { fte_dimensions: dimensions, dimensions })
        }}
      />
    </div>
  )
}

function SusaColumnMapperCard({ payload, onSubmit, disabled }: Props) {
  const meta = payload.mapper_meta ?? {}
  const sessionId = String(meta.session_id ?? '')
  const previewFileId = String(meta.preview_file_id ?? '')
  const layoutFormat = normalizeMapperMeta(meta.layout_format, '')
  const signMode = normalizeMapperMeta(meta.sign_mode, 'sh_column')
  const valueType = normalizeMapperMeta(meta.value_type, 'balances')
  const entityNames = (meta.entity_names as string[]) ?? []

  const handleMapping = async (mapping: SusaColumnMappingPayload) => {
    await onSubmit(payload.card, { column_mapping: mapping })
  }

  return (
    <div
      className="rounded-xl p-4 flex flex-col gap-3 relative"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
    >
      <h3 className="text-sm font-semibold pr-8" style={{ color: '#1E293B' }}>
        {payload.title}
      </h3>
      <SusaColumnMapper
        sessionId={sessionId}
        previewFileId={previewFileId}
        layoutFormat={layoutFormat}
        signMode={signMode}
        valueType={valueType}
        entityNames={entityNames}
        disabled={disabled}
        onSubmit={handleMapping}
      />
    </div>
  )
}

function AdaptiveCardForm({ payload, onSubmit, onFileUpload, disabled }: Props) {
  const inputs = payload.inputs ?? []

  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const init: Record<string, unknown> = {}
    for (const inp of inputs) {
      if (inp.type === 'multi_select') {
        // Honour a `default` array when provided (e.g. gl_databook_scope pre-selects all
        // entities/fiscal-years). Accept a JSON-encoded string, a real array, or fall back
        // to an empty selection when no default is given.
        if (inp.default !== undefined && inp.default !== null && inp.default !== '') {
          if (Array.isArray(inp.default)) {
            init[inp.id] = inp.default as string[]
          } else {
            try {
              const parsed: unknown = JSON.parse(String(inp.default))
              init[inp.id] = Array.isArray(parsed) ? parsed : []
            } catch {
              // Treat a plain comma-separated string as a fallback encoding.
              init[inp.id] = String(inp.default)
                .split(',')
                .map(s => s.trim())
                .filter(Boolean)
            }
          }
        } else {
          init[inp.id] = []
        }
      } else if (inp.type === 'sortable_list') {
        init[inp.id] = inp.options?.map(o => o.value) ?? []
      } else {
        init[inp.id] = inp.default ?? ''
      }
    }
    return init
  })
  const [fileCache, setFileCache] = useState<Record<string, File | File[]>>({})
  // file_id results keyed by input id (regular) or cell key (susa_grid); susa cells hold one or many uploads
  const [fileResults, setFileResults] = useState<Record<string, { file_id: string; file_path: string }[]>>({})
  // display names for susa_grid cells
  const [susaFileNames, setSusaFileNames] = useState<Record<string, string | null>>({})
  const [submitting, setSubmitting] = useState(false)

  const set = (id: string, v: unknown) => setValues(prev => ({ ...prev, [id]: v }))

  const handleSubmit = async () => {
    if (disabled || submitting) return
    setSubmitting(true)

    try {
      const visible = inputs.filter(inp => inputVisible(inp, values))
      // folder_select: path may come only from the subfolder dropdown; root is "" + "" — must not block Confirm
      if (payload.card !== 'folder_select') {
        for (const inp of visible) {
          if (inp.required === false) continue
          if (inp.type === 'file_drop') {
            const cached = fileCache[inp.id]
            if (!cached || Array.isArray(cached)) {
              setSubmitting(false)
              return
            }
            continue
          }
          if (inp.type === 'folder_drop') {
            const cached = fileCache[inp.id]
            const files = Array.isArray(cached) ? cached : cached ? [cached] : []
            if (!files.length) {
              setSubmitting(false)
              return
            }
            continue
          }
          if (inp.type === 'susa_grid') continue
          const raw = values[inp.id]
          if (inp.type === 'multi_select') {
            if (!Array.isArray(raw) || raw.length === 0) {
              setSubmitting(false)
              return
            }
            continue
          }
          if (inp.type === 'month_picker') {
            if (!String(raw ?? '').trim()) {
              setSubmitting(false)
              return
            }
            continue
          }
          if (inp.type === 'number') {
            const s = String(raw ?? '').trim()
            if (s === '' || Number.isNaN(Number(s.replace(',', '.')))) {
              setSubmitting(false)
              return
            }
            continue
          }
          if (String(raw ?? '').trim() === '') {
            setSubmitting(false)
            return
          }
        }
      }

      const out: Record<string, unknown> = {}
      const visibleIds = new Set(visible.map(inp => inp.id))

      for (const inp of inputs) {
        if (!visibleIds.has(inp.id)) {
          continue
        }
        if (inp.type === 'file_drop') {
          const cached = fileCache[inp.id]
          const file = Array.isArray(cached) ? cached[0] : cached
          if (file && onFileUpload) {
            const result = await onFileUpload(file)
            if (result) {
              out[inp.id + '_file_id'] = result.file_id
              out['file_id'] = result.file_id
              out['consolidation_file_id'] = result.file_id
              out['fs_review_file_id'] = result.file_id
              out['file_path'] = result.file_path
              out['headers'] = result.headers
              out['session_id'] = result.session_id
              if (result.sheet_names?.length) {
                out['sheet_names'] = result.sheet_names
              }
            }
          }
        } else if (inp.type === 'folder_drop') {
          const cached = fileCache[inp.id]
          const files = Array.isArray(cached) ? cached : cached ? [cached] : []
          const ids: string[] = []
          if (files.length && onFileUpload) {
            for (const f of files) {
              const result = await onFileUpload(f)
              if (result) ids.push(result.file_id)
            }
          }
          out['pdf_file_ids'] = ids
          out['fs_folder_file_ids'] = ids
        } else if (inp.type === 'sortable_list') {
          out[inp.id] = (values[inp.id] as string[]) ?? inp.options?.map(o => o.value) ?? []
        } else if (inp.type === 'susa_grid') {
          const entityCount = inp.entity_count ?? 5
          const years = inp.options?.map(o => o.value) ?? []
          const entityNames: string[] = []
          for (let ei = 0; ei < entityCount; ei++) {
            entityNames.push(String(values[`${inp.id}_entity_${ei + 1}_name`] ?? `Entity ${ei + 1}`))
          }
          out['entity_names'] = entityNames
          const gridGroups: Record<string, string[]> = {}
          for (let ei = 0; ei < entityCount; ei++) {
            for (const yr of years) {
              const cellKey = `entity_${ei + 1}_${yr}`
              const results = fileResults[cellKey]
              if (results?.length) {
                gridGroups[cellKey] = results.map(r => r.file_id)
              } else {
                const raw = fileCache[cellKey]
                const pendingList = Array.isArray(raw) ? raw : raw ? [raw] : []
                if (pendingList.length && onFileUpload) {
                  const uploadedRows: { file_id: string; file_path: string }[] = []
                  const ids: string[] = []
                  for (const f of pendingList) {
                    const uploaded = await onFileUpload(f)
                    if (uploaded) {
                      ids.push(uploaded.file_id)
                      uploadedRows.push({ file_id: uploaded.file_id, file_path: uploaded.file_path })
                    }
                  }
                  if (ids.length) {
                    gridGroups[cellKey] = ids
                    setFileResults(prev => ({ ...prev, [cellKey]: uploadedRows }))
                  }
                }
              }
            }
          }
          out['susa_file_groups'] = gridGroups
        } else {
          const raw = values[inp.id]
          if (inp.type === 'number') {
            const s = String(raw ?? '').trim().replace(',', '.')
            out[inp.id] = s === '' ? '' : Number(s)
          } else {
            out[inp.id] = raw
          }
        }
      }

      if (payload.card === 'file_upload' && !out.file_id) {
        return
      }

      await Promise.resolve(
        onSubmit(payload.card, out, { submitLabel: payload.submit_label }),
      )
    } finally {
      setSubmitting(false)
    }
  }

  const handleSusaCellFiles = async (_gridInputId: string, entityIdx: number, year: string, files: File[]) => {
    const cellKey = `entity_${entityIdx + 1}_${year}`
    if (!files.length) return
    if (files.length === 1) {
      setSusaFileNames(prev => ({ ...prev, [cellKey]: files[0].name }))
    } else {
      setSusaFileNames(prev => ({ ...prev, [cellKey]: `${files.length} .xlsx files` }))
    }
    setFileCache(prev => ({ ...prev, [cellKey]: files }))
    if (!onFileUpload) return
    const results: { file_id: string; file_path: string }[] = []
    for (const f of files) {
      const r = await onFileUpload(f)
      if (r) {
        results.push({ file_id: r.file_id, file_path: r.file_path })
      }
    }
    if (results.length) {
      setFileResults(prev => ({ ...prev, [cellKey]: results }))
    }
  }

  const handleSusaEntityName = (inputId: string, entityIdx: number, name: string) => {
    set(`${inputId}_entity_${entityIdx + 1}_name`, name)
  }

  const renderInput = (inp: AdaptiveCardInput) => {
    const dense = compact
    switch (inp.type) {
      case 'text':
        return (
          <TextInput
            key={inp.id}
            input={inp}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
            dense={dense}
          />
        )
      case 'number':
        return (
          <TextInput
            key={inp.id}
            input={inp}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
            dense={dense}
          />
        )
      case 'dropdown':
        return (
          <DropdownInput
            key={inp.id}
            input={inp}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
            dense={dense}
          />
        )
      case 'radio':
        return (
          <RadioInput
            key={inp.id}
            input={inp}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
            dense={dense}
          />
        )
      case 'multi_select':
        return (
          <MultiSelectInput
            key={inp.id}
            input={inp}
            value={(values[inp.id] as string[]) ?? []}
            onChange={v => set(inp.id, v)}
          />
        )
      case 'month_picker':
        return (
          <MonthPickerInput
            key={inp.id}
            input={inp}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
            dense={dense}
          />
        )
      case 'day_picker':
        return (
          <TextInput
            key={inp.id}
            input={{ ...inp, type: 'number', placeholder: inp.placeholder ?? '31' }}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
            dense={dense}
          />
        )
      case 'date_picker':
        return (
          <DatePickerInput
            key={inp.id}
            input={inp}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
          />
        )
      case 'file_drop':
        return (
          <FileDropInput
            key={inp.id}
            input={inp}
            onFileSelect={file => setFileCache(prev => ({ ...prev, [inp.id]: file }))}
          />
        )
      case 'folder_drop':
        return (
          <FolderDropInput
            key={inp.id}
            input={inp}
            onFilesSelect={files => setFileCache(prev => ({ ...prev, [inp.id]: files }))}
          />
        )
      case 'sortable_list':
        return (
          <SortableListInput
            key={inp.id}
            input={inp}
            value={(values[inp.id] as string[]) ?? inp.options?.map(o => o.value) ?? []}
            onChange={v => set(inp.id, v)}
          />
        )
      case 'folder_picker':
        return (
          <FolderPickerInput
            key={inp.id}
            input={inp}
            value={String(values[inp.id] ?? '')}
            onChange={v => set(inp.id, v)}
            dense={dense}
          />
        )
      case 'susa_grid':
        return (
          <SusaGridInput
            key={inp.id}
            input={inp}
            fileStatuses={susaFileNames}
            onCellFiles={(entityIdx, year, files) => handleSusaCellFiles(inp.id, entityIdx, year, files)}
            onEntityNameChange={(entityIdx, name) => handleSusaEntityName(inp.id, entityIdx, name)}
          />
        )
      case 'fte_pex_grid':
        return (
          <FtePexGrid
            key={inp.id}
            input={inp}
            values={values}
            disabled={disabled}
            onChange={v => set(inp.id, v)}
          />
        )
      default:
        return null
    }
  }

  const hasSusaGrid = inputs.some(i => i.type === 'susa_grid' || i.type === 'fte_pex_grid')
  const compact = Boolean(payload.compact)
  const visibleInputs = inputs.filter(inp => inputVisible(inp, values))
  const cardMax = hasSusaGrid ? 720 : compact ? 440 : 380

  return (
    <div
      className="rounded-xl mb-3 overflow-hidden"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
        maxWidth: cardMax,
      }}
    >
      {/* Header */}
      <div
        className={compact ? 'px-3 pt-2 pb-2' : 'px-4 pt-4 pb-3'}
        style={{ borderBottom: inputs.length > 0 || payload.review_text ? '1px solid #F1F5F9' : 'none' }}
      >
        <p className={compact ? 'text-xs font-semibold' : 'text-sm font-semibold'} style={{ color: '#111827' }}>{payload.title}</p>
        {payload.subtitle && (
          <p className={`${compact ? 'text-[12px] mt-0.5' : 'text-xs mt-0.5'} leading-snug`} style={{ color: '#64748B' }}>
            {payload.subtitle}
          </p>
        )}
        {payload.download_template && (
          <a
            href={
              payload.download_template.startsWith('http')
                ? payload.download_template
                : `${getApiBaseUrl()}${payload.download_template}`
            }
            download
            className="inline-flex items-center gap-1.5 mt-2 text-xs font-semibold rounded-lg px-3 py-1.5"
            style={{ background: '#E2E8F0', color: '#1E3A5F' }}
          >
            <Download size={14} />
            Download template
          </a>
        )}
      </div>

      {/* Review text */}
      {payload.review_text && (
        <div className="px-4 py-3" style={{ borderBottom: '1px solid #F1F5F9', background: '#F8FAFC' }}>
          <pre
            className="text-xs leading-relaxed whitespace-pre-wrap"
            style={{ color: '#334155', fontFamily: 'inherit' }}
          >
            {payload.review_text}
          </pre>
        </div>
      )}

      {/* Filter info banner */}
      {payload.filter_info && (
        <div className="px-4 py-3" style={{ background: '#FFFBEB', borderBottom: '1px solid #FEF3C7' }}>
          <p className="text-xs font-medium mb-1" style={{ color: '#92400E' }}>
            {payload.filter_info.label}
          </p>
          <p className="text-xs whitespace-pre-wrap" style={{ color: '#78350F' }}>
            {payload.filter_info.value}
          </p>
        </div>
      )}

      {/* Inputs */}
      {inputs.length > 0 && (
        <div
          className={
            compact
              ? 'px-3 py-2 grid grid-cols-2 gap-x-2 gap-y-1.5 [&_label]:text-[12px] [&_input]:text-xs [&_select]:text-xs [&_button]:text-[12px]'
              : 'px-4 py-4 flex flex-col gap-4'
          }
        >
          {chunkInputRows(visibleInputs).map(chunk => {
            if (chunk.length === 1) {
              const inp = chunk[0]
              return (
                <div
                  key={inp.id}
                  className={compact && (inp.span ?? 1) === 2 ? 'col-span-2' : undefined}
                >
                  {renderInput(inp)}
                </div>
              )
            }
            return (
              <div
                key={chunk.map(c => c.id).join('-')}
                className={
                  compact
                    ? 'col-span-2 flex flex-row gap-2 w-full min-w-0 [&_label]:text-[12px]'
                    : 'flex flex-row gap-2 w-full min-w-0'
                }
              >
                {chunk.map(inp => (
                  <div key={inp.id} className="flex-1 min-w-0">
                    {renderInput(inp)}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}

      {payload.susa_grid_note && (
        <p className={`${compact ? 'px-3 pb-1' : 'px-4 pb-2'} text-xs`} style={{ color: '#94A3B8' }}>
          {payload.susa_grid_note}
        </p>
      )}

      {/* Footer — submit / secondary */}
      {!disabled && (
        <div className={`${compact ? 'px-3 pb-2' : 'px-4 pb-4'} flex flex-col gap-2`}>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="w-full rounded-lg py-2 text-xs font-semibold transition-opacity"
            style={{
              background: '#1E3A5F',
              color: '#FFFFFF',
              opacity: submitting ? 0.6 : 1,
            }}
          >
            {submitting ? 'Processing...' : (payload.submit_label ?? 'Submit')}
          </button>
          {payload.secondary_submit_label && payload.secondary_submit_id && (
            <button
              type="button"
              disabled={submitting}
              className="w-full rounded-lg py-2 text-xs font-medium border border-slate-300"
              style={{ color: '#475569', opacity: submitting ? 0.6 : 1 }}
              onClick={async () => {
                setSubmitting(true)
                try {
                  await Promise.resolve(
                    onSubmit(payload.card, { [payload.secondary_submit_id!]: true }),
                  )
                } finally {
                  setSubmitting(false)
                }
              }}
            >
              {payload.secondary_submit_label}
            </button>
          )}
        </div>
      )}

      {disabled && (
        <div className="px-4 pb-3">
          <p className="text-xs" style={{ color: '#94A3B8' }}>Response submitted</p>
        </div>
      )}
    </div>
  )
}
