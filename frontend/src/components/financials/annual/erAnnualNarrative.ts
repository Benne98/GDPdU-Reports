/**
 * Deterministic key-drivers narrative for the annual P&L view.
 * Replaces the sell-side FDD boilerplate with data-driven YoY + YTD deltas.
 */
import type { ErStatementRow, PlNarrativeResponse } from '../../../lib/api'
import { fmtKpi } from '../../../lib/fmt'

export type AnnualNarrativeTone = 'positive' | 'negative' | 'neutral'

export interface AnnualNarrativeBullet {
  index: number
  line_code: string
  label: string
  text: string
  tone: AnnualNarrativeTone
}

export interface AnnualNarrativeResult {
  intro: string
  bullets: AnnualNarrativeBullet[]
}

// Line codes that are aggregate summaries — skip these for bullet selection
const AGGREGATE_LINE_CODES = new Set([
  'TOTAL_OUTPUT',
  'GROSS_PROFIT',
  'EBITDA_ROW',
  'EBIT_ROW',
  'EBT_ROW',
  'NET_PROFIT',
  'TOTAL_EXPENSES',
])

const CF_AGGREGATE_LINE_CODES = new Set([
  'CF_CFO',
  'CF_CFI',
  'CF_CFF',
  'CF_FCF',
  'CF_FREE_CASH_FLOW',
  'CF_NCF',
  'NET_CASH_FLOW',
  'NCF',
])

function amountFor(row: ErStatementRow | null | undefined, key: string): number {
  if (!row?.amounts) return 0
  return Number(row.amounts[key] ?? 0) || 0
}

function pctChange(next: number, prev: number): number | null {
  if (Math.abs(prev) < 1e-3) return null
  return ((next - prev) / Math.abs(prev)) * 100
}

function collectMappingRows(rows: ErStatementRow[]): ErStatementRow[] {
  const out: ErStatementRow[] = []
  function walk(r: ErStatementRow) {
    if (
      r.row_kind !== 'title' &&
      r.row_kind !== 'kpi' &&
      r.row_kind !== 'kpi_header' &&
      r.amounts != null &&
      !AGGREGATE_LINE_CODES.has(String(r.line_code ?? '').toUpperCase())
    ) {
      out.push(r)
    }
    for (const c of r.children ?? []) walk(c)
    for (const a of r.accounts ?? []) walk(a)
  }
  for (const r of rows) walk(r)
  return out
}

function findRow(rows: ErStatementRow[], matcher: (r: ErStatementRow) => boolean): ErStatementRow | null {
  for (const r of rows) {
    if (matcher(r)) return r
    const c = findRow(r.children ?? [], matcher)
    if (c) return c
    const a = findRow(r.accounts ?? [], matcher)
    if (a) return a
  }
  return null
}

/** Materiality score: max absolute delta across YoY and YTD comparisons */
function materialityScore(row: ErStatementRow): number {
  const fy2 = amountFor(row, 'fy2')
  const fy3 = amountFor(row, 'fy3')
  const ytd = amountFor(row, 'ytd')
  const ytdPy = amountFor(row, 'ytd_py')
  return Math.max(Math.abs(fy3 - fy2), Math.abs(ytd - ytdPy))
}

function buildBullet(row: ErStatementRow, index: number, fy3Label: string, fy2Label: string, monthTag: string): AnnualNarrativeBullet {
  const fy2 = amountFor(row, 'fy2')
  const fy3 = amountFor(row, 'fy3')
  const ytd = amountFor(row, 'ytd')
  const ytdPy = amountFor(row, 'ytd_py')

  const yoyDelta = fy3 - fy2
  const ytdDelta = ytd - ytdPy
  const yoyPct = pctChange(fy3, fy2)
  const ytdPct = pctChange(ytd, ytdPy)

  const yoySign = yoyDelta >= 0 ? '+' : ''
  const ytdSign = ytdDelta >= 0 ? '+' : ''

  let text = `${fy3Label}: ${fmtKpi(fy3)}`
  if (yoyPct != null) {
    text += ` (${yoySign}${fmtKpi(yoyDelta)} / ${yoySign}${yoyPct.toLocaleString('en-US', { maximumFractionDigits: 1 })}% vs ${fy2Label})`
  }
  if (Math.abs(ytdDelta) > 1 || ytdPct != null) {
    text += `; YTD${monthTag}: ${fmtKpi(ytd)}`
    if (ytdPct != null) {
      text += ` (${ytdSign}${fmtKpi(ytdDelta)} / ${ytdSign}${ytdPct.toLocaleString('en-US', { maximumFractionDigits: 1 })}% YoY)`
    }
  }

  // Tone: positive = revenue line grew, negative = cost line grew (invert_delta-like logic)
  const isRevenueish = !row.invert_delta
  const tone: AnnualNarrativeTone =
    yoyDelta === 0 ? 'neutral'
    : isRevenueish
      ? yoyDelta > 0 ? 'positive' : 'negative'
      : yoyDelta < 0 ? 'positive' : 'negative'

  return { index, line_code: String(row.line_code ?? row.id), label: row.label, text, tone }
}

