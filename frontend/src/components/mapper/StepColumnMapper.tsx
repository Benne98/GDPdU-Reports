/**
 * Generic step-by-step column-mapping wizard.
 *
 * Mirrors SusaColumnMapper UX: clickable table headers, pending→green-check
 * confirm button, per-role colored assignments, expand-to-fullscreen portal.
 * Step list is derived on every render so gating/conditional steps work
 * automatically — answers are never discarded on Undo/Redo navigation.
 */

import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Maximize2, Minimize2, Redo2, Undo2 } from 'lucide-react'
import type { Answers, MapperStep, NormalizedPreview } from './stepMapperTypes'

/** Deterministic color palette keyed by role assignment order. */
const PALETTE: string[] = [
  '#3B82F6', // blue
  '#10B981', // emerald
  '#F59E0B', // amber
  '#EF4444', // red
  '#8B5CF6', // violet
  '#06B6D4', // cyan
  '#EC4899', // pink
  '#84CC16', // lime
  '#F97316', // orange
  '#6366F1', // indigo
]

interface Props<R> {
  preview: NormalizedPreview
  buildSteps: (a: Answers) => MapperStep[]
  toResult: (a: Answers) => R
  initial?: Answers
  disabled?: boolean
  completeLabel?: string
  onComplete: (r: R) => void | Promise<void>
}

