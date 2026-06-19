import {
  PPT_BADGE_DIAMETER_IN,
  PPT_BADGE_INDEX_FONT_PT,
} from '../../../lib/finssentialsExport/narrativeBadgeLayout'
import type { ReportCommentMarker } from '../statement-two-view/reportCommentMarkers'

type Props = {
  /** Whole number for narrative list; use `marker` in table for sub-indices. */
  index?: number
  marker?: ReportCommentMarker
  /** `export` = scaled for PPT/PDF table capture; `default` = on-screen UI. */
  variant?: 'default' | 'export'
  /** Override diameter (px) for html2canvas — sized to match 0.25 cm on slide. */
  sizePx?: number
  fontPx?: number
}

const UI_BADGE_SIZE_PX = 16

function resolveDisplay(marker?: ReportCommentMarker, index?: number): {
  label: string
  tier: 'primary' | 'sub'
  indexNum: number
} {
  if (marker) {
    if (marker.tier === 'sub') {
      return { label: `${marker.index}.${marker.sub}`, tier: 'sub', indexNum: marker.index }
    }
    return { label: String(marker.index), tier: 'primary', indexNum: marker.index }
  }
  const n = index ?? 0
  return { label: String(n), tier: 'primary', indexNum: n }
}

/** Shared index badge for report table "#" column and narrative bullet list. */
export default function PlCommentIndexBadge({
  index,
  marker,
  variant = 'default',
  sizePx: sizePxProp,
  fontPx: fontPxProp,
}: Props) {
  const { label, tier, indexNum } = resolveDisplay(marker, index)
  const isSub = tier === 'sub'

  const defaultExportPx = Math.round(PPT_BADGE_DIAMETER_IN * 96)
  const size =
    sizePxProp ??
    (variant === 'export' ? Math.max(11, defaultExportPx) : UI_BADGE_SIZE_PX)

  const baseFont =
    fontPxProp ??
    (variant === 'export'
      ? Math.max(6, Math.round(PPT_BADGE_INDEX_FONT_PT * (96 / 72)))
      : 9)
  const fontSize = isSub ? Math.max(5, Math.round(baseFont * 0.78)) : baseFont

  const bg = isSub ? '#CBD5E1' : '#1E3A5F'
  const color = isSub ? '#475569' : '#FFFFFF'

  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold tabular-nums select-none"
      style={{
        width: size,
        height: size,
        minWidth: size,
        maxWidth: size,
        fontSize,
        lineHeight: 1,
        fontFamily: '"Segoe UI", Calibri, sans-serif',
        color,
        backgroundColor: bg,
        boxSizing: 'border-box',
        WebkitFontSmoothing: 'antialiased',
        MozOsxFontSmoothing: 'grayscale',
      }}
      aria-label={isSub ? `Comment ${indexNum}.${label.split('.')[1]}` : `Comment ${indexNum}`}
    >
      {label}
    </span>
  )
}
