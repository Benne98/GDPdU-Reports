import type { NetDebtNarrative, NetDebtTableResponse } from '../../../lib/api'
import type { PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

export function mapNetDebtNarrativeBullets(data: NetDebtTableResponse): PlNarrativeBullet[] {
  const bullets = data.narrative?.bullets
  if (!bullets?.length) return []
  return bullets.map(b => ({
    index: b.index,
    line_code: b.row_id,
    label: b.label ?? '',
    priority: 0,
    text: b.text ?? '',
    tone: (b.tone as PlNarrativeBullet['tone']) ?? 'neutral',
  }))
}

export function resolveNetDebtNarrative(data: NetDebtTableResponse): {
  intro?: string
  bullets: PlNarrativeBullet[]
} {
  return {
    intro: data.narrative?.intro,
    bullets: mapNetDebtNarrativeBullets(data),
  }
}

export function buildNetDebtMarkerMap(bullets: PlNarrativeBullet[]): ReportCommentMarkerMap {
  const map: ReportCommentMarkerMap = {}
  for (const b of bullets) {
    if (!b.line_code) continue
    map[b.line_code] = { tier: 'primary', index: b.index }
  }
  return map
}

export type { NetDebtNarrative }
