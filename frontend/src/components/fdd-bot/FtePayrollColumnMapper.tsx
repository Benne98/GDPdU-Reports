/**
 * Interactive column mapper for FTE Development.
 * Click headers for employment rate and annual working time; multi-select payroll columns via table clicks.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

type FteRole = 'employment' | 'months_sum'

const SINGLE_ROLES: { key: FteRole; label: string; slot: string }[] = [
  { key: 'employment', label: 'Employment rate', slot: 'fte_employment_col' },
  { key: 'months_sum', label: 'Annual working time (months)', slot: 'fte_months_col' },
]

const ROLE_COLORS: Record<FteRole, string> = {
  employment: '#2563EB',
  months_sum: '#059669',
}

interface Props {
  sessionId: string
  previewFileId: string
  periodsJson?: string
  sheetName?: string
  headerRow?: number
  disabled?: boolean
  onSubmit: (payload: Record<string, string>) => void | Promise<void>
}

export default function FtePayrollColumnMapper({
  sessionId,
  previewFileId,
  periodsJson = '[]',
  sheetName = '',
  headerRow = 0,
  disabled,
  onSubmit,
}: Props) {
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [assignments, setAssignments] = useState<Partial<Record<FteRole, string>>>({})
  const [history, setHistory] = useState<Partial<Record<FteRole, string>>[]>([])
  const [stepIndex, setStepIndex] = useState(0)
  const [payCols, setPayCols] = useState<Set<string>>(new Set())
  const [paySelectionOrder, setPaySelectionOrder] = useState<string[]>([])
  const [phase, setPhase] = useState<'map' | 'payroll'>('map')
  const [submitting, setSubmitting] = useState(false)
  const [columnWarning, setColumnWarning] = useState<number | null>(null)
  const [pendingPayload, setPendingPayload] = useState<Record<string, string> | null>(null)
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

  const mappedSingleHeaders = useMemo(() => {
    const headers = new Set<string>()
    for (const role of SINGLE_ROLES) {
      const letter = assignments[role.key]
      const h = letter ? headerByLetter.get(letter) : ''
      if (h) headers.add(h)
    }
    return headers
  }, [assignments, headerByLetter])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        if (!previewFileId.trim()) {
          throw new Error('No file — please upload your personnel file first.')
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
        const resp = await fetch(`${base}/api/v1/fdd/fte_payroll/preview?${params}`)
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

  const auditPeriods = useMemo(() => {
    try {
      const parsed = JSON.parse(periodsJson || '[]') as Array<{
        file_id?: string
        sheet_name?: string
        label?: string
      }>
      if (Array.isArray(parsed) && parsed.length > 0) {
        return parsed.map(p => ({
          file_id: String(p.file_id || previewFileId || '').trim(),
          sheet_name: String(p.sheet_name || sheetName || '').trim(),
          label: String(p.label || '').trim(),
        }))
      }
    } catch {
      /* ignore */
    }
    return [{ file_id: previewFileId, sheet_name: sheetName, label: 'Preview' }]
  }, [periodsJson, previewFileId, sheetName])

  const auditInvalidColumns = useCallback(
    async (payload: Record<string, string>): Promise<number> => {
      if (!previewFileId.trim()) return 0
      const base = getApiBaseUrl()
      const resp = await fetch(`${base}/api/v1/fdd/fte_payroll/audit-columns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          periods: auditPeriods,
          columns: {
            employment: payload.fte_employment_col,
            months_sum: payload.fte_months_col,
          },
          header_row: headerRow,
        }),
      })
      if (!resp.ok) return 0
      const data = (await resp.json()) as { total_invalid?: number }
      return Number(data.total_invalid || 0)
    },
    [auditPeriods, headerRow, previewFileId, sessionId],
  )

  useEffect(() => {
    if (!singleMapped || phase !== 'map') return
    let cancelled = false
    ;(async () => {
      const payload: Record<string, string> = {}
      for (const role of SINGLE_ROLES) {
        const letter = assignments[role.key]
        const header = letter ? headerByLetter.get(letter) : ''
        if (header) payload[role.slot] = header
      }
      const invalid = await auditInvalidColumns(payload)
      if (!cancelled) {
        setColumnWarning(invalid > 0 ? invalid : null)
        setPhase('payroll')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [singleMapped, phase, assignments, headerByLetter, auditInvalidColumns])

  useEffect(() => {
    if (phase !== 'map' || singleMapped) return
    setColumnWarning(null)
  }, [phase, singleMapped, assignments])

  const roleForLetter = useCallback(
    (letter: string): FteRole | undefined => {
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
    const next: Partial<Record<FteRole, string>> = { ...assignments }
    for (const role of SINGLE_ROLES) {
      if (next[role.key] === letter) delete next[role.key]
    }
    next[currentRole] = letter
    setAssignments(next)

    const nextStep = SINGLE_ROLES.findIndex(r => !next[r.key])
    setStepIndex(nextStep >= 0 ? nextStep : SINGLE_ROLES.length - 1)
  }

  const undoLast = () => {
    if (disabled) return
    submittedRef.current = false
    if (phase === 'payroll' && paySelectionOrder.length > 0) {
      const last = paySelectionOrder[paySelectionOrder.length - 1]
      setPaySelectionOrder(prev => prev.slice(0, -1))
      setPayCols(prev => {
        const next = new Set(prev)
        next.delete(last)
        return next
      })
      return
    }
    if (!history.length) return
    setPhase('map')
    setPayCols(new Set())
    setPaySelectionOrder([])
    const prev = history[history.length - 1]
    setHistory(h => h.slice(0, -1))
    setAssignments(prev)
    const nextStep = SINGLE_ROLES.findIndex(r => !prev[r.key])
    setStepIndex(nextStep >= 0 ? nextStep : 0)
  }

  const togglePayCol = (header: string) => {
    if (disabled || phase !== 'payroll') return
    if (mappedSingleHeaders.has(header)) return
    setPayCols(prev => {
      const next = new Set(prev)
      if (next.has(header)) {
        next.delete(header)
        setPaySelectionOrder(order => order.filter(h => h !== header))
      } else {
        next.add(header)
        setPaySelectionOrder(order => [...order, header])
      }
      return next
    })
  }

  const finalizeSubmit = async (payload: Record<string, string>) => {
    setColumnWarning(null)
    setPendingPayload(null)
    await onSubmit(payload)
  }

  const handleSubmit = useCallback(async () => {
    if (!singleMapped || payCols.size === 0 || submitting || disabled || submittedRef.current) return
    setSubmitting(true)
    try {
      const payload: Record<string, string> = {}
      for (const role of SINGLE_ROLES) {
        const letter = assignments[role.key]
        const header = letter ? headerByLetter.get(letter) : ''
        if (header) payload[role.slot] = header
      }
      payload.fte_payroll_cols_json = JSON.stringify([...payCols])
      const invalid = await auditInvalidColumns(payload)
      if (invalid > 0) {
        submittedRef.current = false
        setColumnWarning(invalid)
        setPendingPayload(payload)
        return
      }
      submittedRef.current = true
      await finalizeSubmit(payload)
    } finally {
      setSubmitting(false)
    }
  }, [
    singleMapped,
    payCols,
    assignments,
    disabled,
    headerByLetter,
    onSubmit,
    submitting,
    auditInvalidColumns,
  ])

  const handleContinueDespiteWarning = async () => {
    if (!pendingPayload || submitting || disabled) return
    setSubmitting(true)
    submittedRef.current = true
    try {
      await finalizeSubmit(pendingPayload)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return <p className="text-sm text-slate-500">Loading personnel preview…</p>
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
        </div>
      )}

      {phase === 'payroll' && (
        <p className="text-xs font-semibold text-slate-700">
          Click every column you want included in the payroll total (e.g. base salary only, or full
          gross), then click Continue.
        </p>
      )}

      {(phase === 'payroll' ? paySelectionOrder.length > 0 : history.length > 0) && (
        <div className="self-start">
          <button
            type="button"
            disabled={disabled || submitting}
            onClick={undoLast}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            <Undo2 size={14} />
            {phase === 'payroll' ? 'Undo last column' : 'Undo'}
          </button>
        </div>
      )}

      <div className="overflow-x-auto overflow-y-auto max-h-[320px] rounded-lg border border-slate-200 w-full">
        <table className="text-xs border-collapse min-w-full">
          <thead>
            <tr>
              {preview.columns.map(col => {
                const role = roleForLetter(col.letter)
                const header = col.header || col.letter
                const paySelected = phase === 'payroll' && payCols.has(header)
                const canMap = !disabled && phase === 'map' && currentRole && !singleMapped
                const canPay =
                  !disabled &&
                  phase === 'payroll' &&
                  header.trim() &&
                  !mappedSingleHeaders.has(header)
                let bg = role ? ROLE_COLORS[role] : '#E2E8F0'
                if (paySelected) bg = '#7C3AED'
                return (
                  <th
                    key={col.letter}
                    className="px-2 py-1 border border-slate-200 select-none min-w-[72px] max-w-[120px]"
                    style={{
                      background: bg,
                      color: role || paySelected ? '#fff' : '#475569',
                      cursor: canMap || canPay ? 'pointer' : 'default',
                    }}
                    onClick={() => {
                      if (canMap) assignColumn(col.letter)
                      else if (canPay) togglePayCol(header)
                    }}
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
                  const paySelected = phase === 'payroll' && header && payCols.has(header)
                  return (
                    <td
                      key={ci}
                      className="px-2 py-0.5 border border-slate-100 whitespace-nowrap max-w-[120px] truncate"
                      style={{
                        background: role
                          ? `${ROLE_COLORS[role]}18`
                          : paySelected
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

      {columnWarning !== null && columnWarning > 0 && (
        <div
          className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950"
          role="alert"
        >
          <p className="font-medium">
            {columnWarning.toLocaleString('en-US')} row{columnWarning === 1 ? '' : 's'} have invalid
            employment rate or annual working time values across uploaded personnel files.
          </p>
          {phase === 'payroll' ? (
            <>
              <p className="mt-1 text-amber-900">Do you want to continue anyway?</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={disabled || submitting}
                  onClick={() => void handleContinueDespiteWarning()}
                  className="rounded-md bg-amber-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-800 disabled:opacity-50"
                >
                  Continue anyway
                </button>
              </div>
            </>
          ) : (
            <p className="mt-1 text-amber-900">
              Review the mapped columns or continue to payroll column selection below.
            </p>
          )}
        </div>
      )}

      {phase === 'payroll' && (
        <button
          type="button"
          disabled={disabled || payCols.size === 0 || submitting}
          onClick={() => void handleSubmit()}
          className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          style={{ background: '#1E40AF' }}
        >
          {submitting ? 'Continuing…' : 'Continue'}
        </button>
      )}
    </div>
  )
}
