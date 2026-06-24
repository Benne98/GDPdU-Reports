/**
 * FddBotPage — Full-page, standalone FDD-Bot interface.
 *
 * Two-phase flow:
 *   1. Choice screen — pick "Pipeline data" or "Upload new data"
 *   2. Unified chat canvas — wide two-column layout:
 *        left  = history sidebar (chats for the current mode only)
 *        right = BotConversation card-canvas with toolbar
 *
 * Both modes use the same canvas design. FddChatPanel is no longer the active
 * render path for upload; its behaviour (preloadedFile bootstrap, autoHello,
 * project sidebar) has been migrated here.
 */

import { useState, useCallback, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Database,
  FileSpreadsheet,
  ArrowLeft,
  Undo2,
  RotateCcw,
  WifiOff,
  Plus,
  X,
  MessageSquare,
} from 'lucide-react'
import { useFddBot } from '../components/fdd-bot/useFddBot'
import BotConversation from '../components/fdd-bot/BotConversation'
import {
  listProjectSummariesByMode,
  type FddProjectMode,
  type FddProjectSummary,
} from '../components/fdd-bot/fddProjectStore'

// ─── Types ────────────────────────────────────────────────────────────────────

type Mode = FddProjectMode  // 'pipeline' | 'upload'

// ─── ChoiceCard ───────────────────────────────────────────────────────────────

interface ChoiceCardProps {
  icon: React.ReactNode
  title: string
  description: string
  badge?: string
  onClick: () => void
  index: number
}

function ChoiceCard({ icon, title, description, badge, onClick, index }: ChoiceCardProps) {
  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, y: 28 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1], delay: 0.1 + index * 0.08 }}
      whileHover={{ y: -3, transition: { duration: 0.18 } }}
      whileTap={{ scale: 0.98 }}
      onClick={onClick}
      className="group relative flex flex-col rounded-2xl overflow-hidden text-left w-full h-full cursor-pointer"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 4px rgba(0,0,0,0.06), 0 6px 24px rgba(0,0,0,0.04)',
        padding: '28px 28px 24px',
      }}
    >
      {/* Top accent gradient */}
      <div
        className="absolute top-0 left-0 right-0 h-[3px] rounded-t-2xl"
        style={{
          background: 'linear-gradient(90deg, transparent 0%, #1E3A5F 40%, #2C5F8A 60%, transparent 100%)',
          opacity: 0.55,
        }}
      />

      {/* Hover glow */}
      <div
        className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
        style={{
          background:
            'radial-gradient(ellipse 65% 55% at 50% 0%, rgba(30,58,95,0.05) 0%, transparent 100%)',
          boxShadow: 'inset 0 0 0 1px rgba(30,58,95,0.10)',
        }}
      />

      {/* Icon box */}
      <div
        className="flex items-center justify-center rounded-xl mb-5 shrink-0"
        style={{
          width: 48,
          height: 48,
          background: 'rgba(30,58,95,0.07)',
          border: '1px solid rgba(30,58,95,0.12)',
        }}
      >
        {icon}
      </div>

      {/* Title row */}
      <div className="flex items-center gap-2 mb-2">
        <h2 className="text-lg font-semibold tracking-tight" style={{ color: '#111827' }}>
          {title}
        </h2>
        {badge && (
          <span
            className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full"
            style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
          >
            {badge}
          </span>
        )}
      </div>

      {/* Description */}
      <p className="text-sm leading-relaxed flex-1" style={{ color: '#475569' }}>
        {description}
      </p>

      {/* CTA row */}
      <div
        className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold transition-all duration-200"
        style={{ color: '#1E3A5F' }}
      >
        Select
        <motion.span
          className="inline-block"
          animate={{ x: 0 }}
          whileHover={{ x: 4 }}
          transition={{ duration: 0.15 }}
        >
          →
        </motion.span>
      </div>
    </motion.button>
  )
}

// ─── ErrorBanner ──────────────────────────────────────────────────────────────

