import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import type { ViewPinSnapshot } from '../../lib/api'

export type TableCaptureFn = () => ViewPinSnapshot['table'] | null
export type ExpertCaptureFn = () => ViewPinSnapshot['expert_chat'] | null
export type ChartCaptureFn = () => Promise<ViewPinSnapshot['chart'] | null>

export type TablePinCandidate = {
  id: string
  label: string
  description?: string
  capture: TableCaptureFn
  viewState?: Record<string, unknown>
}

type FilterState = {
  route: string
  year: number
  month: number
  entity: string
  tab?: string
  extra?: Record<string, unknown>
}

export type ChartPinCandidate = {
  id: string
  label: string
  description?: string
  capture: ChartCaptureFn
  viewState?: Record<string, unknown>
}

type ActionNotesContextValue = {
  filters: FilterState
  setFilters: (f: FilterState) => void
  /** @deprecated use registerTableCandidate */
  registerTableCapture: (fn: TableCaptureFn | null) => void
  registerTableCandidate: (candidate: TablePinCandidate | null) => void
  unregisterTableCandidate: (id: string) => void
  listTableCandidates: () => TablePinCandidate[]
  registerChartCandidate: (candidate: ChartPinCandidate | null) => void
  unregisterChartCandidate: (id: string) => void
  listChartCandidates: () => ChartPinCandidate[]
  registerExpertCapture: (fn: ExpertCaptureFn | null) => void
  buildTableSnapshot: (candidateId?: string, restoreMode?: ViewPinSnapshot['restore_mode']) => ViewPinSnapshot | null
  buildChartSnapshot: (candidateId: string, restoreMode?: ViewPinSnapshot['restore_mode']) => Promise<ViewPinSnapshot | null>
  buildExpertSnapshot: () => ViewPinSnapshot | null
  pinTable: (label?: string) => ViewPinSnapshot | null
  pinTableById: (candidateId: string, restoreMode?: ViewPinSnapshot['restore_mode'], label?: string) => ViewPinSnapshot | null
  pinChartById: (candidateId: string, restoreMode?: ViewPinSnapshot['restore_mode'], label?: string) => Promise<ViewPinSnapshot | null>
  pinExpert: (label?: string) => ViewPinSnapshot | null
  toast: string | null
  setToast: (t: string | null) => void
  requestOpenExpertSession: string | null
  setRequestOpenExpertSession: (id: string | null) => void
}

const ActionNotesContext = createContext<ActionNotesContextValue | null>(null)

