import { useMemo } from 'react'
import type { FinancialStatementResponse, PlNarrativeResponse } from '../../../lib/api'
import { mapApiBulletsToUi, type PlNarrativeBullet } from '../pl-two-view/plNarrativeEngine'

/** Map API or client narrative bullets for PDF/PPT export. */
export function useStatementExportBullets(
  data: FinancialStatementResponse | null | undefined,
  narrative: PlNarrativeResponse | null,
  clientNarrative: PlNarrativeResponse | null,
): PlNarrativeBullet[] {
  return useMemo(() => {
    if (!data) return []
    const src = narrative ?? clientNarrative
    if (!src?.bullets?.length) return []
    return mapApiBulletsToUi(src.bullets, data.rows)
  }, [data, narrative, clientNarrative])
}
