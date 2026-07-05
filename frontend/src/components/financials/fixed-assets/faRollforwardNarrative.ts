import type { FixedAssetNarrativeBullet, FixedAssetReportDetailResponse } from '../../../lib/api'
import type { PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

export function mapFaNarrativeBullets(data: FixedAssetReportDetailResponse): PlNarrativeBullet[] {
  const bullets = data.narrative?.bullets
  if (bullets?.length) {
    return bullets.map(b => ({
      index: b.index,
      line_code: b.position_id,
      label: b.label,
      priority: 0,
      text: b.text,
      tone: (b.tone as PlNarrativeBullet['tone']) ?? 'neutral',
    }))
  }
  return (data.bullets ?? []).map((text, i) => ({
    index: i + 1,
    line_code: data.positions[i]?.id ?? `legacy-${i}`,
    label: data.positions[i]?.bilanzposition ?? '',
    priority: 0,
    text,
    tone: 'neutral' as const,
  }))
}

export function buildFaMarkerMap(bullets: PlNarrativeBullet[]): ReportCommentMarkerMap {
  const map: ReportCommentMarkerMap = {}
  for (const b of bullets) {
    map[b.line_code] = { tier: 'primary', index: b.index }
  }
  return map
}

export type { FixedAssetNarrativeBullet }
