/**
 * useFddBot — React hook for communicating with the Rasa REST channel.
 *
 * Manages conversation history, sender_id (persisted in localStorage),
 * and provides a send() function that posts to Rasa and returns bot messages.
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { getApiBaseUrl } from '../../lib/api'
import { CARD_REDO_ACTION } from './cardRedoActions'
import { pollScriptJob, type ScriptJobPayload } from './scriptJobPoller'
import {
  createEmptyProject,
  deleteProjectById,
  ensureMigratedProjects,
  getProject,
  listProjectSummaries,
  setActiveProjectId,
  upsertProject,
  type FddProjectRecord,
  type FddProjectSnapshot,
  type FddProjectSummary,
} from './fddProjectStore'

// Default /rasa is proxied by Vite to 127.0.0.1:5005 (see vite.config.ts). Override with VITE_RASA_URL if needed.
const RASA_URL = (import.meta.env.VITE_RASA_URL as string | undefined)?.trim() || '/rasa'

// ─── Types ────────────────────────────────────────────────────────────────────

export type MessageRole = 'user' | 'bot'

export interface ChatMessage {
  id: string
  role: MessageRole
  text?: string
  custom?: AdaptiveCardPayload
  timestamp: Date
}

export interface AdaptiveCardInput {
  id: string
  type:
    | 'text'
    | 'dropdown'
    | 'radio'
    | 'multi_select'
    | 'number'
    | 'month_picker'
    | 'day_picker'
    | 'file_drop'
    | 'date_picker'
    | 'susa_grid'
    | 'folder_picker'
    | 'folder_drop'
    | 'sortable_list'
  /** susa_grid: one .xlsx per cell vs folder of 12 monthly workbooks */
  grid_mode?: 'single_file' | 'folder'
  /** number inputs: optional min/max (browser native steppers) */
  min?: number
  max?: number
  label: string
  placeholder?: string
  /** Muted helper line shown between label and control */
  hint?: string
  default?: string
  options?: { label: string; value: string; optional?: boolean }[]
  required?: boolean
  accept?: string
  entity_count?: number
  /** In compact cards: 2 = full width row in 2-column grid */
  span?: 1 | 2
  /** Consecutive inputs with the same key render in one horizontal row */
  rowGroup?: string
  /** For type radio: label left, options right */
  layout?: 'default' | 'split'
  /** Show this input only when condition(s) match (client-side). One object or all must match. */
  showWhen?: { field: string; value: string } | { field: string; value: string }[]
  /** Omit from UI but still submit default with the card (e.g. ltm_month on review). */
  hidden?: boolean
}

export interface FilterInfo {
  label: string
  value: string
  actions: { label: string; value: string; card?: string }[]
}

export interface AdaptiveCardPayload {
  type: 'adaptive_card'
  card: string
  title: string
  subtitle?: string
  review_text?: string
  inputs?: AdaptiveCardInput[]
  filter_info?: FilterInfo
  submit_label?: string
  download_template?: string
  card_on_yes?: string
  card_on_no?: string
  next_card?: string
  /** Tighter layout (2-column grid where inputs set span) */
  compact?: boolean
  /** Full width of chat column (uses compact grid without narrow max-width) */
  wide?: boolean
  /** file_attachment card: downloadable output workbook */
  filename?: string
  download_url?: string
  /** Optional footnote under susa_grid */
  susa_grid_note?: string
  /** databook_susa_column_mapper: session_id, preview_file_id, layout, etc. */
  mapper_meta?: Record<string, unknown>
  /** Secondary action button (e.g. AP/AR not identifiable) */
  secondary_submit_label?: string
  secondary_submit_id?: string
  /** script_job card: async run metadata (not rendered as a form) */
  run_id?: string
  session_id?: string
  script_key?: string
  /** file_attachment: compact background notification */
  notification?: boolean
}

/** One undo step: tracker snapshot before that card submit + which user bubble to trim. */
export interface UndoStackFrame {
  slots: Record<string, unknown>
  redoAction: string
  userMessageId: string
}

export interface FddSendOptions {
  /** If true, do not add a user bubble (bootstrap / preload). */
  skipUserBubble?: boolean
  /** Override text shown in the user bubble for card submits */
  userBubbleText?: string
  /** Stable id for the user bubble (used to trim chat on undo). */
  userMessageId?: string
}

