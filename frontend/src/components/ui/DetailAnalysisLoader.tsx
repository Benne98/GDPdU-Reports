import ModernProgressBar from './ModernProgressBar'

export default function DetailAnalysisLoader({
  compact = false,
  message = 'Preparing detail analysis…',
}: {
  compact?: boolean
  message?: string
}) {
  return (
    <div
      className={`flex flex-col items-center justify-center text-center rounded-xl border border-slate-200/80 bg-gradient-to-b from-slate-50 to-white ${
        compact ? 'min-h-[200px] px-4 py-8' : 'min-h-[min(52vh,420px)] px-6 py-12'
      }`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div
        className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl"
        style={{ background: 'rgba(30, 58, 95, 0.07)' }}
      >
        <div
          className="h-6 w-6 rounded-full border-2 border-[#1E3A5F] border-t-transparent animate-spin"
          aria-hidden
        />
      </div>
      <p className="text-sm font-semibold text-slate-800">{message}</p>
      <p className="text-xs text-slate-500 mt-2 max-w-sm leading-relaxed">
        AI is analysing drivers, postings, and trends in the background. This usually takes a few
        seconds.
      </p>
      <div className="w-full max-w-xs mt-6">
        <ModernProgressBar indeterminate />
      </div>
      <div className="mt-5 flex gap-1.5" aria-hidden>
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-[#1E3A5F]/50"
            style={{ animation: `fin-pulse-dot 1.2s ease-in-out ${i * 0.15}s infinite` }}
          />
        ))}
      </div>
      <style>{`
        @keyframes fin-pulse-dot {
          0%, 100% { opacity: 0.25; transform: scale(0.85); }
          50% { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </div>
  )
}
