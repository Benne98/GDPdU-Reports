import type { PlLineDetailResponse } from '../../../lib/api'
import { fmtNarrativeEur } from './narrativeFmt'

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function cmLabel(year: number, month: number): string {
  return `${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`
}

export function buildAccountAnalysis(
  detail: PlLineDetailResponse,
  year: number,
  month: number,
): string {
  const periodLbl = cmLabel(year, month)
  const timelines = detail.accounts_timeline ?? []
  if (!timelines.length && detail.accounts.length) {
    const top = [...detail.accounts].sort((a, b) => Math.abs(b.balance_cm) - Math.abs(a.balance_cm))[0]
    return (
      `In ${periodLbl}, account ${top.account_name || top.gl_account_id} carried the largest balance ` +
      `at ${fmtNarrativeEur(top.balance_cm * 1000)} (movement vs prior month: ${fmtNarrativeEur(top.delta * 1000)}).`
    )
  }
  if (!timelines.length) {
    return 'No account-level balances are available for this line in the selected period.'
  }

  let best = timelines[0]
  let bestSpike = 0
  for (const acc of timelines) {
    const series = acc.series
    if (!series.length) continue
    const cmVal = series[series.length - 1]?.value_keur ?? 0
    const prior = series.slice(0, -1).map(p => p.value_keur)
    const avgPrior = prior.length ? prior.reduce((s, v) => s + v, 0) / prior.length : 0
    const spike = Math.abs(cmVal - avgPrior)
    if (spike > bestSpike) {
      bestSpike = spike
      best = acc
    }
  }

  const series = best.series
  const cmVal = series[series.length - 1]?.value_keur ?? 0
  const maxMonth = [...series].sort((a, b) => Math.abs(b.value_keur) - Math.abs(a.value_keur))[0]
  const momAcc = detail.accounts.find(a => a.gl_account_id === best.gl_account_id)

  return (
    `Account ${best.account_name} shows the clearest pattern over the last ${series.length} months. ` +
    `In ${periodLbl} the balance was ${fmtNarrativeEur(cmVal * 1000)}` +
    (maxMonth && maxMonth.label !== periodLbl
      ? `; the peak month in the window was ${maxMonth.label} at ${fmtNarrativeEur(maxMonth.value_keur * 1000)}.`
      : '. ') +
    (momAcc
      ? `Versus the prior month this account moved by ${fmtNarrativeEur(momAcc.delta * 1000)}.`
      : '')
  )
}

export function buildBookingsAnalysis(detail: PlLineDetailResponse, year: number, month: number): string {
  const bookings = detail.top_bookings ?? []
  if (!bookings.length) {
    return `No individual postings were found for ${cmLabel(year, month)} on this P&L line.`
  }

  const total = bookings.reduce((s, b) => s + Math.abs(b.amount), 0)
  const top = bookings[0]
  const share = total > 0 ? Math.round((Math.abs(top.amount) / total) * 100) : 0
  const counter =
    top.counter_account_name || top.counter_gl_account_id
      ? ` Counter-account: ${top.counter_account_name || top.counter_gl_account_id}.`
      : ''

  const concentrated = share >= 40
  return (
    `The largest posting (ID ${top.booking_line_id}) amounts to ${fmtNarrativeEur(top.amount * 1000)}` +
    ` (${share}% of the top ${bookings.length} postings by absolute value).${counter}` +
    (concentrated
      ? ' This concentration suggests a single event rather than a broad-based movement across many bookings.'
      : ' Postings are spread across several lines; review the table for secondary items.')
  )
}
