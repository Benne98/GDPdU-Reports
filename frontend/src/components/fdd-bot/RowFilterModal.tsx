import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { ListFilter, Trash2, Undo2, X } from 'lucide-react'
import {
  FILTER_OPERATORS,
  buildFilterDisplay,
  humanizeFilterRule,
  parseFilterRules,
  type FilterOperator,
  type FilterRule,
} from './rowFilterTypes'

interface Props {
  open: boolean
  title?: string
  headers: string[]
  rulesJson: string
  onClose: () => void
  onSave: (rules: FilterRule[]) => void | Promise<void>
}

function draftFromJson(rulesJson: string): FilterRule[] {
  return parseFilterRules(rulesJson).map(rule => ({ ...rule }))
}

export default function RowFilterModal({
  open,
  title = 'Row filter',
  headers,
  rulesJson,
  onClose,
  onSave,
}: Props) {
  const [rules, setRules] = useState<FilterRule[]>(() => draftFromJson(rulesJson))
  const [filterCol, setFilterCol] = useState('')
  const [filterOp, setFilterOp] = useState<FilterOperator>('eq')
  const [filterValue, setFilterValue] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setRules(draftFromJson(rulesJson))
      setFilterCol(headers[0] ?? '')
      setFilterOp('eq')
      setFilterValue('')
    }
  }, [open, rulesJson, headers])

  const overview = useMemo(() => buildFilterDisplay(rules), [rules])
  const valueOptional = filterOp === 'isblank' || filterOp === 'notblank'

  const addRule = () => {
    if (!filterCol.trim()) return
    let rule: FilterRule
    if (filterOp === 'in' || filterOp === 'not_in') {
      rule = {
        col: filterCol,
        op: filterOp,
        values: filterValue.split(',').map(v => v.trim()).filter(Boolean),
      }
    } else if (filterOp === 'between') {
      const parts = filterValue.split(',')
      rule = {
        col: filterCol,
        op: filterOp,
        lo: (parts[0] ?? '').trim(),
        hi: (parts[1] ?? '').trim(),
      }
    } else if (valueOptional) {
      rule = { col: filterCol, op: filterOp }
    } else {
      rule = { col: filterCol, op: filterOp, value: filterValue }
    }
    setRules(previous => [...previous, rule])
    setFilterValue('')
  }

  const removeRule = (index: number) => {
    setRules(previous => previous.filter((_, i) => i !== index))
  }

  const removeLastRule = () => {
    setRules(previous => previous.slice(0, -1))
  }

  const handleClose = async () => {
    setSaving(true)
    try {
      await onSave(rules)
    } finally {
      setSaving(false)
      onClose()
    }
  }

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-[1200] flex items-center justify-center bg-slate-900/45 p-4"
      onClick={() => void handleClose()}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={event => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#1E3A5F] text-white">
              <ListFilter size={16} />
            </span>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
              <p className="text-[11px] text-slate-500">Rules apply to the current dataset only.</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void handleClose()}
            disabled={saving}
            className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 disabled:opacity-50"
            aria-label="Close and save"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <section className="mb-5">
            <p className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-400">
              Current rules
            </p>
            {rules.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-xs text-slate-400">
                No active filter rules.
              </p>
            ) : (
              <ul className="space-y-2">
                {rules.map((rule, index) => (
                  <li
                    key={`${rule.col}-${index}`}
                    className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2"
                  >
                    <span className="text-xs leading-relaxed text-slate-700">
                      {humanizeFilterRule(rule)}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeRule(index)}
                      className="shrink-0 rounded p-1 text-slate-400 hover:bg-white hover:text-red-600"
                      aria-label="Delete rule"
                    >
                      <Trash2 size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {overview ? (
              <p className="mt-2 whitespace-pre-line text-[10px] text-slate-400">{overview}</p>
            ) : null}
          </section>

          <section className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
            <p className="mb-3 text-[11px] font-bold uppercase tracking-wide text-slate-400">
              Add rule
            </p>
            <div className="grid gap-2">
              <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
                Column
                <select
                  value={filterCol}
                  onChange={event => setFilterCol(event.target.value)}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs"
                >
                  {headers.map(header => (
                    <option key={header} value={header}>{header}</option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
                Condition
                <select
                  value={filterOp}
                  onChange={event => setFilterOp(event.target.value as FilterOperator)}
                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs"
                >
                  {FILTER_OPERATORS.map(op => (
                    <option key={op.value} value={op.value}>{op.label}</option>
                  ))}
                </select>
              </label>
              {!valueOptional ? (
                <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
                  Value
                  <input
                    type="text"
                    value={filterValue}
                    onChange={event => setFilterValue(event.target.value)}
                    placeholder={
                      filterOp === 'between'
                        ? 'low, high'
                        : filterOp === 'in' || filterOp === 'not_in'
                          ? 'comma-separated values'
                          : 'filter value'
                    }
                    className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs"
                  />
                </label>
              ) : null}
              <button
                type="button"
                onClick={addRule}
                className="mt-1 rounded-lg bg-[#1E3A5F] px-3 py-2 text-xs font-semibold text-white"
              >
                Add filter
              </button>
            </div>
          </section>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-3">
          <button
            type="button"
            onClick={removeLastRule}
            disabled={rules.length === 0 || saving}
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100 disabled:opacity-40"
          >
            <Undo2 size={14} />
            Remove last rule
          </button>
          <button
            type="button"
            onClick={() => void handleClose()}
            disabled={saving}
            className="rounded-lg bg-[#1E3A5F] px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Close'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

interface ToolbarProps {
  label?: string
  disabled?: boolean
  onOpen: () => void
}

export function RowFilterToolbar({ label = 'Row filter', disabled, onOpen }: ToolbarProps) {
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-semibold text-slate-600 transition-colors hover:border-slate-300 hover:text-[#1E3A5F] disabled:opacity-50"
    >
      <ListFilter size={14} />
      {label}
    </button>
  )
}
