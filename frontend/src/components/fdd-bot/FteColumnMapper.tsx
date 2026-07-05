/**
 * Interactive column mapper for FTE calculation and payroll mapping.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchPersonaltablePreview,
  headerNameForColumn,
  idxFromLetter,
  type PreviewData,
} from './fteMapperUtils'

export type FteMapperMode = 'fte' | 'payroll'

export interface FteMappingPayload {
  tenure_mode?: 'months_col' | 'entry_exit_dates'
  payroll_mode?: 'sum_components' | 'total_col' | 'monthly_col'
  employment_pct_col?: string
  months_col?: string
  entry_col?: string
  exit_col?: string
  year_col?: string
  social_col?: string
  total_col?: string
  monthly_col?: string
  component_cols?: string[]
  /** header names resolved from letters */
  column_names?: Record<string, string>
}

interface Props {
  sessionId: string
  previewFileId: string
  mode: FteMapperMode
  uploadMode: string
  disabled?: boolean
  initialTenureMode?: string
  initialPayrollMode?: string
  onSubmit: (mapping: FteMappingPayload) => void | Promise<void>
}

type FteRole =
  | 'employment_pct'
  | 'months'
  | 'entry'
  | 'exit'
  | 'year'
  | 'social'
  | 'total'
  | 'monthly'
  | 'component'

const ROLE_LABEL: Record<FteRole, string> = {
  employment_pct: 'Employment %',
  months: 'Months employed in FY',
  entry: 'Entry date',
  exit: 'Exit date (optional)',
  year: 'Fiscal year column',
  social: 'Social security (optional)',
  total: 'Total personnel cost',
  monthly: 'Monthly personnel cost',
  component: 'Cost component (add)',
}

