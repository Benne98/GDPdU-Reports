import { useEffect, useState } from 'react'
import { StickyNote } from 'lucide-react'
import ActionNotesPanel from './ActionNotesPanel'

type Props = {
  sessionTitle: string
  route: string
}

export default function ActionNotesBubble({ sessionTitle, route }: Props) {
  const [open, setOpen] = useState(false)
  const [pulse, setPulse] = useState(true)

  useEffect(() => {
    const t = window.setTimeout(() => setPulse(false), 2400)
    return () => window.clearTimeout(t)
  }, [])

  return (
    <>
      <ActionNotesPanel open={open} onClose={() => setOpen(false)} sessionTitle={sessionTitle} route={route} />
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className={`flex items-center justify-center rounded-full shadow-lg transition-transform hover:scale-105 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-amber-500 ${
          pulse && !open ? 'animate-pulse' : ''
        }`}
        style={{
          width: 56,
          height: 56,
          background: 'linear-gradient(135deg, #F59E0B 0%, #D97706 100%)',
          color: '#fff',
          boxShadow: '0 10px 30px rgba(245, 158, 11, 0.4)',
        }}
        title="Action Notes"
        aria-label={open ? 'Close Action Notes' : 'Open Action Notes'}
        aria-expanded={open}
      >
        <StickyNote size={24} strokeWidth={1.85} aria-hidden />
      </button>
    </>
  )
}
