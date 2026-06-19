import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type ActionBoard,
  type ActionNote,
  type ActionNotePin,
  type ActionNoteSession,
  type ActionNoteSessionBundle,
  type ViewPinSnapshot,
} from '../../lib/api'
import { authorScope } from './appUserProfile'

const LS_SESSION = 'finssentials.actionNotes.activeSessionId.v1'
const LS_FALLBACK = 'finssentials.actionNotes.fallback.v1'

type FallbackStore = {
  session: ActionNoteSession
  notes: ActionNote[]
  pins: ActionNotePin[]
  boards: Array<{ board_id: string; title: string; board_json: ActionBoard }>
}

function loadFallback(): FallbackStore | null {
  try {
    const raw = localStorage.getItem(LS_FALLBACK)
    return raw ? (JSON.parse(raw) as FallbackStore) : null
  } catch {
    return null
  }
}

function saveFallback(store: FallbackStore) {
  localStorage.setItem(LS_FALLBACK, JSON.stringify(store))
}

function newId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 12)}`
}

export function useActionNotesSession(title: string, route: string, filters: Record<string, unknown>) {
  const [bundle, setBundle] = useState<ActionNoteSessionBundle | null>(null)
  const [loading, setLoading] = useState(true)
  const [apiOk, setApiOk] = useState(true)

  const refresh = useCallback(async (sessionId: string) => {
    try {
      const b = await api.actionNotesGetSession(sessionId)
      setBundle(b)
      setApiOk(true)
      return b
    } catch {
      setApiOk(false)
      const fb = loadFallback()
      if (fb) setBundle({ session: fb.session, notes: fb.notes, pins: fb.pins, boards: fb.boards })
      return fb ? { session: fb.session, notes: fb.notes, pins: fb.pins, boards: fb.boards } : null
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    async function init() {
      setLoading(true)
      const scope = authorScope()
      let sessionId = localStorage.getItem(LS_SESSION)
      try {
        if (sessionId) {
          const b = await api.actionNotesGetSession(sessionId)
          if (!cancelled) {
            setBundle(b)
            setApiOk(true)
            setLoading(false)
            return
          }
        }
        const created = await api.actionNotesCreateSession({
          author_scope: scope,
          title,
          route,
          filters,
        })
        sessionId = created.session_id
        localStorage.setItem(LS_SESSION, sessionId)
        const b = await api.actionNotesGetSession(sessionId)
        if (!cancelled) {
          setBundle(b)
          setApiOk(true)
        }
      } catch {
        setApiOk(false)
        let fb = loadFallback()
        if (!fb) {
          fb = {
            session: {
              session_id: newId('sess'),
              author_scope: scope,
              title,
              route,
              filters_json: filters,
              status: 'open',
            },
            notes: [],
            pins: [],
            boards: [],
          }
          saveFallback(fb)
        }
        if (!cancelled) setBundle({ session: fb.session, notes: fb.notes, pins: fb.pins, boards: fb.boards })
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void init()
    return () => {
      cancelled = true
    }
  }, [title, route, JSON.stringify(filters)])

  const persistFallback = useCallback((next: ActionNoteSessionBundle) => {
    saveFallback({
      session: next.session,
      notes: next.notes,
      pins: next.pins,
      boards: next.boards,
    })
    setBundle(next)
  }, [])

  const addNote = useCallback(
    async (body: string) => {
      if (!bundle) return
      const sid = bundle.session.session_id
      try {
        const note = await api.actionNotesAddNote(sid, { body })
        setBundle(prev => (prev ? { ...prev, notes: [...prev.notes, note] } : prev))
      } catch {
        const note: ActionNote = {
          note_id: newId('note'),
          session_id: sid,
          body,
          is_done: false,
          sort_order: bundle.notes.length,
        }
        persistFallback({ ...bundle, notes: [...bundle.notes, note] })
      }
    },
    [bundle, persistFallback],
  )

  const toggleNote = useCallback(
    async (noteId: string, is_done: boolean) => {
      if (!bundle) return
      const sid = bundle.session.session_id
      try {
        await api.actionNotesPatchNote(sid, noteId, { is_done })
        setBundle(prev =>
          prev
            ? { ...prev, notes: prev.notes.map(n => (n.note_id === noteId ? { ...n, is_done } : n)) }
            : prev,
        )
      } catch {
        persistFallback({
          ...bundle,
          notes: bundle.notes.map(n => (n.note_id === noteId ? { ...n, is_done } : n)),
        })
      }
    },
    [bundle, persistFallback],
  )

  const addPin = useCallback(
    async (snapshot: ViewPinSnapshot, label?: string) => {
      if (!bundle) return
      const sid = bundle.session.session_id
      try {
        const pin = await api.actionNotesAddPin(sid, { label, snapshot })
        setBundle(prev => (prev ? { ...prev, pins: [pin, ...prev.pins] } : prev))
      } catch {
        const pin: ActionNotePin = {
          pin_id: newId('pin'),
          session_id: sid,
          label,
          snapshot_json: snapshot,
        }
        persistFallback({ ...bundle, pins: [pin, ...bundle.pins] })
      }
    },
    [bundle, persistFallback],
  )

  return {
    bundle,
    loading,
    apiOk,
    refresh,
    addNote,
    toggleNote,
    addPin,
    sessionId: bundle?.session.session_id,
  }
}
