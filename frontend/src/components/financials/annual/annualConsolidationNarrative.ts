/**
 * Key drivers for annual entity-breakdown group report (YTD per entity + consolidation).
 * Client prose mirrors consolidated P&L narrative depth; API YoY/YTD bullets are enriched
 * with per-entity attribution when available.
 */
import type { ConsolidationResponse, ConsolidationRow, PlNarrativeResponse } from '../../../lib/api'
import { fmtNarrativeEur, fmtNarrativeEurSigned } from '../pl-two-view/narrativeFmt'
import { proseLabel, isTrustedAnnualNarrative } from '../pl-two-view/plNarrativeEngine'
import { isTrustedApiBsNarrative } from '../statement-two-view/bs/bsNarrativeEngine'
import { isTrustedApiCfNarrative } from '../statement-two-view/cf/cfNarrativeEngine'
import { isTrustedApiWcNarrative } from '../statement-two-view/wc/wcNarrativeEngine'
import type { FinStatementKind } from '../statement-two-view/statementTypes'
import type { AnnualNarrativeTone } from './erAnnualNarrative'
import {
  indexConsolidationRows,
  resolveConsolidationRowIdForBullet,
} from './annualReportMarkers'

const SKIP_ROW_KINDS = new Set(['title', 'kpi', 'kpi_header', 'account', 'detail'])
const AGGREGATE_LABELS = /^(total output|gross profit|ebitda|ebit|ebt|net profit)/i
const BS_AGGREGATE_LABELS = /^(total assets|total equity|total liabilities|equity & liabilities|net working capital)/i
const CF_AGGREGATE_LABELS =
  /^(cash flow from operating|free cash flow|net cash flow|cash flow from investing|cash flow from financing)/i
const TOP_N = 5
const MATERIAL_EUR = 500

type EntityVal = { label: string; code: string; v: number }

function capFirst(s: string): string {
  const t = s.trim()
  if (!t) return t
  return t[0].toUpperCase() + t.slice(1)
}

function entitySpread(row: ConsolidationRow, codes: string[]): number {
  const vals = codes.map(c => Number(row.entity_amounts[c] ?? 0))
  if (vals.length < 2) return Math.abs(row.consolidation)
  return Math.max(...vals) - Math.min(...vals)
}

function rankEntities(row: ConsolidationRow, entities: ConsolidationResponse['entities']): EntityVal[] {
  return [...entities]
    .map(e => ({ label: e.label, code: e.code, v: Number(row.entity_amounts[e.code] ?? 0) }))
    .sort((a, b) => Math.abs(b.v) - Math.abs(a.v))
}

function collectDirectConsolidationRowIds(rows: ConsolidationRow[]): string[] {
  const out: string[] = []
  function walk(rs: ConsolidationRow[]) {
    for (const r of rs) {
      if (r.row_kind === 'line' || r.row_kind === 'subtotal' || r.row_kind === 'detail') {
        out.push(r.id)
      }
      walk(r.children ?? [])
    }
  }
  walk(rows)
  return out
}

function findConsolRow(rows: ConsolidationRow[], pattern: RegExp): ConsolidationRow | undefined {
  for (const r of rows) {
    if (pattern.test(r.label) || pattern.test(r.id)) return r
    const child = findConsolRow(r.children ?? [], pattern)
    if (child) return child
  }
  return undefined
}

function isCostLike(label: string): boolean {
  return /cost|expense|depreciation|tax|interest|material|personnel|amortisation|amortization/i.test(label)
}

function sharePct(part: number, base: number): number {
  if (Math.abs(base) < 1e-3) return 0
  return Math.round((Math.abs(part) / Math.abs(base)) * 100)
}

function toneForRow(row: ConsolidationRow, spread: number): AnnualNarrativeTone {
  if (Math.abs(spread) < MATERIAL_EUR) return 'neutral'
  const costLike = isCostLike(row.label)
  const consol = row.consolidation
  if (costLike) return consol < 0 ? 'positive' : 'negative'
  return consol > 0 ? 'positive' : 'negative'
}

