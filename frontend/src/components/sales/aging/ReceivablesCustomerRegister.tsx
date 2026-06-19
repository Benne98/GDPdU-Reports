import { useEffect, useMemo, useState } from 'react'
import { api, type ReceivablesCustomerRegisterDocument, type ReceivablesCustomerRegisterRow } from '../../../lib/api'
import { fmtAmountWhole } from '../../../lib/fmt'
import PlExportMenu, { type PlExportKind } from '../../financials/pl-two-view/PlExportMenu'
import { BRAND } from '../analytics/salesChartTheme'
import FilterableDataTable, { FilterableColumn } from '../operational/FilterableDataTable'
import TableFilterToolbarButton from '../operational/TableFilterToolbarButton'
import { exportCustomerRegister } from './shared/customerRegisterExport'

function formatDocDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('de-DE', { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return iso.slice(0, 10)
  }
}

function DocumentLinesTable({ docs }: { docs: ReceivablesCustomerRegisterDocument[] }) {
  return (
    <table className="w-full text-[11px]">
      <thead>
        <tr>
          <th className="px-2 py-1.5 text-left font-semibold" style={{ color: BRAND.slate }}>Document</th>
          <th className="px-2 py-1.5 text-left font-semibold" style={{ color: BRAND.slate }}>Posting</th>
          <th className="px-2 py-1.5 text-left font-semibold" style={{ color: BRAND.slate }}>Due</th>
          <th className="px-2 py-1.5 text-right font-semibold" style={{ color: BRAND.slate }}>Amount</th>
          <th className="px-2 py-1.5 text-right font-semibold" style={{ color: BRAND.slate }}>Days out.</th>
        </tr>
      </thead>
      <tbody>
        {docs.map(doc => (
          <tr key={`${doc.document_ref}-${doc.journal_entry_number}`} style={{ borderTop: '1px solid #F1F5F9' }}>
            <td className="px-2 py-1.5 font-medium" style={{ color: BRAND.navy }}>{doc.document_ref}</td>
            <td className="px-2 py-1.5" style={{ color: BRAND.textSecondary }}>{formatDocDate(doc.posting_date)}</td>
            <td className="px-2 py-1.5" style={{ color: doc.is_overdue ? '#D97706' : BRAND.textSecondary }}>
              {formatDocDate(doc.due_date)}
            </td>
            <td className="px-2 py-1.5 text-right tabular-nums font-medium" style={{ color: BRAND.navy }}>
              {fmtAmountWhole(doc.amount)}
            </td>
            <td className="px-2 py-1.5 text-right tabular-nums" style={{ color: BRAND.textSecondary }}>
              {Math.round(doc.days_outstanding)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DocumentDetailPanel({
  row,
  year,
  month,
  entity,
}: {
  row: ReceivablesCustomerRegisterRow
  year: number
  month: number
  entity?: string
}) {
  const [docs, setDocs] = useState<ReceivablesCustomerRegisterDocument[]>(row.documents ?? [])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if ((row.documents?.length ?? 0) > 0) {
      setDocs(row.documents!)
      setError(null)
      return
    }
    if (row.open_documents <= 0) {
      setDocs([])
      return
    }

    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .salesReceivablesCustomerDocuments(row.customer_id, year, month, entity)
      .then(res => {
        if (!cancelled) setDocs(res.documents ?? [])
      })
      .catch(() => {
        if (!cancelled) {
          setDocs([])
          setError('Could not load open documents')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [row.customer_id, row.documents, row.open_documents, year, month, entity])

  if (loading) {
    return (
      <p className="text-[11px] py-2 px-2" style={{ color: BRAND.textMuted }}>
        Loading open documents…
      </p>
    )
  }

  if (error) {
    return (
      <p className="text-[11px] py-2 px-2" style={{ color: '#D97706' }}>
        {error}
      </p>
    )
  }

  if (!docs.length) {
    return (
      <p className="text-[11px] py-2 px-2" style={{ color: BRAND.textMuted }}>
        No open document lines
      </p>
    )
  }

  return <DocumentLinesTable docs={docs} />
}

export default function ReceivablesCustomerRegister({
  rows,
  filterCustomer,
  filterCustomerId,
  onClearFilter,
  year,
  month,
  entity,
  periodLabel,
}: {
  rows: ReceivablesCustomerRegisterRow[]
  filterCustomer?: string | null
  filterCustomerId?: string | null
  onClearFilter?: () => void
  year: number
  month: number
  entity?: string
  periodLabel: string
}) {
  const [filtersEnabled, setFiltersEnabled] = useState(false)

  const filtered = useMemo(() => {
    if (filterCustomerId) return rows.filter(r => r.customer_id === filterCustomerId)
    if (!filterCustomer) return rows
    return rows.filter(r => r.customer_name === filterCustomer)
  }, [rows, filterCustomer, filterCustomerId])

  const columns = useMemo((): FilterableColumn<ReceivablesCustomerRegisterRow>[] => [
    { id: 'name', label: 'Customer', getValue: r => r.customer_name, sortValue: r => r.customer_name },
    {
      id: 'balance',
      label: 'Balance',
      align: 'right',
      getValue: r => String(r.balance),
      sortValue: r => r.balance,
      render: r => fmtAmountWhole(r.balance),
    },
    {
      id: 'overdue_pct',
      label: 'Overdue %',
      align: 'right',
      getValue: r => String(r.overdue_pct),
      sortValue: r => r.overdue_pct,
      render: r => (
        <span style={{ color: r.overdue_pct > 50 ? '#D97706' : '#64748B' }}>{r.overdue_pct}%</span>
      ),
    },
    {
      id: 'days',
      label: 'Days out.',
      align: 'right',
      getValue: r => String(Math.round(r.days_outstanding)),
      sortValue: r => r.days_outstanding,
      render: r => Math.round(r.days_outstanding),
    },
    {
      id: 'terms',
      label: 'Terms',
      align: 'right',
      getValue: r => `${r.payment_terms_days}d`,
      sortValue: r => r.payment_terms_days,
    },
    {
      id: 'docs',
      label: 'Open docs',
      align: 'right',
      getValue: r => String(r.open_documents),
      sortValue: r => r.open_documents,
    },
    {
      id: 'sales',
      label: 'Gross sales',
      align: 'right',
      getValue: r => String(r.gross_sales ?? 0),
      sortValue: r => r.gross_sales ?? 0,
      render: r => (r.gross_sales ?? 0) > 0 ? fmtAmountWhole(r.gross_sales!) : '—',
    },
    {
      id: 'contact',
      label: 'Contact',
      getValue: r => r.contact_name ?? '',
      sortValue: r => r.contact_name ?? '',
      render: r => (
        <span style={{ color: r.contact_name ? BRAND.text : BRAND.textMuted }}>
          {r.contact_name ?? '—'}
        </span>
      ),
    },
  ], [])

  const headerLeft = (
    <div>
      <h4 className="text-sm font-semibold" style={{ color: BRAND.navy }}>
        Customer register
      </h4>
      <p className="text-xs mt-0.5" style={{ color: BRAND.textMuted }}>
        {filterCustomer
          ? (
            <>
              Filtered to <strong style={{ color: BRAND.navy }}>{filterCustomer}</strong>
              {onClearFilter ? (
                <>
                  {' · '}
                  <button
                    type="button"
                    className="underline font-medium"
                    style={{ color: BRAND.navyLight }}
                    onClick={onClearFilter}
                  >
                    Clear filter
                  </button>
                </>
              ) : null}
            </>
          )
          : 'All customers — expand a row for open documents · select a bubble above to focus'}
      </p>
    </div>
  )

  const headerRight = (
    <div className="flex items-center gap-2 shrink-0">
      <TableFilterToolbarButton active={filtersEnabled} onClick={() => setFiltersEnabled(v => !v)} />
      <PlExportMenu
        formats={['pptx', 'xlsx']}
        onExport={async (kind: PlExportKind) => {
          if (!filtered.length) return
          await exportCustomerRegister({
            kind,
            rows: filtered,
            periodLabel,
            filterCustomer,
          })
        }}
        disabled={!filtered.length}
      />
    </div>
  )

  return (
    <FilterableDataTable
      columns={columns}
      rows={filtered}
      maxHeight={360}
      className=""
      headerLeft={headerLeft}
      headerRight={headerRight}
      filtersEnabled={filtersEnabled}
      expandable={{
        getRowId: r => r.customer_id,
        canExpand: r => r.open_documents > 0,
        renderDetail: row => (
          <DocumentDetailPanel row={row} year={year} month={month} entity={entity} />
        ),
      }}
    />
  )
}