export default function FteColumnMapper({
  sessionId,
  previewFileId,
  mode,
  uploadMode,
  disabled,
  initialTenureMode = 'months_col',
  initialPayrollMode = 'sum_components',
  onSubmit,
}: Props) {
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tenureMode, setTenureMode] = useState(initialTenureMode)
  const [payrollMode, setPayrollMode] = useState(initialPayrollMode)
  const [assignments, setAssignments] = useState<Record<string, FteRole>>({})
  const [componentCols, setComponentCols] = useState<string[]>([])
  const [activeRole, setActiveRole] = useState<FteRole | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const steps = useMemo((): FteRole[] => {
    if (mode === 'payroll') {
      if (payrollMode === 'sum_components') {
        return ['component']
      }
      if (payrollMode === 'monthly_col') {
        return ['monthly', 'social']
      }
      return ['total', 'social']
    }
    const s: FteRole[] = ['employment_pct']
    if (tenureMode === 'months_col') {
      s.push('months')
    } else {
      s.push('entry', 'exit')
    }
    if (uploadMode === 'single_combined_file') {
      s.push('year')
    }
    return s
  }, [mode, tenureMode, payrollMode, uploadMode])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
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

  useEffect(() => {
    if (steps.length) setActiveRole(steps[0])
  }, [steps])

  const selectColumn = (letter: string) => {
    if (!activeRole || disabled || !preview) return
    if (activeRole === 'component') {
      setComponentCols(prev => (prev.includes(letter) ? prev.filter(x => x !== letter) : [...prev, letter]))
      return
    }
    const next = { ...assignments }
    for (const [k, v] of Object.entries(next)) {
      if (v === activeRole) delete next[k]
    }
    next[letter] = activeRole
    setAssignments(next)
    const idx = steps.indexOf(activeRole)
    if (idx >= 0 && idx < steps.length - 1) {
      setActiveRole(steps[idx + 1])
    }
  }

  const buildPayload = useCallback((): FteMappingPayload => {
    if (!preview) return {}
    const colNames: Record<string, string> = {}
    const letterFor = (role: FteRole) =>
      Object.entries(assignments).find(([, r]) => r === role)?.[0]

    const payload: FteMappingPayload = {
      tenure_mode: tenureMode as FteMappingPayload['tenure_mode'],
      payroll_mode: payrollMode as FteMappingPayload['payroll_mode'],
      column_names: colNames,
    }

    if (mode === 'fte') {
      const ep = letterFor('employment_pct')
      if (ep) {
        payload.employment_pct_col = headerNameForColumn(preview, ep)
        colNames.employment_pct = ep
      }
      if (tenureMode === 'months_col') {
        const m = letterFor('months')
        if (m) {
          payload.months_col = headerNameForColumn(preview, m)
          colNames.months = m
        }
      } else {
        const en = letterFor('entry')
        const ex = letterFor('exit')
        if (en) {
          payload.entry_col = headerNameForColumn(preview, en)
          colNames.entry = en
        }
        if (ex) {
          payload.exit_col = headerNameForColumn(preview, ex)
          colNames.exit = ex
        }
      }
      const yr = letterFor('year')
      if (yr) {
        payload.year_col = headerNameForColumn(preview, yr)
        colNames.year = yr
      }
    } else {
      if (payrollMode === 'sum_components') {
        payload.component_cols = componentCols.map(l => headerNameForColumn(preview, l))
        colNames.components = componentCols.join(',')
      } else if (payrollMode === 'monthly_col') {
        const mc = letterFor('monthly')
        if (mc) {
          payload.monthly_col = headerNameForColumn(preview, mc)
          colNames.monthly = mc
        }
      } else {
        const tc = letterFor('total')
        if (tc) {
          payload.total_col = headerNameForColumn(preview, tc)
          colNames.total = tc
        }
      }
      const sc = letterFor('social')
      if (sc) {
        payload.social_col = headerNameForColumn(preview, sc)
        colNames.social = sc
      }
    }
    return payload
  }, [assignments, componentCols, mode, payrollMode, preview, tenureMode])

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      await onSubmit(buildPayload())
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <p className="text-sm text-slate-500">Loading preview…</p>
  if (error) return <p className="text-sm text-red-600">{error}</p>
  if (!preview) return null

  return (
    <div className="flex flex-col gap-3">
      {mode === 'fte' && (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-slate-600">FTE tenure basis</span>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              checked={tenureMode === 'months_col'}
              onChange={() => setTenureMode('months_col')}
              disabled={disabled}
            />
            Column with months employed in the year
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="radio"
              checked={tenureMode === 'entry_exit_dates'}
              onChange={() => setTenureMode('entry_exit_dates')}
              disabled={disabled}
            />
            Entry / exit date columns
          </label>
        </div>
      )}
      {mode === 'payroll' && (
        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium text-slate-600">Payroll cost basis</span>
          {(
            [
              ['sum_components', 'Sum of multiple cost columns'],
              ['total_col', 'One total cost column per employee'],
              ['monthly_col', 'Monthly cost × active months'],
            ] as const
          ).map(([val, label]) => (
            <label key={val} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                checked={payrollMode === val}
                onChange={() => {
                  setPayrollMode(val)
                  setAssignments({})
                  setComponentCols([])
                }}
                disabled={disabled}
              />
              {label}
            </label>
          ))}
        </div>
      )}

      {activeRole && (
        <p className="text-xs text-slate-600">
          Click a column for: <strong>{ROLE_LABEL[activeRole]}</strong>
        </p>
      )}

      <div className="overflow-x-auto border rounded-lg max-h-64">
        <table className="text-xs border-collapse min-w-full">
          <thead>
            <tr>
              {preview.columns.map(col => {
                const assigned = assignments[col.letter]
                const isComp = componentCols.includes(col.letter)
                const hr = preview.header_row_index
                const header = preview.rows[hr]?.[idxFromLetter(col.letter)] ?? col.letter
                return (
                  <th
                    key={col.letter}
                    className="border px-1 py-1 cursor-pointer whitespace-nowrap"
                    style={{
                      background: assigned || isComp ? '#DBEAFE' : '#F8FAFC',
                    }}
                    onClick={() => selectColumn(col.letter)}
                  >
                    <div className="font-semibold">{col.letter}</div>
                    <div className="font-normal truncate max-w-[100px]">{header}</div>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {preview.rows.slice(preview.header_row_index + 1, preview.header_row_index + 8).map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci} className="border px-1 py-0.5 truncate max-w-[100px]">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button
        type="button"
        disabled={disabled || submitting}
        onClick={handleSubmit}
        className="self-end px-4 py-2 text-sm font-semibold rounded-lg text-white"
        style={{ background: disabled ? '#94A3B8' : '#1E3A5F' }}
      >
        {submitting ? 'Saving…' : 'Continue'}
      </button>
    </div>
  )
}
