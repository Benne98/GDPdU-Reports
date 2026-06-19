/** Narrative prose amounts: € 1.881k (aligned with backend pl_narrative formatting). */

function narrativeThousands(eur: number): string {
  const thousands = Math.round(Math.abs(eur) / 1000)
  return thousands.toLocaleString('de-DE', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })
}

export function fmtNarrativeEur(eur: number): string {
  const core = narrativeThousands(eur)
  return eur < 0 ? `(€ ${core}k)` : `€ ${core}k`
}

/** Signed delta; + / - stay ASCII regardless of surrounding prose casing. */
export function fmtNarrativeEurSigned(eur: number): string {
  const sign = eur >= 0 ? '+' : '-'
  const core = narrativeThousands(eur)
  return `${sign} € ${core}k`
}

/** Lowercase P&L line label for prose only — never apply to amount strings. */
export function narrativeLabelPhrase(label: string): string {
  return label.trim().toLowerCase()
}
