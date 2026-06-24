import { useState, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { scoreColor, scoreBg, scoreLabel, SIGNAL_SCORE_INFO } from '../../lib/anomalyCharts'
import type { ScoreBand } from '../../lib/anomalyCharts'

interface Props {
  score: number
  band: ScoreBand | string
  size?: 'sm' | 'md'
}

interface TooltipPos {
  top: number
  left: number
}

/** Renders the info-popover via a React portal so it never gets clipped by
 *  a parent's overflow-hidden (e.g. AnomalyReport card). */
function InfoTooltipPortal({ anchorRef, visible }: { anchorRef: React.RefObject<HTMLButtonElement | null>; visible: boolean }) {
  const [pos, setPos] = useState<TooltipPos | null>(null)

  useEffect(() => {
    if (!visible || !anchorRef.current) { setPos(null); return }
    const rect = anchorRef.current.getBoundingClientRect()
    setPos({
      top: rect.top + window.scrollY - 8,   // 8 px gap above button
      left: rect.left + window.scrollX + rect.width / 2,
    })
  }, [visible, anchorRef])

  if (!visible || !pos) return null

  return createPortal(
    <span
      role="tooltip"
      style={{
        position: 'absolute',
        top: pos.top,
        left: pos.left,
        transform: 'translate(-50%, -100%)',
        zIndex: 9999,
        background: '#1E293B',
        color: '#F1F5F9',
        borderRadius: 8,
        padding: '8px 12px',
        fontSize: 11,
        lineHeight: '1.4',
        width: 240,
        pointerEvents: 'none',
        boxShadow: '0 4px 16px rgba(0,0,0,0.18)',
      }}
    >
      {SIGNAL_SCORE_INFO}
    </span>,
    document.body,
  )
}

export default function SignalScoreBadge({ score, band, size = 'md' }: Props) {
  const [tooltipVisible, setTooltipVisible] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const color = scoreColor(band)
  const bg = scoreBg(band)
  const label = scoreLabel(band)
  const textSize = size === 'sm' ? 'text-[10px]' : 'text-xs'

  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`inline-block rounded px-1.5 py-0.5 font-semibold uppercase tracking-wide ${textSize}`}
        style={{ background: bg, color }}
      >
        {label} · {score}
      </span>
      {/* Info icon — tooltip rendered via portal to avoid overflow-hidden clipping */}
      <span className="relative inline-block">
        <button
          ref={btnRef}
          type="button"
          className="rounded-full flex items-center justify-center"
          style={{ width: 14, height: 14, background: 'rgba(100,116,139,0.15)', color: '#64748B', fontSize: 9, lineHeight: 1 }}
          onMouseEnter={() => setTooltipVisible(true)}
          onMouseLeave={() => setTooltipVisible(false)}
          onFocus={() => setTooltipVisible(true)}
          onBlur={() => setTooltipVisible(false)}
          aria-label="Signal score explanation"
        >
          i
        </button>
        <InfoTooltipPortal anchorRef={btnRef} visible={tooltipVisible} />
      </span>
    </span>
  )
}
