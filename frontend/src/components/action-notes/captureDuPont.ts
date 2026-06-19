import type { DuPontData, ViewPinSnapshot } from '../../lib/api'

type TableSnap = NonNullable<ViewPinSnapshot['table']>

const KEYS: { id: string; label: string }[] = [
  { id: 'roe', label: 'Return on Equity' },
  { id: 'roi', label: 'Return on Investment' },
  { id: 'ros', label: 'Return on Sales' },
  { id: 'gross_margin', label: 'Gross Margin' },
  { id: 'ebitda_margin', label: 'EBITDA Margin' },
  { id: 'asset_turnover', label: 'Asset Turnover' },
  { id: 'net_sales', label: 'Net Sales' },
  { id: 'cost_of_materials', label: 'Cost of Materials' },
  { id: 'ebit', label: 'EBIT' },
  { id: 'dso', label: 'DSO' },
  { id: 'dpo', label: 'DPO' },
  { id: 'dio', label: 'DIO' },
  { id: 'total_assets', label: 'Total Assets' },
  { id: 'equity', label: 'Equity' },
]

export function captureDuPontSnapshot(data: DuPontData): TableSnap {
  const row_preview: TableSnap['row_preview'] = KEYS.filter(k => data.metrics[k.id]).map(k => {
    const m = data.metrics[k.id]
    return {
      id: k.id,
      label: k.label,
      values: {
        [data.col_label]: m.value ?? '—',
        [data.pm_label]: m.pm ?? '—',
        [data.py_label]: m.py ?? '—',
      },
    }
  })
  return { component: 'DuPontTree', expanded_row_ids: [], row_preview }
}
