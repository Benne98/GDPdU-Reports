import { useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { ConsolidationResponse, ConsolidationRow } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { ValCell } from '../pl-two-view/plTableCore'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import { renderAnnualCommentCell } from './annualMiniTableCore'
import { shouldDisplayConsolidationRow } from './annualRowVisibility'
import { computeAutoExpandedIds } from '../statementRowExpansion'
import {
  REPORT_LABEL_COL_MIN_PX,
  REPORT_MARKER_COL_PX,
  REPORT_PERIOD_COL_PX,
} from '../statement-two-view/finReportLayout'

const WC_KPI_HEADER_LABEL = 'KPIs — working capital days'

/** The three margin KPI rows that must render bold (label + value cells). */
const BOLD_MARGIN_KPI_LABELS = new Set(['Gross margin %', 'EBITDA margin %', 'Net profit margin %'])

type Props = {
  consol: ConsolidationResponse
  year: number
  month: number
  commentMarkersByLineCode?: ReportCommentMarkerMap
  checkOpen?: (id: string) => boolean
  toggle?: (id: string) => void
  onRegisterCheckOpen?: (checkOpen: (id: string) => boolean) => void
  onDrill: (d: FinancialsDrillOpen) => void
}

function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}

export default function AnnualConsolidationGridMiniTable({
  consol,
  year,
  month,
  commentMarkersByLineCode,
  checkOpen: checkOpenProp,
  toggle: toggleProp,
  onRegisterCheckOpen,
  onDrill,
}: Props) {
  const hasCommentCol = Boolean(commentMarkersByLineCode)
  // 1 label + optional # + 1 spacer + entities + 1 consolidation
  const colCount = consol.entities.length + 3 + (hasCommentCol ? 1 : 0)
  const entityCodes = consol.entities.map(e => e.code)

  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())
  const autoExpandedIds = useMemo(
    () => computeAutoExpandedIds(consol.rows, consol.statement ?? 'pl'),
    [consol.rows, consol.statement],
  )

  const internalCheckOpen = (id: string) => autoExpandedIds.has(id) !== userToggles.has(id)
  const checkOpen = checkOpenProp ?? internalCheckOpen
  const toggle =
    toggleProp ??
    ((id: string) => {
      setUserToggles(prev => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      })
    })

  useEffect(() => {
    onRegisterCheckOpen?.(checkOpen)
  }, [onRegisterCheckOpen, checkOpen, userToggles, autoExpandedIds, checkOpenProp])

  function renderKpiHeaderRow(key: string, label = 'KPIs'): JSX.Element {
    return (
      <tr key={key} style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
        <td
          className="px-2 py-1.5 text-xs font-semibold italic"
          style={{ color: '#1E3A5F' }}
        >
          {label}
        </td>
        {renderAnnualCommentCell(undefined, hasCommentCol)}
        <td />
        {consol.entities.map(e => (
          <td key={`${key}-${e.code}`} style={{ background: '#F8FAFC' }} />
        ))}
        <td style={{ background: 'rgba(30,58,95,0.04)' }} />
      </tr>
    )
  }

  function renderRow(row: ConsolidationRow, depth = 0): JSX.Element[] {
    const nodes: JSX.Element[] = []
    if (!shouldDisplayConsolidationRow(row, entityCodes)) return nodes

    const isTitle = row.row_kind === 'title'
    const isKpiHeader = row.row_kind === 'kpi_header'
    const isKpi = row.row_kind === 'kpi'
    // WC KPI rows (DIO/DSO/DPO/CCC) are in DAYS, not percent.
    const isWcKpi = isKpi && consol.statement === 'wc'
    const isSubtotal = row.row_kind === 'subtotal'
    const isBold = row.is_bold || isSubtotal || (isKpi && BOLD_MARGIN_KPI_LABELS.has(row.label))
    const marker = commentMarkersByLineCode?.[row.id]
    const pad = 8 + depth * 12
    const showChevron = (row.children?.length ?? 0) > 0
    const isOpen = checkOpen(row.id)

    if (isTitle) {
      nodes.push(
        <tr
          key={row.id}
          style={{
            background: '#F8FAFC',
            borderTop: '1px solid #E2E8F0',
          }}
        >
          <td
            colSpan={colCount}
            className="px-2 py-1.5 text-xs font-semibold"
            style={{ color: '#1E3A5F' }}
          >
            {row.label}
          </td>
        </tr>,
      )
    } else if (isKpiHeader) {
      nodes.push(renderKpiHeaderRow(row.id, row.label))
    } else {
      nodes.push(
        <tr
          key={row.id}
          style={{
            borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
            borderTop: isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
            // BS: grey only the grand totals (Assets / Equity & liabilities, depth 0);
            // nested subtotals stay bold-only. WC/PL keep greying all subtotals.
            background:
              isKpi || (isSubtotal && (consol.statement !== 'bs' || depth === 0)) ? '#F8FAFC' : undefined,
          }}
        >
          <td
            className="py-0.5 text-left"
            style={{ paddingLeft: pad, paddingRight: 4 }}
            title={row.label}
          >
            <span className="flex items-start gap-0.5 min-w-0">
              {showChevron ? (
                <button
                  type="button"
                  className="shrink-0 p-0 border-0 bg-transparent cursor-pointer"
                  style={{ color: '#94A3B8' }}
                  onClick={() => toggle(row.id)}
                  aria-expanded={isOpen}
                >
                  <ChevronRight
                    size={14}
                    style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}
                  />
                </button>
              ) : (
                <span className="w-[14px] shrink-0" />
              )}
              <span
                className="text-xs block whitespace-normal break-words"
                style={{
                  fontWeight: isBold ? 600 : 400,
                  fontStyle: isKpi ? 'italic' : undefined,
                  color: isKpi ? '#64748B' : '#111827',
                }}
              >
                {row.label}
              </span>
            </span>
          </td>
          {renderAnnualCommentCell(marker, hasCommentCol)}
          <td />
          {consol.entities.map(e => {
            const v = row.entity_amounts[e.code]
            const display = isKpi ? Number(v ?? 0) : Number(v ?? 0)
            return (
              <ValCell
                key={e.code}
                value={display}
                bold={isBold}
                isPct={isKpi && !isWcKpi}
                isDays={isWcKpi}
                italic={isKpi}
                compact
                denser
                onClick={
                  !isKpi
                    ? () =>
                        onDrill({
                          dateFrom: `${year}-01-01`,
                          dateTo: lastDay(year, month),
                          title: `${row.label} — ${e.label}`,
                          entityOverride: e.code,
                        })
                    : undefined
                }
              />
            )
          })}
          <ValCell
            value={isKpi ? row.consolidation : row.consolidation}
            bold={isBold}
            isPct={isKpi && !isWcKpi}
            isDays={isWcKpi}
            italic={isKpi}
            compact
            denser
            highlighted
          />
        </tr>,
      )
    }

    if (showChevron && isOpen) {
      for (const ch of row.children ?? []) {
        nodes.push(...renderRow(ch, depth + 1))
      }
    }
    return nodes
  }

  function renderAllRows(rows: ConsolidationRow[]): JSX.Element[] {
    const nodes: JSX.Element[] = []
    let kpiHeaderInserted = false
    for (const row of rows) {
      if (row.row_kind === 'kpi_header') {
        kpiHeaderInserted = true
        nodes.push(...renderRow(row))
        continue
      }
      if (row.row_kind === 'kpi' && !kpiHeaderInserted && consol.statement === 'wc') {
        kpiHeaderInserted = true
        nodes.push(renderKpiHeaderRow('wc-consol-kpi-header-fallback', WC_KPI_HEADER_LABEL))
      }
      if (row.row_kind === 'kpi' && !kpiHeaderInserted && consol.statement === 'bs') {
        kpiHeaderInserted = true
        nodes.push(renderKpiHeaderRow('bs-consol-kpi-header-fallback'))
      }
      nodes.push(...renderRow(row))
    }
    return nodes
  }

  return (
    <div className="min-w-0 w-full overflow-x-auto">
      <table className="w-full border-collapse text-xs table-fixed">
        <colgroup>
          <col style={{ width: REPORT_LABEL_COL_MIN_PX }} />
          {hasCommentCol && <col style={{ width: REPORT_MARKER_COL_PX }} />}
          <col />
          {consol.entities.map(e => (
            <col key={e.code} style={{ width: REPORT_PERIOD_COL_PX }} />
          ))}
          <col style={{ width: REPORT_PERIOD_COL_PX }} />
        </colgroup>
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th
              className="px-1 py-1.5 text-left font-semibold text-xs"
              style={{ color: '#475569' }}
            >
              EURk
            </th>
            {hasCommentCol && (
              <th
                className="px-0 py-1.5 text-center font-medium align-middle"
                style={{ color: '#94A3B8', fontSize: '0.62rem' }}
              >
                #
              </th>
            )}
            <th />
            {consol.entities.map(e => (
              <th
                key={e.code}
                className="px-0.5 py-1.5 text-right font-semibold text-xs truncate"
                style={{ color: '#475569' }}
                title={e.label}
              >
                {e.label}
              </th>
            ))}
            <th
              className="px-0.5 py-1.5 text-right font-semibold text-xs whitespace-nowrap"
              style={{
                color: '#1E3A5F',
                background: 'rgba(30,58,95,0.04)',
                borderLeft: '2px solid rgba(30,58,95,0.15)',
              }}
              title="Consolidation"
            >
              Cons.
            </th>
          </tr>
        </thead>
        <tbody>{renderAllRows(consol.rows)}</tbody>
      </table>
    </div>
  )
}