interface RasaMessage {
  text?: string
  custom?: AdaptiveCardPayload
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function makeId(): string {
  return Math.random().toString(36).slice(2, 10)
}

function messagesFromSnapshot(snapshot: FddProjectSnapshot): ChatMessage[] {
  return (snapshot.messages ?? []).map(m => ({
    id: m.id,
    role: m.role,
    text: m.text,
    custom: m.custom as AdaptiveCardPayload | undefined,
    timestamp: new Date(m.timestamp),
  }))
}

function buildSnapshot(
  messages: ChatMessage[],
  datesContext: FddDatesContext | null,
  uploadContext: FddUploadContext | null,
  columnRoles: Record<string, string>,
  firstFy: string | null,
  submittedCardIds: Set<string>,
  undoStack: UndoStackFrame[],
): FddProjectSnapshot {
  return {
    messages: messages.map(m => ({
      id: m.id,
      role: m.role,
      text: m.text,
      custom: m.custom as Record<string, unknown> | undefined,
      timestamp: m.timestamp.toISOString(),
    })),
    datesContext,
    uploadContext,
    columnRoles,
    firstFy,
    submittedCardIds: [...submittedCardIds],
    undoStack: undoStack.map(f => ({ ...f })),
  }
}

/** Semantic column roles shared across GST / PVM / TOP / Bubble (mirrors actions.py). */
const COLUMN_SLOT_TO_ROLE: Record<string, string> = {
  gst_revenue_col: 'revenue',
  pvm_revenue_col: 'revenue',
  top_value_col: 'revenue',
  bs_revenue_col: 'revenue',
  gst_invoice_col: 'invoice',
  pvm_invoice_col: 'invoice',
  top_invoice_col: 'invoice',
  bs_invoice_col: 'invoice',
  gst_start_col: 'start',
  top_start_col: 'start',
  bs_start_col: 'start',
  gst_end_col: 'end',
  top_end_col: 'end',
  bs_end_col: 'end',
  gst_cost_col: 'cost',
  pvm_cost_col: 'cost',
  bs_cogs_col: 'cost',
  gst_profit_col: 'profit',
  pvm_profit_col: 'profit',
  bs_profit_col: 'profit',
  pvm_quantity_col: 'quantity',
  top_col: 'dimension',
  pvm_group_col: 'dimension',
  gst_group_col_1: 'dimension',
  bs_group_col_1: 'dimension',
  opos_partner_id_col: 'partner_id',
  opos_partner_name_col: 'partner_name',
  opos_amount_col: 'amount',
  opos_due_date_col: 'due_date',
}

const COLUMN_CARD_IDS = new Set([
  'gst_columns',
  'pvm_columns',
  'top_columns',
  'opos_columns',
  'opos_snapshots',
  'fa_rollf_columns',
  'bs_value_columns',
  'bs_period_calc',
  'top_labels',
  'pvm_group',
  'gst_groups',
  'bs_groups',
])

function rememberColumnRolesFromPayload(
  ref: { current: Record<string, string> },
  payload: Record<string, unknown>,
): void {
  const next = { ...ref.current }
  for (const [slot, role] of Object.entries(COLUMN_SLOT_TO_ROLE)) {
    const raw = payload[slot]
    if (raw === null || raw === undefined || String(raw).trim() === '') continue
    next[role] = String(raw).trim()
  }
  ref.current = next
}

async function fetchTrackerSlots(senderId: string): Promise<Record<string, unknown>> {
  try {
    const resp = await fetch(`${RASA_URL}/conversations/${encodeURIComponent(senderId)}/tracker`)
    if (!resp.ok) return {}
    const data = (await resp.json()) as { slots?: Record<string, unknown> }
    const out: Record<string, unknown> = {}
    const slots = data.slots ?? {}
    // Rasa 3.6 returns slots as a flat dict {name: value}; older payload variants
    // wrap each entry as {value: ...}. Handle both robustly.
    for (const [key, meta] of Object.entries(slots)) {
      if (meta !== null && typeof meta === 'object' && 'value' in (meta as object)) {
        out[key] = (meta as { value: unknown }).value
      } else {
        out[key] = meta
      }
    }
    return out
  } catch {
    return {}
  }
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

export interface FddUploadContext {
  file_id: string
  session_id: string
  file_path?: string
  sheet_names?: string[]
  headers?: string[]
  sheet_name?: string
}

export interface FddDatesContext {
  ltm_month: string
  fy_end_month: string
  fy_end_day: number
  first_fy?: number
}

export function useFddBot() {
  const initialProjectRef = useRef<FddProjectRecord>(ensureMigratedProjects())
  const senderId = useRef<string>(initialProjectRef.current.senderId)
  const firstFyRef = useRef<string | null>(initialProjectRef.current.snapshot.firstFy)
  /** Last successful FastAPI upload for this chat session (survives card submits). */
  const uploadContextRef = useRef<FddUploadContext | null>(
    initialProjectRef.current.snapshot.uploadContext,
  )
  /** Date Settings from the revenue-databook flow (same card for all script runs). */
  const datesContextRef = useRef<FddDatesContext | null>(
    initialProjectRef.current.snapshot.datesContext,
  )
  /** Shared column picks by semantic role (revenue, invoice, …) across strands. */
  const columnRolesContextRef = useRef<Record<string, string>>(
    { ...initialProjectRef.current.snapshot.columnRoles },
  )
  const undoStack = useRef<UndoStackFrame[]>(
    [...(initialProjectRef.current.snapshot.undoStack ?? [])],
  )
  const [activeProjectId, setActiveProjectIdState] = useState(initialProjectRef.current.id)
  const [projectName, setProjectName] = useState(initialProjectRef.current.name)
  const [projects, setProjects] = useState<FddProjectSummary[]>(() => listProjectSummaries())
  const [sessionEpoch, setSessionEpoch] = useState(0)
  const [messages, setMessages] = useState<ChatMessage[]>(() =>
    messagesFromSnapshot(initialProjectRef.current.snapshot),
  )
  const [loading, setLoading] = useState(false)
  /** Typing indicator while async revenue scripts poll (separate from Rasa webhook loading). */
  const [scriptJobLoading, setScriptJobLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [undoStackLen, setUndoStackLen] = useState(
    () => initialProjectRef.current.snapshot.undoStack?.length ?? 0,
  )
  const [submittedCardIds, setSubmittedCardIds] = useState<Set<string>>(
    () => new Set(initialProjectRef.current.snapshot.submittedCardIds ?? []),
  )
  /** Abort in-flight Rasa webhook calls on undo; ignore stale responses after undo/new send. */
  const inflightAbort = useRef<AbortController | null>(null)
  const sendGeneration = useRef(0)
  /** Background script jobs keyed by run_id (not cancelled on undo). */
  const activeScriptJobs = useRef<Set<string>>(new Set())

  const markCardSubmitted = useCallback((cardMessageId: string) => {
    setSubmittedCardIds(prev => new Set(prev).add(cardMessageId))
  }, [])

  const addMessage = useCallback((msg: Omit<ChatMessage, 'id' | 'timestamp'> & { id?: string }) => {
    setMessages(prev => [
      ...prev,
      { ...msg, id: msg.id ?? makeId(), timestamp: new Date() },
    ])
  }, [])

  const startScriptJob = useCallback(
    (payload: AdaptiveCardPayload) => {
      const runId = payload.run_id
      const session = payload.session_id
      const scriptKey = payload.script_key
      if (!runId || !session || !scriptKey) return
      if (activeScriptJobs.current.has(runId)) return
      activeScriptJobs.current.add(runId)

      const job: ScriptJobPayload = {
        run_id: runId,
        session_id: session,
        script_key: scriptKey,
        title: payload.title,
      }

      void pollScriptJob(job, { addMessage, setScriptJobLoading }).finally(() => {
        activeScriptJobs.current.delete(runId)
      })
    },
    [addMessage],
  )

  const rememberFirstFy = useCallback((payload: Record<string, unknown>) => {
    const raw = payload.first_fy ?? payload.pvm_first_fy_override ?? payload.gst_first_fy_override
    if (raw === null || raw === undefined || raw === '') return
    const n = Number(String(raw).replace(',', '.'))
    if (Number.isFinite(n)) firstFyRef.current = String(Math.trunc(n))
  }, [])

  const withRememberedFirstFy = useCallback((custom: AdaptiveCardPayload): AdaptiveCardPayload => {
    const fy = firstFyRef.current
    if (!fy || !custom.inputs?.length) return custom
    const firstFyInputIds = new Set(['pvm_first_fy_override', 'gst_first_fy_override'])
    const inputs = custom.inputs.map(inp => {
      if (!firstFyInputIds.has(inp.id)) return inp
      return { ...inp, default: fy, placeholder: inp.placeholder ?? fy }
    })
    return { ...custom, inputs }
  }, [])

  const withRememberedColumnDefaults = useCallback((custom: AdaptiveCardPayload): AdaptiveCardPayload => {
    if (!custom.inputs?.length || !COLUMN_CARD_IDS.has(custom.card)) return custom
    const roles = columnRolesContextRef.current
    if (!Object.keys(roles).length) return custom
    const inputs = custom.inputs.map(inp => {
      const role = COLUMN_SLOT_TO_ROLE[inp.id]
      if (!role) return inp
      const remembered = roles[role]
      if (!remembered) return inp
      if (inp.default && String(inp.default).trim() !== '') return inp
      return { ...inp, default: remembered }
    })
    return { ...custom, inputs }
  }, [])

  const enrichBotCard = useCallback(
    (custom: AdaptiveCardPayload): AdaptiveCardPayload =>
      withRememberedColumnDefaults(withRememberedFirstFy(custom)),
    [withRememberedFirstFy, withRememberedColumnDefaults],
  )

  /** Send a message payload to Rasa and append bot responses. Returns false on failure. */
  const send = useCallback(
    async (payload: Record<string, unknown> | string, options?: FddSendOptions): Promise<boolean> => {
      let messageText: string
      let displayText: string

      if (typeof payload === 'string') {
        messageText = payload
        displayText = payload
      } else {
        rememberFirstFy(payload)
        if (typeof payload === 'object' && payload !== null && !Array.isArray(payload)) {
          rememberColumnRolesFromPayload(columnRolesContextRef, payload as Record<string, unknown>)
        }
        const ctx = uploadContextRef.current
        const datesCtx = datesContextRef.current
        const enriched: Record<string, unknown> = { ...payload }
        if (datesCtx?.ltm_month && !enriched.ltm_month) {
          enriched.ltm_month = datesCtx.ltm_month
        }
        if (datesCtx?.fy_end_month != null && enriched.fy_end_month == null) {
          enriched.fy_end_month = datesCtx.fy_end_month
        }
        if (datesCtx?.fy_end_day != null && enriched.fy_end_day == null) {
          enriched.fy_end_day = datesCtx.fy_end_day
        }
        if (datesCtx?.first_fy != null && enriched.first_fy == null) {
          enriched.first_fy = datesCtx.first_fy
        }
        if (ctx?.file_id && !enriched.file_id) {
          enriched.file_id = ctx.file_id
        }
        if (ctx?.session_id && !enriched.session_id) {
          enriched.session_id = ctx.session_id
        }
        if (ctx?.file_path && !enriched.file_path) {
          enriched.file_path = ctx.file_path
        }
        if (ctx?.sheet_names?.length && !enriched.sheet_names) {
          enriched.sheet_names = ctx.sheet_names
        }
        if (ctx?.headers?.length && !enriched.headers) {
          enriched.headers = ctx.headers
        }
        if (ctx?.sheet_name && !enriched.sheet_name) {
          enriched.sheet_name = ctx.sheet_name
        }
        if (enriched.ltm_month) {
          const ltm = String(enriched.ltm_month)
          const prev = datesContextRef.current
          const fem =
            enriched.fy_end_month != null
              ? String(enriched.fy_end_month)
              : (prev?.fy_end_month ?? '12')
          const fed =
            enriched.fy_end_day != null
              ? Number(enriched.fy_end_day)
              : (prev?.fy_end_day ?? 31)
          const ffyRaw = enriched.first_fy ?? prev?.first_fy
          const ffy =
            ffyRaw != null && ffyRaw !== '' ? Number(ffyRaw) : undefined
          datesContextRef.current = {
            ltm_month: ltm,
            fy_end_month: fem,
            fy_end_day: Number.isFinite(fed) ? fed : 31,
            ...(ffy != null && Number.isFinite(ffy) ? { first_fy: ffy } : {}),
          }
        }
        const fid = enriched.file_id
        const sid = enriched.session_id
        if (fid && sid) {
          const hdrs = enriched.headers
          uploadContextRef.current = {
            file_id: String(fid),
            session_id: String(sid),
            file_path: enriched.file_path ? String(enriched.file_path) : ctx?.file_path,
            sheet_names: Array.isArray(enriched.sheet_names)
              ? (enriched.sheet_names as string[])
              : ctx?.sheet_names,
            headers: Array.isArray(hdrs)
              ? (hdrs as string[])
              : ctx?.headers,
            sheet_name: enriched.sheet_name
              ? String(enriched.sheet_name)
              : ctx?.sheet_name,
          }
        }
        if (Array.isArray(enriched.headers) && (enriched.headers as string[]).length) {
          const prev = uploadContextRef.current
          uploadContextRef.current = {
            file_id: prev?.file_id ?? String(enriched.file_id ?? ''),
            session_id: prev?.session_id ?? senderId.current,
            file_path: prev?.file_path,
            sheet_names: prev?.sheet_names,
            headers: enriched.headers as string[],
            sheet_name: enriched.sheet_name
              ? String(enriched.sheet_name)
              : prev?.sheet_name,
          }
        }
        const payloadJson = JSON.stringify(enriched)
        messageText = `/submit_card{"card_payload": ${payloadJson}}`
        displayText = options?.userBubbleText ?? (payload as { submit_label?: string }).submit_label ?? 'Submitted'
      }

      inflightAbort.current?.abort()
      const controller = new AbortController()
      inflightAbort.current = controller
      const generation = ++sendGeneration.current

      setLoading(true)
      setError(null)

      const userMsgId = options?.userMessageId ?? makeId()
      if (!options?.skipUserBubble) {
        addMessage({ role: 'user', text: displayText, id: userMsgId })
      }

      try {
        const resp = await fetch(`${RASA_URL}/webhooks/rest/webhook`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: JSON.stringify({
            sender: senderId.current,
            message: messageText,
          }),
        })

        if (generation !== sendGeneration.current) {
          return false
        }

        if (!resp.ok) {
          throw new Error(`Rasa responded ${resp.status}`)
        }

        const data: RasaMessage[] = await resp.json()

        const DEFAULT_FALLBACK_SUB = "didn't quite understand"
        const hasCustom = data.some(m => m.custom)
        const filtered = hasCustom
          ? data.filter(m => !(m.text && m.text.includes(DEFAULT_FALLBACK_SUB)))
          : data

        for (const msg of filtered) {
          if (msg.custom?.card === 'script_job') {
            startScriptJob(msg.custom)
            continue
          }
          if (msg.custom) {
            addMessage({ role: 'bot', custom: enrichBotCard(msg.custom) })
          } else if (msg.text) {
            addMessage({ role: 'bot', text: msg.text })
          }
        }
        return true
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          return false
        }
        if (generation !== sendGeneration.current) {
          return false
        }
        const msg = err instanceof Error ? err.message : 'Network error'
        setError(msg)
        addMessage({ role: 'bot', text: `⚠ Could not reach the FDD Bot: ${msg}` })
        return false
      } finally {
        if (inflightAbort.current === controller) {
          inflightAbort.current = null
        }
        if (generation === sendGeneration.current) {
          setLoading(false)
        }
      }
    },
    [addMessage, rememberFirstFy, enrichBotCard, startScriptJob],
  )

  /** Upload an XLSX file to FastAPI and return the file_id + headers. */
  const uploadFile = useCallback(
    async (
      file: File,
    ): Promise<{
      file_id: string
      file_path: string
      headers: string[]
      sheet_names?: string[]
      session_id: string
    } | null> => {
      setLoading(true)
      try {
        const form = new FormData()
        form.append('file', file)
        form.append('session_id', senderId.current)

        const resp = await fetch(`${getApiBaseUrl()}/api/v1/fdd/upload`, {
          method: 'POST',
          body: form,
        })

        if (!resp.ok) throw new Error(`Upload failed: ${resp.status}`)
        const data = await resp.json()
        const row = { ...data, session_id: senderId.current }
        uploadContextRef.current = {
          file_id: row.file_id,
          session_id: row.session_id,
          file_path: row.file_path,
          sheet_names: row.sheet_names,
          headers: row.headers?.length ? row.headers : undefined,
        }
        addMessage({ role: 'bot', text: `✓ ${file.name} hochgeladen — bereit für den nächsten Schritt.` })
        return row
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Upload error'
        setError(msg)
        addMessage({ role: 'bot', text: `⚠ File upload failed: ${msg}` })
        return null
      } finally {
        setLoading(false)
      }
    },
    [addMessage],
  )

  /** Submit an adaptive card's filled values back to Rasa. */
  const submitCard = useCallback(
    async (
      cardName: string,
      values: Record<string, unknown>,
      options?: FddSendOptions & { submitLabel?: string },
    ): Promise<boolean> => {
      const { submitLabel, ...sendOpts } = options ?? {}
      const slotsBefore = await fetchTrackerSlots(senderId.current)
      const redoAction = CARD_REDO_ACTION[cardName] ?? 'action_listen'
      const userMsgId = sendOpts.userMessageId ?? makeId()
      const payload = { card: cardName, ...values }
      const ok = await send(payload, {
        ...sendOpts,
        userBubbleText: sendOpts.userBubbleText ?? submitLabel,
        userMessageId: userMsgId,
      })
      if (ok && !sendOpts.skipUserBubble) {
        undoStack.current.push({ slots: slotsBefore, redoAction, userMessageId: userMsgId })
        setUndoStackLen(undoStack.current.length)
      }
      if (ok && cardName === 'main_welcome') {
        const pn = values.project_name
        if (pn != null && String(pn).trim()) {
          setProjectName(String(pn).trim())
        }
      }
      return ok
    },
    [addMessage, send],
  )

  /** Undo the last card submit only (one step). Tracker is restored to the snapshot before that submit. */
  const undoLastCard = useCallback(async () => {
    const frame = undoStack.current.pop()
    if (!frame) return
    setUndoStackLen(undoStack.current.length)

    const { slots, redoAction, userMessageId } = frame

    inflightAbort.current?.abort()
    sendGeneration.current += 1

    setMessages(prev => {
      const idx = prev.findIndex(m => m.id === userMessageId)
      if (idx < 0) {
        undoStack.current.push(frame)
        setUndoStackLen(undoStack.current.length)
        return prev
      }
      let cut = idx
      return prev.slice(0, cut)
    })
    setSubmittedCardIds(new Set())

    const undoBody = {
      slots,
      next_action: redoAction,
    }
    const controller = new AbortController()
    inflightAbort.current = controller
    const generation = sendGeneration.current

    setLoading(true)
    setError(null)
    try {
      const resp = await fetch(`${RASA_URL}/webhooks/rest/webhook`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          sender: senderId.current,
          message: `/undo_to_checkpoint${JSON.stringify(undoBody)}`,
        }),
      })
      if (generation !== sendGeneration.current) return
      if (!resp.ok) throw new Error(`Rasa responded ${resp.status}`)
      const data: RasaMessage[] = await resp.json()
      const DEFAULT_FALLBACK_SUB = "didn't quite understand"
      const hasCustom = data.some(m => m.custom)
      const filtered = hasCustom
        ? data.filter(m => !(m.text && m.text.includes(DEFAULT_FALLBACK_SUB)))
        : data
      for (const msg of filtered) {
        if (msg.custom) {
          addMessage({ role: 'bot', custom: enrichBotCard(msg.custom) })
        } else if (msg.text) {
          addMessage({ role: 'bot', text: msg.text })
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') return
      if (generation !== sendGeneration.current) return
      const msg = err instanceof Error ? err.message : 'Network error'
      setError(msg)
      addMessage({ role: 'bot', text: `⚠ Undo failed: ${msg}` })
    } finally {
      if (inflightAbort.current === controller) {
        inflightAbort.current = null
      }
      if (generation === sendGeneration.current) {
        setLoading(false)
      }
    }
  }, [addMessage, enrichBotCard])

