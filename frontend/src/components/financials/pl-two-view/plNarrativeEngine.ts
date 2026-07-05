import type {
  FinancialStatementResponse,
  FinancialStatementRow,
  PlNarrativeBullet as ApiBullet,
  PlNarrativeResponse,
} from '../../../lib/api'
import {
  fmtNarrativeEur,
  fmtNarrativeEurSigned,
} from './narrativeFmt'
import {
  comparedToPriorPeriod,
  formatAccountProse,
  openingSentence as styledOpeningSentence,
} from './proseStyle'
import { bulletHasPeriodComparison } from '../statement-two-view/narrativeTrust'
import type { PlPlanMap } from './usePlStatementData'

export type PlNarrativeTone = 'positive' | 'negative' | 'mixed' | 'neutral'

export type PlDeepLink = {
  route: string
  label: string
  snippet_id?: string
}

export interface PlNarrativeBullet {
  index: number
  line_code: string
  label: string
  priority: number
  text: string
  tone: PlNarrativeTone
  deep_links?: PlDeepLink[]
  mom_keur?: number
}

const TARGET_BULLETS = 5
const MIN_BULLETS = 4
const MAX_BULLETS = 6

const NARRATIVE_AGGREGATE_LINE_CODES = new Set([
  'TOTAL_OUTPUT',
  'GROSS_PROFIT',
  'EBITDA_ROW',
  'EBIT_ROW',
  'EBT_ROW',
  'NET_PROFIT',
])

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Lowercase first char except ∆/Δ and all-caps acronyms (EBITDA, EBIT, D&A, …). */
export function proseLabel(label: string | null | undefined): string {
  const s = (label ?? '').trim()
  if (!s) return ''
  if (s[0] === '∆' || s[0] === 'Δ') return s
  if (s === s.toUpperCase() && /[A-Z]/.test(s)) return s
  if (s.length === 1) return s.toLowerCase()
  return s[0].toLowerCase() + s.slice(1)
}

const CLIENT_DEEP_LINKS: Record<string, PlDeepLink[]> = {
  NET_SALES: [{ route: '/sales', label: 'Sales · Profitability' }],
  COM: [{ route: '/inventory', label: 'Procurement & inventory' }],
  PERS: [{ route: '/financials', label: 'Financials' }],
  PEX: [{ route: '/financials', label: 'Financials' }],
  DA: [{ route: '/financials', label: 'Financials — D&A' }],
}

const GENERIC_BULLET_RE =
  /not visible from the headline|can be reviewed in|available under|explored in Sales analytics/i

function findRow(rows: FinancialStatementRow[], match: (r: FinancialStatementRow) => boolean): FinancialStatementRow | null {
  for (const r of rows) {
    if (match(r)) return r
    if (r.children?.length) {
      const c = findRow(r.children, match)
      if (c) return c
    }
  }
  return null
}

export function isNarrativeCommentaryLine(row: FinancialStatementRow): boolean {
  return row.row_kind === 'line' && !NARRATIVE_AGGREGATE_LINE_CODES.has(row.line_code)
}

function flattenLines(rows: FinancialStatementRow[]): FinancialStatementRow[] {
  const out: FinancialStatementRow[] = []
  function walk(r: FinancialStatementRow) {
    if (isNarrativeCommentaryLine(r)) out.push(r)
    for (const c of r.children ?? []) walk(c)
  }
  for (const r of rows) walk(r)
  return out
}

function signedMom(row: FinancialStatementRow): number {
  const mom = row.deltas?.mom ?? 0
  return row.invert_delta ? -mom : mom
}

function planQualifier(planVsActual: number, planCm: number): string {
  const ratio = Math.abs(planVsActual) / Math.max(Math.abs(planCm), 1)
  if (ratio < 0.05) return 'in line with forecast'
  if (ratio < 0.15) return planVsActual >= 0 ? 'slightly above forecast' : 'slightly below forecast'
  return planVsActual >= 0 ? 'ahead of forecast' : 'below forecast'
}

function toPlanPhrase(planDelta: number): string {
  return `(${fmtNarrativeEurSigned(planDelta)} to plan)`
}

