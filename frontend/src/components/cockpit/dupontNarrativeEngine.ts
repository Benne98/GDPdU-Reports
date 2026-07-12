import type { DuPontData } from '../../lib/api'
import { fmtKpi, fmtPct } from '../../lib/fmt'

type MetricPoint = { value: number | null; pm: number | null; py: number | null }
type Unit = 'pct' | 'keur' | 'x' | 'days'

interface DriverCandidate {
  id: string
  label: string
  unit: Unit
  metric: MetricPoint
  /** Approximate share of parent delta (0–1) when decomposing ROE. */
  share?: number
  detail?: string
}

function delta(v: number | null, ref: number | null): number | null {
  if (v === null || ref === null) return null
  return v - ref
}

function fmtDeltaPlain(d: number | null, unit: Unit): string {
  if (d === null) return '—'
  const sign = d >= 0 ? '+' : '−'
  const abs = Math.abs(d)
  switch (unit) {
    case 'pct':
      return `${sign}${abs.toLocaleString('de-DE', { maximumFractionDigits: 1 })} pp`
    case 'keur':
      return `${sign}€ ${(abs / 1_000).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k`
    case 'x':
      return `${sign}${abs.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}x`
    case 'days':
      return `${sign}${abs.toLocaleString('de-DE', { maximumFractionDigits: 1 })} d`
    default:
      return `${sign}${abs}`
  }
}

function fmtVal(v: number | null, unit: Unit): string {
  if (v === null) return '—'
  if (unit === 'pct') return fmtPct(v)
  if (unit === 'keur') return fmtKpi(v)
  if (unit === 'x') return `${Math.abs(v).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}x`
  if (unit === 'days') return `${Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 1 })} d`
  return String(v)
}

function rankPeriodDrivers(m: DuPontData['metrics']): DriverCandidate[] {
  const drivers: DriverCandidate[] = [
    { id: 'net_sales', label: 'Net sales', unit: 'keur', metric: m.net_sales },
    { id: 'cost_of_materials', label: 'Cost of materials', unit: 'keur', metric: m.cost_of_materials },
    { id: 'personnel_expenses', label: 'Personnel expenses', unit: 'keur', metric: m.personnel_expenses },
    { id: 'trade_receivables', label: 'Trade receivables', unit: 'keur', metric: m.trade_receivables },
    { id: 'trade_payables', label: 'Trade payables', unit: 'keur', metric: m.trade_payables },
    { id: 'inventories', label: 'Inventories', unit: 'keur', metric: m.inventories },
    { id: 'equity', label: 'Equity', unit: 'keur', metric: m.equity },
    { id: 'dso', label: 'DSO', unit: 'days', metric: m.dso },
    { id: 'dpo', label: 'DPO', unit: 'days', metric: m.dpo },
    { id: 'dio', label: 'DIO', unit: 'days', metric: m.dio },
  ]

  return drivers
    .map(d => ({
      ...d,
      mom: delta(d.metric.value, d.metric.pm),
      yoy: delta(d.metric.value, d.metric.py),
    }))
    .filter(d => d.mom !== null && Math.abs(d.mom) > 1e-6)
    .sort((a, b) => Math.abs((b.mom ?? 0)) - Math.abs((a.mom ?? 0)))
    .slice(0, 4)
    .map(d => ({
      id: d.id,
      label: d.label,
      unit: d.unit,
      metric: d.metric,
      detail:
        `${d.label} stood at ${fmtVal(d.metric.value, d.unit)} ` +
        `(Δ vs ${dataPmLabel()} ${fmtDeltaPlain(d.mom, d.unit)}, ` +
        `Δ vs ${dataPyLabel()} ${fmtDeltaPlain(d.yoy, d.unit)}).`,
    }))
}

let _pmLabel = 'PM'
let _pyLabel = 'PY'

function dataPmLabel(): string {
  return _pmLabel
}
function dataPyLabel(): string {
  return _pyLabel
}

