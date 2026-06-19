/**
 * Interactive column-mapping wizard for SuSa trial balances.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Maximize2, Minimize2 } from 'lucide-react'
import { getApiBaseUrl } from '../../lib/api'
import {
  buildMarkSteps,
  blockRolesForSignMode,
  roleColor,
  type ColumnRole,
  type MarkStep,
} from './susaMapperSteps'

interface PreviewColumn {
  letter: string
  sample_values: string[]
}

interface PreviewData {
  header_row_index: number
  columns: PreviewColumn[]
  rows: string[][]
}

export interface SusaColumnMappingPayload {
  default: {
    header_row: number
    columns: {
      account: string
      account_description?: string
      opening_balance?: string
      eb_sh?: string
      eb_sh_alt?: string
      eb_s?: string
      eb_h?: string
    }
    period_blocks?: Array<{
      month?: number
      year?: number
      amount?: string
      sh?: string
      sh_s?: string
      sh_h?: string
      soll?: string
      haben?: string
    }>
    repeating?: {
      enabled: boolean
      from_col: string
      block_size: number
      pattern: string
    }
  }
  entities: Record<string, SusaColumnMappingPayload['default']>
}

interface Props {
  sessionId: string
  previewFileId: string
  layoutFormat: string
  signMode: string
  valueType: string
  entityNames: string[]
  disabled?: boolean
  onSubmit: (mapping: SusaColumnMappingPayload) => void | Promise<void>
}

type WizardPhase =
  | 'mark'
  | 'confirm_pattern'
  | 'confirm_assignment'
  | 'entity_same'
  | 'done'

const MONTH_KEYS: [number, string[]][] = [
  [1, ['jan', 'januar', 'january', 'janury']],
  [2, ['feb', 'februar', 'february']],
  [3, ['mar', 'maerz', 'mrz', 'march', 'märz']],
  [4, ['apr', 'april']],
  [5, ['mai', 'may']],
  [6, ['jun', 'juni', 'june']],
  [7, ['jul', 'juli', 'july']],
  [8, ['aug', 'august']],
  [9, ['sep', 'sept', 'september']],
  [10, ['okt', 'oct', 'oktober', 'october']],
  [11, ['nov', 'november']],
  [12, ['dez', 'dec', 'dezember', 'december']],
]

function normalizeLabel(text: string): string {
  return text
    .toLowerCase()
    .replace(/ä/g, 'ae')
    .replace(/ö/g, 'oe')
    .replace(/ü/g, 'ue')
    .replace(/ß/g, 'ss')
}

function parseMonthYearFromHeader(
  preview: PreviewData,
  amountLetter: string,
  defaultYear: number,
): { month?: number; year?: number } {
  const colIdx = idxFromLetter(amountLetter)
  const hr = preview.header_row_index
  const parts: string[] = []
  for (let r = Math.max(0, hr - 3); r <= hr && r < preview.rows.length; r++) {
    const cell = preview.rows[r]?.[colIdx]
    if (cell?.trim()) parts.push(cell.trim())
  }
  const s = normalizeLabel(parts.join(' '))
  if (!s) return { year: defaultYear }
  // Data cells (e.g. "64.62 56.83") must not be parsed as month/year.
  if (/^[\d\s.,+-]+$/.test(s.replace(/\s+/g, ''))) return { year: defaultYear }

  let year = defaultYear
  const y4 = s.match(/(20\d{2}|19\d{2})/)
  if (y4) year = parseInt(y4[1], 10)
  const y2 = s.match(/(?<![0-9])(\d{2})(?![0-9])/)
  if (!y4 && y2) {
    const yy = parseInt(y2[1], 10)
    year = yy < 70 ? 2000 + yy : 1900 + yy
  }
  for (const [mm, keys] of MONTH_KEYS) {
    if (keys.some(k => s.includes(k))) return { month: mm, year }
  }
  const mNum = s.match(/\b(0?[1-9]|1[0-2])\b/)
  if (mNum) return { month: parseInt(mNum[1], 10), year }
  return { year: defaultYear }
}

function idxFromLetter(letter: string): number {
  let idx = 0
  for (const c of letter.toUpperCase()) {
    idx = idx * 26 + (c.charCodeAt(0) - 64)
  }
  return idx - 1
}

function letterFromIdx(idx: number): string {
  let n = idx + 1
  let s = ''
  while (n > 0) {
    const rem = (n - 1) % 26
    s = String.fromCharCode(65 + rem) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

function patternForSignMode(signMode: string): string {
  if (signMode === 'sh_two_columns') return 'month_sh_double'
  if (signMode === 'sh_column') return 'month_sh_single'
  return 'month_amount'
}

function stepRoleForBlockField(_signMode: string, field: string): ColumnRole {
  if (field === 'amount') return 'amount'
  if (field === 'sh') return 'sh'
  if (field === 'sh_s') return 'period1_s'
  if (field === 'sh_h') return 'period1_h'
  return 'amount'
}

function period1StepRole(signMode: string, field: string): ColumnRole | null {
  if (signMode === 'already_signed' && field === 'amount') return 'period1_amount'
  if (signMode === 'sh_column') {
    if (field === 'amount') return 'period1_amount'
    if (field === 'sh') return 'period1_sh'
  }
  if (signMode === 'sh_two_columns') {
    if (field === 'amount') return 'period1_amount'
    if (field === 'sh_s') return 'period1_s'
    if (field === 'sh_h') return 'period1_h'
  }
  return null
}

function extendRepeatingPattern(
  assignments: Record<string, ColumnRole>,
  signMode: string,
  colCount: number,
): {
  assignments: Record<string, ColumnRole>
  repeating: SusaColumnMappingPayload['default']['repeating']
} | null {
  const fields = blockRolesForSignMode(signMode)
  const p1Letters: string[] = []
  for (const field of fields) {
    const stepRole = period1StepRole(signMode, field)
    if (!stepRole) return null
    const letter = Object.entries(assignments).find(([, r]) => r === stepRole)?.[0]
    if (!letter) return null
    p1Letters.push(letter)
  }

  const startIdx = Math.min(...p1Letters.map(idxFromLetter))
  const blockSize = fields.length
  const extended = { ...assignments }

  for (let i = startIdx; i < colCount; i += blockSize) {
    fields.forEach((field, offset) => {
      const colIdx = i + offset
      if (colIdx < colCount) {
        extended[letterFromIdx(colIdx)] = stepRoleForBlockField(signMode, field)
      }
    })
  }

  return {
    assignments: extended,
    repeating: {
      enabled: true,
      from_col: letterFromIdx(startIdx),
      block_size: blockSize,
      pattern: patternForSignMode(signMode),
    },
  }
}

export default function SusaColumnMapper({
  sessionId,
  previewFileId,
  layoutFormat,
  signMode,
  valueType,
  entityNames,
  disabled,
  onSubmit,
}: Props) {
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [assignments, setAssignments] = useState<Record<string, ColumnRole>>({})
  const [pendingColumn, setPendingColumn] = useState<string | null>(null)
  const [stepIndex, setStepIndex] = useState(0)
  const [phase, setPhase] = useState<WizardPhase>('mark')
  const [repeating, setRepeating] = useState<SusaColumnMappingPayload['default']['repeating']>()
  const [entityOverrides, setEntityOverrides] = useState<Record<string, SusaColumnMappingPayload['default']>>({})
  const [activeEntity, setActiveEntity] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const isSingleSheet = layoutFormat === 'single_sheet'

  const markSteps = useMemo(
    () => buildMarkSteps(signMode, valueType, isSingleSheet),
    [signMode, valueType, isSingleSheet],
  )

  const currentStep: MarkStep | undefined = markSteps[stepIndex]
  const currentRole = currentStep?.role
  const colCount = preview?.columns.length ?? 0

  const fyYear = useMemo(() => {
    const m = previewFileId.match(/20\d{2}|19\d{2}/)
    return m ? parseInt(m[0], 10) : new Date().getFullYear()
  }, [previewFileId])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      try {
        const base = getApiBaseUrl()
        const url = `${base}/api/v1/fdd/susa/preview?session_id=${encodeURIComponent(sessionId)}&file_id=${encodeURIComponent(previewFileId)}`
        if (!previewFileId.trim()) {
          throw new Error('No preview file id — please re-upload trial balances in the grid step.')
        }
        if (!sessionId.trim()) {
          throw new Error('No session id — please refresh the bot conversation.')
        }
        const resp = await fetch(url)
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}))
          const detail = (err as { detail?: string | unknown }).detail
          const detailStr =
            typeof detail === 'string'
              ? detail
              : Array.isArray(detail)
                ? detail.map((d: { msg?: string }) => d?.msg ?? String(d)).join('; ')
                : `Preview failed (${resp.status})`
          throw new Error(detailStr)
        }
        const data = (await resp.json()) as PreviewData
        if (!cancelled) setPreview(data)
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Preview failed'
        if (!cancelled) setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sessionId, previewFileId])

  const selectColumn = (letter: string) => {
    if (phase !== 'mark' || !currentRole || disabled) return
    const existing = assignments[letter]
    if (existing && existing !== currentRole) return
    setPendingColumn(letter)
  }

  const tryFinishMarking = (next: Record<string, ColumnRole>) => {
    if (isSingleSheet && colCount > 0) {
      const extended = extendRepeatingPattern(next, signMode, colCount)
      if (extended) {
        setAssignments(extended.assignments)
        setRepeating(extended.repeating)
        setPhase('confirm_pattern')
        return
      }
    }
    setPhase('confirm_assignment')
  }

  const skipCurrentStep = () => {
    if (!currentStep?.skippable) return
    setPendingColumn(null)
    if (stepIndex < markSteps.length - 1) {
      setStepIndex(stepIndex + 1)
      return
    }
    tryFinishMarking(assignments)
  }

  const confirmPendingColumn = useCallback(() => {
    if (!pendingColumn || !currentRole || phase !== 'mark') return

    const next: Record<string, ColumnRole> = { ...assignments }
    for (const [k, v] of Object.entries(next)) {
      if (v === currentRole) delete next[k]
    }
    next[pendingColumn] = currentRole
    setAssignments(next)
    setPendingColumn(null)

    if (stepIndex < markSteps.length - 1) {
      setStepIndex(stepIndex + 1)
      return
    }
    tryFinishMarking(next)
  }, [pendingColumn, currentRole, phase, assignments, stepIndex, markSteps.length, isSingleSheet, colCount, signMode])

  const buildProfile = (): SusaColumnMappingPayload['default'] => {
    const account = Object.entries(assignments).find(([, r]) => r === 'account')?.[0] ?? 'A'
    const account_description = Object.entries(assignments).find(([, r]) => r === 'account_description')?.[0]
    const opening_balance = Object.entries(assignments).find(([, r]) => r === 'opening_balance')?.[0]
    const eb_sh = Object.entries(assignments).find(([, r]) => r === 'eb_sh')?.[0]
    const eb_s = Object.entries(assignments).find(([, r]) => r === 'eb_s')?.[0]
    const eb_h = Object.entries(assignments).find(([, r]) => r === 'eb_h')?.[0]

    const period_blocks: SusaColumnMappingPayload['default']['period_blocks'] = []
    const fields = blockRolesForSignMode(signMode)

    if (isSingleSheet && repeating?.enabled && preview) {
      const startIdx = idxFromLetter(repeating.from_col)
      const block = repeating.block_size
      for (let i = startIdx; i + block - 1 < colCount; i += block) {
        const amountOffset = Math.max(0, fields.indexOf('amount'))
        const amountLetter = letterFromIdx(i + amountOffset)
        const { month, year } = parseMonthYearFromHeader(preview, amountLetter, fyYear)
        const blockDef: NonNullable<SusaColumnMappingPayload['default']['period_blocks']>[0] = {
          month,
          year,
        }
        fields.forEach((field, offset) => {
          const letter = letterFromIdx(i + offset)
          const key = field as keyof typeof blockDef
          if (key === 'month' || key === 'year') return
          ;(blockDef as Record<string, string>)[field] = letter
        })
        if (blockDef.month) period_blocks.push(blockDef)
      }
      period_blocks.sort(
        (a, b) =>
          (a.year ?? fyYear) - (b.year ?? fyYear) ||
          (a.month ?? 99) - (b.month ?? 99),
      )
    } else {
      const blockDef: NonNullable<SusaColumnMappingPayload['default']['period_blocks']>[0] = {
        month: 1,
        year: fyYear,
      }
      for (const [letter, role] of Object.entries(assignments)) {
        let field = 'amount'
        if (role === 'period1_sh' || role === 'period2_sh' || role === 'sh') field = 'sh'
        else if (role === 'period1_s' || role === 'period2_s') field = 'sh_s'
        else if (role === 'period1_h' || role === 'period2_h') field = 'sh_h'
        else if (role.includes('amount') || role === 'amount') field = 'amount'
        if (['amount', 'sh', 'sh_s', 'sh_h', 'soll', 'haben'].includes(field)) {
          ;(blockDef as Record<string, string>)[field] = letter
        }
      }
      period_blocks.push(blockDef)
    }

    return {
      header_row: preview?.header_row_index ?? 0,
      columns: {
        account,
        ...(account_description ? { account_description } : {}),
        ...(opening_balance ? { opening_balance } : {}),
        ...(eb_sh ? { eb_sh } : {}),
        ...(eb_s ? { eb_s } : {}),
        ...(eb_h ? { eb_h } : {}),
      },
      period_blocks,
      ...(repeating ? { repeating } : {}),
    }
  }

  const finishMapping = async (entities: Record<string, SusaColumnMappingPayload['default']>) => {
    setSubmitting(true)
    try {
      await onSubmit({
        default: entities.default ?? buildProfile(),
        entities: Object.fromEntries(
          Object.entries(entities).filter(([k]) => k !== 'default'),
        ),
      })
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmYes = async () => {
    if (phase === 'confirm_pattern' || phase === 'confirm_assignment') {
      const hasEb = Object.values(assignments).includes('opening_balance')
      if (hasEb && signMode === 'sh_column' && !Object.values(assignments).includes('eb_sh')) {
        setError('Opening balance debit/credit indicator column is required when opening balance is mapped.')
        setPhase('mark')
        setStepIndex(markSteps.findIndex(s => s.role === 'eb_sh'))
        return
      }
      if (
        hasEb &&
        signMode === 'sh_two_columns' &&
        (!Object.values(assignments).includes('eb_s') ||
          !Object.values(assignments).includes('eb_h'))
      ) {
        setError('Opening balance debit (S) and credit (H) indicator columns are required when opening balance is mapped.')
        setPhase('mark')
        return
      }
      if (entityNames.length <= 1) {
        await finishMapping({ default: buildProfile() })
        return
      }
      setPhase('entity_same')
    }
  }

  const handleConfirmNo = () => {
    setPhase('mark')
    setStepIndex(0)
    setAssignments({})
    setPendingColumn(null)
    setRepeating(undefined)
    setError(null)
  }

  const handleEntitySame = async (same: boolean) => {
    const profile = buildProfile()
    if (same) {
      await finishMapping({ default: profile })
    } else {
      setEntityOverrides({ default: profile })
      setActiveEntity(entityNames[1] ?? entityNames[0])
      setAssignments({})
      setPendingColumn(null)
      setStepIndex(0)
      setPhase('mark')
    }
  }

  const handleEntityOverrideDone = async () => {
    if (!activeEntity) return
    const profile = buildProfile()
    const next = { ...entityOverrides, [activeEntity]: profile }
    const done = entityNames.every(name => name in next || name === entityNames[0])
    if (!done && entityNames.length > 1) {
      const remaining = entityNames.find(n => !(n in next) && n !== entityNames[0])
      if (remaining) {
        setActiveEntity(remaining)
        setAssignments({})
        setPendingColumn(null)
        setStepIndex(0)
        setPhase('mark')
        return
      }
    }
    await finishMapping(next)
  }

  if (loading) {
    return <p className="text-sm text-slate-500">Loading trial balance preview…</p>
  }
  if (error && !preview) {
    return <p className="text-sm text-red-600">{error}</p>
  }
  if (!preview) return null

  const instruction =
    phase === 'mark' && currentStep
      ? `Select the column for “${currentStep.label}”, then confirm with the green checkmark in the column header.`
      : phase === 'confirm_pattern'
        ? 'The repeating pattern has been applied to the remaining columns. Does this look correct?'
        : phase === 'confirm_assignment'
          ? 'Does this column assignment look correct?'
          : phase === 'entity_same'
            ? 'Is this layout the same for all entities?'
            : activeEntity
              ? `Map columns for entity: ${activeEntity}`
              : ''

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

  const tableBlock = (
    <div
      className={`overflow-auto rounded-lg border border-slate-200 ${expanded ? 'flex-1 min-h-0 w-full' : 'max-h-[320px] w-full'}`}
    >
      <table className="text-xs border-collapse min-w-full">
        <thead>
          <tr>
            {preview.columns.map(col => {
              const role = assignments[col.letter]
              const isPending = pendingColumn === col.letter
              const canSelect =
                phase === 'mark' &&
                currentRole &&
                (!role || role === currentRole)
              const bg = role ? roleColor(role) : isPending ? '#94A3B8' : '#E2E8F0'
              return (
                <th
                  key={col.letter}
                  className="relative px-2 py-1 border border-slate-200 select-none min-w-[52px]"
                  style={{
                    background: bg,
                    color: role || isPending ? '#fff' : '#475569',
                    cursor: canSelect ? 'pointer' : 'default',
                  }}
                  onClick={() => canSelect && selectColumn(col.letter)}
                  title={col.sample_values.join(', ')}
                >
                  <span className="font-semibold">{col.letter}</span>
                  {role && (
                    <div className="text-[9px] font-normal opacity-90 leading-tight">
                      {currentStep?.label && role === currentRole
                        ? currentStep.label
                        : role.replace(/_/g, ' ')}
                    </div>
                  )}
                  {isPending && (
                    <button
                      type="button"
                      className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-white shadow-md hover:bg-emerald-600"
                      title={`Confirm: ${currentStep?.label}`}
                      onClick={e => {
                        e.stopPropagation()
                        confirmPendingColumn()
                      }}
                    >
                      <Check size={12} strokeWidth={3} />
                    </button>
                  )}
                  {role && !isPending && (
                    <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-white/90 text-emerald-600">
                      <Check size={10} strokeWidth={3} />
                    </span>
                  )}
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
                const role = letter ? assignments[letter] : undefined
                return (
                  <td
                    key={ci}
                    className="px-2 py-0.5 border border-slate-100 whitespace-nowrap max-w-[140px] truncate"
                    style={{
                      background: role ? `${roleColor(role)}18` : undefined,
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
  )

  const panelContent = (
    <div className={`relative flex flex-col gap-3 ${expanded ? 'h-full' : ''}`}>
      {expandToggle}
      <p className={`text-sm font-medium text-slate-700 ${expanded ? 'pl-10' : 'pl-9'}`}>
        {instruction}
      </p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {phase === 'mark' && currentStep?.skippable && (
        <button
          type="button"
          className="self-start text-xs text-slate-500 underline"
          onClick={skipCurrentStep}
        >
          Skip — no opening balance column
        </button>
      )}
      {tableBlock}
      {(phase === 'confirm_pattern' || phase === 'confirm_assignment') && (
        <div className="flex gap-2">
          <button
            type="button"
            disabled={disabled || submitting}
            className="px-3 py-1.5 rounded-md text-sm bg-slate-900 text-white"
            onClick={() => void handleConfirmYes()}
          >
            Yes, continue
          </button>
          <button
            type="button"
            disabled={disabled || submitting}
            className="px-3 py-1.5 rounded-md text-sm border border-slate-300"
            onClick={handleConfirmNo}
          >
            No, remap manually
          </button>
        </div>
      )}
      {phase === 'entity_same' && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="px-3 py-1.5 rounded-md text-sm bg-slate-900 text-white"
            disabled={disabled || submitting}
            onClick={() => void handleEntitySame(true)}
          >
            Yes, same for all
          </button>
          <button
            type="button"
            className="px-3 py-1.5 rounded-md text-sm border border-slate-300"
            disabled={disabled || submitting}
            onClick={() => void handleEntitySame(false)}
          >
            No, map per entity
          </button>
        </div>
      )}
      {phase === 'mark' && activeEntity && entityNames.length > 1 && (
        <button
          type="button"
          className="self-start px-3 py-1.5 rounded-md text-sm bg-slate-900 text-white"
          disabled={disabled || submitting || stepIndex < markSteps.length - 1}
          onClick={() => void handleEntityOverrideDone()}
        >
          Confirm mapping for {activeEntity}
        </button>
      )}
    </div>
  )

  if (expanded) {
    return createPortal(
      <div
        className="fixed z-[9999] flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-2xl"
        style={{
          top: 12,
          left: 12,
          right: 12,
          bottom: 12,
          width: 'auto',
          maxWidth: 'none',
        }}
      >
        {panelContent}
      </div>,
      document.body,
    )
  }

  return panelContent
}
