import { useState } from 'react'
import { MapPin, X } from 'lucide-react'
import type { TablePinCandidate } from './ActionNotesContext'

type Props = {
  candidates: TablePinCandidate[]
  onClose: () => void
  onConfirm: (candidateId: string, restoreMode: 'navigate' | 'preview_only') => void
}

export default function PinTablePicker({ candidates, onClose, onConfirm }: Props) {
  const [selectedId, setSelectedId] = useState(candidates[0]?.id ?? '')
  const [restoreMode, setRestoreMode] = useState<'navigate' | 'preview_only'>('navigate')

  if (!candidates.length) {
    return (
      <div className="fixed inset-0 z-[62] flex items-center justify-center p-4 bg-slate-900/40" onClick={onClose}>
        <div
          className="bg-white rounded-xl p-5 max-w-sm w-full shadow-xl border border-slate-200"
          onClick={e => e.stopPropagation()}
        >
          <p className="text-sm text-slate-700">No pinnable tables on this page. Open a statement or table view first.</p>
          <button type="button" className="mt-4 w-full py-2 rounded-lg bg-slate-100 text-xs font-medium" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 z-[62] flex items-center justify-center p-4 bg-slate-900/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl max-w-md w-full shadow-xl border border-slate-200 overflow-hidden"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="pin-picker-title"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <h3 id="pin-picker-title" className="text-sm font-semibold text-slate-900 flex items-center gap-2">
            <MapPin size={16} className="text-amber-600" />
            Pin a table view
          </h3>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100" aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="p-4 space-y-3 max-h-[50vh] overflow-y-auto">
          <p className="text-[0.65rem] text-slate-500">
            Choose which table to pin. You can reopen the same filters later or keep a data preview only.
          </p>
          <ul className="space-y-1.5">
            {candidates.map(c => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(c.id)}
                  className={`w-full text-left px-3 py-2.5 rounded-lg border text-xs transition-colors ${
                    selectedId === c.id
                      ? 'border-amber-400 bg-amber-50 text-amber-950'
                      : 'border-slate-200 hover:border-slate-300 text-slate-800'
                  }`}
                >
                  <span className="font-semibold block">{c.label}</span>
                  {c.description && <span className="text-[0.65rem] text-slate-500 mt-0.5 block">{c.description}</span>}
                </button>
              </li>
            ))}
          </ul>
          <div className="pt-2 space-y-2">
            <p className="text-[0.65rem] font-medium text-slate-600 uppercase tracking-wide">When opening the pin</p>
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                name="restore"
                checked={restoreMode === 'navigate'}
                onChange={() => setRestoreMode('navigate')}
                className="mt-0.5"
              />
              <span className="text-xs text-slate-700">
                <strong>Restore view</strong> — same period, entity, and tab (recommended)
              </span>
            </label>
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                name="restore"
                checked={restoreMode === 'preview_only'}
                onChange={() => setRestoreMode('preview_only')}
                className="mt-0.5"
              />
              <span className="text-xs text-slate-700">
                <strong>Preview only</strong> — saved rows in Action Notes, no navigation
              </span>
            </label>
          </div>
        </div>
        <div className="px-4 py-3 border-t border-slate-100 flex gap-2">
          <button type="button" onClick={onClose} className="flex-1 py-2 rounded-lg border border-slate-200 text-xs font-medium">
            Cancel
          </button>
          <button
            type="button"
            disabled={!selectedId}
            onClick={() => onConfirm(selectedId, restoreMode)}
            className="flex-1 py-2 rounded-lg text-white text-xs font-medium disabled:opacity-40"
            style={{ background: '#1E3A5F' }}
          >
            Pin selected
          </button>
        </div>
      </div>
    </div>
  )
}
