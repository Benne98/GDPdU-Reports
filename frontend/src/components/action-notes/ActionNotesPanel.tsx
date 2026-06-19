import { useEffect, useMemo, useState } from 'react'
import { FLOATING_PANEL_INSET } from './floatingPanelLayout'
import { ArrowLeft, ArrowRight, Check, Copy, Image, Mail, Pin, Send, Sparkles, StickyNote, X } from 'lucide-react'
import PinTablePicker from './PinTablePicker'
import PinChartPicker from './PinChartPicker'
import PinDetailModal from './PinDetailModal'
import type { ActionNotePin } from '../../lib/api'
import {
  api,
  type ActionBoard,
  type EmailDraftResponse,
  type InternalContact,
} from '../../lib/api'
import { useActionNotesContext } from './ActionNotesContext'
import { useActionNotesSession } from './useActionNotesSession'
import { loadUserProfile, saveUserProfile, type AppUserProfile } from './appUserProfile'
import { buildPinNavigateUrl } from './navigateToPin'

type Tab = 'notes' | 'pins' | 'actions' | 'board'

type Props = {
  open: boolean
  onClose: () => void
  sessionTitle: string
  route: string
}

export default function ActionNotesPanel({ open, onClose, sessionTitle, route }: Props) {
  const ctx = useActionNotesContext()
  const { bundle, loading, addNote, toggleNote, addPin, sessionId } = useActionNotesSession(
    sessionTitle,
    route,
    ctx.filters as unknown as Record<string, unknown>,
  )
  const [tab, setTab] = useState<Tab>('notes')
  const [draft, setDraft] = useState('')
  const [profile, setProfile] = useState<AppUserProfile | null>(() => loadUserProfile())
  const [nameDraft, setNameDraft] = useState('')
  const [emailAddressDraft, setEmailAddressDraft] = useState('')
  const [contacts, setContacts] = useState<InternalContact[]>([])
  const [contactId, setContactId] = useState('')
  const [mailDraft, setMailDraft] = useState<EmailDraftResponse | null>(null)
  const [board, setBoard] = useState<ActionBoard | null>(null)
  const [busy, setBusy] = useState(false)
  const [pinPickerOpen, setPinPickerOpen] = useState(false)
  const [pinChartPickerOpen, setPinChartPickerOpen] = useState(false)
  const [selectedPin, setSelectedPin] = useState<ActionNotePin | null>(null)
  const [boardAssigneeFilter, setBoardAssigneeFilter] = useState<string>('all')
  const [selectedNoteIds, setSelectedNoteIds] = useState<string[]>([])
  const [selectedPinIds, setSelectedPinIds] = useState<string[]>([])

  useEffect(() => {
    void api.directoryContacts().then(r => setContacts(r.contacts)).catch(() => setContacts([]))
  }, [])

  useEffect(() => {
    const noteIds = (bundle?.notes ?? []).map(n => n.note_id)
    const pinIds = (bundle?.pins ?? []).map(p => p.pin_id)
    setSelectedNoteIds(prev => {
      const keep = prev.filter(id => noteIds.includes(id))
      return keep.length ? keep : noteIds
    })
    setSelectedPinIds(prev => {
      const keep = prev.filter(id => pinIds.includes(id))
      return keep.length ? keep : pinIds
    })
  }, [bundle?.notes, bundle?.pins])

  const allNoteIds = useMemo(() => (bundle?.notes ?? []).map(n => n.note_id), [bundle?.notes])
  const allPinIds = useMemo(() => (bundle?.pins ?? []).map(p => p.pin_id), [bundle?.pins])
  const hasSelectedEvidence = selectedNoteIds.length > 0 || selectedPinIds.length > 0

  function toggleSelection(id: string, selected: string[], setSelected: (next: string[]) => void) {
    if (selected.includes(id)) {
      setSelected(selected.filter(v => v !== id))
    } else {
      setSelected([...selected, id])
    }
  }

  function handleOpenPinPicker() {
    const candidates = ctx.listTableCandidates()
    if (!candidates.length) {
      ctx.setToast('No pinnable tables on this page — open a statement or table first')
      return
    }
    setPinPickerOpen(true)
  }

  async function handleConfirmPin(candidateId: string, restoreMode: 'navigate' | 'preview_only') {
    const snap = ctx.pinTableById(
      candidateId,
      restoreMode === 'navigate' ? 'navigate' : 'preview_only',
    )
    setPinPickerOpen(false)
    if (!snap) {
      ctx.setToast('Could not capture table — refresh and try again')
      return
    }
    await addPin(snap, snap.label)
    ctx.setToast('View pinned')
    setTab('pins')
  }

  async function handlePinExpert() {
    const snap = ctx.pinExpert()
    if (!snap) {
      ctx.setToast('Open Expert chat first or send a message')
      return
    }
    await addPin(snap, snap.label)
    ctx.setToast('Expert chat pinned')
    setTab('pins')
  }

  async function handlePinChart() {
    const chartCandidates = ctx.listChartCandidates()
    if (!chartCandidates.length) {
      ctx.setToast('No chart capture target available on this page')
      return
    }
    setPinChartPickerOpen(true)
  }

  async function handleConfirmPinChart(candidateId: string, restoreMode: 'navigate' | 'preview_only') {
    setPinChartPickerOpen(false)
    const snap = await ctx.pinChartById(candidateId, restoreMode)
    if (!snap) {
      ctx.setToast('Could not capture chart screenshot')
      return
    }
    await addPin(snap, snap.label)
    ctx.setToast('Chart pinned')
    setTab('pins')
  }

  async function handleDraftEmail() {
    if (!sessionId || !contactId || !profile) return
    setBusy(true)
    try {
      const res = await api.actionNotesDraftEmail(sessionId, {
        contact_id: contactId,
        note_ids: selectedNoteIds,
        pin_ids: selectedPinIds,
        user_name: profile.display_name,
        user_email: profile.email,
        language: 'en',
        app_base_url: window.location.origin,
        simulate_reply: true,
      })
      setMailDraft(res)
    } catch (e) {
      ctx.setToast(e instanceof Error ? e.message : 'Draft failed')
    } finally {
      setBusy(false)
    }
  }

  async function handleGenerateBoard() {
    if (!sessionId) return
    setBusy(true)
    try {
      const res = await api.actionNotesGenerateBoard(sessionId, {
        note_ids: selectedNoteIds,
        pin_ids: selectedPinIds,
      })
      setBoard(res.board)
      setTab('board')
    } catch (e) {
      ctx.setToast(e instanceof Error ? e.message : 'Board generation failed')
    } finally {
      setBusy(false)
    }
  }

  function moveTask(taskId: string, direction: -1 | 1) {
    setBoard(prev => {
      if (!prev) return prev
      const columns = prev.columns ?? []
      const idxById = new Map(columns.map((c, idx) => [c.id, idx]))
      const nextTasks = prev.tasks.map(task => {
        if (task.id !== taskId) return task
        const current = idxById.get(task.column_id ?? 'backlog') ?? 0
        const nextIdx = Math.max(0, Math.min(columns.length - 1, current + direction))
        return { ...task, column_id: columns[nextIdx]?.id ?? task.column_id }
      })
      return { ...prev, tasks: nextTasks }
    })
  }

  const boardStats = useMemo(() => {
    if (!board) return null
    const total = board.tasks.length
    const doneCol = board.columns.find(c => c.id.toLowerCase().includes('done'))?.id ?? 'done'
    const done = board.tasks.filter(t => (t.column_id ?? 'backlog') === doneCol).length
    const high = board.tasks.filter(t => (t.priority ?? '').toLowerCase() === 'high').length
    return { total, done, high, progress: total ? Math.round((done / total) * 100) : 0 }
  }, [board])

  const tabs: Array<{ id: Tab; label: string }> = [
    { id: 'notes', label: 'Notes' },
    { id: 'pins', label: 'Pinned' },
    { id: 'actions', label: 'AI actions' },
    { id: 'board', label: 'Board' },
  ]

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-[57] bg-slate-900/25 backdrop-blur-[2px]"
          aria-hidden
          onClick={onClose}
        />
      )}
      <div
        className={`fixed z-[58] left-2 right-2 sm:left-auto sm:right-4 sm:w-[min(400px,calc(100vw-1.5rem))] flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl transition-all duration-200 ${FLOATING_PANEL_INSET} ${
          open ? 'translate-y-0 opacity-100 pointer-events-auto' : 'pointer-events-none translate-y-2 opacity-0 invisible'
        }`}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
      >
      <header
        className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-slate-100"
        style={{ background: 'linear-gradient(180deg, rgba(245,158,11,0.08) 0%, #fff 100%)' }}
      >
        <div className="w-9 h-9 rounded-full flex items-center justify-center shrink-0" style={{ background: '#F59E0B', color: '#fff' }}>
          <StickyNote size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-900 truncate">Action Notes</h2>
          <p className="text-[0.65rem] text-slate-500 truncate">To-dos · pins · team handoff</p>
        </div>
        <button type="button" onClick={onClose} className="p-2 rounded-lg hover:bg-slate-100" aria-label="Close">
          <X size={18} />
        </button>
      </header>

      <div className="flex border-b border-slate-100 px-2 gap-0.5">
        {tabs.map(t => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`flex-1 py-2 text-[0.65rem] font-medium rounded-t-md ${tab === t.id ? 'text-amber-800 border-b-2 border-amber-500' : 'text-slate-500'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3 text-xs">
        {loading && <p className="text-slate-500 text-center py-8">Loading workspace…</p>}

        {!loading && tab === 'notes' && (
          <div className="space-y-3">
            {!profile && (
              <div className="p-3 rounded-lg border border-amber-200 bg-amber-50/50 space-y-2">
                <p className="font-medium text-slate-800">Your profile (for email drafts)</p>
                <input
                  className="w-full rounded border px-2 py-1.5 text-xs"
                  placeholder="Full name"
                  value={nameDraft}
                  onChange={e => setNameDraft(e.target.value)}
                />
                <input
                  className="w-full rounded border px-2 py-1.5 text-xs"
                  placeholder="Email"
                  value={emailAddressDraft}
                  onChange={e => setEmailAddressDraft(e.target.value)}
                />
                <button
                  type="button"
                  className="w-full py-1.5 rounded bg-[#1E3A5F] text-white text-xs font-medium"
                  onClick={() => {
                    const p: AppUserProfile = {
                      display_name: nameDraft.trim(),
                      email: emailAddressDraft.trim(),
                      default_language: 'de',
                    }
                    setProfile(p)
                    saveUserProfile(p)
                    ctx.setToast('Profile saved')
                  }}
                >
                  Save profile
                </button>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <button type="button" onClick={handleOpenPinPicker} className="flex-1 flex items-center justify-center gap-1 py-2 rounded-lg border border-slate-200 hover:bg-slate-50">
                <Pin size={14} /> Pin table view
              </button>
              <button type="button" onClick={() => void handlePinExpert()} className="flex-1 flex items-center justify-center gap-1 py-2 rounded-lg border border-slate-200 hover:bg-slate-50">
                <Pin size={14} /> Pin chat
              </button>
              <button type="button" onClick={() => void handlePinChart()} className="col-span-2 flex items-center justify-center gap-1 py-2 rounded-lg border border-slate-200 hover:bg-slate-50">
                <Image size={14} /> Pin chart screenshot
              </button>
            </div>
            <textarea
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-xs min-h-[72px]"
              placeholder="Add a to-do or observation…"
              value={draft}
              onChange={e => setDraft(e.target.value)}
            />
            <button
              type="button"
              disabled={!draft.trim()}
              className="w-full py-2 rounded-lg text-white text-xs font-medium disabled:opacity-40"
              style={{ background: '#1E3A5F' }}
              onClick={() => {
                void addNote(draft.trim()).then(() => setDraft(''))
              }}
            >
              Add note
            </button>
            <ul className="space-y-2">
              {bundle?.notes.map(n => (
                <li key={n.note_id} className="flex gap-2 items-start p-2 rounded-lg bg-slate-50 border border-slate-100">
                  <button type="button" onClick={() => void toggleNote(n.note_id, !n.is_done)} className="mt-0.5 shrink-0">
                    <Check size={16} className={n.is_done ? 'text-emerald-600' : 'text-slate-300'} />
                  </button>
                  <span className={`flex-1 ${n.is_done ? 'line-through text-slate-400' : 'text-slate-800'}`}>{n.body}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!loading && tab === 'pins' && (
          <ul className="space-y-2">
            {!bundle?.pins.length && (
              <p className="text-slate-500 text-center py-6">No pins yet — choose a table on this page or pin an Expert chat.</p>
            )}
            {bundle?.pins.map(p => {
              const s = p.snapshot_json
              const isChat = s.pin_type === 'expert_chat'
              const isChart = s.pin_type === 'chart'
              const openUrl = s.restore_mode !== 'preview_only' && s.route ? buildPinNavigateUrl(s) : null
              return (
                <li key={p.pin_id}>
                  <div className="w-full rounded-lg border border-slate-200 bg-slate-50 p-3 transition-colors hover:border-amber-300 hover:bg-amber-50/50">
                    <button
                      type="button"
                      onClick={() => setSelectedPin(p)}
                      className="w-full text-left"
                    >
                      <p className="font-semibold text-slate-800">{p.label ?? s.label ?? s.pin_type}</p>
                      <p className="text-[0.65rem] text-slate-500 mt-1">
                        {isChart ? 'Chart' : isChat ? 'Expert chat' : 'Table'} · {s.route} · {String(s.filters?.year)}-{String(s.filters?.month)}
                      </p>
                      {s.table?.row_preview?.length ? (
                        <p className="mt-1 text-[0.65rem] text-slate-600">{s.table.row_preview.length} rows captured</p>
                      ) : null}
                      {s.expert_chat?.messages?.length ? (
                        <p className="mt-1 text-[0.65rem] text-slate-600 italic">{s.expert_chat.messages.length} messages</p>
                      ) : null}
                      {s.chart?.chart_id ? (
                        <p className="mt-1 text-[0.65rem] text-slate-600 italic">Chart target: {s.chart.chart_id}</p>
                      ) : null}
                    </button>
                    <div className="mt-2 flex items-center justify-between gap-2">
                      <button
                        type="button"
                        onClick={() => setSelectedPin(p)}
                        className="text-[0.65rem] font-medium text-[#1E3A5F] hover:underline"
                      >
                        Open details →
                      </button>
                      {openUrl ? (
                        <a
                          href={openUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-[0.65rem] font-medium text-emerald-700 hover:underline"
                        >
                          Open in browser ↗
                        </a>
                      ) : null}
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}

        {!loading && tab === 'actions' && (
          <div className="space-y-3">
            {profile && (
              <p className="text-[0.65rem] text-slate-500">
                From: <strong>{profile.display_name}</strong> &lt;{profile.email}&gt;
              </p>
            )}
            {!profile && (
              <p className="text-amber-800 bg-amber-50 p-2 rounded">Set your profile in the Notes tab first.</p>
            )}
            <select
              className="w-full rounded border px-2 py-2 text-xs"
              value={contactId}
              onChange={e => setContactId(e.target.value)}
            >
              <option value="">Select recipient…</option>
              {contacts.map(c => (
                <option key={c.contact_id} value={c.contact_id}>
                  {c.department} — {c.display_name}
                  {c.org_role_name ? ` (${c.org_role_name})` : ''}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={!contactId || !profile || busy || !hasSelectedEvidence}
              onClick={() => void handleDraftEmail()}
              className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-white text-xs font-medium disabled:opacity-40"
              style={{ background: '#1E3A5F' }}
            >
              <Sparkles size={14} /> Draft email
            </button>
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-2 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-slate-600">
                  Included evidence ({selectedNoteIds.length} notes, {selectedPinIds.length} pins)
                </p>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className="text-[0.6rem] px-2 py-1 rounded border border-slate-200 bg-white hover:bg-slate-100"
                    onClick={() => {
                      setSelectedNoteIds(allNoteIds)
                      setSelectedPinIds(allPinIds)
                    }}
                  >
                    Select all
                  </button>
                  <button
                    type="button"
                    className="text-[0.6rem] px-2 py-1 rounded border border-slate-200 bg-white hover:bg-slate-100"
                    onClick={() => {
                      setSelectedNoteIds([])
                      setSelectedPinIds([])
                    }}
                  >
                    Clear
                  </button>
                </div>
              </div>

              <div>
                <p className="text-[0.6rem] uppercase tracking-wide text-slate-500 mb-1">Notes</p>
                {!bundle?.notes.length ? (
                  <p className="text-[0.65rem] text-slate-500">No notes available</p>
                ) : (
                  <div className="space-y-1 max-h-24 overflow-y-auto pr-1">
                    {bundle.notes.map(n => (
                      <label key={n.note_id} className="flex items-start gap-2 text-[0.65rem] text-slate-700">
                        <input
                          type="checkbox"
                          checked={selectedNoteIds.includes(n.note_id)}
                          onChange={() => toggleSelection(n.note_id, selectedNoteIds, setSelectedNoteIds)}
                          className="mt-0.5"
                        />
                        <span className="line-clamp-2">{n.body}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <div>
                <p className="text-[0.6rem] uppercase tracking-wide text-slate-500 mb-1">Pins</p>
                {!bundle?.pins.length ? (
                  <p className="text-[0.65rem] text-slate-500">No pins available</p>
                ) : (
                  <div className="space-y-1 max-h-24 overflow-y-auto pr-1">
                    {bundle.pins.map(p => (
                      <label key={p.pin_id} className="flex items-start gap-2 text-[0.65rem] text-slate-700">
                        <input
                          type="checkbox"
                          checked={selectedPinIds.includes(p.pin_id)}
                          onChange={() => toggleSelection(p.pin_id, selectedPinIds, setSelectedPinIds)}
                          className="mt-0.5"
                        />
                        <span className="line-clamp-2">{p.label ?? p.snapshot_json?.label ?? p.pin_id}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {mailDraft && (
              <div className="rounded-lg border border-slate-200 p-3 space-y-2 bg-white">
                <p className="font-semibold text-slate-800">{mailDraft.subject}</p>
                <p className="text-[0.65rem] text-slate-500">To: {mailDraft.contact.display_name}</p>
                <pre className="text-[0.65rem] whitespace-pre-wrap text-slate-700 max-h-40 overflow-y-auto">{mailDraft.body_text}</pre>
                {mailDraft.notes?.length ? (
                  <div className="rounded-md border border-slate-200 bg-slate-50 p-2">
                    <p className="text-[0.6rem] font-semibold uppercase tracking-wide text-slate-600">
                      Notes included in email
                    </p>
                    <ul className="mt-1 list-disc pl-4 text-[0.65rem] text-slate-700 space-y-1">
                      {mailDraft.notes.slice(0, 8).map((n) => (
                        <li key={n}>{n}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {mailDraft.pin_links?.length ? (
                  <div className="rounded-md border border-sky-200 bg-sky-50 p-2">
                    <p className="text-[0.6rem] font-semibold uppercase tracking-wide text-sky-700">
                      Pinned links included in email
                    </p>
                    <ul className="mt-1 text-[0.65rem] space-y-1">
                      {mailDraft.pin_links.map((l) => (
                        <li key={`${l.label}-${l.url}`}>
                          <a href={l.url} target="_blank" rel="noreferrer" className="text-sky-700 hover:underline">
                            {l.label}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {mailDraft.simulated_reply?.body_text && (
                  <div className="rounded-md border border-emerald-200 bg-emerald-50 p-2">
                    <p className="text-[0.6rem] font-semibold uppercase tracking-wide text-emerald-700">
                      Simulated reply ({mailDraft.simulated_reply.from ?? 'contact'})
                    </p>
                    <p className="text-[0.65rem] text-emerald-900 mt-1 whitespace-pre-wrap">
                      {mailDraft.simulated_reply.body_text}
                    </p>
                  </div>
                )}
                <div className="flex gap-2">
                  <a
                    href={mailDraft.mailto}
                    className="flex-1 flex items-center justify-center gap-1 py-2 rounded-lg text-white text-xs font-medium"
                    style={{ background: '#059669' }}
                  >
                    <Send size={14} /> Open in mail
                  </a>
                  <button
                    type="button"
                    className="px-3 py-2 rounded-lg border border-slate-200"
                    onClick={() => void navigator.clipboard.writeText(mailDraft.body_text)}
                  >
                    <Copy size={14} />
                  </button>
                </div>
              </div>
            )}
            <button
              type="button"
              disabled={busy || !hasSelectedEvidence}
              onClick={() => void handleGenerateBoard()}
              className="w-full flex items-center justify-center gap-2 py-2 rounded-lg border border-slate-200 hover:bg-slate-50 font-medium"
            >
              <Mail size={14} /> Generate task board from notes
            </button>
          </div>
        )}

        {!loading && tab === 'board' && (
          <div className="space-y-3">
            {!board && <p className="text-slate-500 text-center py-6">Generate a board from the AI actions tab.</p>}
            {board && (
              <>
                <h3 className="font-semibold text-slate-900">{board.title}</h3>
                {boardStats && (
                  <div className="grid grid-cols-3 gap-2">
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-2">
                      <p className="text-[0.6rem] uppercase tracking-wide text-slate-500">Tasks</p>
                      <p className="text-sm font-semibold text-slate-800">{boardStats.total}</p>
                    </div>
                    <div className="rounded-md border border-emerald-200 bg-emerald-50 p-2">
                      <p className="text-[0.6rem] uppercase tracking-wide text-emerald-700">Done</p>
                      <p className="text-sm font-semibold text-emerald-800">{boardStats.done}</p>
                    </div>
                    <div className="rounded-md border border-amber-200 bg-amber-50 p-2">
                      <p className="text-[0.6rem] uppercase tracking-wide text-amber-700">High prio</p>
                      <p className="text-sm font-semibold text-amber-800">{boardStats.high}</p>
                    </div>
                  </div>
                )}
                <div className="rounded-md border border-slate-200 p-2 bg-white">
                  <label className="text-[0.6rem] uppercase tracking-wide text-slate-500">Filter assignee</label>
                  <select
                    className="mt-1 w-full rounded border px-2 py-1.5 text-[0.7rem]"
                    value={boardAssigneeFilter}
                    onChange={e => setBoardAssigneeFilter(e.target.value)}
                  >
                    <option value="all">All owners</option>
                    {contacts.map(c => (
                      <option key={c.contact_id} value={c.contact_id}>
                        {c.display_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-1 gap-2">
                  {board.columns.map(col => (
                    <div key={col.id} className="rounded-lg border border-slate-200 p-2 bg-slate-50/80">
                      <p className="text-[0.65rem] font-bold uppercase text-slate-500 mb-2">{col.title}</p>
                      {board.tasks
                        .filter(t => (t.column_id ?? 'backlog') === col.id || (!t.column_id && col.id === 'backlog'))
                        .filter(t => boardAssigneeFilter === 'all' || t.assignee_contact_id === boardAssigneeFilter)
                        .map(t => {
                          const assignee = contacts.find(c => c.contact_id === t.assignee_contact_id)
                          const priority = (t.priority ?? 'medium').toLowerCase()
                          const priorityColor = priority === 'high'
                            ? 'text-rose-700 bg-rose-50 border-rose-200'
                            : priority === 'low'
                              ? 'text-slate-600 bg-slate-100 border-slate-200'
                              : 'text-amber-700 bg-amber-50 border-amber-200'
                          return (
                            <div key={t.id} className="mb-2 p-2 rounded bg-white border border-slate-100 shadow-sm">
                              <p className="font-medium text-slate-800">{t.title}</p>
                              <div className="mt-1 flex items-center gap-1.5 flex-wrap">
                                <span className={`text-[0.58rem] uppercase tracking-wide border rounded px-1.5 py-0.5 ${priorityColor}`}>
                                  {priority}
                                </span>
                                {t.due_date ? (
                                  <span className="text-[0.6rem] text-slate-600">
                                    Due: {t.due_date}
                                  </span>
                                ) : null}
                              </div>
                              {t.description ? (
                                <p className="text-[0.65rem] text-slate-600 mt-1">{t.description}</p>
                              ) : null}
                              {assignee && (
                                <p className="text-[0.65rem] text-slate-500 mt-1">{assignee.display_name}</p>
                              )}
                              {Array.isArray((t as Record<string, unknown>).dependencies) && ((t as Record<string, unknown>).dependencies as string[]).length ? (
                                <p className="text-[0.6rem] text-slate-500 mt-1">
                                  Depends on: {((t as Record<string, unknown>).dependencies as string[]).join(', ')}
                                </p>
                              ) : null}
                              <div className="mt-2 flex items-center gap-1">
                                <button
                                  type="button"
                                  className="inline-flex items-center gap-1 rounded border border-slate-200 px-1.5 py-0.5 text-[0.6rem] text-slate-600 hover:bg-slate-50"
                                  onClick={() => moveTask(t.id, -1)}
                                >
                                  <ArrowLeft size={11} />
                                  Prev
                                </button>
                                <button
                                  type="button"
                                  className="inline-flex items-center gap-1 rounded border border-slate-200 px-1.5 py-0.5 text-[0.6rem] text-slate-600 hover:bg-slate-50"
                                  onClick={() => moveTask(t.id, 1)}
                                >
                                  Next
                                  <ArrowRight size={11} />
                                </button>
                              </div>
                            </div>
                          )
                        })}
                    </div>
                  ))}
                </div>
                {board.evidence?.length ? (
                  <div className="rounded-md border border-sky-200 bg-sky-50 p-2">
                    <p className="text-[0.6rem] font-semibold uppercase tracking-wide text-sky-700">Attached evidence</p>
                    <ul className="mt-1 space-y-1 text-[0.65rem] text-sky-900">
                      {board.evidence.slice(0, 8).map((e, i) => (
                        <li key={`${e.pin_id ?? 'e'}-${i}`}>• {e.label ?? e.pin_id ?? 'Pinned view'}{e.route ? ` (${e.route})` : ''}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                <button
                  type="button"
                  className="w-full py-2 rounded border border-slate-200 text-xs"
                  onClick={() => {
                    const md = board.tasks.map(t => `- [ ] ${t.title} (${t.priority ?? 'medium'} · ${t.column_id ?? 'backlog'})`).join('\n')
                    void navigator.clipboard.writeText(`# ${board.title}\n\n${md}`)
                    ctx.setToast('Board copied as Markdown')
                  }}
                >
                  Copy as Markdown
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {ctx.toast && (
        <div className="shrink-0 px-3 py-2 text-[0.65rem] bg-slate-800 text-white text-center">{ctx.toast}</div>
      )}
    </div>

      {pinPickerOpen && (
        <PinTablePicker
          candidates={ctx.listTableCandidates()}
          onClose={() => setPinPickerOpen(false)}
          onConfirm={(id, mode) => void handleConfirmPin(id, mode)}
        />
      )}

      {pinChartPickerOpen && (
        <PinChartPicker
          candidates={ctx.listChartCandidates()}
          onClose={() => setPinChartPickerOpen(false)}
          onConfirm={(id, mode) => void handleConfirmPinChart(id, mode)}
        />
      )}

      {selectedPin && bundle && (
        <PinDetailModal
          pin={selectedPin}
          notes={bundle.notes}
          onClose={() => setSelectedPin(null)}
          onOpenExpertSession={id => ctx.setRequestOpenExpertSession(id)}
        />
      )}
    </>
  )
}
