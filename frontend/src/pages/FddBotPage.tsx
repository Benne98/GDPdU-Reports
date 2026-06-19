/**
 * FddBotPage — Full-page, standalone FDD-Bot interface.
 *
 * No drawer / slide-over. Two-phase flow:
 *   1. Choice screen — pick "Pipeline data" or "Upload new data"
 *   2. Chat canvas — full BotConversation with mode-aware chrome
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
} from 'lucide-react'
import { useFddBot } from '../components/fdd-bot/useFddBot'
import BotConversation from '../components/fdd-bot/BotConversation'
import FddChatPanel, { type FddPanelMode } from '../components/fdd-bot/FddChatPanel'

// ─── Types ────────────────────────────────────────────────────────────────────

type Mode = 'pipeline' | 'upload'

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
        background: mode === 'pipeline' ? 'rgba(30,58,95,0.09)' : 'rgba(30,58,95,0.09)',
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

// ─── FddBotPage ───────────────────────────────────────────────────────────────

export default function FddBotPage() {
  const bot = useFddBot()
  const [mode, setMode] = useState<Mode | null>(null)
  const [panelMode, setPanelMode] = useState<FddPanelMode>('closed')
  const [preloadedFile, setPreloadedFile] = useState<File | null>(null)

  // Track whether Rasa is reachable — set to true once send() returns an error
  // (bot.error is transient; we show the banner persistently once it fires)
  const [rasaUnreachable, setRasaUnreachable] = useState(false)

  useEffect(() => {
    if (bot.error) setRasaUnreachable(true)
  }, [bot.error])

  const handleSelectMode = useCallback((selected: Mode) => {
    setRasaUnreachable(false)
    setPreloadedFile(null)
    setMode(selected)
    if (selected === 'upload') {
      setPanelMode('expanded')
    } else {
      setPanelMode('closed')
    }
  }, [])

  const handleBack = useCallback(() => {
    setMode(null)
    setPanelMode('closed')
    setPreloadedFile(null)
  }, [])

  // ── Page-level page header ─────────────────────────────────────────────────

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
              onClick={() => void handleSelectMode('pipeline')}
            />
            <ChoiceCard
              index={1}
              icon={<FileSpreadsheet size={20} style={{ color: '#1E3A5F' }} aria-hidden />}
              title="Upload new data"
              description="Bring in a new GL export, trial balance (SuSa) or sales file and let the bot map and prepare it step by step."
              badge="Ingestion"
              onClick={() => void handleSelectMode('upload')}
            />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )

  // ── Upload: Mathis FddChatPanel (fullscreen below app header) ───────────────

  const uploadPanel =
    mode === 'upload' ? (
      <>
        <button
          type="button"
          onClick={handleBack}
          className="fixed left-4 z-[45] flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium shadow-md"
          style={{
            top: 72,
            color: '#1E3A5F',
            border: '1px solid #E2E8F0',
            background: '#FFFFFF',
          }}
        >
          <ArrowLeft size={12} aria-hidden />
          Modus wechseln
        </button>
        {rasaUnreachable && (
          <div className="fixed left-4 right-4 z-[45] max-w-lg" style={{ top: 112 }}>
            <ErrorBanner />
          </div>
        )}
        <FddChatPanel
          mode={panelMode}
          preloadedFile={preloadedFile}
          bot={bot}
          topOffsetPx={112}
          bottomOffsetPx={20}
        />
      </>
    ) : null

  const pipelineCanvas = (
    <AnimatePresence mode="wait">
      {mode === 'pipeline' && (
        <motion.div
          key="chat-pipeline"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
          className="max-w-[880px] mx-auto"
        >
          <div
            className="rounded-2xl overflow-hidden flex flex-col"
            style={{
              background: '#FFFFFF',
              border: '1px solid #E2E8F0',
              boxShadow:
                '0 2px 8px rgba(0,0,0,0.06), 0 12px 40px rgba(0,0,0,0.06)',
              minHeight: '72vh',
            }}
          >
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
                <ModeBadge mode="pipeline" />
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
                  onClick={() => bot.newSession()}
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

            <div className="flex flex-col flex-1 min-h-0" style={{ minHeight: '60vh' }}>
              <BotConversation
                bot={bot}
                active={true}
                autoHello={true}
                footerNote="FDD Bot · Working with loaded pipeline data"
                inputPlaceholder="Ask about P&L, balance sheet, working capital…"
              />
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )

  // ── Page render ────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen" style={{ background: mode === 'upload' ? '#F8FAFC' : '#F4F6F9' }}>
      {mode !== 'upload' && (
        <div className="max-w-[1120px] mx-auto px-6 py-10">
          {mode === null && header}
          {choiceScreen}
          {pipelineCanvas}
        </div>
      )}
      {uploadPanel}
    </div>
  )
}
