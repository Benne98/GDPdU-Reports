import type { FinancialStatementColLabels, MonthlyPeriod } from '../../../lib/api'
import type { PlColumnKind, PlTableColumnDef } from './plColumnRegistry'
import { STANDARD_KINDS, priorMonthKey, priorYearKey } from './plColumnRegistry'
import { periodKey as pk, periodLabel } from './plPeriodLabels'

export const CONSOL_TARGET_AGGREGATED = '__aggregated__'
export const CONSOL_TARGET_CONSOLIDATION = '__consolidation__'

export type PlConsolidationTargetScope =
  | 'single_entity'
  | 'all_entities'
  | 'aggregated'
  | 'consolidation'

export type PlConsolidationColumnDef = {
  id: string
  target: PlConsolidationTargetScope
  entityCode?: string
  kind: PlColumnKind
  labelLine1: string
  labelLine2?: string
  periodKey?: string
  periodKeyA?: string
  periodKeyB?: string
}

const STORAGE_KEY_PL = 'finssentials.pl.consolidation.columns.v2'

function storageKey(statement = 'pl'): string {
  return statement === 'pl' ? STORAGE_KEY_PL : `finssentials.${statement}.consolidation.columns.v2`
}

function periodLabelFromKey(key: string, periods?: MonthlyPeriod[]): string {
  const hit = periods?.find(p => pk(p.year, p.month) === key)
  return hit?.label ?? periodLabel(parseInt(key.slice(0, 4), 10), parseInt(key.slice(5), 10))
}

/** Catalog with month-only labels and ∆ Jul25 − Jun25 style deltas (no "Prior month" subtitles). */
export function buildConsolidationColumnCatalog(
  lbl: FinancialStatementColLabels,
  periods: MonthlyPeriod[],
): Map<string, PlTableColumnDef> {
  const map = new Map<string, PlTableColumnDef>()
  const cm = lbl.cm ?? 'CM'
  const pm = lbl.pm ?? 'PM'
  const py = lbl.py_cm ?? 'PY'

  const standard: PlTableColumnDef[] = [
    { id: 'py_cm', kind: 'py_cm', labelLine1: py },
    { id: 'pm', kind: 'pm', labelLine1: pm },
    { id: 'cm', kind: 'cm', labelLine1: cm },
    { id: 'mom', kind: 'mom', labelLine1: `∆ ${cm} − ${pm}` },
    { id: 'yoy', kind: 'yoy', labelLine1: `∆ ${cm} − ${py}` },
    { id: 'ytd', kind: 'ytd', labelLine1: lbl.ytd ?? 'YTD' },
    { id: 'ytd_py', kind: 'ytd_py', labelLine1: lbl.ytd_py ?? 'YTD PY' },
    {
      id: 'ytd_delta',
      kind: 'ytd_delta',
      labelLine1: lbl.ytd && lbl.ytd_py ? `∆ ${lbl.ytd} − ${lbl.ytd_py}` : '∆ YTD',
    },
    { id: 'ytd_plan', kind: 'ytd_plan', labelLine1: lbl.ytd ? `Plan ${lbl.ytd}` : 'Plan YTD' },
    {
      id: 'ytd_vs_plan',
      kind: 'ytd_vs_plan',
      labelLine1: lbl.ytd ? `∆ ${lbl.ytd} − Plan` : '∆ YTD Plan',
    },
    { id: 'plan_cm', kind: 'plan_cm', labelLine1: lbl.cm ? `Plan ${cm}` : 'Plan' },
    {
      id: 'plan_vs_actual',
      kind: 'plan_vs_actual',
      labelLine1: lbl.cm ? `∆ ${cm} Plan` : '∆ Plan',
    },
    { id: 'ytg', kind: 'ytg', labelLine1: lbl.cm ? `YTG ${cm}` : 'YTG' },
    { id: 'coverage', kind: 'coverage', labelLine1: 'Coverage' },
  ]
  for (const c of standard) map.set(c.id, c)

  for (const p of periods) {
    const key = pk(p.year, p.month)
    map.set(`month:${key}`, {
      id: `month:${key}`,
      kind: 'month',
      periodKey: key,
      labelLine1: p.label,
    })
    const pmKey = priorMonthKey(key)
    const pyKey = priorYearKey(key)
    map.set(`month_mom:${key}`, {
      id: `month_mom:${key}`,
      kind: 'month_mom',
      periodKey: key,
      labelLine1: `∆ ${p.label} − ${periodLabelFromKey(pmKey, periods)}`,
    })
    map.set(`month_yoy:${key}`, {
      id: `month_yoy:${key}`,
      kind: 'month_yoy',
      periodKey: key,
      labelLine1: `∆ ${p.label} − ${periodLabelFromKey(pyKey, periods)}`,
    })
  }

  return map
}