export default function StepColumnMapper<R,>({
  preview,
  buildSteps,
  toResult,
  initial,
  disabled,
  completeLabel,
  onComplete,
}: Props<R>) {
  const [answers, setAnswers] = useState<Answers>(initial ?? {})
  const [cursor, setCursor] = useState(0)
  const [pendingColumn, setPendingColumn] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  // Temp states for steps that need a "Continue" confirmation
  const [multiItems, setMultiItems] = useState<Array<{ id: string; label?: string }>>([])
  const [checklistValues, setChecklistValues] = useState<string[]>([])
  const [customRows, setCustomRows] = useState<
    Array<{ source_col: string; output_label: string }>
  >([])
  const [customNewSrc, setCustomNewSrc] = useState('')
  const [customNewLabel, setCustomNewLabel] = useState('')

  // ── Derived state ────────────────────────────────────────────────────────────

  /** Steps are derived every render so gating steps work automatically. */
  const steps = buildSteps(answers)
  const current: MapperStep | undefined =
    steps.length > 0 ? steps[Math.min(cursor, steps.length - 1)] : undefined

  // Narrowed shortcuts used in handlers and JSX
  const colStep =
    current !== undefined && current.kind === 'column' ? current : null
  const multiStep =
    current !== undefined && current.kind === 'multiColumn' ? current : null
  const choiceStep =
    current !== undefined && current.kind === 'choice' ? current : null
  const checkStep =
    current !== undefined && current.kind === 'checklist' ? current : null
  const customStep =
    current !== undefined && current.kind === 'custom' ? current : null

  // Build role→color and colId→role maps from confirmed answers
  const roleColorMap: Record<string, string> = {}
  const colToRole: Record<string, string> = {}
  let colorIdx = 0
  for (const s of steps) {
    if (s.kind === 'column') {
      const ans = answers[s.role]
      if (ans?.t === 'column' && ans.id !== null && !(s.role in roleColorMap)) {
        roleColorMap[s.role] = PALETTE[colorIdx % PALETTE.length]
        colToRole[ans.id] = s.role
        colorIdx++
      }
    } else if (s.kind === 'multiColumn') {
      const ans = answers[s.role]
      if (ans?.t === 'multiColumn' && ans.items.length > 0 && !(s.role in roleColorMap)) {
        roleColorMap[s.role] = PALETTE[colorIdx % PALETTE.length]
        for (const item of ans.items) colToRole[item.id] = s.role
        colorIdx++
      }
    }
  }

  // Pending multi-column selection (before Continue is clicked)
  const pendingMultiSet = new Set(multiItems.map(i => i.id))

  // Resolve choice options (function or array)
  const choiceOptions = choiceStep
    ? typeof choiceStep.options === 'function'
      ? choiceStep.options(answers, preview)
      : choiceStep.options
    : []

  // Resolve checklist options (function or array) — same seam as choiceOptions.
  const checkOptions = checkStep
    ? typeof checkStep.options === 'function'
      ? checkStep.options(answers, preview)
      : checkStep.options
    : []

  // Complete button gate: every required column step must have a non-null answer
  const allRequiredFilled = steps
    .filter(
      (s): s is Extract<MapperStep, { kind: 'column' }> =>
        s.kind === 'column' && (s.required ?? false),
    )
    .every(s => {
      const ans = answers[s.role]
      return ans?.t === 'column' && ans.id !== null
    })

  const isLastStep = steps.length > 0 && cursor >= steps.length - 1

  // ── Sync temp states when cursor moves ──────────────────────────────────────

  useEffect(() => {
    setPendingColumn(null)
    // Re-sync temp states from confirmed answers so that Undo/Redo and
    // pre-populated `initial` answers are reflected in the editable controls.
    // `current` and `answers` are intentionally excluded from deps — we only
    // re-sync on cursor change, not on every keystroke.
    if (current === undefined) return
    if (current.kind === 'multiColumn') {
      const ans = answers[current.role]
      setMultiItems(ans?.t === 'multiColumn' ? [...ans.items] : [])
    } else if (current.kind === 'checklist') {
      const ans = answers[current.role]
      setChecklistValues(ans?.t === 'checklist' ? [...ans.values] : [])
    } else if (current.kind === 'custom') {
      const ans = answers[current.role]
      setCustomRows(ans?.t === 'custom' ? [...ans.rows] : [])
      setCustomNewSrc('')
      setCustomNewLabel('')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cursor])

  // ── Advance helper ───────────────────────────────────────────────────────────

  const advance = () => {
    if (steps.length === 0) return
    setCursor(c => Math.min(c + 1, steps.length - 1))
  }

  // ── Column step handlers ─────────────────────────────────────────────────────

  const handleColumnClick = (colId: string) => {
    if (!colStep || disabled) return
    const existingRole = colToRole[colId]
    if (existingRole !== undefined && existingRole !== colStep.role) return
    setPendingColumn(prev => (prev === colId ? null : colId))
  }

  const confirmPending = () => {
    if (!colStep || !pendingColumn) return
    setAnswers(prev => ({
      ...prev,
      [colStep.role]: { t: 'column', id: pendingColumn },
    }))
    setPendingColumn(null)
    advance()
  }

  const skipColumn = () => {
    if (!colStep || !colStep.skippable) return
    setAnswers(prev => ({
      ...prev,
      [colStep.role]: { t: 'column', id: null },
    }))
    setPendingColumn(null)
    advance()
  }

  // ── MultiColumn step handlers ────────────────────────────────────────────────

  const toggleMultiColumn = (colId: string) => {
    if (!multiStep || disabled) return
    const max = multiStep.max
    setMultiItems(prev => {
      const exists = prev.some(i => i.id === colId)
      if (exists) return prev.filter(i => i.id !== colId)
      if (prev.length >= max) return prev
      return [...prev, { id: colId }]
    })
  }

  const updateMultiLabel = (colId: string, label: string) => {
    setMultiItems(prev => prev.map(i => (i.id === colId ? { ...i, label } : i)))
  }

  const confirmMulti = () => {
    if (!multiStep) return
    setAnswers(prev => ({
      ...prev,
      [multiStep.role]: { t: 'multiColumn', items: multiItems },
    }))
    advance()
  }

  const skipMulti = () => {
    if (!multiStep) return
    setAnswers(prev => ({
      ...prev,
      [multiStep.role]: { t: 'multiColumn', items: [] },
    }))
    setMultiItems([])
    advance()
  }

  // ── Choice step handlers ─────────────────────────────────────────────────────

  const pickChoice = (value: string) => {
    if (!choiceStep || disabled) return
    setAnswers(prev => ({
      ...prev,
      [choiceStep.role]: { t: 'choice', value },
    }))
    advance()
  }

  // ── Checklist step handlers ──────────────────────────────────────────────────

  const toggleChecklist = (value: string) => {
    setChecklistValues(prev =>
      prev.includes(value) ? prev.filter(v => v !== value) : [...prev, value],
    )
  }

  const confirmChecklist = () => {
    if (!checkStep) return
    setAnswers(prev => ({
      ...prev,
      [checkStep.role]: { t: 'checklist', values: checklistValues },
    }))
    advance()
  }

  const skipChecklist = () => {
    if (!checkStep) return
    setAnswers(prev => ({
      ...prev,
      [checkStep.role]: { t: 'checklist', values: [] },
    }))
    setChecklistValues([])
    advance()
  }

  // ── Custom step handlers ─────────────────────────────────────────────────────

  const addCustomRow = () => {
    if (!customNewSrc.trim()) return
    setCustomRows(prev => [
      ...prev,
      { source_col: customNewSrc, output_label: customNewLabel },
    ])
    setCustomNewSrc('')
    setCustomNewLabel('')
  }

  const removeCustomRow = (idx: number) => {
    setCustomRows(prev => prev.filter((_, i) => i !== idx))
  }

  const confirmCustom = () => {
    if (!customStep) return
    setAnswers(prev => ({
      ...prev,
      [customStep.role]: { t: 'custom', rows: customRows },
    }))
    advance()
  }

  const skipCustom = () => {
    if (!customStep) return
    setAnswers(prev => ({
      ...prev,
      [customStep.role]: { t: 'custom', rows: [] },
    }))
    setCustomRows([])
    setCustomNewSrc('')
    setCustomNewLabel('')
    advance()
  }

  // ── Submit ───────────────────────────────────────────────────────────────────

  /** Flush the current step's temp state into answers before submitting.
   *  Column and choice steps commit on interaction, so they are a no-op here. */
  const flushCurrentStep = (a: Answers): Answers => {
    if (multiStep) return { ...a, [multiStep.role]: { t: 'multiColumn', items: multiItems } }
    if (checkStep) return { ...a, [checkStep.role]: { t: 'checklist', values: checklistValues } }
    if (customStep) return { ...a, [customStep.role]: { t: 'custom', rows: customRows } }
    return a
  }

  const handleComplete = async () => {
    if (!allRequiredFilled || submitting || disabled) return
    setSubmitting(true)
    try {
      await onComplete(toResult(flushCurrentStep(answers)))
    } finally {
      setSubmitting(false)
    }
  }

  // ── Instruction line ─────────────────────────────────────────────────────────

  const instruction = current
    ? current.kind === 'column'
      ? `Select the column for "${current.label}", then confirm with the green checkmark in the column header.`
      : current.kind === 'multiColumn'
        ? `Select up to ${current.max} column(s) for "${current.label}", then click Continue.`
        : current.kind === 'choice'
          ? `Choose an option for "${current.label}".`
          : current.kind === 'checklist'
            ? `Select all that apply for "${current.label}", then click Continue.`
            : `Configure output columns for "${current.label}", then click Continue.`
    : 'All steps complete — click Confirm mapping.'

  // ── Table ────────────────────────────────────────────────────────────────────

  const tableBlock = (
    <div
      className={`overflow-auto rounded-lg border border-slate-200 ${
        expanded ? 'flex-1 min-h-0 w-full' : 'max-h-[320px] w-full'
      }`}
    >
      <table className="text-xs border-collapse min-w-full">
        <thead>
          <tr>
            {preview.columns.map(col => {
              const role = colToRole[col.id]
              const isPending = colStep !== null && pendingColumn === col.id
              const isMultiPend = multiStep !== null && pendingMultiSet.has(col.id)

              const bg = role
                ? (roleColorMap[role] ?? '#94A3B8')
                : isPending || isMultiPend
                  ? '#94A3B8'
                  : '#E2E8F0'
              const textColor =
                role || isPending || isMultiPend ? '#fff' : '#475569'

              const canSelect =
                colStep !== null &&
                !disabled &&
                (role === undefined || role === colStep.role)
              const canToggle =
                multiStep !== null &&
                !disabled &&
                (role === undefined || role === multiStep.role)

              const roleLabel = role
                ? (steps.find(s => s.role === role)?.label ?? role.replace(/_/g, ' '))
                : undefined

              return (
                <th
                  key={col.id}
                  className="relative px-2 py-1 border border-slate-200 select-none min-w-[52px]"
                  style={{
                    background: bg,
                    color: textColor,
                    cursor: canSelect || canToggle ? 'pointer' : 'default',
                  }}
                  onClick={() => {
                    if (canSelect) handleColumnClick(col.id)
                    else if (canToggle) toggleMultiColumn(col.id)
                  }}
                  title={col.sampleValues.join(', ')}
                >
                  <span className="font-semibold">{col.header}</span>
                  {col.id !== col.header && (
                    <span className="ml-1 font-mono text-[9px] opacity-75">
                      {col.id}
                    </span>
                  )}
                  {roleLabel && (
                    <div className="text-[9px] font-normal opacity-90 leading-tight">
                      {roleLabel}
                    </div>
                  )}
                  {/* Pending column — green check confirm button */}
                  {isPending && colStep && (
                    <button
                      type="button"
                      className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-white shadow-md hover:bg-emerald-600"
                      title={`Confirm: ${colStep.label}`}
                      onClick={e => {
                        e.stopPropagation()
                        confirmPending()
                      }}
                    >
                      <Check size={12} strokeWidth={3} />
                    </button>
                  )}
                  {/* Confirmed column assignment */}
                  {role && !isPending && (
                    <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-white/90 text-emerald-600">
                      <Check size={10} strokeWidth={3} />
                    </span>
                  )}
                  {/* Pending multi-column selection (pre-Continue) */}
                  {isMultiPend && !role && (
                    <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-emerald-500/90 text-white">
                      <Check size={10} strokeWidth={3} />
                    </span>
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {(preview.rows ?? []).slice(0, 3).map((row, ri) => (
            <tr key={ri}>
              {preview.columns.map((col, ci) => {
                const role = colToRole[col.id]
                return (
                  <td
                    key={ci}
                    className="px-2 py-0.5 border border-slate-100 whitespace-nowrap max-w-[140px] truncate"
                    style={{
                      background: role ? `${roleColorMap[role]}18` : undefined,
                    }}
                  >
                    {row[ci] ?? ''}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )

  // ── Step-specific controls ───────────────────────────────────────────────────

  const stepControls = ((): React.ReactNode => {
    if (colStep) {
      return colStep.skippable ? (
        <button
          type="button"
          className="self-start text-xs text-slate-500 underline"
          onClick={skipColumn}
        >
          Skip — no {colStep.label.toLowerCase()} column
        </button>
      ) : null
    }

    if (multiStep) {
      return (
        <div className="flex flex-col gap-2">
          {multiStep.withLabels && multiItems.length > 0 && (
            <div className="flex flex-col gap-1">
              {multiItems.map(item => {
                const col = preview.columns.find(c => c.id === item.id)
                return (
                  <div key={item.id} className="flex items-center gap-2">
                    <span className="min-w-[60px] text-xs font-medium text-slate-600">
                      {col?.header ?? item.id}
                    </span>
                    <input
                      type="text"
                      placeholder="Output label"
                      value={item.label ?? ''}
                      onChange={e => updateMultiLabel(item.id, e.target.value)}
                      className="flex-1 rounded border border-slate-200 px-2 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
                    />
                  </div>
                )
              })}
            </div>
          )}
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-500">
              {multiItems.length} / {multiStep.max} selected
            </span>
            {!isLastStep && (
              <>
                <button
                  type="button"
                  disabled={disabled}
                  className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                  onClick={confirmMulti}
                >
                  Continue
                </button>
                {multiStep.skippable && (
                  <button
                    type="button"
                    className="text-xs text-slate-500 underline"
                    onClick={skipMulti}
                  >
                    Skip
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )
    }

    if (choiceStep) {
      return (
        <div className="flex flex-wrap gap-2">
          {choiceOptions.map(opt => {
            const choiceAns = answers[choiceStep.role]
            const selected =
              choiceAns !== undefined &&
              choiceAns.t === 'choice' &&
              choiceAns.value === opt.value
            return (
              <button
                key={opt.value}
                type="button"
                disabled={disabled}
                className={`rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                  selected
                    ? 'border-blue-500 bg-blue-50 font-medium text-blue-700'
                    : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50'
                }`}
                onClick={() => pickChoice(opt.value)}
              >
                <div className="font-medium">{opt.label}</div>
                {opt.hint && (
                  <div className="mt-0.5 text-xs text-slate-500">{opt.hint}</div>
                )}
              </button>
            )
          })}
        </div>
      )
    }

    if (checkStep) {
      return (
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-1">
            {checkOptions.map(opt => (
              <label
                key={opt.value}
                className="flex cursor-pointer items-center gap-2 text-sm text-slate-700"
              >
                <input
                  type="checkbox"
                  checked={checklistValues.includes(opt.value)}
                  onChange={() => toggleChecklist(opt.value)}
                  className="h-4 w-4 rounded border-slate-300 text-blue-600"
                />
                <span>{opt.label}</span>
                {opt.hint && (
                  <span className="text-xs text-slate-500">— {opt.hint}</span>
                )}
              </label>
            ))}
          </div>
          {!isLastStep && (
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={disabled}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                onClick={confirmChecklist}
              >
                Continue
              </button>
              {checkStep.skippable && (
                <button
                  type="button"
                  className="text-xs text-slate-500 underline"
                  onClick={skipChecklist}
                >
                  Skip
                </button>
              )}
            </div>
          )}
        </div>
      )
    }

    if (customStep) {
      return (
        <div className="flex flex-col gap-2">
          {customRows.length > 0 && (
            <div className="flex flex-col gap-1">
              {customRows.map((row, idx) => (
                <div key={idx} className="flex items-center gap-2 text-xs">
                  <span className="font-medium text-slate-700">{row.source_col}</span>
                  <span className="text-slate-400">→</span>
                  <span className="text-slate-600">{row.output_label}</span>
                  <button
                    type="button"
                    className="ml-auto text-red-500 hover:text-red-700"
                    onClick={() => removeCustomRow(idx)}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <select
              value={customNewSrc}
              onChange={e => setCustomNewSrc(e.target.value)}
              className="rounded border border-slate-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            >
              <option value="">— select column —</option>
              {preview.columns.map(c => (
                <option key={c.id} value={c.id}>
                  {c.header}
                </option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Output label"
              value={customNewLabel}
              onChange={e => setCustomNewLabel(e.target.value)}
              className="flex-1 rounded border border-slate-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
            <button
              type="button"
              disabled={!customNewSrc.trim()}
              className="rounded-md bg-slate-700 px-2 py-1 text-xs text-white disabled:opacity-40"
              onClick={addCustomRow}
            >
              Add
            </button>
          </div>
          {!isLastStep && (
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={disabled}
                className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                onClick={confirmCustom}
              >
                Continue
              </button>
              {customStep.skippable && (
                <button
                  type="button"
                  className="text-xs text-slate-500 underline"
                  onClick={skipCustom}
                >
                  Skip
                </button>
              )}
            </div>
          )}
        </div>
      )
    }

    return null
  })()

  // ── Expand toggle (mirrored from SuSa) ──────────────────────────────────────

  const expandToggle = (
    <button
      type="button"
      className="absolute left-2 top-2 z-10 rounded-md border border-slate-200 bg-white p-1.5 text-slate-600 shadow-sm hover:bg-slate-50"
      title={expanded ? 'Exit expanded view' : 'Expand table (full width)'}
      onClick={() => setExpanded(v => !v)}
    >
      {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
    </button>
  )

  // ── Panel ────────────────────────────────────────────────────────────────────

  const panelContent = (
    <div className={`relative flex flex-col gap-3 ${expanded ? 'h-full' : ''}`}>
      {expandToggle}

      {/* Progress + Undo / Redo */}
      <div className="flex items-center gap-1 pl-9 pt-0.5">
        <span className="text-xs font-medium text-slate-500">
          Step {steps.length > 0 ? Math.min(cursor + 1, steps.length) : 0} /{' '}
          {steps.length}
        </span>
        <button
          type="button"
          disabled={cursor === 0}
          className="rounded p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30"
          title="Undo"
          onClick={() => setCursor(c => Math.max(0, c - 1))}
        >
          <Undo2 size={14} />
        </button>
        <button
          type="button"
          disabled={steps.length === 0 || cursor >= steps.length - 1}
          className="rounded p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30"
          title="Redo"
          onClick={() => setCursor(c => Math.min(steps.length - 1, c + 1))}
        >
          <Redo2 size={14} />
        </button>
      </div>

      {/* Instruction */}
      <p className={`text-sm font-medium text-slate-700 ${expanded ? 'pl-10' : 'pl-9'}`}>
        {instruction}
      </p>

      {/* Optional per-step explanation (generic, system-agnostic guidance) */}
      {current && (current.kind === 'column' || current.kind === 'multiColumn') && current.hint && (
        <p className={`-mt-1 text-xs text-slate-500 ${expanded ? 'pl-10' : 'pl-9'}`}>
          {current.hint}
        </p>
      )}

      {/* Step-specific controls */}
      {stepControls}

      {/* Terminal confirm button — only on the last step, replaces per-step Continue */}
      {isLastStep && (
        <button
          type="button"
          disabled={!allRequiredFilled || submitting || disabled}
          className="mt-1 self-start rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
          onClick={() => {
            void handleComplete()
          }}
        >
          {submitting ? 'Submitting…' : (completeLabel ?? 'Confirm mapping')}
        </button>
      )}

      {/* Always-visible column table */}
      {tableBlock}
    </div>
  )

  if (expanded) {
    return createPortal(
      <div
        className="fixed z-[9999] flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-2xl"
        style={{ top: 12, left: 12, right: 12, bottom: 12, width: 'auto', maxWidth: 'none' }}
      >
        {panelContent}
      </div>,
      document.body,
    )
  }

  return panelContent
}
