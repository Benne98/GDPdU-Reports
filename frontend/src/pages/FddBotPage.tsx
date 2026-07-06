/**
 * FddBotPage — Full-page FDD-Bot interface.
 *
 * Opens directly into the "Upload new data" chat (the intermediate choice screen was
 * removed — upload is the only offered flow). Layout mirrors Budget Planning:
 *   page header → action/summary bar (history · new chat · undo · reset) → chat card.
 * The version history (past chats) is an EXPANDABLE panel toggled from the action bar.
 */

import { useState, useCallback, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  Undo2,
  RotateCcw,
  WifiOff,
  Plus,
  X,
  MessageSquare,
  History,
} from 'lucide-react'
import { useFddBot } from '../components/fdd-bot/useFddBot'
import BotConversation from '../components/fdd-bot/BotConversation'
import {
  listProjectSummariesByMode,
  type FddProjectMode,
  type FddProjectSummary,
} from '../components/fdd-bot/fddProjectStore'

/** The FDD-Bot now offers only the upload flow. */
const MODE: FddProjectMode = 'upload'

// ─── ErrorBanner ──────────────────────────────────────────────────────────────

function ErrorBanner() {
  return (
    <div
      className="flex items-start gap-3 rounded-xl px-4 py-3 text-sm"
      style={{ background: 'rgba(248,250,252,1)', border: '1px solid #E2E8F0' }}
    >
      <div
        className="flex items-center justify-center rounded-lg shrink-0 mt-0.5"
        style={{ width: 32, height: 32, background: 'rgba(30,58,95,0.06)', border: '1px solid rgba(30,58,95,0.10)' }}
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

// ─── ChatHistorySidebar (version history — collapsible panel) ─────────────────

interface ChatHistorySidebarProps {
  activeProjectId: string
  onNewChat: () => void
  onSelectChat: (id: string) => void
  onDeleteChat: (id: string) => void
  onClose: () => void
  refreshKey: number
}

function ChatHistorySidebar({
  activeProjectId,
  onNewChat,
  onSelectChat,
  onDeleteChat,
  onClose,
  refreshKey,
}: ChatHistorySidebarProps) {
  const [projects, setProjects] = useState<FddProjectSummary[]>([])

  useEffect(() => {
    setProjects(listProjectSummariesByMode(MODE))
  }, [refreshKey])

  return (
    <aside
      className="flex flex-col shrink-0"
      style={{ width: 244, background: '#FFFFFF', borderRight: '1px solid #E2E8F0', minHeight: 0 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 pt-3.5 pb-1 shrink-0">
        <p className="text-[10px] font-bold uppercase tracking-widest" style={{ color: '#94A3B8' }}>
          Version history
        </p>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded transition-colors"
          style={{ color: '#94A3B8' }}
          aria-label="Hide version history"
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#F1F5F9' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
        >
          <X size={14} />
        </button>
      </div>

      {/* New chat */}
      <div className="px-3 pt-1 pb-2 shrink-0">
        <button
          type="button"
          onClick={onNewChat}
          className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{ color: '#1E3A5F', border: '1px solid #E2E8F0', background: '#F8FAFC' }}
          onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = '#EFF6FF' }}
          onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = '#F8FAFC' }}
        >
          <Plus size={15} />
          New chat
        </button>
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
              const dateStr = new Date(project.updatedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
              return (
                <li key={project.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => onSelectChat(project.id)}
                    className="w-full text-left px-3 py-2 pr-8 rounded-lg transition-colors"
                    style={{ background: isActive ? 'rgba(30,58,95,0.08)' : 'transparent' }}
                    title={project.name}
                  >
                    <p className="text-sm truncate" style={{ color: isActive ? '#1E3A5F' : '#334155', fontWeight: isActive ? 600 : 400 }}>
                      {project.name}
                    </p>
                    <p className="text-[10px] mt-0.5" style={{ color: '#94A3B8' }}>
                      {dateStr}
                    </p>
                  </button>
                  <button
                    type="button"
                    onClick={e => { e.stopPropagation(); onDeleteChat(project.id) }}
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

// ─── Action bar button ────────────────────────────────────────────────────────

function BarButton({ onClick, active, disabled, title, children }: {
  onClick: () => void; active?: boolean; disabled?: boolean; title?: string; children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-40"
      style={{
        color: '#1E3A5F',
        background: active ? 'rgba(30,58,95,0.1)' : '#F4F6F9',
        border: `1px solid ${active ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
      }}
    >
      {children}
    </button>
  )
}

// ─── FddBotPage ───────────────────────────────────────────────────────────────

export default function FddBotPage() {
  const bot = useFddBot()
  const [preloadedFile] = useState<File | null>(null)
  const [sidebarRefreshKey, setSidebarRefreshKey] = useState(0)
  const [rasaUnreachable, setRasaUnreachable] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  useEffect(() => { if (bot.error) setRasaUnreachable(true) }, [bot.error])
  // Bump the history refresh key whenever the project list may have changed.
  useEffect(() => { setSidebarRefreshKey(k => k + 1) }, [bot.projects])

  const handleNewChat = useCallback(() => {
    bot.createProject(MODE)
    setSidebarRefreshKey(k => k + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSelectChat = useCallback((id: string) => {
    bot.switchProject(id)
    setSidebarRefreshKey(k => k + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleDeleteChat = useCallback((id: string) => {
    bot.deleteProject(id)
    setSidebarRefreshKey(k => k + 1)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <div className="mx-auto max-w-[1680px] px-6 lg:px-8 py-8">
        {/* Page header — Budget-Planning style */}
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-5"
        >
          <p className="text-xs font-semibold uppercase tracking-widest mb-1.5" style={{ color: '#1E3A5F' }}>
            FDD-Bot
          </p>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
            Financial Due Diligence Assistant
          </h1>
          <p className="text-sm mt-1" style={{ color: '#94A3B8' }}>
            Upload a new GL export, trial balance (SuSa) or sales file — the bot maps and prepares it step by step.
          </p>
        </motion.div>

        {/* Action / summary bar */}
        <div
          className="rounded-xl mb-4 px-3 py-2 flex items-center gap-2"
          style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
        >
          <BarButton onClick={() => setHistoryOpen(v => !v)} active={historyOpen} title="Toggle version history">
            <History size={14} aria-hidden />
            <span className="hidden sm:inline">Version history</span>
          </BarButton>
          <BarButton onClick={handleNewChat} title="Start a new chat">
            <Plus size={14} aria-hidden />
            <span className="hidden sm:inline">New chat</span>
          </BarButton>

          <div className="flex-1 min-w-0" />

          {bot.loading && (
            <span className="text-xs mr-1" style={{ color: '#94A3B8' }}>Thinking…</span>
          )}
          <BarButton onClick={() => void bot.undoLastCard()} disabled={!bot.canUndo || bot.loading} title="Undo last card">
            <Undo2 size={14} aria-hidden />
            <span className="hidden sm:inline">Undo</span>
          </BarButton>
          <BarButton onClick={() => bot.newSession(MODE)} title="Reset this chat">
            <RotateCcw size={14} aria-hidden />
            <span className="hidden sm:inline">Reset</span>
          </BarButton>
        </div>

        {/* Chat card — optional version-history panel + conversation */}
        <div
          className="rounded-2xl overflow-hidden flex"
          style={{
            background: '#FFFFFF',
            border: '1px solid #E2E8F0',
            boxShadow: '0 2px 8px rgba(0,0,0,0.05), 0 12px 40px rgba(0,0,0,0.05)',
            minHeight: '72vh',
          }}
        >
          {historyOpen && (
            <ChatHistorySidebar
              activeProjectId={bot.activeProjectId}
              onNewChat={handleNewChat}
              onSelectChat={handleSelectChat}
              onDeleteChat={handleDeleteChat}
              onClose={() => setHistoryOpen(false)}
              refreshKey={sidebarRefreshKey}
            />
          )}

          <div className="flex flex-col flex-1 min-w-0">
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
                footerNote="FDD Bot · Upload and prepare new data"
                inputPlaceholder="Type a message or use the cards above…"
                wide={true}
                mode={MODE}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