export function consolidationMonthDelta(
  aKey: string,
  bKey: string,
  periods?: MonthlyPeriod[],
): PlTableColumnDef {
  const la = periodLabelFromKey(aKey, periods)
  const lb = periodLabelFromKey(bKey, periods)
  return {
    id: `month_delta:${aKey}:${bKey}`,
    kind: 'month_delta',
    periodKeyA: aKey,
    periodKeyB: bKey,
    labelLine1: `∆ ${la} − ${lb}`,
  }
}

export function loadConsolidationColumns(statement = 'pl'): PlConsolidationColumnDef[] {
  try {
    const raw = localStorage.getItem(storageKey(statement))
    if (!raw) return []
    const parsed = JSON.parse(raw) as PlConsolidationColumnDef[]
    if (!Array.isArray(parsed)) return []
    return parsed.map(c => {
      const legacy = c as PlConsolidationColumnDef & { scope?: string }
      if (legacy.scope === 'single') {
        return { ...c, target: 'single_entity' as const }
      }
      if (legacy.scope === 'all_entities') {
        return { ...c, target: 'all_entities' as const }
      }
      return c
    })
  } catch {
    return []
  }
}

export function saveConsolidationColumns(cols: PlConsolidationColumnDef[], statement = 'pl'): void {
  localStorage.setItem(storageKey(statement), JSON.stringify(cols))
}

function templateKeyFromConsolId(id: string, kind: string): string {
  if (id.startsWith('e:')) return id.split(':').slice(2).join(':')
  if (id.startsWith('agg:') || id.startsWith('con:') || id.startsWith('all:')) {
    return id.split(':').slice(1).join(':')
  }
  return kind
}

export function reconcileConsolidationColumns(
  cols: PlConsolidationColumnDef[],
  catalog: Map<string, PlTableColumnDef>,
  periods: MonthlyPeriod[],
): PlConsolidationColumnDef[] {
  return cols.map(c => {
    const templateKey = templateKeyFromConsolId(c.id, c.kind)
    const tpl =
      catalog.get(templateKey) ??
      [...catalog.values()].find(t => t.kind === c.kind && t.periodKey === c.periodKey)
    if (!tpl) return c
    if (tpl.kind === 'month_delta' && tpl.periodKeyA && tpl.periodKeyB) {
      const delta = consolidationMonthDelta(tpl.periodKeyA, tpl.periodKeyB, periods)
      return { ...c, labelLine1: delta.labelLine1, labelLine2: undefined }
    }
    return { ...c, labelLine1: tpl.labelLine1, labelLine2: undefined }
  })
}

export function makeConsolidationColumn(
  target: PlConsolidationTargetScope,
  entityCode: string | undefined,
  template: PlTableColumnDef,
): PlConsolidationColumnDef {
  const suffix = template.id
  let id: string
  switch (target) {
    case 'single_entity':
      id = `e:${entityCode}:${suffix}`
      break
    case 'all_entities':
      id = `all:${suffix}`
      break
    case 'aggregated':
      id = `agg:${suffix}`
      break
    case 'consolidation':
      id = `con:${suffix}`
      break
  }
  return {
    id,
    target,
    entityCode: target === 'single_entity' ? entityCode : undefined,
    kind: template.kind,
    labelLine1: template.labelLine1,
    labelLine2: template.labelLine2,
    periodKey: template.periodKey,
    periodKeyA: template.periodKeyA,
    periodKeyB: template.periodKeyB,
  }
}

export const CONSOL_STANDARD_GROUPS: Array<{ title: string; kinds: typeof STANDARD_KINDS }> = [
  {
    title: 'Current period',
    kinds: ['py_cm', 'pm', 'cm', 'mom', 'yoy', 'plan_cm', 'plan_vs_actual'],
  },
  {
    title: 'Year to date & plan',
    kinds: ['ytd', 'ytd_py', 'ytd_delta', 'ytd_plan', 'ytd_vs_plan', 'ytg', 'coverage'],
  },
]
