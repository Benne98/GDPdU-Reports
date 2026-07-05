import type { BudgetPartnerRow } from './gdpduApi';

export const PARTNER_RANK_BANDS = [
  { id: 'top5', label: 'Top 5', from: 0, to: 5 },
  { id: 'top6_10', label: 'Top 6–10', from: 5, to: 10 },
  { id: 'top11_20', label: 'Top 11–20', from: 10, to: 20 },
  { id: 'rank21plus', label: 'Rank 21+', from: 20, to: null as number | null },
] as const;

export type PartnerBandId = (typeof PARTNER_RANK_BANDS)[number]['id'];

export interface PartnerBandGroup {
  id: PartnerBandId;
  label: string;
  partners: BudgetPartnerRow[];
}

/** Sort partners by historical annual (desc) and split into rank bands. */
export function groupPartnersByRank(partners: BudgetPartnerRow[]): PartnerBandGroup[] {
  const sorted = [...partners].sort((a, b) => (b.annual ?? 0) - (a.annual ?? 0));
  return PARTNER_RANK_BANDS.map((band) => ({
    id: band.id,
    label: band.label,
    partners: sorted.slice(band.from, band.to ?? undefined),
  })).filter((g) => g.partners.length > 0);
}
