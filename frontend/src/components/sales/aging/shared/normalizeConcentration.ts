import type {
  AgingConcentrationSegment,
  PayablesConcentration,
  ReceivablesConcentration,
} from '../../../../lib/api'
import { AGING_CONCENTRATION_BANDS } from './agingConcentrationBands'

type LegacyConcentration = ReceivablesConcentration & {
  top_3_amount?: number
  top_5_amount?: number
  top_10_amount?: number
}

type LegacyPayablesConcentration = PayablesConcentration & {
  top_3_amount?: number
  top_5_amount?: number
  top_10_amount?: number
}

function pct(amount: number, total: number): number {
  return total > 0 ? Math.round((amount / total) * 1000) / 10 : 0
}

function emptySegments(): AgingConcentrationSegment[] {
  return AGING_CONCENTRATION_BANDS.map(({ band }) => ({
    band,
    label:
      band === 'rank_1_5'
        ? 'Top 1–5'
        : band === 'rank_6_10'
          ? 'Top 6–10'
          : band === 'rank_11_20'
            ? 'Top 11–20'
            : 'Rank 21+',
    amount: 0,
    pct: 0,
    customer_count: 0,
    supplier_count: 0,
  }))
}

/** Map legacy cumulative top-N API payloads to non-overlapping rank bands. */
function legacySegments(data: LegacyConcentration): AgingConcentrationSegment[] {
  const total = data.total
  const top5 = data.top_5_amount ?? 0
  const top10 = data.top_10_amount ?? 0
  const band610 = Math.max(0, top10 - top5)
  const band11plus = Math.max(0, total - top10)

  return [
    {
      band: 'rank_1_5',
      label: 'Top 1–5',
      amount: top5,
      pct: pct(top5, total),
      customer_count: 0,
    },
    {
      band: 'rank_6_10',
      label: 'Top 6–10',
      amount: band610,
      pct: pct(band610, total),
      customer_count: 0,
    },
    {
      band: 'rank_11_20',
      label: 'Top 11–20',
      amount: band11plus,
      pct: pct(band11plus, total),
      customer_count: 0,
    },
    {
      band: 'rank_21_plus',
      label: 'Rank 21+',
      amount: 0,
      pct: 0,
      customer_count: 0,
    },
  ]
}

/** Map legacy payables top-3/5/10 cumulative payload to approximate rank bands. */
function legacyPayablesSegments(data: LegacyPayablesConcentration): AgingConcentrationSegment[] {
  const total = data.total
  const top5 = data.top_5_amount ?? 0
  const top10 = data.top_10_amount ?? 0
  const band610 = Math.max(0, top10 - top5)
  const band11plus = Math.max(0, total - top10)

  return [
    {
      band: 'rank_1_5',
      label: 'Top 1–5',
      amount: top5,
      pct: pct(top5, total),
      customer_count: 0,
      supplier_count: 0,
    },
    {
      band: 'rank_6_10',
      label: 'Top 6–10',
      amount: band610,
      pct: pct(band610, total),
      customer_count: 0,
      supplier_count: 0,
    },
    {
      band: 'rank_11_20',
      label: 'Top 11–20',
      amount: band11plus,
      pct: pct(band11plus, total),
      customer_count: 0,
      supplier_count: 0,
    },
    {
      band: 'rank_21_plus',
      label: 'Rank 21+',
      amount: 0,
      pct: 0,
      customer_count: 0,
      supplier_count: 0,
    },
  ]
}

export function normalizeReceivablesConcentration(
  raw: LegacyConcentration | null | undefined,
): ReceivablesConcentration | null {
  if (!raw) return null

  const total = Number(raw.total ?? 0)
  if (Array.isArray(raw.segments) && raw.segments.length > 0) {
    return { ...raw, total, segments: raw.segments }
  }

  if (total <= 0) {
    return { ...raw, total, segments: emptySegments() }
  }

  if (raw.top_5_amount != null || raw.top_10_amount != null) {
    return { ...raw, total, segments: legacySegments(raw) }
  }

  return { ...raw, total, segments: emptySegments() }
}

export function normalizePayablesConcentration(
  raw: LegacyPayablesConcentration | null | undefined,
): PayablesConcentration | null {
  if (!raw) return null

  const total = Number(raw.total ?? 0)
  if (Array.isArray(raw.segments) && raw.segments.length > 0) {
    return { ...raw, total, segments: raw.segments }
  }

  if (total <= 0) {
    return { ...raw, total, segments: emptySegments() }
  }

  if (raw.top_3_amount != null || raw.top_5_amount != null || raw.top_10_amount != null) {
    return { ...raw, total, segments: legacyPayablesSegments(raw) }
  }

  return { ...raw, total, segments: emptySegments() }
}
