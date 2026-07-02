/**
 * Interactive column mapper for Fixed Assets Rollforward.
 * Click column headers for opening / additions / disposals; multi-select depreciation columns.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Undo2 } from 'lucide-react'
import { getApiBaseUrl } from '../../lib/api'

interface PreviewColumn {
  letter: string
  header: string
  sample_values: string[]
}

interface PreviewData {
  header_row_index: number
  columns: PreviewColumn[]
  rows: string[][]
}

export type FaRollfRole = 'opening' | 'additions' | 'disposals'

const SINGLE_ROLES: { key: FaRollfRole; label: string; slot: string }[] = [
  { key: 'opening', label: 'Opening (FY begin)', slot: 'fa_opening_col' },
  { key: 'additions', label: 'Additions', slot: 'fa_additions_col' },
  { key: 'disposals', label: 'Disposals', slot: 'fa_disposals_col' },
]

const ROLE_COLORS: Record<FaRollfRole, string> = {
  opening: '#2563EB',
  additions: '#059669',
  disposals: '#D97706',
}

interface Props {
  sessionId: string
  previewFileId: string
  sheetName?: string
  headerRow?: number
  disabled?: boolean
  onSubmit: (payload: Record<string, string>) => void | Promise<void>
}

export default function FaRollfColumnMapper({
  sessionId,
  previewFileId,
  sheetName = '',
  headerRow = 0,
  disabled,
  onSubmit,
}: Props) {
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [assignments, setAssignments] = useState<Partial<Record<FaRollfRole, string>>>({})
  const [history, setHistory] = useState<Partial<Record<FaRollfRole, string>>[]>([])
  const [stepIndex, setStepIndex] = useState(0)
  const [depCols, setDepCols] = useState<Set<string>>(new Set())
  const [phase, setPhase] = useState<'map' | 'depreciation'>('map')
  const [submitting, setSubmitting] = useState(false)
  const submittedRef = useRef(false)

  const currentRole = SINGLE_ROLES[stepIndex]?.key
  const singleMapped = SINGLE_ROLES.every(r => assignments[r.key]?.trim())

  const headerByLetter = useMemo(() => {
    const map = new Map<string, string>()
    for (const col of preview?.columns ?? []) {
      map.set(col.letter, col.header || col.letter)
    }
    return map
  }, [preview])

  const mappedLetters = useMemo(
    () => new Set(Object.values(assignments).filter(Boolean) as string[]),
    [assignments],
  )

  const depCandidates = useMemo(() => {
    return (preview?.columns ?? []).filter(c => {
      const h = (c.header || c.letter).trim()
      return h && !mappedLetters.has(c.letter)
    })
  }, [preview, mappedLetters])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        if (!previewFileId.trim()) {
          throw new Error('No file — please upload your register file first.')
        }
        if (!sessionId.trim()) {
          throw new Error('No session — please refresh the conversation.')
        }
        const base = getApiBaseUrl()
        const params = new URLSearchParams({
          session_id: sessionId,
          file_id: previewFileId,
          header_row: String(headerRow),
          max_rows: '12',
        })
        if (sheetName.trim()) params.set('sheet_name', sheetName.trim())
        const resp = await fetch(`${base}/api/v1/fdd/fa_rollf/preview?${params}`)
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}))
          const detail = (err as { detail?: string }).detail
          throw new Error(detail || `Preview failed (${resp.status})`)
        }
        const data = (await resp.json()) as PreviewData
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
  }, [sessionId, previewFileId, sheetName, headerRow])

  useEffect(() => {
    if (singleMapped && phase === 'map') {
      setPhase('depreciation')
    }
  }, [singleMapped, phase])

  const roleForLetter = useCallback(
    (letter: string): FaRollfRole | undefined => {
      for (const role of SINGLE_ROLES) {
        if (assignments[role.key] === letter) return role.key
      }
      return undefined
    },
    [assignments],
  )

  const assignColumn = (letter: string) => {
    if (!currentRole || disabled || phase !== 'map') return
    const header = headerByLetter.get(letter)?.trim()
    if (!header) return

    setHistory(prev => [...prev, { ...assignments }])
    const next: Partial<Record<FaRollfRole, string>> = { ...assignments }
    for (const role of SINGLE_ROLES) {
      if (next[role.key] === letter) delete next[role.key]
    }
    next[currentRole] = letter
    setAssignments(next)

    const nextStep = SINGLE_ROLES.findIndex(r => !next[r.key])
    setStepIndex(nextStep >= 0 ? nextStep : SINGLE_ROLES.length - 1)
  }

  const undoLast = () => {
    if (!history.length || disabled) return
    submittedRef.current = false
    setPhase('map')
    setDepCols(new Set())
    const prev = history[history.length - 1]
    setHistory(h => h.slice(0, -1))
    setAssignments(prev)
    const nextStep = SINGLE_ROLES.findIndex(r => !prev[r.key])
    setStepIndex(nextStep >= 0 ? nextStep : 0)
  }

  const toggleDepCol = (header: string) => {
    if (disabled) return
    setDepCols(prev => {
      const next = new Set(prev)
      if (next.has(header)) next.delete(header)
      else next.add(header)
      return next
    })
  }

  const handleSubmit = useCallback(async () => {
    if (!singleMapped || depCols.size === 0 || submitting || disabled || submittedRef.current) return
    submittedRef.current = true
    setSubmitting(true)
    try {
      const payload: Record<string, string> = {}
      for (const role of SINGLE_ROLES) {
        const letter = assignments[role.key]
        const header = letter ? headerByLetter.get(letter) : ''
        if (header) payload[role.slot] = header
      }
      payload.fa_depreciation_cols_json = JSON.stringify([...depCols])
      await onSubmit(payload)
    } finally {
      setSubmitting(false)
    }
  }, [singleMapped, depCols, assignments, disabled, headerByLetter, onSubmit, submitting])

  if (loading) {
    return <p className="text-sm text-slate-500">Loading register preview…</p>
  }
  if (error && !preview) {
    return <p className="text-sm text-red-600">{error}</p>
  }
  if (!preview) return null

  return (
    <div className="flex flex-col gap-3">
      {phase === 'map' && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {SINGLE_ROLES.map((role, i) => {
            const mapped = assignments[role.key]
            const header = mapped ? headerByLetter.get(mapped) : ''
            const active = i === stepIndex && !singleMapped
            return (
              <span
                key={role.key}
                className="rounded-full px-2.5 py-1 font-medium"
                style={{
                  background: mapped ? ROLE_COLORS[role.key] : active ? '#E2E8F0' : '#F1F5F9',
                  color: mapped || active ? (mapped ? '#fff' : '#334155') : '#94A3B8',
                  outline: active ? `2px solid ${ROLE_COLORS[role.key]}` : undefined,
                }}
              >
                {role.label}
                {header ? `: ${header}` : active ? ' ← click column' : ''}
              </span>
            )
          })}
          {history.length > 0 && (
            <button
              type="button"
              disabled={disabled}
              onClick={undoLast}
              className="ml-auto inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              <Undo2 size={14} />
              Undo
            </button>
          )}
        </div>
      )}

      {phase === 'depreciation' && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-slate-600">
            Select all depreciation columns to sum (e.g. annual AfA + disposal AfA), then confirm.
          </p>
          <div className="flex flex-wrap gap-2">
            {depCandidates.map(col => {
              const header = col.header || col.letter
              const selected = depCols.has(header)
              return (
                <button
                  key={col.letter}
                  type="button"
                  disabled={disabled}
                  onClick={() => toggleDepCol(header)}
                  className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
                  style={{
                    borderColor: selected ? '#7C3AED' : '#E2E8F0',
                    background: selected ? '#7C3AED' : '#FFFFFF',
                    color: selected ? '#FFFFFF' : '#475569',
                  }}
                >
                  {selected && <Check size={12} />}
                  {header}
                </button>
              )
            })}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={undoLast}
              className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
            >
              <Undo2 size={14} />
              Back to column mapping
            </button>
            <button
              type="button"
              disabled={disabled || depCols.size === 0 || submitting}
              onClick={() => void handleSubmit()}
              className="ml-auto inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              style={{ background: '#7C3AED' }}
            >
              <Check size={14} />
              Confirm depreciation columns
            </button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto overflow-y-auto max-h-[320px] rounded-lg border border-slate-200 w-full">
        <table className="text-xs border-collapse min-w-full">
          <thead>
            <tr>
              {preview.columns.map(col => {
                const role = roleForLetter(col.letter)
                const header = col.header || col.letter
                const depSelected = phase === 'depreciation' && depCols.has(header)
                const canClick = !disabled && phase === 'map' && currentRole && !singleMapped
                let bg = role ? ROLE_COLORS[role] : '#E2E8F0'
                if (depSelected) bg = '#7C3AED'
                return (
                  <th
                    key={col.letter}
                    className="px-2 py-1 border border-slate-200 select-none min-w-[72px] max-w-[120px]"
                    style={{
                      background: bg,
                      color: role || depSelected ? '#fff' : '#475569',
                      cursor: canClick ? 'pointer' : 'default',
                    }}
                    onClick={() => canClick && assignColumn(col.letter)}
                    title={[col.header, ...col.sample_values.slice(0, 3)].filter(Boolean).join(' · ')}
                  >
                    <div className="font-semibold truncate">{col.header || col.letter}</div>
                    <div className="text-[9px] opacity-80">{col.letter}</div>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => {
                  const letter = preview.columns[ci]?.letter
                  const role = letter ? roleForLetter(letter) : undefined
                  const header = preview.columns[ci]?.header || letter
                  const depSelected = phase === 'depreciation' && header && depCols.has(header)
                  return (
                    <td
                      key={ci}
                      className="px-2 py-0.5 border border-slate-100 whitespace-nowrap max-w-[120px] truncate"
                      style={{
                        background: role
                          ? `${ROLE_COLORS[role]}18`
                          : depSelected
                            ? '#7C3AED18'
                            : undefined,
                      }}
                    >
                      {cell}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {submitting && <p className="text-xs text-slate-500 self-end">Continuing…</p>}
    </div>
  )
}