export function buildAnnualNarrative(
  rows: ErStatementRow[],
  year: number,
  month: number,
  fy3Label: string,
  fy2Label: string,
): AnnualNarrativeResult {
  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const monthTag = monthNames[(month - 1) % 12] ?? 'YTD'

  // Find key summary rows for intro
  const netProfitRow = findRow(rows, r => /net.?profit|net.?income|ergebnis|jahresüberschuss/i.test(r.label) || r.line_code === 'NET_PROFIT')
  const revenueRow = findRow(rows, r => AGGREGATE_LINE_CODES.has(String(r.line_code).toUpperCase()) && /output|revenue|umsatz/i.test(r.label))

  const fy3Net = amountFor(netProfitRow, 'fy3')
  const ytdNet = amountFor(netProfitRow, 'ytd')
  const fy3Rev = amountFor(revenueRow, 'fy3')
  const ytdRev = amountFor(revenueRow, 'ytd')

  let intro = `${fy3Label}`
  if (fy3Net !== 0) intro += `: net profit ${fmtKpi(fy3Net)}`
  if (fy3Rev !== 0) intro += `, revenue ${fmtKpi(fy3Rev)}`
  intro += `. YTD${monthTag}${year}`
  if (ytdNet !== 0) intro += `: net profit ${fmtKpi(ytdNet)}`
  if (ytdRev !== 0) intro += `, revenue ${fmtKpi(ytdRev)}`
  intro += '.'

  // Pick top-N most material rows
  const candidates = collectMappingRows(rows)
  const ranked = [...candidates].sort((a, b) => materialityScore(b) - materialityScore(a))
  const TOP_N = 5
  const selected = ranked.slice(0, TOP_N)

  const bullets = selected.map((row, idx) =>
    buildBullet(row, idx + 1, fy3Label, fy2Label, `${monthTag}${String(year).slice(-2)}`),
  )

  return { intro, bullets }
}

function collectCfMappingRows(rows: ErStatementRow[]): ErStatementRow[] {
  const out: ErStatementRow[] = []
  function walk(r: ErStatementRow) {
    const lc = String(r.line_code ?? '').toUpperCase()
    if (
      r.row_kind !== 'title' &&
      r.row_kind !== 'kpi' &&
      r.row_kind !== 'kpi_header' &&
      r.amounts != null &&
      !CF_AGGREGATE_LINE_CODES.has(lc) &&
      !/^(cash flow from operating|free cash flow|net cash flow)/i.test((r.label || '').trim())
    ) {
      out.push(r)
    }
    for (const c of r.children ?? []) walk(c)
    for (const a of r.accounts ?? []) walk(a)
  }
  for (const r of rows) walk(r)
  return out
}

export function buildAnnualCfNarrative(
  rows: ErStatementRow[],
  year: number,
  month: number,
  fy3Label: string,
  fy2Label: string,
): AnnualNarrativeResult {
  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const monthTag = monthNames[(month - 1) % 12] ?? 'YTD'

  const ocfRow = findRow(rows, r => /cash flow from operating/i.test(r.label) || r.line_code === 'CF_CFO')
  const fcfRow = findRow(rows, r => /free cash flow/i.test(r.label) || r.line_code === 'CF_FCF')
  const ncfRow = findRow(rows, r => /net cash flow/i.test(r.label))

  const fy3Ocf = amountFor(ocfRow, 'fy3')
  const ytdOcf = amountFor(ocfRow, 'ytd')
  const fy3Fcf = amountFor(fcfRow, 'fy3')

  let intro = `${fy3Label}`
  if (fy3Ocf !== 0) intro += `: operating cash flow ${fmtKpi(fy3Ocf)}`
  if (fy3Fcf !== 0) intro += `, free cash flow ${fmtKpi(fy3Fcf)}`
  intro += `. YTD${monthTag}${year}`
  if (ytdOcf !== 0) intro += `: operating cash flow ${fmtKpi(ytdOcf)}`
  if (ncfRow && amountFor(ncfRow, 'ytd') !== 0) {
    intro += `, net cash flow ${fmtKpi(amountFor(ncfRow, 'ytd'))}`
  }
  intro += '.'

  const candidates = collectCfMappingRows(rows)
  const ranked = [...candidates].sort((a, b) => materialityScore(b) - materialityScore(a))
  const selected = ranked.slice(0, 5)
  const bullets = selected.map((row, idx) =>
    buildBullet(row, idx + 1, fy3Label, fy2Label, `${monthTag}${String(year).slice(-2)}`),
  )

  return { intro, bullets }
}