function materialityScore(row: ConsolidationRow, entityCodes: string[]): number {
  const spread = entitySpread(row, entityCodes)
  const group = Math.abs(row.consolidation)
  return spread * 1.2 + group * 0.08
}

/** Full bullet prose when no trusted API narrative is available. */
function buildEntityBulletText(
  row: ConsolidationRow,
  entities: ConsolidationResponse['entities'],
  ytdLabel: string,
): string {
  const group = row.consolidation
  const absGroup = Math.abs(group)
  const label = capFirst(proseLabel(row.label))
  const ranked = rankEntities(row, entities)
  const meaningful = ranked.filter(e => Math.abs(e.v) > MATERIAL_EUR)

  if (absGroup < MATERIAL_EUR) {
    return `${label} is immaterial at consolidated group level for ${ytdLabel}.`
  }

  const parts: string[] = [
    `${label} totals ${fmtNarrativeEur(group)} at group level for ${ytdLabel}.`,
  ]

  if (meaningful.length >= 1) {
    const lead = meaningful[0]
    const leadShare = sharePct(lead.v, group)
    parts.push(
      `${lead.label} contributes the largest share at ${fmtNarrativeEur(lead.v)} (${leadShare}% of the group line).`,
    )
  }

  if (meaningful.length >= 2) {
    const runner = meaningful[1]
    const runnerShare = sharePct(runner.v, group)
    parts.push(
      `${runner.label} follows at ${fmtNarrativeEur(runner.v)} (${runnerShare}%), ` +
        `while ${meaningful[meaningful.length - 1].label} remains the smallest contributor at ` +
        `${fmtNarrativeEur(meaningful[meaningful.length - 1].v)}.`,
    )
  }

  const spread = meaningful.length >= 2
    ? Math.abs(meaningful[0].v) - Math.abs(meaningful[meaningful.length - 1].v)
    : 0
  if (spread > 5000 && meaningful.length >= 3) {
    parts.push(
      `The ${fmtNarrativeEur(spread)} spread between the strongest and weakest entity points to uneven operating performance across the portfolio.`,
    )
  }

  const agg = row.aggregated
  const icGap = agg - group
  if (Math.abs(icGap) > 1000) {
    parts.push(
      `Before intercompany elimination the pre-consolidation sum is ${fmtNarrativeEur(agg)} ` +
        `(${fmtNarrativeEurSigned(icGap)} IC effect).`,
    )
  }

  return parts.join(' ')
}

/** Shorter entity clause appended to API group-level bullets. */
function buildEntityLevelClause(
  row: ConsolidationRow,
  entities: ConsolidationResponse['entities'],
  ytdLabel: string,
): string {
  const group = row.consolidation
  const absGroup = Math.abs(group)
  if (absGroup < MATERIAL_EUR) return ''

  const ranked = rankEntities(row, entities).filter(e => Math.abs(e.v) > MATERIAL_EUR)
  if (!ranked.length) return ''

  const lead = ranked[0]
  const leadShare = sharePct(lead.v, group)
  let clause =
    ` Across entities in ${ytdLabel}, ${lead.label} leads at ${fmtNarrativeEur(lead.v)} (${leadShare}% of this line).`

  if (ranked.length >= 2) {
    const tail = ranked[ranked.length - 1]
    if (tail.code !== lead.code) {
      clause +=
        ` ${ranked[1].label} contributes ${fmtNarrativeEur(ranked[1].v)}, ` +
        `with ${tail.label} at ${fmtNarrativeEur(tail.v)}.`
    }
  }

  const spread =
    ranked.length >= 2 ? Math.abs(ranked[0].v) - Math.abs(ranked[ranked.length - 1].v) : 0
  if (spread > 5000) {
    clause += ` Entity dispersion is material (${fmtNarrativeEur(spread)} between top and bottom).`
  }

  return clause
}

