import type { NetDebtRow, NetDebtTableResponse } from '../../../lib/api'

export function resolveNetDebtColumns(data: NetDebtTableResponse): {
  keys: string[]
  labels: Record<string, string>
  anchorKey: string
} {
  const anchorKey = data.anchor_date
  if (data.col_keys?.length) {
    const labels = data.col_labels ?? {}
    return {
      keys: data.col_keys,
      labels: Object.fromEntries(
        data.col_keys.map(k => [k, labels[k] ?? k]),
      ),
      anchorKey: anchorKey || data.col_keys[data.col_keys.length - 1],
    }
  }

  const fallbackKey = anchorKey || 'anchor'
  const fallbackLabel = data.col_label ?? 'CM'
  return {
    keys: [fallbackKey],
    labels: { [fallbackKey]: fallbackLabel },
    anchorKey: fallbackKey,
  }
}

export function netDebtCellValue(
  row: NetDebtRow,
  key: string,
  anchorKey: string,
): number | null | undefined {
  const fromAmounts = row.amounts?.[key]
  if (fromAmounts != null && !Number.isNaN(fromAmounts)) return fromAmounts
  if (key === anchorKey) return row.amount_keur
  return undefined
}

export function formatNetDebtColumnLabels(data: NetDebtTableResponse): string {
  const { keys, labels } = resolveNetDebtColumns(data)
  return keys.map(k => labels[k] ?? k).join(', ')
}