/** Client-side fallback when annual narrative snapshot is cold or untrusted. */
export function buildAnnualFlowNarrativeResponse(
  rows: ErStatementRow[],
  year: number,
  month: number,
  fy3Label: string,
  fy2Label: string,
  entity?: string,
): PlNarrativeResponse {
  const r = buildAnnualNarrative(rows, year, month, fy3Label, fy2Label)
  return {
    headline: '',
    intro: r.intro,
    intro_facts: {},
    bullets: r.bullets.map(b => ({
      index: b.index,
      line_code: b.line_code,
      label: b.label,
      text: b.text,
      tone: b.tone === 'neutral' ? 'mixed' : b.tone,
    })),
    meta: { entity_scope: entity ?? 'all', llm_used: false, algorithm_version: 'client-annual-flow-v1' },
  } as PlNarrativeResponse
}

export function buildAnnualCfNarrativeResponse(
  rows: ErStatementRow[],
  year: number,
  month: number,
  fy3Label: string,
  fy2Label: string,
  entity?: string,
): PlNarrativeResponse {
  const r = buildAnnualCfNarrative(rows, year, month, fy3Label, fy2Label)
  return {
    headline: '',
    intro: r.intro,
    intro_facts: {},
    bullets: r.bullets.map(b => ({
      index: b.index,
      line_code: b.line_code,
      label: b.label,
      text: b.text,
      tone: b.tone === 'neutral' ? 'mixed' : b.tone,
    })),
    meta: { entity_scope: entity ?? 'all', llm_used: false, algorithm_version: 'client-annual-cf-v1' },
  } as PlNarrativeResponse
}

function materialitySnapshot(row: ErStatementRow): number {
  const fy = amountFor(row, 'fy')
  const fyPy = amountFor(row, 'fy_py')
  const cm = amountFor(row, 'cm')
  const cmPy = amountFor(row, 'cm_py')
  return Math.max(Math.abs(fy - fyPy), Math.abs(cm - cmPy))
}

function buildSnapshotBullet(
  row: ErStatementRow,
  index: number,
  fyLabel: string,
  fyPyLabel: string,
  cmLabel: string,
): AnnualNarrativeBullet {
  const fy = amountFor(row, 'fy')
  const fyPy = amountFor(row, 'fy_py')
  const cm = amountFor(row, 'cm')
  const cmPy = amountFor(row, 'cm_py')
  const fyDelta = fy - fyPy
  const cmDelta = cm - cmPy
  const fyPct = pctChange(fy, fyPy)
  const cmPct = pctChange(cm, cmPy)
  const fySign = fyDelta >= 0 ? '+' : ''
  const cmSign = cmDelta >= 0 ? '+' : ''

  let text = `${fyLabel}: ${fmtKpi(fy)}`
  if (fyPct != null) {
    text += ` (${fySign}${fmtKpi(fyDelta)} / ${fySign}${fyPct.toLocaleString('en-US', { maximumFractionDigits: 1 })}% vs ${fyPyLabel})`
  }
  if (Math.abs(cmDelta) > 1 || cmPct != null) {
    text += `; ${cmLabel}: ${fmtKpi(cm)}`
    if (cmPct != null) {
      text += ` (${cmSign}${fmtKpi(cmDelta)} / ${cmSign}${cmPct.toLocaleString('en-US', { maximumFractionDigits: 1 })}% YoY)`
    }
  }

  const isRevenueish = !row.invert_delta
  const tone: AnnualNarrativeTone =
    fyDelta === 0 ? 'neutral'
    : isRevenueish
      ? fyDelta > 0 ? 'positive' : 'negative'
      : fyDelta < 0 ? 'positive' : 'negative'

  return { index, line_code: String(row.line_code ?? row.id), label: row.label, text, tone }
}

/** Snapshot statements (BS, WC) — client narrative fallback for annual report view. */
export function buildAnnualSnapshotNarrative(
  rows: ErStatementRow[],
  fyLabel: string,
  fyPyLabel: string,
  cmLabel: string,
): AnnualNarrativeResult {
  const candidates = collectMappingRows(rows)
  const ranked = [...candidates].sort((a, b) => materialitySnapshot(b) - materialitySnapshot(a))
  const selected = ranked.slice(0, 5)
  const bullets = selected.map((row, idx) =>
    buildSnapshotBullet(row, idx + 1, fyLabel, fyPyLabel, cmLabel),
  )
  const intro = `Annual snapshot — ${fyLabel} vs ${fyPyLabel}; current month ${cmLabel}.`
  return { intro, bullets }
}

export function buildAnnualSnapshotNarrativeResponse(
  rows: ErStatementRow[],
  fyLabel: string,
  fyPyLabel: string,
  cmLabel: string,
  entity?: string,
): PlNarrativeResponse {
  const r = buildAnnualSnapshotNarrative(rows, fyLabel, fyPyLabel, cmLabel)
  return {
    headline: '',
    intro: r.intro,
    intro_facts: {},
    bullets: r.bullets.map(b => ({
      index: b.index,
      line_code: b.line_code,
      label: b.label,
      text: b.text,
      tone: b.tone === 'neutral' ? 'mixed' : b.tone,
    })),
    meta: { entity_scope: entity ?? 'all', llm_used: false, algorithm_version: 'client-annual-snapshot-v1' },
  } as PlNarrativeResponse
}