function collectCandidateRows(rows: ConsolidationRow[], statement: ConsolidationResponse['statement']): ConsolidationRow[] {
  const out: ConsolidationRow[] = []
  function walk(rs: ConsolidationRow[]) {
    for (const r of rs) {
      if (SKIP_ROW_KINDS.has(r.row_kind)) {
        walk(r.children ?? [])
        continue
      }
      if (AGGREGATE_LABELS.test(r.label.trim())) continue
      if (statement === 'bs' && BS_AGGREGATE_LABELS.test(r.label.trim())) continue
      if (statement === 'cf' && CF_AGGREGATE_LABELS.test(r.label.trim())) continue
      if (Math.abs(r.consolidation) > 1e-3) out.push(r)
      walk(r.children ?? [])
    }
  }
  walk(rows)
  return out
}

function buildBsIntro(consol: ConsolidationResponse, anchorLabel: string): string {
  const entities = consol.entities
  let intro =
    `The ${anchorLabel} view compares balance sheet positions by legal entity before group consolidation.`

  const assets = findConsolRow(consol.rows, /total assets/i)
  if (assets) {
    intro += ` Consolidated total assets stand at ${fmtNarrativeEur(assets.consolidation)}`
    const ranked = rankEntities(assets, entities).filter(e => Math.abs(e.v) > MATERIAL_EUR)
    if (ranked[0]) {
      intro += `, with ${ranked[0].label} carrying the largest asset base (${fmtNarrativeEur(ranked[0].v)})`
    }
    intro += '.'
  }

  const eql = findConsolRow(consol.rows, /equity.*liabilit|eigenkapital/i)
  if (eql) {
    intro += ` Equity and liabilities total ${fmtNarrativeEur(eql.consolidation)} at group level.`
  }

  return intro
}

function buildCfIntro(consol: ConsolidationResponse, anchorLabel: string): string {
  const entities = consol.entities
  let intro =
    `The ${anchorLabel} view compares cash flow by legal entity before group consolidation.`

  const ocf = findConsolRow(consol.rows, /cash flow from operating/i)
  if (ocf) {
    intro += ` Consolidated operating cash flow is ${fmtNarrativeEur(ocf.consolidation)}`
    const ranked = rankEntities(ocf, entities).filter(e => Math.abs(e.v) > MATERIAL_EUR)
    if (ranked[0]) {
      intro += `, led by ${ranked[0].label} (${fmtNarrativeEur(ranked[0].v)})`
    }
    intro += '.'
  }

  const fcf = findConsolRow(consol.rows, /free cash flow/i)
  if (fcf) {
    intro += ` Free cash flow totals ${fmtNarrativeEur(fcf.consolidation)} at group level.`
  }

  return intro
}

function buildIntro(consol: ConsolidationResponse, ytdLabel: string): string {
  if (consol.statement === 'bs') return buildBsIntro(consol, ytdLabel)
  if (consol.statement === 'wc') {
    const nwc = findConsolRow(consol.rows, /net working capital/i)
    let intro = `The ${ytdLabel} view compares working capital by legal entity before group consolidation.`
    if (nwc) intro += ` Group net working capital is ${fmtNarrativeEur(nwc.consolidation)}.`
    return intro
  }
  if (consol.statement === 'cf') return buildCfIntro(consol, ytdLabel)

  const entities = consol.entities
  const netRow = findConsolRow(consol.rows, /net profit/i)
  const revRow = findConsolRow(consol.rows, /net sales/i)

  let intro =
    `The ${ytdLabel} view compares each legal entity before group consolidation and highlights where profit and cost momentum concentrate in the portfolio.`

  if (revRow) {
    const ranked = rankEntities(revRow, entities).filter(e => Math.abs(e.v) > MATERIAL_EUR)
    intro += ` Consolidated net sales reach ${fmtNarrativeEur(revRow.consolidation)}`
    if (ranked[0]) {
      intro += `, with ${ranked[0].label} as the top revenue contributor (${fmtNarrativeEur(ranked[0].v)}`
      if (ranked[1]) intro += `) and ${ranked[1].label} next (${fmtNarrativeEur(ranked[1].v)}`
      intro += ').'
    } else {
      intro += '.'
    }
  }

  if (netRow) {
    const ranked = rankEntities(netRow, entities).filter(e => Math.abs(e.v) > MATERIAL_EUR)
    intro += ` Group net profit stands at ${fmtNarrativeEur(netRow.consolidation)}`
    if (ranked[0]) {
      intro += `, led by ${ranked[0].label} (${fmtNarrativeEur(ranked[0].v)}`
      const tail = ranked[ranked.length - 1]
      if (tail && tail.code !== ranked[0].code && Math.abs(tail.v) > MATERIAL_EUR) {
        intro += `), while ${tail.label} trails at ${fmtNarrativeEur(tail.v)}.`
      } else {
        intro += ').'
      }
    } else {
      intro += '.'
    }
  }

  return intro
}

