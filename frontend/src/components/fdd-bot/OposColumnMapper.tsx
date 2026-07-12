/**
 * Interactive column mapper for OPOS (creditor / debitor aging).
 * Click a column header to assign the active role; undo last assignment inline.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Undo2 } from 'lucide-react'
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

export type OposRole =
  | 'partner'
  | 'amount'
  | 'due_date'

const ROLES: { key: OposRole; label: string; slot: string }[] = [
  { key: 'partner', label: 'Partner (Debitor/Kreditor)', slot: 'opos_partner_col' },
  { key: 'amount', label: 'Amount', slot: 'opos_amount_col' },
  { key: 'due_date', label: 'Due date', slot: 'opos_due_date_col' },
]

const ROLE_COLORS: Record<OposRole, string> = {
  partner: '#2563EB',
  amount: '#059669',
  due_date: '#D97706',
}

interface Props {
  sessionId: string
  previewFileId: string
  sheetName?: string
  headerRow?: number
  snapshotsJson?: string
  disabled?: boolean
  onSubmit: (payload: Record<string, string>) => void | Promise<void>
  onReupload?: () => void | Promise<void>
}

function buildSubmitPayload(
  assignments: Partial<Record<OposRole, string>>,
  headerByLetter: Map<string, string>,
): Record<string, string> {
  const payload: Record<string, string> = {}
  const letters: Record<string, string> = {}
  for (const role of ROLES) {
    const letter = assignments[role.key]
    const header = letter ? headerByLetter.get(letter) : ''
    if (letter) {
      letters[role.key === 'partner' ? 'partner' : role.key] = letter
    }
    if (header) {
      payload[role.slot] = header
      if (role.key === 'partner') {
        payload.opos_partner_id_col = header
        payload.opos_partner_name_col = header
      }
    }
  }
  payload.opos_column_letters_json = JSON.stringify(letters)
  return payload
}

export default function OposColumnMapper({
  sessionId,
  previewFileId,
  sheetName = '',
  headerRow = 0,
  snapshotsJson = '[]',
  disabled,
  onSubmit,
  onReupload,
}: Props) {
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [assignments, setAssignments] = useState<Partial<Record<OposRole, string>>>({})
  const [history, setHistory] = useState<Partial<Record<OposRole, string>>[]>([])
  const [stepIndex, setStepIndex] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [dueDateWarning, setDueDateWarning] = useState<number | null>(null)
  const [pendingPayload, setPendingPayload] = useState<Record<string, string> | null>(null)

  const currentRole = ROLES[stepIndex]?.key
  const allMapped = ROLES.every(r => assignments[r.key]?.trim())

  const snapshots = useMemo(() => {
    try {
      const parsed = JSON.parse(snapshotsJson || '[]')
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }, [snapshotsJson])

  const headerByLetter = useMemo(() => {
    const map = new Map<string, string>()
    for (const col of preview?.columns ?? []) {
      map.set(col.letter, col.header || col.letter)
    }
    return map
  }, [preview])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        if (!previewFileId.trim()) {
          throw new Error('No file — please upload your OPOS file first.')
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
        const resp = await fetch(`${base}/api/v1/fdd/opos/preview?${params}`)
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

  const roleForLetter = useCallback(
    (letter: string): OposRole | undefined => {
      for (const role of ROLES) {
        if (assignments[role.key] === letter) return role.key
      }
      return undefined
    },
    [assignments],
  )

  const assignColumn = (letter: string) => {
    if (!currentRole || disabled) return
    const header = headerByLetter.get(letter)?.trim()
    if (!header) return

    setHistory(prev => [...prev, { ...assignments }])
    const next: Partial<Record<OposRole, string>> = { ...assignments }
    for (const role of ROLES) {
      if (next[role.key] === letter) delete next[role.key]
    }
    next[currentRole] = letter
    setAssignments(next)
    setDueDateWarning(null)
    setPendingPayload(null)

    const nextStep = ROLES.findIndex(r => !next[r.key])
    setStepIndex(nextStep >= 0 ? nextStep : ROLES.length - 1)
  }

  const undoLast = () => {
    if (!history.length || disabled) return
    const prev = history[history.length - 1]
    setHistory(h => h.slice(0, -1))
    setAssignments(prev)
    setDueDateWarning(null)
    setPendingPayload(null)
    const nextStep = ROLES.findIndex(r => !prev[r.key])
    setStepIndex(nextStep >= 0 ? nextStep : 0)
  }

  const auditMissingDueDates = async (payload: Record<string, string>): Promise<number> => {
    if (!snapshots.length) return 0
    let letters: Record<string, string> = {}
    try {
      letters = JSON.parse(payload.opos_column_letters_json || '{}') as Record<string, string>
    } catch {
      return 0
    }
    if (!letters.partner && !letters.due_date) return 0

    const base = getApiBaseUrl()
    const resp = await fetch(`${base}/api/v1/fdd/opos/audit-due-dates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        column_letters: letters,
        snapshots,
      }),
    })
    if (!resp.ok) return 0
    const data = (await resp.json()) as { total_excluded?: number }
    return Number(data.total_excluded || 0)
  }

  const finalizeSubmit = async (payload: Record<string, string>) => {
    setDueDateWarning(null)
    setPendingPayload(null)
    await onSubmit(payload)
  }

  const handleSubmit = async () => {
    if (!allMapped || submitting || disabled) return
    setSubmitting(true)
    try {
      const payload = buildSubmitPayload(assignments, headerByLetter)
      const excluded = await auditMissingDueDates(payload)
      if (excluded > 0) {
        setDueDateWarning(excluded)
        setPendingPayload(payload)
        return
      }
      await finalizeSubmit(payload)
    } finally {
      setSubmitting(false)
    }
  }

  const handleContinueDespiteWarning = async () => {
    if (!pendingPayload || submitting || disabled) return
    setSubmitting(true)
    try {
      await finalizeSubmit(pendingPayload)
    } finally {
      setSubmitting(false)
    }
  }

  const handleReupload = async () => {
    if (submitting || disabled) return
    setDueDateWarning(null)
    setPendingPayload(null)
    if (onReupload) {
      await onReupload()
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-500">Loading OPOS preview…</p>
  }
  if (error && !preview) {
    return <p className="text-sm text-red-600">{error}</p>
  }
  if (!preview) return null

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {ROLES.map((role, i) => {
          const mapped = assignments[role.key]
          const header = mapped ? headerByLetter.get(mapped) : ''
          const active = i === stepIndex && !allMapped
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
      </div>

      <div className="overflow-x-auto overflow-y-auto max-h-[320px] rounded-lg border border-slate-200 w-full">
        <table className="text-xs border-collapse min-w-full">
          <thead>
            <tr>
              {preview.columns.map(col => {
                const role = roleForLetter(col.letter)
                const canClick = !disabled && currentRole && !allMapped
                const bg = role ? ROLE_COLORS[role] : '#E2E8F0'
                return (
                  <th
                    key={col.letter}
                    className="px-2 py-1 border border-slate-200 select-none min-w-[72px] max-w-[120px]"
                    style={{
                      background: bg,
                      color: role ? '#fff' : '#475569',
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
                  return (
                    <td
                      key={ci}
                      className="px-2 py-0.5 border border-slate-100 whitespace-nowrap max-w-[120px] truncate"
                      style={{
                        background: role ? `${ROLE_COLORS[role]}18` : undefined,
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

      {dueDateWarning !== null && dueDateWarning > 0 && (
        <div
          className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950"
          role="alert"
        >
          <p className="font-medium">
            {dueDateWarning.toLocaleString('en-US')} row{dueDateWarning === 1 ? '' : 's'} will be
            excluded due to missing due dates.
          </p>
          <p className="mt-1 text-amber-900">
            Do you want to continue anyway, or correct the file and re-upload?
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={disabled || submitting}
              onClick={handleContinueDespiteWarning}
              className="rounded-md bg-amber-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-800 disabled:opacity-50"
            >
              Continue anyway
            </button>
            <button
              type="button"
              disabled={disabled || submitting}
              onClick={handleReupload}
              className="rounded-md border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
            >
              Re-upload files
            </button>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 self-start">
        {history.length > 0 && (
          <button
            type="button"
            disabled={disabled || submitting}
            onClick={undoLast}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            <Undo2 size={14} />
            Undo
          </button>
        )}
        {!dueDateWarning && (
          <button
            type="button"
            disabled={!allMapped || submitting || disabled}
            onClick={handleSubmit}
            className="rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            style={{ background: '#1E40AF' }}
          >
            {submitting ? 'Checking…' : 'Continue'}
          </button>
        )}
      </div>
    </div>
  )
}
