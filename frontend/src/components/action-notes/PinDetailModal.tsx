import { ExternalLink, MessageCircle, Table2, X } from 'lucide-react'
import type { ActionNote, ActionNotePin } from '../../lib/api'
import { navigateToPinSnapshot } from './navigateToPin'

type Props = {
  pin: ActionNotePin
  notes: ActionNote[]
  onClose: () => void
  onOpenExpertSession?: (sessionId: string) => void
}

export default function PinDetailModal({ pin, notes, onClose, onOpenExpertSession }: Props) {
  const s = pin.snapshot_json
  const isChat = s.pin_type === 'expert_chat' || !!s.expert_chat?.messages?.length
  const isTable = !!s.table?.row_preview?.length
  const isChart = s.pin_type === 'chart' || !!s.chart
  const canNavigate = s.restore_mode !== 'preview_only' && s.route

  return (
    <div
      className="fixed inset-0 z-[62] flex items-center justify-center p-4 bg-slate-900/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl max-w-lg w-full max-h-[85vh] flex flex-col shadow-xl border border-slate-200"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="shrink-0 flex items-start justify-between gap-3 px-4 py-3 border-b border-slate-100">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-slate-900 truncate">
              {pin.label ?? s.label ?? (isChat ? 'Expert chat' : 'Table view')}
            </h3>
            <p className="text-[0.65rem] text-slate-500 mt-0.5">
              {s.route} · {String(s.filters?.year)}-{String(s.filters?.month)}
              {s.filters?.entity ? ` · ${String(s.filters.entity)}` : ''}
            </p>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100 shrink-0" aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
          {canNavigate && (
            <button
              type="button"
              onClick={() => navigateToPinSnapshot(s)}
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-lg text-white font-medium"
              style={{ background: '#1E3A5F' }}
            >
              <ExternalLink size={14} />
              Open this view in the app
            </button>
          )}

          {isTable && (
            <section>
              <p className="flex items-center gap-1.5 font-semibold text-slate-800 mb-2">
                <Table2 size={14} /> Table snapshot
              </p>
              <ul className="rounded-lg border border-slate-200 divide-y divide-slate-100 bg-slate-50/50">
                {s.table!.row_preview.slice(0, 12).map(r => (
                  <li key={r.id} className="px-3 py-1.5 flex justify-between gap-2">
                    <span className="text-slate-800 truncate">{r.label}</span>
                    <span className="text-slate-500 tabular-nums shrink-0">
                      {Object.entries(r.values)
                        .slice(0, 2)
                        .map(([, v]) => v)
                        .join(' · ')}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {isChart && (
            <section>
              <p className="flex items-center gap-1.5 font-semibold text-slate-800 mb-2">Chart snapshot</p>
              {s.chart?.image_data_url ? (
                <img
                  src={s.chart.image_data_url}
                  alt={s.chart.title ?? s.chart.chart_id ?? 'Chart snapshot'}
                  className="w-full rounded-lg border border-slate-200"
                />
              ) : (
                <p className="text-slate-600">No image preview saved. Open in app to regenerate capture.</p>
              )}
            </section>
          )}

          {isChat && (
            <section>
              <p className="flex items-center gap-1.5 font-semibold text-slate-800 mb-2">
                <MessageCircle size={14} /> Chat transcript
              </p>
              <ul className="space-y-2 max-h-48 overflow-y-auto rounded-lg border border-slate-200 p-2 bg-slate-50/50">
                {(s.expert_chat?.messages ?? []).map((m, i) => (
                  <li
                    key={i}
                    className={`px-2 py-1.5 rounded-lg ${
                      m.role === 'user' ? 'bg-[#1E3A5F]/10 text-slate-800 ml-4' : 'bg-white border border-slate-100 mr-4'
                    }`}
                  >
                    <span className="text-[0.6rem] uppercase font-bold text-slate-400">{m.role}</span>
                    <p className="mt-0.5 whitespace-pre-wrap">{m.text ?? ''}</p>
                  </li>
                ))}
              </ul>
              {s.expert_chat?.session_id && onOpenExpertSession && (
                <button
                  type="button"
                  className="mt-2 text-[0.65rem] font-medium text-[#1E3A5F] hover:underline"
                  onClick={() => {
                    onOpenExpertSession(s.expert_chat!.session_id!)
                    onClose()
                  }}
                >
                  Open in Finssentials Expert →
                </button>
              )}
            </section>
          )}

          {notes.length > 0 && (
            <section>
              <p className="font-semibold text-slate-800 mb-2">Related notes in this workspace</p>
              <ul className="space-y-1.5">
                {notes.map(n => (
                  <li key={n.note_id} className="px-2 py-1.5 rounded-lg bg-amber-50/80 border border-amber-100 text-slate-800">
                    {n.body}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}