export function buildAnnualConsolidationNarrative(
  consol: ConsolidationResponse,
  ytdLabel: string,
): {
  intro: string
  bullets: Array<{ index: number; line_code: string; label: string; text: string; tone: AnnualNarrativeTone }>
} {
  const entities = consol.entities
  const entityCodes = entities.map(e => e.code)
  const candidates = collectCandidateRows(consol.rows, consol.statement)
  const ranked = [...candidates].sort(
    (a, b) => materialityScore(b, entityCodes) - materialityScore(a, entityCodes),
  )
  const selected = ranked.slice(0, TOP_N)

  const bullets = selected.map((row, idx) => {
    const spread = entitySpread(row, entityCodes)
    return {
      index: idx + 1,
      line_code: row.id,
      label: row.label,
      text: buildEntityBulletText(row, entities, ytdLabel),
      tone: toneForRow(row, spread),
    }
  })

  return { intro: buildIntro(consol, ytdLabel), bullets }
}

export function buildAnnualConsolidationNarrativeResponse(
  consol: ConsolidationResponse,
  ytdLabel: string,
): PlNarrativeResponse {
  const r = buildAnnualConsolidationNarrative(consol, ytdLabel)
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
    meta: {
      entity_scope: 'all',
      llm_used: false,
      algorithm_version: 'client-annual-consolidation-v2',
    },
  } as PlNarrativeResponse
}

/** Prefer API group narrative enriched with entity attribution; else client consolidation prose. */
export function mergeAnnualConsolidationNarrative(
  apiNarrative: PlNarrativeResponse | null | undefined,
  clientNarrative: PlNarrativeResponse,
  consol: ConsolidationResponse,
  ytdLabel: string,
  statement: FinStatementKind = 'pl',
): PlNarrativeResponse {
  const apiTrusted =
    statement === 'bs'
      ? isTrustedApiBsNarrative(apiNarrative, undefined)
      : statement === 'wc'
        ? isTrustedApiWcNarrative(apiNarrative, undefined)
        : statement === 'cf'
          ? isTrustedApiCfNarrative(apiNarrative, undefined)
          : isTrustedAnnualNarrative(apiNarrative)

  if (!apiNarrative?.bullets?.length || !apiTrusted) {
    return clientNarrative
  }

  const directIds = new Set(collectDirectConsolidationRowIds(consol.rows))
  const rowIndex = indexConsolidationRows(consol.rows)

  const bullets = apiNarrative.bullets.slice(0, TOP_N).map((b, i) => {
    const rowId = resolveConsolidationRowIdForBullet(b, rowIndex, directIds)
    const row = rowId ? rowIndex.get(rowId) : undefined
    const entityClause = row ? buildEntityLevelClause(row, consol.entities, ytdLabel) : ''
    const text = entityClause ? `${b.text.trim()}${entityClause}` : b.text
    return {
      ...b,
      index: i + 1,
      line_code: row?.id ?? b.line_code,
      label: row?.label ?? b.label,
      text,
    }
  })

  const mappedCount = bullets.filter(b => directIds.has(b.line_code)).length
  if (mappedCount < Math.min(2, bullets.length)) {
    return clientNarrative
  }

  const intro =
    statement === 'pl' || statement === 'cf'
      ? clientNarrative.intro
      : (apiNarrative.intro?.trim() ? apiNarrative.intro : clientNarrative.intro)

  return {
    ...apiNarrative,
    intro,
    bullets,
    meta: {
      ...apiNarrative.meta,
      entity_scope: 'all',
      algorithm_version: `${apiNarrative.meta?.algorithm_version ?? 'compat'}+entity-v2`,
    },
  }
}
