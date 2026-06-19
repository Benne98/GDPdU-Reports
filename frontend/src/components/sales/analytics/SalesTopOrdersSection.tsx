import { useCallback, useEffect, useMemo, useState } from 'react'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import PlViewToggleButton from '../../financials/pl-two-view/PlViewToggleButton'
import { FIN_REPORT_SPLIT_GRID } from '../../financials/statement-two-view/finReportLayout'
import { exportFlatTablePptx } from '../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr } from '../../../lib/exportXlsx'
import { fmtChartKpi } from '../../../lib/fmt'
import type { SalesTopOrder } from '../../../lib/api'
import SalesColumnEditor from './SalesColumnEditor'
import SalesTopOrdersLeaderboard from './SalesTopOrdersLeaderboard'
import {
  TOP_ORDERS_AMOUNT_FIELD,
  TOP_ORDERS_CATALOG,
  loadTopOrdersColumns,
  orderTopOrdersColumns,
  reconcileSalesColumns,
  saveSalesColumns,
} from './salesColumnRegistry'
import type { SalesColumnDef, SalesViewMode } from './salesTableTypes'

const VIEW_STORAGE_KEY = 'finssentials.sales.topOrders.viewMode.v1'

function loadViewMode(): SalesViewMode {
  try {
    const v = localStorage.getItem(VIEW_STORAGE_KEY)
    if (v === 'table' || v === 'report') return v
  } catch { /* ignore */ }
  return 'report'
}

type Props = {
  rows: SalesTopOrder[]
  loading: boolean
  reportIntro?: string
  bullets: string[]
}

function strField(v: string | null | undefined): string {
  return v != null && v !== '' ? v : '—'
}

function cellValue(row: SalesTopOrder, field: string): string {
  switch (field) {
    case 'amount_keur':
      return fmtChartKpi(row.amount_keur)
    case 'gross_profit_keur':
      return row.gross_profit_keur != null ? fmtChartKpi(row.gross_profit_keur) : '—'
    case 'gross_margin_pct':
      return row.gross_margin_pct != null ? `${Number(row.gross_margin_pct).toFixed(1)}%` : '—'
    case 'product_count':
      return row.product_count != null ? String(row.product_count) : '—'
    case 'line_count':
      return row.line_count != null ? String(row.line_count) : '—'
    case 'invoice_number':
      return strField(row.invoice_number)
    case 'customer_name':
      return strField(row.customer_name)
    case 'invoice_date':
      return strField(row.invoice_date)
    case 'due_date':
      return strField(row.due_date)
    case 'supplier_invoice_date':
      return strField(row.supplier_invoice_date)
    case 'contract_start_date':
      return strField(row.contract_start_date)
    case 'contract_end_date':
      return strField(row.contract_end_date)
    case 'contact_name':
      return strField(row.contact_name)
    case 'customer_location':
      return strField(row.customer_location)
    case 'entity':
      return strField(row.entity)
    case 'segment':
      return strField(row.segment)
    case 'product_revenue_model':
      return strField(row.product_revenue_model)
    case 'supplier_invoice_number':
      return strField(row.supplier_invoice_number)
    case 'gl_reference_document':
      return strField(row.gl_reference_document)
    default:
      return '—'
  }
}

function isNumericColumn(field: string): boolean {
  return field === TOP_ORDERS_AMOUNT_FIELD
    || field === 'gross_profit_keur'
    || field === 'gross_margin_pct'
    || field === 'product_count'
    || field === 'line_count'
}