  /** Reset the conversation (clears history and tells Rasa to reset). */
  const reset = useCallback(async () => {
    uploadContextRef.current = null
    datesContextRef.current = null
    columnRolesContextRef.current = {}
    setMessages([])
    undoStack.current.length = 0
    setUndoStackLen(0)
    setSubmittedCardIds(new Set())
    setSessionEpoch(e => e + 1)
    await send('reset')
  }, [send])


  const refreshProjectList = useCallback(() => {
    setProjects(listProjectSummaries())
  }, [])

  const saveActiveProject = useCallback(() => {
    const record: FddProjectRecord = {
      id: activeProjectId,
      name: projectName,
      senderId: senderId.current,
      updatedAt: new Date().toISOString(),
      snapshot: buildSnapshot(
        messages,
        datesContextRef.current,
        uploadContextRef.current,
        columnRolesContextRef.current,
        firstFyRef.current,
        submittedCardIds,
        undoStack.current,
      ),
    }
    upsertProject(record)
    setActiveProjectId(activeProjectId)
    refreshProjectList()
  }, [activeProjectId, projectName, messages, submittedCardIds, refreshProjectList])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      saveActiveProject()
    }, 500)
    return () => window.clearTimeout(timer)
  }, [messages, undoStackLen, submittedCardIds, projectName, saveActiveProject])

  const applyProjectRecord = useCallback((record: FddProjectRecord) => {
    senderId.current = record.senderId
    setActiveProjectId(record.id)
    setActiveProjectIdState(record.id)
    setProjectName(record.name)
    firstFyRef.current = record.snapshot.firstFy
    uploadContextRef.current = record.snapshot.uploadContext
    datesContextRef.current = record.snapshot.datesContext
    columnRolesContextRef.current = { ...record.snapshot.columnRoles }
    undoStack.current = [...(record.snapshot.undoStack ?? [])]
    setUndoStackLen(undoStack.current.length)
    setSubmittedCardIds(new Set(record.snapshot.submittedCardIds ?? []))
    setMessages(messagesFromSnapshot(record.snapshot))
    setError(null)
    inflightAbort.current?.abort()
    sendGeneration.current += 1
  }, [])

  const clearSessionState = useCallback(() => {
    uploadContextRef.current = null
    datesContextRef.current = null
    columnRolesContextRef.current = {}
    firstFyRef.current = null
    undoStack.current = []
    setUndoStackLen(0)
    setSubmittedCardIds(new Set())
    setMessages([])
    setError(null)
    inflightAbort.current?.abort()
    sendGeneration.current += 1
  }, [])

  const createProject = useCallback(() => {
    saveActiveProject()
    const project = createEmptyProject()
    upsertProject(project)
    clearSessionState()
    senderId.current = project.senderId
    setActiveProjectId(project.id)
    setActiveProjectIdState(project.id)
    setProjectName(project.name)
    setSessionEpoch(e => e + 1)
    refreshProjectList()
  }, [saveActiveProject, clearSessionState, refreshProjectList])

  const switchProject = useCallback(
    (id: string) => {
      if (id === activeProjectId) return
      saveActiveProject()
      const record = getProject(id)
      if (!record) return
      const hadMessages = (record.snapshot.messages?.length ?? 0) > 0
      applyProjectRecord(record)
      if (!hadMessages) {
        setSessionEpoch(e => e + 1)
      }
      refreshProjectList()
    },
    [activeProjectId, saveActiveProject, applyProjectRecord, refreshProjectList],
  )

  const deleteProject = useCallback(
    (id: string) => {
      if (id !== activeProjectId) {
        deleteProjectById(id)
        refreshProjectList()
        return
      }
      saveActiveProject()
      deleteProjectById(id)
      const remaining = listProjectSummaries()
      if (remaining.length > 0) {
        const record = getProject(remaining[0].id)
        if (record) {
          applyProjectRecord(record)
          if ((record.snapshot.messages?.length ?? 0) === 0) {
            setSessionEpoch(e => e + 1)
          }
          refreshProjectList()
          return
        }
      }
      const project = createEmptyProject()
      upsertProject(project)
      clearSessionState()
      senderId.current = project.senderId
      setActiveProjectId(project.id)
      setActiveProjectIdState(project.id)
      setProjectName(project.name)
      setSessionEpoch(e => e + 1)
      refreshProjectList()
    },
    [activeProjectId, saveActiveProject, applyProjectRecord, clearSessionState, refreshProjectList],
  )

  const newSession = useCallback(() => {
    createProject()
  }, [createProject])

  return {
    messages,
    loading,
    scriptJobLoading,
    error,
    senderId: senderId.current,
    sessionEpoch,
    send,
    submitCard,
    uploadFile,
    reset,
    newSession,
    createProject,
    switchProject,
    deleteProject,
    saveActiveProject,
    activeProjectId,
    projectName,
    projects,
    undoLastCard,
    canUndo: undoStackLen > 0,
    submittedCardIds,
    markCardSubmitted,
  }
}

export type FddBotApi = ReturnType<typeof useFddBot>