function roeBridgeSentence(m: DuPontData['metrics'], pmLabel: string, pyLabel: string): string {
  const roe = m.roe
  const roi = m.roi
  const em = m.equity_multiplier
  const ros = m.ros
  const at = m.asset_turnover

  if (!roe?.value) {
    return 'Return on equity is not available for this selection.'
  }

  const dRoePm = delta(roe.value, roe.pm)
  const dRoiPm = delta(roi?.value ?? null, roi?.pm ?? null)
  const dEmPm = delta(em?.value ?? null, em?.pm ?? null)
  const dRosPm = delta(ros?.value ?? null, ros?.pm ?? null)
  const dAtPm = delta(at?.value ?? null, at?.pm ?? null)

  const dir =
    dRoePm === null ? 'unchanged' : dRoePm >= 0 ? 'improved' : 'weakened'

  let bridge = `Return on equity ${dir} to ${fmtPct(roe.value)}`
  if (dRoePm !== null) {
    bridge += ` (Δ vs ${pmLabel} ${fmtDeltaPlain(dRoePm, 'pct')}, Δ vs ${pyLabel} ${fmtDeltaPlain(delta(roe.value, roe.py), 'pct')})`
  }
  bridge += '. '

  const parts: string[] = []
  if (dRoiPm !== null && Math.abs(dRoiPm) >= 0.05) {
    parts.push(`return on investment ${fmtDeltaPlain(dRoiPm, 'pct')} vs ${pmLabel}`)
  }
  if (dEmPm !== null && Math.abs(dEmPm) >= 0.02) {
    parts.push(`equity multiplier ${fmtDeltaPlain(dEmPm, 'x')} vs ${pmLabel}`)
  }
  if (parts.length) {
    bridge +=
      `The period move is explained by ${parts.join(' and ')}, ` +
      `with return on equity reflecting return on investment scaled by financial leverage. `
  }

  const opParts: string[] = []
  if (dRosPm !== null && Math.abs(dRosPm) >= 0.05) {
    opParts.push(`return on sales ${fmtDeltaPlain(dRosPm, 'pct')}`)
  }
  if (dAtPm !== null && Math.abs(dAtPm) >= 0.02) {
    opParts.push(`asset turnover ${fmtDeltaPlain(dAtPm, 'x')}`)
  }
  if (opParts.length) {
    bridge +=
      `Return on investment in turn reflects ${opParts.join(' and ')} — ` +
      `operating margin and capital-efficiency effects that feed through to shareholder returns.`
  }

  return bridge
}

function driverBullet(driver: DriverCandidate, parentLabel: string): string {
  const share =
    driver.share != null && driver.share > 0
      ? `, accounting for roughly ${Math.round(driver.share * 100)}% of the ${parentLabel} movement`
      : ''
  return (
    `${driver.detail ?? driver.label}${share}. ` +
    `Confirm in the general ledger at position and account level` +
    ` and, where commercial exposure is relevant, cross-check customer or supplier concentration.`
  )
}

/**
 * Driver-first ROE narrative: profitability bridge, then ranked P&L and
 * balance-sheet drivers that explain the period movement.
 */