export function ActionNotesProvider({
  children,
  initialFilters,
}: {
  children: ReactNode
  initialFilters: FilterState
}) {
  const [filters, setFilters] = useState<FilterState>(initialFilters)
  const [toast, setToast] = useState<string | null>(null)
  const [requestOpenExpertSession, setRequestOpenExpertSession] = useState<string | null>(null)
  // Pin candidates live in refs (not state): registering one must NOT change the
  // context value identity, otherwise consumer effects that list `notesCtx` in
  // their deps (the common pin-registration pattern) ping-pong register/unregister
  // and trip React's "Maximum update depth exceeded". Lists are read on-demand
  // (when the pin picker opens), so a re-render on registration is unnecessary.
  const tableCandidatesRef = useRef<Map<string, TablePinCandidate>>(new Map())
  const chartCandidatesRef = useRef<Map<string, ChartPinCandidate>>(new Map())
  const expertCaptureRef = useRef<ExpertCaptureFn | null>(null)

  useEffect(() => {
    setFilters(initialFilters)
  }, [
    initialFilters.route,
    initialFilters.year,
    initialFilters.month,
    initialFilters.entity,
    initialFilters.tab,
  ])

  const registerTableCapture = useCallback((fn: TableCaptureFn | null) => {
    if (!fn) tableCandidatesRef.current.delete('default')
    else tableCandidatesRef.current.set('default', { id: 'default', label: 'Current table', capture: fn })
  }, [])

  const registerTableCandidateImpl = useCallback((candidate: TablePinCandidate | null) => {
    if (!candidate) return
    tableCandidatesRef.current.set(candidate.id, candidate)
  }, [])

  const unregisterTableCandidate = useCallback((id: string) => {
    tableCandidatesRef.current.delete(id)
  }, [])

  const listTableCandidates = useCallback((): TablePinCandidate[] => {
    return [...tableCandidatesRef.current.values()]
  }, [])

  const registerChartCandidate = useCallback((candidate: ChartPinCandidate | null) => {
    if (!candidate) return
    chartCandidatesRef.current.set(candidate.id, candidate)
  }, [])

  const unregisterChartCandidate = useCallback((id: string) => {
    chartCandidatesRef.current.delete(id)
  }, [])

  const listChartCandidates = useCallback((): ChartPinCandidate[] => {
    return [...chartCandidatesRef.current.values()]
  }, [])

  const registerExpertCapture = useCallback((fn: ExpertCaptureFn | null) => {
    expertCaptureRef.current = fn
  }, [])

  const buildTableSnapshot = useCallback(
    (candidateId?: string, restoreMode: ViewPinSnapshot['restore_mode'] = 'navigate'): ViewPinSnapshot | null => {
      const id = candidateId ?? 'default'
      const cand = tableCandidatesRef.current.get(id)
      if (!cand) return null
      const table = cand.capture()
      if (!table) return null
      const searchParts: string[] = []
      if (filters.tab) searchParts.push(`tab=${filters.tab}`)
      const vm = cand.viewState?.view_mode
      if (vm) searchParts.push(`view=${String(vm)}`)
      return {
        pin_type: 'table',
        captured_at: new Date().toISOString(),
        route: filters.route,
        filters: {
          year: filters.year,
          month: filters.month,
          entity: filters.entity,
          tab: filters.tab,
          ...filters.extra,
        },
        view_state: {
          ...cand.viewState,
          search: searchParts.length ? `?${searchParts.join('&')}` : undefined,
        },
        restore_mode: restoreMode,
        table_id: id,
        label: cand.label,
        table,
      }
    },
    [filters],
  )

  const buildExpertSnapshot = useCallback((): ViewPinSnapshot | null => {
    const expert_chat = expertCaptureRef.current?.()
    if (!expert_chat?.messages?.length) return null
    return {
      pin_type: 'expert_chat',
      captured_at: new Date().toISOString(),
      route: filters.route,
      filters: { year: filters.year, month: filters.month, entity: filters.entity, tab: filters.tab },
      restore_mode: 'navigate',
      expert_chat: {
        session_id: expert_chat.session_id,
        messages: expert_chat.messages,
      },
    }
  }, [filters])

  const buildChartSnapshot = useCallback(
    async (candidateId: string, restoreMode: ViewPinSnapshot['restore_mode'] = 'navigate'): Promise<ViewPinSnapshot | null> => {
      const cand = chartCandidatesRef.current.get(candidateId)
      if (!cand) return null
      const chart = await cand.capture()
      if (!chart) return null
      const searchParts: string[] = []
      if (filters.tab) searchParts.push(`tab=${filters.tab}`)
      return {
        pin_type: 'chart',
        captured_at: new Date().toISOString(),
        route: filters.route,
        filters: { year: filters.year, month: filters.month, entity: filters.entity, tab: filters.tab, ...filters.extra },
        view_state: {
          ...cand.viewState,
          search: searchParts.length ? `?${searchParts.join('&')}` : undefined,
        },
        restore_mode: restoreMode,
        label: cand.label,
        chart,
      }
    },
    [filters],
  )

  const pinTableById = useCallback(
    (candidateId: string, restoreMode?: ViewPinSnapshot['restore_mode'], label?: string) => {
      const snap = buildTableSnapshot(candidateId, restoreMode ?? 'navigate')
      if (snap && label) snap.label = label
      return snap
    },
    [buildTableSnapshot],
  )

  const pinTable = useCallback(
    (label?: string) => {
      const snap = buildTableSnapshot('default')
      if (snap) snap.label = label ?? snap.label ?? 'Table view'
      return snap
    },
    [buildTableSnapshot],
  )

  const pinExpert = useCallback(
    (label?: string) => {
      const snap = buildExpertSnapshot()
      if (snap) snap.label = label ?? 'Expert chat'
      return snap
    },
    [buildExpertSnapshot],
  )

  const pinChartById = useCallback(
    async (candidateId: string, restoreMode?: ViewPinSnapshot['restore_mode'], label?: string) => {
      const snap = await buildChartSnapshot(candidateId, restoreMode ?? 'navigate')
      if (snap && label) snap.label = label
      return snap
    },
    [buildChartSnapshot],
  )

  const value = useMemo(
    () => ({
      filters,
      setFilters,
      registerTableCapture,
      registerTableCandidate: registerTableCandidateImpl,
      unregisterTableCandidate,
      listTableCandidates,
      registerChartCandidate,
      unregisterChartCandidate,
      listChartCandidates,
      registerExpertCapture,
      buildTableSnapshot,
      buildChartSnapshot,
      buildExpertSnapshot,
      pinTable,
      pinTableById,
      pinChartById,
      pinExpert,
      toast,
      setToast,
      requestOpenExpertSession,
      setRequestOpenExpertSession,
    }),
    [
      filters,
      registerTableCapture,
      registerTableCandidateImpl,
      unregisterTableCandidate,
      listTableCandidates,
      registerChartCandidate,
      unregisterChartCandidate,
      listChartCandidates,
      registerExpertCapture,
      buildTableSnapshot,
      buildChartSnapshot,
      buildExpertSnapshot,
      pinTable,
      pinTableById,
      pinChartById,
      pinExpert,
      toast,
      requestOpenExpertSession,
    ],
  )

  return <ActionNotesContext.Provider value={value}>{children}</ActionNotesContext.Provider>
}

export function useActionNotesContext(): ActionNotesContextValue {
  const ctx = useContext(ActionNotesContext)
  if (!ctx) throw new Error('useActionNotesContext requires ActionNotesProvider')
  return ctx
}

export function useOptionalActionNotesContext(): ActionNotesContextValue | null {
  return useContext(ActionNotesContext)
}
