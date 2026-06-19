import { useEffect, useRef, useState } from 'react'
import ModernProgressBar from './ModernProgressBar'

export default function PageLoadingOverlay({
  message = 'Loading analytics…',
  submessage,
  visible,
  sessionKey = '',
}: {
  message?: string
  submessage?: string
  visible: boolean
  sessionKey?: string
}) {
  const [progress, setProgress] = useState(8)
  const [indeterminate, setIndeterminate] = useState(false)
  const visibleRef = useRef(visible)
  visibleRef.current = visible

  useEffect(() => {
    setProgress(12)
    setIndeterminate(false)
    const t0 = Date.now()
    const id = window.setInterval(() => {
      if (!visibleRef.current) return
      const elapsed = Date.now() - t0
      const eased = 12 + (1 - Math.exp(-elapsed / 2800)) * 78
      setProgress(prev => Math.max(prev, Math.min(90, eased)))
      if (elapsed > 3500) setIndeterminate(true)
    }, 100)
    return () => clearInterval(id)
  }, [sessionKey])

  useEffect(() => {
    if (visible) return
    setProgress(12)
    setIndeterminate(false)
  }, [visible])

  if (!visible) return null

  return (
    <div
      className="fixed inset-0 z-[55] flex items-center justify-center px-6"
      style={{
        background: 'rgba(244, 246, 249, 0.92)',
        backdropFilter: 'blur(10px)',
      }}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div
        className="w-full max-w-md rounded-2xl border border-slate-200/80 bg-white px-8 py-8 shadow-xl"
        style={{ boxShadow: '0 20px 50px rgba(30, 58, 95, 0.12)' }}
      >
        <div className="flex items-center gap-3 mb-5">
          <div
            className="h-10 w-10 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'rgba(30, 58, 95, 0.08)' }}
          >
            <div
              className="h-5 w-5 rounded-full border-2 border-[#1E3A5F] border-t-transparent animate-spin"
              aria-hidden
            />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800">{message}</p>
            {submessage && (
              <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">{submessage}</p>
            )}
          </div>
        </div>
        <ModernProgressBar progress={progress} indeterminate={indeterminate} />
        <p className="text-[10px] text-slate-400 mt-2.5 text-right tabular-nums">
          {indeterminate ? 'Still working…' : `${Math.round(progress)}%`}
        </p>
      </div>
    </div>
  )
}
