import { useMemo, useState } from 'react'
import { Pencil, X } from 'lucide-react'
import {
  EBIT_COLUMN_CATALOG,
  saveEbitColumns,
  type EbitDisplayColumn,
} from './ebitColumnRegistry'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from '../financials/pl-two-view/plToolbarButton'

type Props = {
  visible: EbitDisplayColumn[]
  onChange: (cols: EbitDisplayColumn[]) => void
}

export default function EbitColumnEditor({ visible, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const optional = useMemo(
    () => EBIT_COLUMN_CATALOG.filter(c => !c.fixed),
    [],
  )

  const toggle = (id: EbitDisplayColumn) => {
    const fixed = EBIT_COLUMN_CATALOG.filter(c => c.fixed).map(c => c.id)
    const next = visible.includes(id)
      ? visible.filter(c => c !== id)
      : [...visible, id]
    const ordered = [...fixed, ...optional.map(c => c.id).filter(id => next.includes(id))]
    onChange(ordered)
    saveEbitColumns(ordered)
  }

  return (
    <div className="relative">
      <button
        type="button"
        title="Edit columns"
        onClick={() => setOpen(v => !v)}
        className={PL_TOOLBAR_ICON_BTN}
        style={PL_TOOLBAR_BTN_STYLE}
      >
        <Pencil size={12} strokeWidth={1.75} />
      </button>
      {open && (
        <>
          <button
            type="button"
            className="fixed inset-0 z-40 cursor-default"
            aria-label="Close column editor"
            onClick={() => setOpen(false)}
          />
          <div
            className="absolute right-0 top-full mt-1 z-50 w-56 rounded-lg border bg-white shadow-lg p-3 text-xs"
            style={{ borderColor: '#E2E8F0' }}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="font-semibold text-slate-700">Columns</span>
              <button type="button" onClick={() => setOpen(false)} className="p-0.5 text-slate-400 hover:text-slate-600">
                <X size={14} />
              </button>
            </div>
            <div className="space-y-1.5">
              {EBIT_COLUMN_CATALOG.map(col => (
                <label key={col.id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={visible.includes(col.id)}
                    disabled={col.fixed}
                    onChange={() => toggle(col.id)}
                    className="rounded border-slate-300"
                  />
                  <span style={{ color: col.fixed ? '#94A3B8' : '#475569' }}>{col.label}</span>
                </label>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
