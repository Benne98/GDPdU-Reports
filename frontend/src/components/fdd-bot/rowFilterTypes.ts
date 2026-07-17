export type FilterOperator =
  | 'eq'
  | 'ne'
  | 'in'
  | 'not_in'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'between'
  | 'contains'
  | 'startswith'
  | 'endswith'
  | 'isblank'
  | 'notblank'

export interface FilterRule {
  col: string
  op: FilterOperator | string
  value?: string
  values?: string[]
  lo?: string
  hi?: string
}

export const FILTER_OPERATORS: { label: string; value: FilterOperator }[] = [
  { label: 'equals', value: 'eq' },
  { label: 'does not equal', value: 'ne' },
  { label: 'is in list', value: 'in' },
  { label: 'is not in list', value: 'not_in' },
  { label: 'greater than', value: 'gt' },
  { label: 'greater than or equal', value: 'gte' },
  { label: 'less than', value: 'lt' },
  { label: 'less than or equal', value: 'lte' },
  { label: 'between', value: 'between' },
  { label: 'contains', value: 'contains' },
  { label: 'starts with', value: 'startswith' },
  { label: 'ends with', value: 'endswith' },
  { label: 'is blank', value: 'isblank' },
  { label: 'is not blank', value: 'notblank' },
]

export function parseFilterRules(raw: unknown): FilterRule[] {
  if (Array.isArray(raw)) return raw as FilterRule[]
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? (parsed as FilterRule[]) : []
    } catch {
      return []
    }
  }
  return []
}

export function humanizeFilterRule(rule: FilterRule): string {
  const col = rule.col || '?'
  const op = String(rule.op || '').toLowerCase()
  if (op === 'eq' || op === '==') return `${col} equals ${rule.value ?? ''}`
  if (op === 'ne' || op === '!=') return `${col} does not equal ${rule.value ?? ''}`
  if (op === 'in') {
    const vals = rule.values ?? (rule.value ? [rule.value] : [])
    return `${col} is in [${vals.join(', ')}]`
  }
  if (op === 'not_in') {
    const vals = rule.values ?? (rule.value ? [rule.value] : [])
    return `${col} is not in [${vals.join(', ')}]`
  }
  if (op === 'gt' || op === '>') return `${col} is greater than ${rule.value ?? ''}`
  if (op === 'gte' || op === '>=') return `${col} is greater than or equal to ${rule.value ?? ''}`
  if (op === 'lt' || op === '<') return `${col} is less than ${rule.value ?? ''}`
  if (op === 'lte' || op === '<=') return `${col} is less than or equal to ${rule.value ?? ''}`
  if (op === 'between') return `${col} is between ${rule.lo ?? ''} and ${rule.hi ?? ''}`
  if (op === 'contains') return `${col} contains ${rule.value ?? ''}`
  if (op === 'startswith') return `${col} starts with ${rule.value ?? ''}`
  if (op === 'endswith') return `${col} ends with ${rule.value ?? ''}`
  if (op === 'isblank') return `${col} is blank`
  if (op === 'notblank') return `${col} is not blank`
  return `${col} ${op} ${rule.value ?? ''}`.trim()
}

export function buildFilterDisplay(rules: FilterRule[]): string {
  return rules.map(rule => `• ${humanizeFilterRule(rule)}`).join('\n')
}
