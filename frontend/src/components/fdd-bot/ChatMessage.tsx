/**
 * ChatMessage — renders a single message bubble in the FDD Bot chat.
 * Bot messages are left-aligned; user messages are right-aligned.
 */

import type { ChatMessage as ChatMessageType } from './useFddBot'

interface Props {
  message: ChatMessageType
}

export default function ChatMessage({ message }: Props) {
  const isBot = message.role === 'bot'

  if (message.custom) {
    // Structured messages (adaptive cards) are rendered by AdaptiveCard
    return null
  }

  return (
    <div className={`flex ${isBot ? 'justify-start' : 'justify-end'} mb-3`}>
      {isBot && (
        <div
          className="flex items-center justify-center rounded-full shrink-0 mr-2 text-xs font-bold"
          style={{
            width: 28,
            height: 28,
            background: '#1E3A5F',
            color: '#FFFFFF',
            alignSelf: 'flex-end',
          }}
        >
          F
        </div>
      )}
      <div
        className="max-w-[min(48rem,85%)] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed"
        style={
          isBot
            ? {
                background: '#F1F5F9',
                color: '#1E293B',
                borderBottomLeftRadius: 4,
              }
            : {
                background: '#1E3A5F',
                color: '#FFFFFF',
                borderBottomRightRadius: 4,
              }
        }
      >
        {renderText(message.text ?? '')}
      </div>
    </div>
  )
}

function renderText(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>
    }
    return <span key={i}>{part}</span>
  })
}
