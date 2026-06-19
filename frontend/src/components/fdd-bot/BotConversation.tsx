/**
 * Shared Rasa conversation body: message list, typing indicator, adaptive cards, composer.
 * Used inside FddChatPanel (slide-over).
 */

import { useEffect, useRef, useState } from 'react'
import { Send } from 'lucide-react'
import type { ChatMessage as ChatMessageType, FddBotApi } from './useFddBot'
import ChatMessage from './ChatMessage'
import AdaptiveCard from './AdaptiveCard'

export interface BotConversationProps {
  bot: FddBotApi
  /** When panel / rail is visible and user should get the FDD hello flow */
  active: boolean
  /** Send `hello` once when active and history is still empty (same behaviour as legacy FddChatPanel). */
  autoHello?: boolean
  preloadedFile?: File | null
  /** Line under the composer */
  footerNote?: string
  inputPlaceholder?: string
  /** Use full width centered content column */
  wide?: boolean
}

export default function BotConversation({
  bot,
  active,
  autoHello = true,
  preloadedFile,
  footerNote = 'FDD Bot · Powered by Rasa',
  inputPlaceholder = 'Type a message or use the cards above…',
  wide = false,
}: BotConversationProps) {
  const {
    messages,
    loading,
    scriptJobLoading,
    send,
    submitCard,
    uploadFile,
    submittedCardIds,
    markCardSubmitted,
    sessionEpoch,
  } = bot
  const [freeText, setFreeText] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const hasInitiated = useRef(false)
  const hasPreloadedFile = useRef(false)
  const preloadComplete = useRef(false)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading, scriptJobLoading])

  useEffect(() => {
    hasInitiated.current = false
    hasPreloadedFile.current = false
    preloadComplete.current = false
    if (active && autoHello) {
      if (preloadedFile) return
      if (messages.length > 0) {
        hasInitiated.current = true
        return
      }
      hasInitiated.current = true
      void send('/greet', { skipUserBubble: true })
    }
  }, [sessionEpoch, active, autoHello, send, preloadedFile, messages.length])

  useEffect(() => {
    if (!active || !autoHello || hasInitiated.current) return
    if (messages.length > 0) {
      hasInitiated.current = true
      return
    }
    // If we have a preloaded file (Exit Readiness flow), wait until it has been
    // stored in the Rasa session before starting the guided conversation.
    if (preloadedFile && !preloadComplete.current) return
    hasInitiated.current = true
    void send('/greet', { skipUserBubble: true })
  }, [active, autoHello, send, preloadedFile, messages.length])

  useEffect(() => {
    // If a file was preloaded before starting the bot (e.g. Exit Readiness tab),
    // we upload it and store it in the Rasa session WITHOUT advancing the dialogue.
    // The normal conversation should still start at Project Setup.
    if (active && preloadedFile && !hasPreloadedFile.current) {
      hasPreloadedFile.current = true
      void uploadFile(preloadedFile).then(async (result) => {
        try {
          if (result) {
            await submitCard(
              'preload_file',
              {
                file_id: result.file_id,
                file_path: result.file_path,
                headers: result.headers,
                session_id: result.session_id,
                ...(result.sheet_names?.length ? { sheet_names: result.sheet_names } : {}),
              },
              { skipUserBubble: true },
            )
          }
        } finally {
          preloadComplete.current = true
          // Kick off the normal hello flow if it hasn't started yet.
          if (active && autoHello && !hasInitiated.current) {
            hasInitiated.current = true
            void send('/greet', { skipUserBubble: true })
          }
        }
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- match legacy panel: run when active/file changes
  }, [active, preloadedFile])

  const handleFreeText = async () => {
    const text = freeText.trim()
    if (!text) return
    setFreeText('')
    await send(text)
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div
        className={`flex-1 overflow-y-auto py-4 ${wide ? 'px-6' : 'px-4'}`}
        style={{ overscrollBehavior: 'contain' }}
      >
        <div className={wide ? 'max-w-4xl mx-auto w-full' : 'w-full'}>
        {messages.length === 0 && !loading && !scriptJobLoading && (
          <div className="flex flex-col items-center justify-center h-full min-h-[120px] gap-3 text-center">
            <div
              className="flex items-center justify-center rounded-full"
              style={{ width: 56, height: 56, background: 'rgba(30,58,95,0.08)' }}
            >
              <span className="text-xl">💼</span>
            </div>
            <p className="text-sm font-medium" style={{ color: '#374151' }}>
              Starting FDD Bot…
            </p>
          </div>
        )}

        {messages.map((msg: ChatMessageType) => {
          if (msg.custom) {
            const isNotification = Boolean(msg.custom.notification)
            return (
              <div
                key={msg.id}
                className={`flex justify-start mb-3${isNotification ? ' pl-2' : ''}`}
              >
                <AdaptiveCard
                  payload={msg.custom}
                  onSubmit={async (cardName, values, meta) => {
                    const ok = await submitCard(cardName, values, meta)
                    if (ok) markCardSubmitted(msg.id)
                  }}
                  onFileUpload={uploadFile}
                  disabled={submittedCardIds.has(msg.id)}
                />
              </div>
            )
          }
          return <ChatMessage key={msg.id} message={msg} />
        })}

        {(loading || scriptJobLoading) && (
          <div className="flex justify-start mb-3">
            <div
              className="flex items-center gap-1 px-3.5 py-2.5 rounded-2xl"
              style={{ background: '#F1F5F9', borderBottomLeftRadius: 4 }}
            >
              <TypingDot delay={0} />
              <TypingDot delay={0.2} />
              <TypingDot delay={0.4} />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
        </div>
      </div>

      <div
        className={`shrink-0 ${wide ? 'px-6' : 'px-4'} py-3`}
        style={{ borderTop: '1px solid #E2E8F0', background: '#FFFFFF' }}
      >
        <div className={wide ? 'max-w-4xl mx-auto w-full' : 'w-full'}>
        <div
          className="flex items-center gap-2 rounded-xl px-3 py-2"
          style={{ border: '1px solid #E2E8F0', background: '#F8FAFC' }}
        >
          <input
            type="text"
            value={freeText}
            placeholder={inputPlaceholder}
            onChange={(e) => setFreeText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void handleFreeText()
              }
            }}
            className="flex-1 text-sm bg-transparent outline-none"
            style={{ color: '#1E293B' }}
          />
          <button
            type="button"
            onClick={() => void handleFreeText()}
            disabled={!freeText.trim() || loading || scriptJobLoading}
            className="rounded-lg p-1.5 transition-colors"
            style={{
              background: freeText.trim() && !loading && !scriptJobLoading ? '#1E3A5F' : '#E2E8F0',
              color:      freeText.trim() && !loading && !scriptJobLoading ? '#fff' : '#94A3B8',
            }}
            aria-label="Send message"
          >
            <Send size={13} />
          </button>
        </div>
        {footerNote ? (
          <p className="text-xs text-center mt-2" style={{ color: '#94A3B8' }}>
            {footerNote}
          </p>
        ) : null}
        </div>
      </div>
    </div>
  )
}

function TypingDot({ delay }: { delay: number }) {
  return (
    <div
      style={{
        width:        6,
        height:       6,
        borderRadius: '50%',
        background:   '#94A3B8',
        animation:    `bounce 1.2s ${delay}s infinite`,
      }}
    />
  )
}