export default function SalesTopOrdersSection({ rows, loading, reportIntro, bullets }: Props) {
  const [viewMode, setViewMode] = useState<SalesViewMode>(loadViewMode)
  const [visible, setVisible] = useState(10)
  const catalog = TOP_ORDERS_CATALOG
  const [columns, setColumns] = useState<SalesColumnDef[]>(() => loadTopOrdersColumns(catalog))

  useEffect(() => {
    setColumns(prev => orderTopOrdersColumns(reconcileSalesColumns(prev, catalog)))
  }, [catalog])

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, viewMode)
    } catch { /* ignore */ }
  }, [viewMode])

  const tableColumns = useMemo(() => orderTopOrdersColumns(columns), [columns])

  const handleColumnsChange = useCallback((cols: SalesColumnDef[]) => {
    const ordered = orderTopOrdersColumns(cols)
    setColumns(ordered)
    saveSalesColumns('top-orders', ordered)
  }, [])

  const shown = rows.slice(0, visible)

  async function handleExport(kind: PlExportKind) {
    const headers = ['#', ...tableColumns.map(c => c.label)]
    const xlsxRows = rows.map((r, i) => ({
      label: String(i + 1),
      values: [String(i + 1), ...tableColumns.map(c => cellValue(r, c.field))],
      kind: 'data' as const,
    }))
    const base = `Top_Orders_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: 'Top Orders',
        tableHeading: 'Top Orders',
        breadcrumbCurrent: 'Profitability',
        breadcrumbParent: 'Sales',
        footerRight: 'By invoiced amount — selected period',
        headers,
        rows: xlsxRows,
      })
      return
    }
    await exportToXlsx({
      title: 'Top Orders',
      subtitle: 'By invoiced amount — selected period',
      headers,
      rows: xlsxRows,
      filename: `${base}.xlsx`,
    })
  }

  function renderTable(cols: SalesColumnDef[], maxRows: number) {
    const slice = rows.slice(0, maxRows)
    return (
      <table className="w-full text-xs">
        <thead>
          <tr style={{ background: '#F8FAFC' }}>
            <th className="px-2 py-1.5 text-left font-semibold w-8" style={{ color: '#475569' }}>#</th>
            {cols.map(c => {
              const isNum = isNumericColumn(c.field)
              return (
              <th
                key={c.id}
                className={`px-2 py-1.5 font-semibold ${isNum ? 'text-right' : 'text-left'}`}
                style={{ color: '#475569' }}
              >
                {c.label}
              </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {slice.map((r, i) => (
            <tr key={`${r.invoice_number}-${i}`} style={{ borderBottom: '1px solid #F8FAFC' }}>
              <td className="px-2 py-1.5 tabular-nums" style={{ color: '#64748B' }}>{i + 1}</td>
              {cols.map(c => {
                const isAmount = c.field === TOP_ORDERS_AMOUNT_FIELD
                const isNum = isNumericColumn(c.field)
                return (
                <td
                  key={c.id}
                  className={`px-2 py-1.5 max-w-[14rem] truncate ${isNum ? 'text-right tabular-nums' : ''} ${isAmount ? 'font-semibold' : ''}`}
                  style={{ color: isAmount ? '#1E3A5F' : '#334155' }}
                  title={cellValue(r, c.field)}
                >
                  {cellValue(r, c.field)}
                </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    )
  }

  return (
    <div className="rounded-xl" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}>
      <div className="px-5 pt-4 pb-3 border-b flex justify-between gap-3 flex-wrap items-start" style={{ borderColor: '#F1F5F9' }}>
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>Top Orders</h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            {viewMode === 'report'
              ? 'Ranked leaderboard and key drivers — invoiced amount for selected period'
              : 'Full invoice table — configure columns with the pencil'}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
          {viewMode === 'table' && (
            <SalesColumnEditor
              tableId="top-orders"
              catalog={catalog}
              columns={columns}
              onChange={handleColumnsChange}
              disabled={loading}
            />
          )}
          <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!rows.length || loading} />
        </div>
      </div>

      {loading ? (
        <div className="p-8 text-center text-xs" style={{ color: '#94A3B8' }}>Loading…</div>
      ) : viewMode === 'report' ? (
        <div className={`p-4 ${FIN_REPORT_SPLIT_GRID} min-h-[320px]`}>
          <div className="overflow-auto min-h-[280px] pr-1" style={{ maxHeight: 400 }}>
            <SalesTopOrdersLeaderboard rows={rows} maxRows={10} />
          </div>
          <div className="pt-1 lg:pt-0">
            {reportIntro && (
              <p className="text-xs leading-relaxed mb-3" style={{ color: '#475569' }}>
                {reportIntro}
              </p>
            )}
            <ul className="space-y-2 text-xs" style={{ color: '#334155' }}>
              {bullets.map((b, i) => (
                <li key={i} className="flex gap-2">
                  <span className="font-semibold tabular-nums shrink-0" style={{ color: '#1E3A5F' }}>{i + 1}.</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      ) : (
        <div className="p-4">
          <div className="overflow-auto min-h-[280px]" style={{ maxHeight: 420 }}>
            {rows.length ? renderTable(tableColumns, shown.length) : (
              <p className="text-xs py-8 text-center" style={{ color: '#94A3B8' }}>No orders for this period</p>
            )}
          </div>
          {rows.length > visible && (
            <button
              type="button"
              className="w-full mt-2 text-xs py-1.5 rounded-lg"
              style={{ background: '#F4F6F9', color: '#475569' }}
              onClick={() => setVisible(v => v + 10)}
            >
              Show 10 more
            </button>
          )}
        </div>
      )}
    </div>
  )
}