function toneFromDelta(d: number, invert: boolean): PlNarrativeTone {
  const v = invert ? -d : d
  if (v > 1e3) return 'positive'
  if (v < -1e3) return 'negative'
  return 'neutral'
}

function normalizeEntityScope(entity?: string): string {
  if (!entity || entity.toLowerCase() === 'all') return ''
  return entity.trim()
}

/** True when API narrative scope matches the requested entity (consolidated = empty scope). */
export function narrativeMatchesEntityScope(
  n: PlNarrativeResponse | null | undefined,
  entity?: string,
): boolean {
  if (!n) return false
  const expected = normalizeEntityScope(entity)
  const actual = normalizeEntityScope(n.meta?.entity_scope)
  return expected === actual
}

export function isTrustedAnnualNarrative(
  n: PlNarrativeResponse | null | undefined,
  entity?: string,
): boolean {
  if (!n?.bullets?.length) return false
  if (!narrativeMatchesEntityScope(n, entity)) return false
  if (n.meta?.llm_used) return false
  if (GENERIC_BULLET_RE.test(n.bullets.map(b => b.text).join(' '))) return false
  const algo = n.meta?.algorithm_version ?? ''
  return algo.includes('narrative_compat') || algo.includes('compat_v')
}

export function isTrustedApiNarrative(
  n: PlNarrativeResponse | null | undefined,
  entity?: string,
): boolean {
  if (!n?.bullets?.length) return false
  if (!narrativeMatchesEntityScope(n, entity)) return false
  if (n.meta?.llm_used) return false
  if (GENERIC_BULLET_RE.test(n.bullets.map(b => b.text).join(' '))) return false
  const intro = n.intro ?? ''
  if (/ was notably .+ while .+ was /i.test(intro)) return false
  const b0 = n.bullets[0]
  const t = b0?.text ?? ''
  const lbl = b0?.label ?? ''
  return (
    bulletHasPeriodComparison(t) &&
    (lbl.length === 0 || t.toLowerCase().startsWith(proseLabel(lbl).toLowerCase()))
  )
}

function topAccountInScope(
  accounts: FinancialStatementRow[],
  scopeLabel: string,
  data: FinancialStatementResponse,
  year: number,
  month: number,
): string {
  if (!accounts.length) return ''
  const sorted = [...accounts].sort(
    (a, b) =>
      Math.abs((b.deltas?.mom ?? (b.amounts?.cm ?? 0) - (b.amounts?.pm ?? 0))) -
      Math.abs((a.deltas?.mom ?? (a.amounts?.cm ?? 0) - (a.amounts?.pm ?? 0))),
  )
  const top = sorted[0]
  const mom = top.deltas?.mom ?? (top.amounts?.cm ?? 0) - (top.amounts?.pm ?? 0)
  const acct = formatAccountProse(top.label, top.line_code)
  const scope = proseLabel(scopeLabel)
  const cmp = comparedToPriorPeriod(data, year, month)
  return ` Within ${scope}, the main driver is ${acct} (${fmtNarrativeEurSigned(mom)} ${cmp}).`
}

function analysisChainSentence(
  row: FinancialStatementRow,
  data: FinancialStatementResponse,
  year: number,
  month: number,
): string {
  const parentMom = row.deltas?.mom ?? 0
  const cmp = comparedToPriorPeriod(data, year, month)
  if (Math.abs(parentMom) < 500) {
    return topAccountInScope(row.accounts ?? [], row.label, data, year, month)
  }
  const children =
    row.children?.filter(
      c => (c.row_kind === 'line' || c.row_kind === 'detail') && c.deltas && c.amounts,
    ) ?? []
  if (!children.length) {
    return topAccountInScope(row.accounts ?? [], row.label, data, year, month)
  }
  const sorted = [...children].sort(
    (a, b) => Math.abs(b.deltas!.mom) - Math.abs(a.deltas!.mom),
  )
  const lead = sorted[0]
  const leadLbl = proseLabel(lead.label)
  const share = Math.round((Math.abs(lead.deltas!.mom) / Math.abs(parentMom)) * 100)
  let text = (
    ` The move is led by ${leadLbl} (${fmtNarrativeEurSigned(lead.deltas!.mom)}, ` +
    `${share}% of the change ${cmp}).`
  )
  const leadAccounts = lead.accounts ?? []
  if (leadAccounts.length) {
    text += topAccountInScope(leadAccounts, lead.label, data, year, month)
  } else {
    text += topAccountInScope(row.accounts ?? [], lead.label, data, year, month)
  }
  if (sorted.length > 1) {
    const sec = sorted[1]
    const sShare = Math.round((Math.abs(sec.deltas!.mom) / Math.abs(parentMom)) * 100)
    text += (
      ` A secondary contributor is ${proseLabel(sec.label)} ` +
      `(${fmtNarrativeEurSigned(sec.deltas!.mom)}, ${sShare}% of the movement ${cmp}).`
    )
  }
  return text
}

