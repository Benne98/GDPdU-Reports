import { useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { ConsolidationResponse, ConsolidationRow } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { ValCell } from '../pl-two-view/plTableCore'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import { renderAnnualCommentCell } from './annualMiniTableCore'
import { shouldDisplayConsolidationRow } from './annualRowVisibility'
import { computeAutoExpandedIds } from '../statementRowExpansion'

const COMMENT_COL_PCT = 3
const LABEL_COL_PCT = 30

function valueColPct(entityCount: number, hasCommentCol: boolean): number {
  const valueColCount = entityCount + 1
  const remaining = 100 - LABEL_COL_PCT - (hasCommentCol ? COMMENT_COL_PCT : 0)
  return remaining / valueColCount
}

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
  const colCount = consol.entities.length + 2 + (hasCommentCol ? 1 : 0)
  const entityCodes = consol.entities.map(e => e.code)
  const numericColPct = valueColPct(consol.entities.length, hasCommentCol)

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

  function renderRow(row: ConsolidationRow, depth = 0): JSX.Element[] {
    const nodes: JSX.Element[] = []
    if (!shouldDisplayConsolidationRow(row, entityCodes)) return nodes

    const isTitle = row.row_kind === 'title'
    const isKpiHeader = row.row_kind === 'kpi_header'
    const isKpi = row.row_kind === 'kpi'
    const isSubtotal = row.row_kind === 'subtotal'
    const isBold = row.is_bold || isSubtotal
    const marker = commentMarkersByLineCode?.[row.id]
    const pad = 8 + depth * 12
    const showChevron = (row.children?.length ?? 0) > 0
    const isOpen = checkOpen(row.id)

    if (isTitle || isKpiHeader) {
      nodes.push(
        <tr
          key={row.id}
          style={{
            background: '#F8FAFC',
            borderTop: isKpiHeader ? '2px solid #E2E8F0' : '1px solid #E2E8F0',
          }}
        >
          <td
            colSpan={colCount}
            className="px-2 py-1.5 text-xs font-semibold"
            style={{ color: '#1E3A5F', fontStyle: isKpiHeader ? 'italic' : undefined }}
          >
            {row.label}
          </td>
        </tr>,
      )
    } else {
      nodes.push(
        <tr
          key={row.id}
          style={{
            borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
            borderTop: isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
            background: isKpi || isSubtotal ? '#F8FAFC' : undefined,
          }}
        >
          <td
            className="py-0.5 text-left truncate"
            style={{ paddingLeft: pad, paddingRight: 4 }}
            title={row.label}
          >
            <span className="flex items-center gap-0.5 min-w-0">
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
                className="text-xs block truncate"
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
          {consol.entities.map(e => {
            const v = row.entity_amounts[e.code]
            const display = isKpi ? Number(v ?? 0) : Number(v ?? 0)
            return (
              <ValCell
                key={e.code}
                value={display}
                bold={isBold}
                isPct={isKpi}
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
            isPct={isKpi}
            italic={isKpi}
            compact
            denser
            highlighted={!isKpi}
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

  return (
    <div className="min-w-0 w-full">
      <table className="w-full border-collapse text-xs table-fixed">
        <colgroup>
          <col style={{ width: `${LABEL_COL_PCT}%` }} />
          {hasCommentCol && <col style={{ width: `${COMMENT_COL_PCT}%` }} />}
          {consol.entities.map(e => (
            <col key={e.code} style={{ width: `${numericColPct}%` }} />
          ))}
          <col style={{ width: `${numericColPct}%` }} />
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
        <tbody>{consol.rows.flatMap(r => renderRow(r))}</tbody>
      </table>
    </div>
  )
}
