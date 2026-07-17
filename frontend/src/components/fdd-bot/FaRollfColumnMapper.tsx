/**
 * Interactive column mapper for Fixed Assets Rollforward.
 * Click column headers for opening / additions / disposals; multi-select depreciation columns.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Undo2 } from 'lucide-react'
import { getApiBaseUrl } from '../../lib/api'
import SourceDataAuditWarning, {
  type SourceDataAuditResult,
  hasAuditContent,
} from './SourceDataAuditWarning'

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
  periodsJson?: string
  groupColsJson?: string
  sheetName?: string
  headerRow?: number
  /** Fast Track keeps exactly one grouping choice in this mapper. */
  fastTrack?: boolean
  groupingDefault?: string
  disabled?: boolean
  onSubmit: (payload: Record<string, string>) => void | Promise<void>
}

export default function FaRollfColumnMapper({
  sessionId,
  previewFileId,
  periodsJson = '[]',
  groupColsJson = '[]',
  sheetName = '',
  headerRow = 0,
  fastTrack = false,
  groupingDefault = '',
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
  const [depSelectionOrder, setDepSelectionOrder] = useState<string[]>([])
  const [phase, setPhase] = useState<'map' | 'depreciation'>('map')
  const [submitting, setSubmitting] = useState(false)
  const [auditResult, setAuditResult] = useState<SourceDataAuditResult | null>(null)
  const [pendingPayload, setPendingPayload] = useState<Record<string, string> | null>(null)
  const [fastTrackGroup, setFastTrackGroup] = useState(groupingDefault)
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

  const groupCols = useMemo(() => {
    try {
      const parsed = JSON.parse(groupColsJson || '[]')
      if (!Array.isArray(parsed)) return []
      return parsed.map(c => String(c).trim()).filter(Boolean)
    } catch {
      return []
    }
  }, [groupColsJson])

  useEffect(() => {
    if (fastTrack && !fastTrackGroup && groupCols[0]) setFastTrackGroup(groupCols[0])
  }, [fastTrack, fastTrackGroup, groupCols])

  const auditMappedColumns = useCallback(
    async (payload: Record<string, string>): Promise<SourceDataAuditResult | null> => {
      if (!previewFileId.trim()) return null
      let depCols: string[] = []
      try {
        depCols = JSON.parse(payload.fa_depreciation_cols_json || '[]') as string[]
      } catch {
        depCols = []
      }
      const base = getApiBaseUrl()
      const resp = await fetch(`${base}/api/v1/fdd/fa_rollf/audit-columns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          periods: auditPeriods,
          columns: {
            opening: payload.fa_opening_col,
            additions: payload.fa_additions_col,
            disposals: payload.fa_disposals_col,
            depreciation_cols: depCols,
          },
          group_cols: fastTrack ? [fastTrackGroup].filter(Boolean) : groupCols,
          header_row: headerRow,
        }),
      })
      if (!resp.ok) return null
      return (await resp.json()) as SourceDataAuditResult
    },
    [auditPeriods, fastTrack, fastTrackGroup, groupCols, headerRow, previewFileId, sessionId],
  )

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
    if (disabled) return
    submittedRef.current = false
    if (phase === 'depreciation' && depSelectionOrder.length > 0) {
      const last = depSelectionOrder[depSelectionOrder.length - 1]
      setDepSelectionOrder(prev => prev.slice(0, -1))
      setDepCols(prev => {
        const next = new Set(prev)
        next.delete(last)
        return next
      })
      return
    }
    if (!history.length) return
    setPhase('map')
    setDepCols(new Set())
    setDepSelectionOrder([])
    const prev = history[history.length - 1]
    setHistory(h => h.slice(0, -1))
    setAssignments(prev)
    const nextStep = SINGLE_ROLES.findIndex(r => !prev[r.key])
    setStepIndex(nextStep >= 0 ? nextStep : 0)
  }

  const toggleDepCol = (header: string) => {
    if (disabled || phase !== 'depreciation') return
    if (mappedSingleHeaders.has(header)) return
    setDepCols(prev => {
      const next = new Set(prev)
      if (next.has(header)) {
        next.delete(header)
        setDepSelectionOrder(order => order.filter(h => h !== header))
      } else {
        next.add(header)
        setDepSelectionOrder(order => [...order, header])
      }
      return next
    })
  }

  const finalizeSubmit = async (payload: Record<string, string>) => {
    setAuditResult(null)
    setPendingPayload(null)
    await onSubmit(payload)
  }

  const handleSubmit = useCallback(async () => {
    if (
      !singleMapped ||
      depCols.size === 0 ||
      (fastTrack && !fastTrackGroup.trim()) ||
      submitting ||
      disabled ||
      submittedRef.current
    ) return
    setSubmitting(true)
    try {
      const payload: Record<string, string> = {}
      for (const role of SINGLE_ROLES) {
        const letter = assignments[role.key]
        const header = letter ? headerByLetter.get(letter) : ''
        if (header) payload[role.slot] = header
      }
      payload.fa_depreciation_cols_json = JSON.stringify([...depCols])
      if (fastTrack) payload.fa_group_col_1 = fastTrackGroup.trim()
      const result = await auditMappedColumns(payload)
      const invalid = Number(result?.total_invalid || 0)
      if (result && hasAuditContent(result)) {
        setAuditResult(result)
      }
      if (invalid > 0) {
        submittedRef.current = false
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
    depCols,
    assignments,
    disabled,
    headerByLetter,
    onSubmit,
    submitting,
    auditMappedColumns,
    fastTrack,
    fastTrackGroup,
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
        </div>
      )}

      {phase === 'depreciation' && (
        <>
          <p className="text-xs font-semibold text-slate-700">
            Click every depreciation column you want summed (e.g. annual AfA + disposal AfA), then
            click Continue.
          </p>
          {fastTrack && (
            <label className="flex max-w-sm flex-col gap-1 text-xs font-semibold text-slate-700">
              Grouping column
              <select
                value={fastTrackGroup}
                disabled={disabled || submitting}
                onChange={event => setFastTrackGroup(event.target.value)}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-normal text-slate-800"
              >
                <option value="">Select one column…</option>
                {preview.columns
                  .map(column => column.header || column.letter)
                  .filter(header => !mappedSingleHeaders.has(header) && !depCols.has(header))
                  .map(header => <option key={header} value={header}>{header}</option>)}
              </select>
            </label>
          )}
        </>
      )}

      {(phase === 'depreciation' ? depSelectionOrder.length > 0 : history.length > 0) && (
        <div className="self-start">
          <button
            type="button"
            disabled={disabled || submitting}
            onClick={undoLast}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            <Undo2 size={14} />
            {phase === 'depreciation' ? 'Undo last column' : 'Undo'}
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
                const depSelected = phase === 'depreciation' && depCols.has(header)
                const canMap = !disabled && phase === 'map' && currentRole && !singleMapped
                const canDep =
                  !disabled &&
                  phase === 'depreciation' &&
                  header.trim() &&
                  !mappedSingleHeaders.has(header)
                let bg = role ? ROLE_COLORS[role] : '#E2E8F0'
                if (depSelected) bg = '#7C3AED'
                return (
                  <th
                    key={col.letter}
                    className="px-2 py-1 border border-slate-200 select-none min-w-[72px] max-w-[120px]"
                    style={{
                      background: bg,
                      color: role || depSelected ? '#fff' : '#475569',
                      cursor: canMap || canDep ? 'pointer' : 'default',
                    }}
                    onClick={() => {
                      if (canMap) assignColumn(col.letter)
                      else if (canDep) toggleDepCol(header)
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

      {auditResult && hasAuditContent(auditResult) && (
        <SourceDataAuditWarning
          result={auditResult}
          summary={
            Number(auditResult.total_invalid || 0) > 0
              ? `${Number(auditResult.total_invalid).toLocaleString('en-US')} row${
                  Number(auditResult.total_invalid) === 1 ? '' : 's'
                } have invalid fixed-asset column values across uploaded register files.`
              : 'Source data review — see details below.'
          }
          blockingCount={Number(auditResult.total_invalid || 0)}
          onContinue={pendingPayload ? handleContinueDespiteWarning : undefined}
          submitting={submitting}
          disabled={disabled}
        />
      )}

      {phase === 'depreciation' && !pendingPayload && (
        <button
          type="button"
          disabled={
            disabled ||
            depCols.size === 0 ||
            (fastTrack && !fastTrackGroup.trim()) ||
            submitting
          }
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