function scoreLine(row: FinancialStatementRow, revCm: number, peerMomSum: number): number {
  const mom = Math.abs(row.deltas?.mom ?? 0)
  const yoy = Math.abs(row.deltas?.yoy ?? 0)
  const cm = Math.abs(row.amounts?.cm ?? 0)
  let score = mom * 0.0005 + yoy * 0.0003 + (cm / revCm) * 50
  if (row.line_code === 'NET_SALES') score += 30
  if (row.line_code === 'COM') score += 18
  const pm = row.amounts?.pm ?? 0
  if (Math.abs(pm) >= 5000 && Math.abs(mom) >= Math.abs(pm) * 3) score += 20
  if (mom >= peerMomSum * 0.15 && peerMomSum > 30000) score += 8
  return score
}

function isMaterial(row: FinancialStatementRow, revCm: number, peerMomSum: number): boolean {
  const mom = Math.abs(row.deltas?.mom ?? 0)
  const cm = Math.abs(row.amounts?.cm ?? 0)
  const pm = row.amounts?.pm ?? 0
  if (mom >= Math.max(30000, cm * 0.03)) return true
  if (cm >= revCm * 0.02 && mom >= peerMomSum * 0.12) return true
  if (Math.abs(pm) >= 5000 && Math.abs(mom) >= Math.max(Math.abs(pm) * 3, 30000)) return true
  if (Math.abs(row.deltas?.yoy ?? 0) >= Math.max(30000, cm * 0.025)) return true
  return false
}

function rankIntroDrivers(
  lines: FinancialStatementRow[],
  planMap: PlPlanMap,
): Array<{ label: string; line_code: string; delta: number; direction: string; strength: number }> {
  const drivers: Array<{
    label: string
    line_code: string
    delta: number
    direction: string
    strength: number
  }> = []
  for (const row of lines) {
    const mom = Math.abs(row.deltas?.mom ?? 0)
    const yoy = Math.abs(row.deltas?.yoy ?? 0)
    const plan = planMap[row.line_code]
    const pva = Math.abs(plan?.plan_vs_actual ?? row.amounts?.plan_vs_actual ?? 0)
    const strength = mom * 0.6 + yoy * 0.25 + pva * 0.15
    if (strength < 1) continue
    const sm = signedMom(row)
    drivers.push({
      label: row.label,
      line_code: row.line_code,
      delta: plan?.plan_vs_actual ?? row.amounts?.plan_vs_actual ?? 0,
      direction: sm >= 0 ? 'increase' : 'decrease',
      strength,
    })
  }
  drivers.sort((a, b) => b.strength - a.strength)
  return drivers
}

function buildBulletText(
  row: FinancialStatementRow,
  planMap: PlPlanMap,
  data: FinancialStatementResponse,
  year: number,
  month: number,
): string {
  const plan = planMap[row.line_code] ?? {}
  const priorLbl = data.period_grain === 'week' ? data.col_labels?.pm : undefined
  const parts = [
    styledOpeningSentence(proseLabel(row.label), {
      invert_delta: row.invert_delta,
      amounts: row.amounts ?? undefined,
      deltas: row.deltas ?? undefined,
    }, plan, year, month, priorLbl),
    analysisChainSentence(row, data, year, month),
  ]
  if (/other|miscellaneous|sonstige/i.test(row.label)) {
    parts.push(
      ' Miscellaneous and “other” sub-accounts include one-off postings that warrant validation at account level.',
    )
  }
  return parts.filter(Boolean).join('')
}

