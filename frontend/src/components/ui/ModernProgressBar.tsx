/** Indeterminate progress bar with sliding shimmer — used in page and detail loaders */
export default function ModernProgressBar({
  progress,
  indeterminate = false,
  className = '',
}: {
  /** 0–100; ignored when indeterminate */
  progress?: number
  indeterminate?: boolean
  className?: string
}) {
  const pct = indeterminate
    ? undefined
    : Math.min(100, Math.max(0, progress ?? 0))

  return (
    <div
      className={`relative h-1.5 w-full overflow-hidden rounded-full bg-slate-200/90 ${className}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : pct}
    >
      {indeterminate ? (
        <div
          className="absolute inset-y-0 w-[42%] rounded-full"
          style={{
            background: 'linear-gradient(90deg, transparent, #1E3A5F 35%, #3B82F6 65%, transparent)',
            animation: 'fin-load-slide 1.35s ease-in-out infinite',
          }}
        />
      ) : (
        <div
          className="h-full rounded-full transition-[width] duration-300 ease-out"
          style={{
            width: `${pct}%`,
            background: 'linear-gradient(90deg, #1E3A5F, #3B82F6)',
          }}
        />
      )}
      <style>{`
        @keyframes fin-load-slide {
          0% { left: -45%; }
          100% { left: 105%; }
        }
      `}</style>
    </div>
  )
}
