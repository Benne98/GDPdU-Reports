import type { ReceivablesCustomerScatterRow } from '../../../../lib/api'
import { exportFlatTablePptx } from '../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../lib/exportXlsx'
import { fmtAmount } from '../../../../lib/fmt'
import type { PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import {
  buildCustomerRiskMatrix,
  CUSTOMER_RISK_QUADRANTS,
  overdueAmount,
  quadrantMeta,
} from './customerRiskMatrix'

export async function exportCustomerRiskMatrix(opts: {
  kind: PlExportKind
  data: ReceivablesCustomerScatterRow[]
  periodLabel: string
}): Promise<void> {
  const { kind, data, periodLabel } = opts
  if (!data.length) return

  const matrix = buildCustomerRiskMatrix(data)
  const { points, midBalanceKeur, midOverduePct, insights, counts } = matrix
  const headers = ['Customer', 'Open receivables', 'Overdue %', 'Overdue amount', 'Quadrant', 'Volume (kEUR)']

  const tableRows: XlsxRow[] = [...points]
    .sort((a, b) => b.balance - a.balance)
    .map(p => ({
      label: p.customer_name,
      values: [
        p.customer_name,
        p.balance,
        p.overdue_pct,
        +overdueAmount(p).toFixed(2),
        quadrantMeta(p.quadrant).label,
        +p.balanceKeur.toFixed(2),
      ],
      kind: 'data' as const,
    }))

  const base = `Customer_Risk_Matrix_${todayStr()}`

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: 'Customer risk matrix',
      tableHeading: 'Customer risk positions',
      breadcrumbCurrent: 'Aging',
      breadcrumbParent: 'Sales',
      footerRight: `${insights.intro.slice(0, 120)}… · ${periodLabel}`,
      headers,
      rows: tableRows,
    })
    return
  }

  const xlsxRows: XlsxRow[] = [
    { label: 'Matrix thresholds', values: ['Matrix thresholds', '', '', '', '', ''], kind: 'section' },
    {
      label: 'Volume split',
      values: ['Volume split', `${midBalanceKeur.toFixed(0)} kEUR median`, '', '', '', ''],
      kind: 'data',
    },
    {
      label: 'Overdue split',
      values: ['Overdue split', insights.splitOverdueLabel, '', '', '', ''],
      kind: 'data',
    },
    { label: 'Quadrant counts', values: ['Quadrant counts', '', '', '', '', ''], kind: 'section' },
    ...CUSTOMER_RISK_QUADRANTS.map(q => ({
      label: q.label,
      values: [q.label, counts[q.id], q.hint, q.action, '', ''],
      kind: 'data' as const,
    })),
    { label: 'Customers', values: ['Customers', '', '', '', '', ''], kind: 'section' },
    ...tableRows,
    { label: 'Analysis', values: ['Analysis', '', '', '', '', ''], kind: 'section' },
    { label: 'Intro', values: [insights.intro, '', '', '', '', ''], kind: 'data' },
    ...insights.bullets.map(b => ({
      label: b,
      values: [b, '', '', '', '', ''],
      kind: 'data' as const,
    })),
  ]

  if (insights.topOpportunity) {
    const t = insights.topOpportunity
    xlsxRows.push(
      { label: 'Top collection opportunity', values: ['Top collection opportunity', t.customer_name, '', '', '', ''], kind: 'section' },
      {
        label: t.customer_name,
        values: [
          t.customer_name,
          fmtAmount(t.balance),
          `${t.overdue_pct}%`,
          fmtAmount(overdueAmount(t)),
          quadrantMeta(t.quadrant).label,
          t.balanceKeur.toFixed(2),
        ],
        kind: 'data',
      },
    )
  }

  if (insights.topCore) {
    const t = insights.topCore
    xlsxRows.push(
      { label: 'Strongest low-risk account', values: ['Strongest low-risk account', t.customer_name, '', '', '', ''], kind: 'section' },
      {
        label: t.customer_name,
        values: [
          t.customer_name,
          fmtAmount(t.balance),
          `${t.overdue_pct}%`,
          fmtAmount(overdueAmount(t)),
          quadrantMeta(t.quadrant).label,
          t.balanceKeur.toFixed(2),
        ],
        kind: 'data',
      },
    )
  }

  if (insights.criticalOverdueTotal > 0) {
    xlsxRows.push({
      label: 'Critical quadrant overdue',
      values: ['Critical quadrant overdue', fmtAmount(insights.criticalOverdueTotal), '', '', '', ''],
      kind: 'data',
    })
  }

  await exportToXlsx({
    title: 'Customer risk matrix',
    subtitle: `${periodLabel} · Volume median ${midBalanceKeur.toFixed(0)} kEUR · Overdue split ${midOverduePct.toFixed(0)}%`,
    headers,
    rows: xlsxRows,
    filename: `${base}.xlsx`,
  })
}