function ErrorBanner() {
  return (
    <div
      className="flex items-start gap-3 rounded-xl px-4 py-3 text-sm"
      style={{
        background: 'rgba(248,250,252,1)',
        border: '1px solid #E2E8F0',
      }}
    >
      <div
        className="flex items-center justify-center rounded-lg shrink-0 mt-0.5"
        style={{
          width: 32,
          height: 32,
          background: 'rgba(30,58,95,0.06)',
          border: '1px solid rgba(30,58,95,0.10)',
        }}
      >
        <WifiOff size={14} style={{ color: '#475569' }} />
      </div>
      <div>
        <p className="font-medium" style={{ color: '#374151' }}>
          Assistant backend not connected
        </p>
        <p className="text-xs mt-0.5 leading-relaxed" style={{ color: '#94A3B8' }}>
          Start FDD Rasa on port 5005 and actions on 5055
          (<code className="text-[10px]">scripts/run-fdd-merge-rasa*.ps1</code>), then reload.
        </p>
      </div>
    </div>
  )
}

// ─── ModeBadge ────────────────────────────────────────────────────────────────

function ModeBadge({ mode }: { mode: Mode }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full text-xs font-semibold px-2.5 py-1"
      style={{
        background: 'rgba(30,58,95,0.09)',
        color: '#1E3A5F',
      }}
    >
      {mode === 'pipeline' ? (
        <Database size={11} aria-hidden />
      ) : (
        <FileSpreadsheet size={11} aria-hidden />
      )}
      {mode === 'pipeline' ? 'Pipeline data' : 'New upload'}
    </span>
  )
}

// ─── ChatHistorySidebar ───────────────────────────────────────────────────────
// Renders only the chats whose mode matches the currently active tile.

interface ChatHistorySidebarProps {
  mode: Mode
  activeProjectId: string
  onNewChat: () => void
  onSelectChat: (id: string) => void
  onDeleteChat: (id: string) => void
  /** Re-render trigger: incremented whenever the project list may have changed */
  refreshKey: number
}