export function buildDuPontNarrative(data: DuPontData): { title: string; body: string }[] {
  _pmLabel = data.pm_label || 'PM'
  _pyLabel = data.py_label || 'PY'
  const m = data.metrics
  const sections: { title: string; body: string }[] = []

  sections.push({
    title: 'Return on equity — headline and bridge',
    body: roeBridgeSentence(m, _pmLabel, _pyLabel),
  })

  const ros = m.ros
  const gm = m.gross_margin
  const ns = m.net_sales
  const com = m.cost_of_materials
  const dRosPm = delta(ros?.value ?? null, ros?.pm ?? null)

  if (ros && ros.value != null && Math.abs(dRosPm ?? 0) >= 0.05) {
    const gmDir = delta(gm?.value ?? null, gm?.pm ?? null)
    const nsDir = delta(ns?.value ?? null, ns?.pm ?? null)
    const comDir = delta(com?.value ?? null, com?.pm ?? null)
    sections.push({
      title: 'Operating profitability — margin bridge',
      body:
        `Return on sales is the primary margin indicator at ${fmtPct(ros.value)} ` +
        `(Δ vs ${_pmLabel} ${fmtDeltaPlain(dRosPm, 'pct')}). ` +
        `Gross margin ${gm?.value != null ? fmtPct(gm.value) : '—'} moved ${fmtDeltaPlain(gmDir, 'pct')} vs ${_pmLabel}, ` +
        `with net sales ${fmtDeltaPlain(nsDir, 'keur')} and cost of materials ${fmtDeltaPlain(comDir, 'keur')}. ` +
        `Trace the movement through revenue and cost-of-sales lines to the underlying accounts and postings ` +
        `that explain the period-on-period shift — consistent with the Income Statement report view.`,
    })
  }

  const at = m.asset_turnover
  const dAtPm = delta(at?.value ?? null, at?.pm ?? null)
  const dso = m.dso
  const dpo = m.dpo
  const dio = m.dio
  const tr = m.trade_receivables
  const tp = m.trade_payables
  const inv = m.inventories

  if (at && Math.abs(dAtPm ?? 0) >= 0.02) {
    sections.push({
      title: 'Capital efficiency — working capital',
      body:
        `Asset turnover of ${fmtVal(at.value, 'x')} (Δ vs ${_pmLabel} ${fmtDeltaPlain(dAtPm, 'x')}) ` +
        `indicates how efficiently the asset base supports sales. ` +
        `On the balance sheet, trade receivables ${fmtDeltaPlain(delta(tr?.value ?? null, tr?.pm ?? null), 'keur')}, ` +
        `trade payables ${fmtDeltaPlain(delta(tp?.value ?? null, tp?.pm ?? null), 'keur')}, and inventories ` +
        `${fmtDeltaPlain(delta(inv?.value ?? null, inv?.pm ?? null), 'keur')} vs ${_pmLabel} ` +
        `are the principal working-capital drivers behind turnover movements. ` +
        `Receivables days ${fmtVal(dso?.value ?? null, 'days')} (${fmtDeltaPlain(delta(dso?.value ?? null, dso?.pm ?? null), 'days')}), ` +
        `payables days ${fmtVal(dpo?.value ?? null, 'days')} (${fmtDeltaPlain(delta(dpo?.value ?? null, dpo?.pm ?? null), 'days')}), ` +
        `inventory days ${fmtVal(dio?.value ?? null, 'days')} (${fmtDeltaPlain(delta(dio?.value ?? null, dio?.pm ?? null), 'days')}). ` +
        `Where sales or payables move materially, validate against receivables and payables aging.`,
    })
  }

  const em = m.equity_multiplier
  const eq = m.equity
  const dEmPm = delta(em?.value ?? null, em?.pm ?? null)
  if (em && Math.abs(dEmPm ?? 0) >= 0.02) {
    sections.push({
      title: 'Financial leverage — equity multiplier',
      body:
        `Equity multiplier of ${fmtVal(em.value, 'x')} (Δ vs ${_pmLabel} ${fmtDeltaPlain(dEmPm, 'x')}) ` +
        `scales return on investment into return on equity. ` +
        `Equity ${fmtVal(eq?.value ?? null, 'keur')} changed ${fmtDeltaPlain(delta(eq?.value ?? null, eq?.pm ?? null), 'keur')} vs ${_pmLabel}; ` +
        `if leverage shifts without a matching operating narrative, review retained earnings, distributions ` +
        `and the balance-sheet funding mix in the general ledger.`,
    })
  }

  const ranked = rankPeriodDrivers(m)
  if (ranked.length) {
    sections.push({
      title: 'Principal drivers (ranked by period movement)',
      body: ranked.map((d, i) => `${i + 1}. ${driverBullet(d, 'period')}`).join(' '),
    })
  }

  return sections
}