/** Intro when narrative API is unavailable (pl_narrative_v2 shape). */
export function buildPlNarrativeIntro(
  data: FinancialStatementResponse | null,
  planMap: PlPlanMap,
  year: number,
  month: number,
): string | null {
  if (!data?.rows?.length) return null
  const np = findRow(data.rows, r => r.line_code === 'NET_PROFIT')
  if (!np?.amounts) return null

  const plan = planMap.NET_PROFIT ?? data.plan?.lines?.find(l => l.line_code === 'NET_PROFIT')
  const ytd = np.amounts.ytd
  const pva = plan?.plan_vs_actual ?? np.amounts.plan_vs_actual ?? 0
  const pcm = plan?.plan_cm ?? np.amounts.plan_cm ?? 0
  const cov = plan?.coverage_pct ?? null
  const periodLabel = `YTD${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`
  const cmLabel = data.col_labels?.cm ?? `${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`

  const lines = flattenLines(data.rows)
  const drivers = rankIntroDrivers(lines, planMap).slice(0, 2)

  const covPart = cov != null ? ` leading to a coverage of ${cov}%` : ''
  const qual = planQualifier(pva, pcm)

  let driverPart = ''
  if (drivers.length) {
    const d0 = drivers[0]
    let d0Txt = `a ${d0.direction} in ${d0.label} ${toPlanPhrase(d0.delta)}`
    if (drivers.length > 1) {
      const d1 = drivers[1]
      d0Txt += ` and a ${d1.direction} in ${d1.label} ${toPlanPhrase(d1.delta)}`
    }
    const periodNoun = data.period_grain === 'week' ? 'week' : 'month'
    driverPart = ` The ${periodNoun} was primarily shaped by ${d0Txt}.`
    const sameDir = drivers.every(d => d.direction === drivers[0].direction)
    if (sameDir && data.period_grain !== 'week') {
      driverPart += ' Recent months suggest the trend has continued in a similar direction.'
    }
  }

  return (
    `As of ${periodLabel} the group's net profit amounted to ${fmtNarrativeEur(ytd)}${covPart}, ` +
    `and actuals for ${cmLabel} were ${qual} (${fmtNarrativeEurSigned(pva)} to plan).${driverPart} ` +
    `To conclude, key drivers consist of:`
  )
}

/** Fix legacy mis-cased acronyms at bullet start (eBITDA → EBITDA). */
function fixLeadingAcronym(body: string): string {
  const m = body.match(/^e(BITDA|BIT|BT)\b/)
  if (m) return `E${m[1]}${body.slice(m[0].length)}`
  return body
}

/** Capitalize the first character only when the bullet opens with this line's position label. */
function capitalizeBulletLeadingLabel(body: string, label: string): string {
  const normalized = fixLeadingAcronym(body)
  if (!normalized.length) return normalized
  const first = normalized[0]
  if (first === '∆' || first === 'Δ') return normalized

  const bodyLower = normalized.toLowerCase()
  const labelKey = label.trim().toLowerCase()
  const proseKey = proseLabel(label).toLowerCase()
  const opensWithPosition =
    (labelKey.length > 0 && bodyLower.startsWith(labelKey)) ||
    (proseKey.length > 0 && bodyLower.startsWith(proseKey))
  if (!opensWithPosition) return normalized

  return first.toUpperCase() + normalized.slice(1)
}

/** Bullet body for display: no duplicate "Label:" prefix; leading position title-cased once. */
export function bulletDisplayText(b: PlNarrativeBullet): string {
  const t = b.text.trim()
  const withColon = `${b.label}:`
  const body = t.startsWith(withColon) ? t.slice(withColon.length).trim() : t
  return capitalizeBulletLeadingLabel(body, b.label)
}

export function mapApiBulletsToUi(
  bullets: ApiBullet[],
  rows: FinancialStatementRow[],
): PlNarrativeBullet[] {
  return bullets.map(b => ({
    index: b.index,
    line_code: b.line_code,
    label: b.label,
    priority: 0,
    text: b.text,
    tone: (b.tone as PlNarrativeTone) ?? 'neutral',
    deep_links: (b as ApiBullet & { deep_links?: PlDeepLink[] }).deep_links,
    mom_keur: rowMomKeur(rows, b.line_code),
  }))
}

