import type { PlLineDetailResponse } from '../../../lib/api'

type Driver = { account_name: string; delta_keur?: number }

function topDriver(detail: PlLineDetailResponse | null): Driver | undefined {
  const drivers = (
    detail?.outlier_facts as { driver_accounts?: Driver[] } | undefined
  )?.driver_accounts
  return drivers?.[0]
}

/** Plain-language intro: ties charts on the right to what the expert highlights. */
export function buildExpertContext(
  lineLabel: string,
  periodLabel: string,
  detail: PlLineDetailResponse | null,
): string {
  if (!detail) {
    return 'The charts on the right summarise this line. Findings will appear here once data is loaded.'
  }

  const driver = topDriver(detail)
  const accountsNote = detail.commentary?.accounts?.trim()

  if (driver?.account_name) {
    const base =
      `As shown on the right, the largest impact on ${lineLabel} in ${periodLabel} ` +
      `comes from ${driver.account_name}.`
    if (accountsNote && accountsNote.length <= 280) {
      return `${base} ${accountsNote}`
    }
    return (
      `${base} You may want to look at wider drivers too — for example volume, ` +
      `pricing, energy costs, or one-off postings in the table below.`
    )
  }

  if (accountsNote) return accountsNote

  return (
    `The charts on the right show how ${lineLabel} moved in ${periodLabel}. ` +
    `No single account dominated the change — consider mix, seasonality, or external cost pressures.`
  )
}

export function lineTotalsFromAccounts(detail: PlLineDetailResponse | null): {
  cmKeur: number
  pmKeur: number
  momPct: number | null
} {
  const accounts = detail?.accounts ?? []
  const cmKeur = accounts.reduce((s, a) => s + (a.balance_cm ?? 0), 0)
  const pmKeur = accounts.reduce((s, a) => s + (a.balance_pm ?? 0), 0)
  const momPct =
    Math.abs(pmKeur) > 0.01 ? Math.round(((cmKeur - pmKeur) / Math.abs(pmKeur)) * 1000) / 10 : null
  return { cmKeur, pmKeur, momPct }
}
