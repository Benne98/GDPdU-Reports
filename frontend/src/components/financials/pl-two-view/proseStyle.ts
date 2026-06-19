/** Mirror backend narrative wording (period labels, accounts, opening variety). */

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function priorMonth(year: number, month: number): { year: number; month: number } {
  return month > 1 ? { year, month: month - 1 } : { year: year - 1, month: 12 }
}

export function monthLabel(year: number, month: number): string {
  return `${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`
}

export function priorMonthLabel(year: number, month: number): string {
  const pm = priorMonth(year, month)
  return monthLabel(pm.year, pm.month)
}

export function comparedToMonth(year: number, month: number): string {
  return `compared to ${priorMonthLabel(year, month)}`
}

/** Week-aware: uses statement col_labels.pm when in weekly grain. */
export function comparedToPriorPeriod(
  data: { period_grain?: string; col_labels?: { pm?: string } },
  year: number,
  month: number,
): string {
  if (data.period_grain === 'week' && data.col_labels?.pm) {
    return `compared to ${data.col_labels.pm.replace(/^KW/i, 'CW')}`
  }
  return comparedToMonth(year, month)
}

export function sanitizeAccountName(name: string): string {
  return name.replace(/\s*\|\s*/g, ' ').trim()
}

function accountNumber(glAccountId: string | undefined, accountName: string): string {
  const raw = (glAccountId || '').trim()
  if (/^\d+$/.test(raw)) return raw
  const m = (accountName || '').match(/(\d{4,})/)
  return m ? m[1] : raw || '—'
}

/** Markdown italics for account names — rendered in PlNarrativeRichText. */
export function formatAccountProse(accountName: string | undefined, glAccountId?: string): string {
  const name = sanitizeAccountName(accountName || '')
  const num = accountNumber(glAccountId, name)
  if (name && num && name !== glAccountId) return `account ${num} *${name}*`
  if (name) return `account *${name}*`
  if (num) return `account ${num}`
  return 'one account'
}

export function openingSentence(
  label: string,
  row: { invert_delta?: boolean; amounts?: { pm?: number; ytd?: number }; deltas?: { mom?: number } },
  plan: { ytd_plan?: number },
  year: number,
  month: number,
  priorLabel?: string,
): string {
  const proseLbl = label.trim()
  const inv = !!row.invert_delta
  let sm = row.deltas?.mom ?? 0
  if (inv) sm = -sm
  const pmLbl = priorLabel ?? priorMonthLabel(year, month)
  const pm = row.amounts?.pm ?? 0
  const mom = row.deltas?.mom ?? 0
  let pctPart = ''
  if (Math.abs(pm) >= 500) {
    pctPart = ` by ${Math.abs((mom / Math.abs(pm)) * 100).toFixed(1)}%`
  }
  let ytdClause = ''
  const ytd = row.amounts?.ytd ?? 0
  const ytdPlan = plan.ytd_plan ?? 0
  if (Math.abs(ytdPlan) >= 1) {
    const gapPct = ((ytd - ytdPlan) / Math.abs(ytdPlan)) * 100
    const behind = inv ? ytd > ytdPlan : ytd < ytdPlan
    ytdClause = `, leaving ${proseLbl} ${Math.abs(gapPct).toFixed(0)}% ${behind ? 'behind' : 'ahead of'} the YTD plan`
  }
  const seed = proseLbl.length
  const up = sm >= 0
  if (up) {
    const templates = [
      `${proseLbl} increased${pctPart} compared to ${pmLbl}${ytdClause}.`,
      `${proseLbl} was higher${pctPart} compared to ${pmLbl}${ytdClause}.`,
      `${proseLbl} rose${pctPart} compared to ${pmLbl}${ytdClause}.`,
      `Compared to ${pmLbl}, ${proseLbl} moved up${pctPart}${ytdClause}.`,
      `${proseLbl} strengthened${pctPart} versus ${pmLbl}${ytdClause}.`,
    ]
    return templates[seed % templates.length]
  }
  const templates = [
    `${proseLbl} decreased${pctPart} compared to ${pmLbl}${ytdClause}.`,
    `${proseLbl} was lower${pctPart} compared to ${pmLbl}${ytdClause}.`,
    `${proseLbl} fell${pctPart} compared to ${pmLbl}${ytdClause}.`,
    `Compared to ${pmLbl}, ${proseLbl} eased${pctPart}${ytdClause}.`,
    `${proseLbl} softened${pctPart} versus ${pmLbl}${ytdClause}.`,
  ]
  return templates[seed % templates.length]
}