function rowMomKeur(rows: FinancialStatementRow[], lineCode: string): number | undefined {
  const row = findRow(rows, r => r.line_code === lineCode)
  if (row?.deltas?.mom == null) return undefined
  return Math.round((row.deltas.mom / 1000) * 100) / 100
}

export function buildPlNarrativeBullets(
  data: FinancialStatementResponse | null,
  planMap: PlPlanMap,
  year: number,
  month: number,
  maxVisible = TARGET_BULLETS,
): PlNarrativeBullet[] {
  if (!data?.rows?.length) return []

  const cap = Math.max(MIN_BULLETS, Math.min(maxVisible, MAX_BULLETS))
  const revRow = findRow(data.rows, r => r.line_code === 'TOTAL_OUTPUT' || r.line_code === 'NET_SALES')
  const revCm = Math.abs(revRow?.amounts?.cm ?? 1) || 1

  const lines = flattenLines(data.rows).filter(r => r.amounts && r.deltas)
  const peerMomSum = lines.reduce((s, r) => s + Math.abs(r.deltas!.mom), 0)

  const scored = lines
    .filter(r => isMaterial(r, revCm, peerMomSum))
    .map(row => ({ row, score: scoreLine(row, revCm, peerMomSum) }))

  const keep = new Set(
    [...scored].sort((a, b) => b.score - a.score).slice(0, cap).map(s => s.row.line_code),
  )

  let index = 0
  const bullets: PlNarrativeBullet[] = []
  for (const { row } of scored) {
    if (!keep.has(row.line_code)) continue
    index += 1
    const base = row.line_code.split('::')[0]
    bullets.push({
      index,
      line_code: row.line_code,
      label: row.label,
      priority: Math.abs(row.deltas!.mom),
      text: buildBulletText(row, planMap, data, year, month),
      tone: toneFromDelta(row.deltas!.mom, row.invert_delta),
      deep_links: CLIENT_DEEP_LINKS[base] ?? CLIENT_DEEP_LINKS[row.line_code],
      mom_keur: Math.round((row.deltas!.mom / 1000) * 100) / 100,
    })
  }

  return bullets
}

/** Stable client-side narrative for UI + export (avoids API/cache overwriting good copy). */
export function buildClientNarrativeResponse(
  data: FinancialStatementResponse,
  planMap: PlPlanMap,
  year: number,
  month: number,
  maxBullets = TARGET_BULLETS,
): PlNarrativeResponse {
  const bullets = buildPlNarrativeBullets(data, planMap, year, month, maxBullets)
  const intro = buildPlNarrativeIntro(data, planMap, year, month) ?? ''
  const np = findRow(data.rows, r => r.line_code === 'NET_PROFIT')
  const plan = planMap.NET_PROFIT
  const drivers = rankIntroDrivers(flattenLines(data.rows), planMap).slice(0, 2)

  return {
    headline: 'Key drivers',
    intro,
    intro_facts: {
      period_label: `YTD${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`,
      group_label: 'the group\'s',
      net_profit_ytd: np?.amounts?.ytd ?? 0,
      coverage_pct: plan?.coverage_pct ?? null,
      cm_month_label: data.col_labels?.cm ?? `${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`,
      cm_vs_plan: plan?.plan_vs_actual ?? np?.amounts?.plan_vs_actual ?? 0,
      cm_vs_plan_qualifier: planQualifier(
        plan?.plan_vs_actual ?? np?.amounts?.plan_vs_actual ?? 0,
        plan?.plan_cm ?? np?.amounts?.plan_cm ?? 0,
      ),
      primary_drivers: drivers.map(d => ({
        label: d.label,
        delta: d.delta,
        direction: d.direction,
      })),
    },
    bullets: bullets.map(b => ({
      index: b.index,
      line_code: b.line_code,
      label: b.label,
      text: b.text,
      tone: b.tone,
      deep_links: b.deep_links,
    })),
    entity_split: null,
    meta: {
      algorithm_version: 'pl_narrative_v2_client',
      llm_used: false,
      max_bullets: maxBullets,
      cache_hit: false,
    },
  }
}