function ChatHistorySidebar({
  mode,
  activeProjectId,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  refreshKey,
}: ChatHistorySidebarProps) {
  const [projects, setProjects] = useState<FddProjectSummary[]>([])

  useEffect(() => {
    setProjects(listProjectSummariesByMode(mode))
  }, [mode, refreshKey])

  return (
    <aside
      className="flex flex-col shrink-0"
      style={{
        width: 240,
        background: '#FFFFFF',
        borderRight: '1px solid #E2E8F0',
        minHeight: 0,
      }}
    >
      {/* New chat button */}
      <div className="p-3 shrink-0">
        <button
          type="button"
          onClick={onNewChat}
          className="flex items-center gap-2 w-full px-3 py-2.5 rounded-lg text-sm font-medium transition-colors"
          style={{
            color: '#1E3A5F',
            border: '1px solid #E2E8F0',
            background: '#F8FAFC',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#EFF6FF' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '#F8FAFC' }}
        >
          <Plus size={15} />
          New chat
        </button>
      </div>

      {/* Section label */}
      <div className="px-4 pb-2 shrink-0">
        <p className="text-[10px] font-bold uppercase tracking-wide" style={{ color: '#94A3B8' }}>
          {mode === 'pipeline' ? 'Pipeline chats' : 'Upload chats'}
        </p>
      </div>

      {/* Chat list */}
      <div className="flex-1 overflow-y-auto px-2 pb-3 min-h-0">
        {projects.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center">
            <MessageSquare size={20} style={{ color: '#CBD5E1' }} />
            <p className="text-xs leading-relaxed" style={{ color: '#94A3B8' }}>
              No chats yet. Start one above.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {projects.map(project => {
              const isActive = project.id === activeProjectId
              const date = new Date(project.updatedAt)
              const dateStr = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
              return (
                <li key={project.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelectChat(project.id)}
                    className="w-full text-left px-3 py-2 pr-8 rounded-lg transition-colors"
                    style={{
                      background: isActive ? 'rgba(30,58,95,0.08)' : 'transparent',
                    }}
                    title={project.name}
                  >
                    <p
                      className="text-sm truncate"
                      style={{
                        color: isActive ? '#1E3A5F' : '#334155',
                        fontWeight: isActive ? 600 : 400,
                      }}
                    >
                      {project.name}
                    </p>
                    <p className="text-[10px] mt-0.5" style={{ color: '#94A3B8' }}>
                      {dateStr}
                    </p>
                  </button>
                  <button
                    type="button"
                    onClick={e => {
                      e.stopPropagation()
                      onDeleteChat(project.id)
                    }}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ color: '#94A3B8' }}
                    aria-label="Delete chat"
                  >
                    <X size={13} />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </aside>
  )
}

// ─── FddBotPage ───────────────────────────────────────────────────────────────

export default function FddBotPage() {
  const bot = useFddBot()
  const [mode, setMode] = useState<Mode | null>(null)
  const [preloadedFile] = useState<File | null>(null)
  // Sidebar refresh counter — bumped after any project mutation so the sidebar re-reads storage.
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0)

  const [rasaUnreachable, setRasaUnreachable] = useState(false)

  useEffect(() => {
    if (bot.error) setRasaUnreachable(true)
  }, [bot.error])

  // Bump the sidebar refresh key whenever bot.projects changes (debounced persist fires).
  useEffect(() => {
    setSidebarRefreshKey(k => k + 1)
  }, [bot.projects])

  const handleSelectMode = useCallback((selected: Mode) => {
    setRasaUnreachable(false)
    setMode(selected)
    // Start a fresh chat in this mode (saves any existing session first).
    bot.createProject(selected)
  // bot.createProject is stable (useCallback with stable deps); exclude bot object itself.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleBack = useCallback(() => {
    setMode(null)
  }, [])

  const handleNewChat = useCallback(() => {
    if (!mode) return
    bot.createProject(mode)
    setSidebarRefreshKey(k => k + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  const handleSelectChat = useCallback((id: string) => {
    // switchProject saves the current session, loads the target record,
    // and bumps sessionEpoch only for empty chats (triggering autoHello).
    // For non-empty chats the transcript is restored without re-greeting.
    bot.switchProject(id)
    setSidebarRefreshKey(k => k + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleDeleteChat = useCallback((id: string) => {
    bot.deleteProject(id)
    setSidebarRefreshKey(k => k + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Page-level header ──────────────────────────────────────────────────────

  const header = (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="mb-8 text-center"
    >
      <p
        className="text-[11px] font-bold uppercase tracking-widest mb-2"
        style={{ color: '#1E3A5F' }}
      >
        FDD-BOT
      </p>
      <h1 className="text-3xl font-bold tracking-tight mb-2" style={{ color: '#111827' }}>
        Financial Due Diligence Assistant
      </h1>
      <p className="text-sm max-w-[560px] mx-auto leading-relaxed" style={{ color: '#94A3B8' }}>
        Upload and prepare new data — or work directly with the data already loaded
        for reporting.
      </p>
    </motion.div>
  )

  // ── Choice screen ──────────────────────────────────────────────────────────

  const choiceScreen = (
    <AnimatePresence mode="wait">
      {mode === null && (
        <motion.div
          key="choice"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, scale: 0.98 }}
          transition={{ duration: 0.22 }}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 max-w-[920px] mx-auto">
            <ChoiceCard
              index={0}
              icon={<Database size={20} style={{ color: '#1E3A5F' }} aria-hidden />}
              title="Work with pipeline data"
              description="Ask questions and run analyses on the GL data already loaded into reporting — P&L, balance sheet, working capital and cash flow."
              badge="Reporting"
              onClick={() => handleSelectMode('pipeline')}
            />
            <ChoiceCard
              index={1}
              icon={<FileSpreadsheet size={20} style={{ color: '#1E3A5F' }} aria-hidden />}
              title="Upload new data"
              description="Bring in a new GL export, trial balance (SuSa) or sales file and let the bot map and prepare it step by step."
              badge="Ingestion"
              onClick={() => handleSelectMode('upload')}
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )

  // ── Unified chat canvas (both modes) ──────────────────────────────────────

  const footerNote =
    mode === 'pipeline'
      ? 'FDD Bot · Working with loaded pipeline data'
      : 'FDD Bot · Upload and prepare new data'

  const inputPlaceholder =
    mode === 'pipeline'
      ? 'Ask about P&L, balance sheet, working capital…'
      : 'Type a message or use the cards above…'

  const chatCanvas = mode !== null ? (
    <AnimatePresence mode="wait">
      <motion.div
        key={`chat-${mode}`}
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -10 }}
        transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        className="w-full"
        style={{ maxWidth: '100%' }}
      >
        <div
          className="rounded-2xl overflow-hidden flex"
          style={{
            background: '#FFFFFF',
            border: '1px solid #E2E8F0',
            boxShadow: '0 2px 8px rgba(0,0,0,0.06), 0 12px 40px rgba(0,0,0,0.06)',
            minHeight: '76vh',
          }}
        >
          {/* ── Left: history sidebar ── */}
          <ChatHistorySidebar
            mode={mode}
            activeProjectId={bot.activeProjectId}
            onNewChat={handleNewChat}
            onSelectChat={handleSelectChat}
            onDeleteChat={handleDeleteChat}
            refreshKey={sidebarRefreshKey}
          />

          {/* ── Right: chat area ── */}
          <div className="flex flex-col flex-1 min-w-0">
            {/* Toolbar */}
            <div
              className="flex items-center gap-3 px-5 py-3 shrink-0 flex-wrap"
              style={{
                borderBottom: '1px solid #E2E8F0',
                background: '#FAFBFC',
              }}
            >
              <div className="flex items-center gap-2.5 flex-1 min-w-0">
                <button
                  type="button"
                  onClick={handleBack}
                  className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors shrink-0"
                  style={{
                    color: '#475569',
                    border: '1px solid #E2E8F0',
                    background: 'transparent',
                  }}
                  onMouseEnter={e => {
                    ;(e.currentTarget as HTMLElement).style.background = '#F1F5F9'
                  }}
                  onMouseLeave={e => {
                    ;(e.currentTarget as HTMLElement).style.background = 'transparent'
                  }}
                >
                  <ArrowLeft size={12} aria-hidden />
                  Change mode
                </button>
                <ModeBadge mode={mode} />
                {bot.loading && (
                  <span className="text-xs" style={{ color: '#94A3B8' }}>
                    Thinking…
                  </span>
                )}
              </div>

              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  type="button"
                  onClick={() => void bot.undoLastCard()}
                  disabled={!bot.canUndo || bot.loading}
                  title="Undo last card"
                  className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors disabled:opacity-40"
                  style={{
                    color: '#475569',
                    border: '1px solid #E2E8F0',
                    background: 'transparent',
                  }}
                >
                  <Undo2 size={12} aria-hidden />
                  Undo
                </button>
                <button
                  type="button"
                  onClick={() => bot.newSession(mode)}
                  title="New session"
                  className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors"
                  style={{
                    color: '#475569',
                    border: '1px solid #E2E8F0',
                    background: 'transparent',
                  }}
                >
                  <RotateCcw size={12} aria-hidden />
                  Reset
                </button>
              </div>
            </div>

            {rasaUnreachable && (
              <div className="px-5 pt-4">
                <ErrorBanner />
              </div>
            )}

            <div className="flex flex-col flex-1 min-h-0">
              <BotConversation
                bot={bot}
                active={true}
                autoHello={true}
                preloadedFile={preloadedFile}
                footerNote={footerNote}
                inputPlaceholder={inputPlaceholder}
                wide={true}
                mode={mode ?? undefined}
              />
            </div>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  ) : null

  // ── Page render ────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div
        className="mx-auto px-6 py-10"
        style={{ maxWidth: mode !== null ? '1400px' : '1120px' }}
      >
        {mode === null && header}
        {choiceScreen}
        {chatCanvas}
      </div>
    </div>
  )
}
