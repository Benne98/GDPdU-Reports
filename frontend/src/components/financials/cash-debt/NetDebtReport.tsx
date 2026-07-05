import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import type { NetDebtRow, NetDebtTableResponse } from '../../../lib/api'
import { ExpandChevron } from '../pl-two-view/plTableCore'
import PlCommentIndexBadge from '../../financials/pl-two-view/PlCommentIndexBadge'
import type { PlNarrativeBullet } from '../../financials/pl-two-view/plNarrativeEngine'
import { KEY_DRIVERS_HEADING } from '../annual/annualReportSectionHeadings'
import { fmtFaCell } from '../fixed-assets/fixedAssetsTableFormat'
import { FA_BORDER, FA_HEADER_BG, FA_NAVY, FA_SNAPSHOT_COL_BG } from '../fixed-assets/fixedAssetsTableTheme'
import { FIN_TABLE_CELL_CLASS, FIN_REPORT_SPLIT_GRID, FIN_TABLE_VALUE_FONT } from '../statement-two-view/finReportLayout'
import StatementNarrativeList from '../statement-two-view/StatementNarrativeList'
import StatementSectionHeading from '../statement-two-view/StatementSectionHeading'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import CashDebtBookingPanel from './CashDebtBookingPanel'
import NetDebtLoanTrendChart from './NetDebtLoanTrendChart'
import type { CashDebtPositionBookingsResponse } from '../../../lib/api'
import {
  buildNetDebtMarkerMap,
  mapNetDebtNarrativeBullets,
  resolveNetDebtNarrative,
} from './cashDebtNarrative'
import {
  netDebtCellValue,
  resolveNetDebtColumns,
} from './netDebtColumns'
import {
  expandAncestorsForNetDebt,
  isDrillableRow,
  isExpandableRow,
} from './netDebtRowUtils'

type FlatRow = NetDebtRow & { depth: number; hasChildren: boolean }

function flattenRows(rows: NetDebtRow[] | undefined, depth = 0, expanded: Set<string>): FlatRow[] {
  const out: FlatRow[] = []
  for (const row of rows ?? []) {
    const kids = row.children ?? []
    const hasChildren = kids.length > 0
    out.push({ ...row, depth, hasChildren })
    if (hasChildren && expanded.has(row.id)) {
      out.push(...flattenRows(kids, depth + 1, expanded))
    }
  }
  return out
}

function cellValue(row: NetDebtRow, key: string, anchorKey: string): number | null | undefined {
  return netDebtCellValue(row, key, anchorKey)
}

type Props = {
  data: NetDebtTableResponse
  bookings?: CashDebtPositionBookingsResponse | null
  bookingsLoading?: boolean
  /** Row id of the account whose chart is open — null when closed. */
  selectedChartRowId?: string | null
  selectedLoanLabel?: string
  onSelectAccount?: (row: NetDebtRow) => void
  onCloseChart?: () => void
}

export default function NetDebtReport({
  data,
  bookings,
  bookingsLoading,
  selectedChartRowId,
  selectedLoanLabel,
  onSelectAccount,
  onCloseChart,
}: Props) {
  const { keys, labels: colLabels, anchorKey } = useMemo(
    () => resolveNetDebtColumns(data),
    [data],
  )
  const anchorLabel = colLabels[anchorKey] ?? data.col_label ?? anchorKey

  const narrative = useMemo(() => resolveNetDebtNarrative(data), [data])
  const bullets = useMemo(() => mapNetDebtNarrativeBullets(data), [data])
  const markerMap = useMemo(() => buildNetDebtMarkerMap(bullets), [bullets])
  const hasMarkers = bullets.length > 0
  const hasNarrative = Boolean(narrative.intro || bullets.length)

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [highlightId, setHighlightId] = useState<string | null>(null)
  const tableWrapRef = useRef<HTMLDivElement>(null)
  const [tableHeightPx, setTableHeightPx] = useState<number | null>(null)
  const dataKey = `${data.year}-${data.month}-${data.anchor_date}-${data.entity ?? 'all'}`
  const didInitialExpand = useRef<string | null>(null)

  const chartOpen = Boolean(selectedChartRowId)

  const rowById = useMemo(() => {
    const map = new Map<string, NetDebtRow>()
    function walk(rows: NetDebtRow[] | undefined) {
      for (const r of rows ?? []) {
        map.set(r.id, r)
        walk(r.children)
      }
    }
    walk(data.rows)
    return map
  }, [data.rows])

  // Expand narrative targets once per dataset.
  useEffect(() => {
    if (!bullets.length) return
    if (didInitialExpand.current === dataKey) return
    didInitialExpand.current = dataKey
    setExpanded(prev => {
      const next = new Set(prev)
      for (const b of bullets) {
        expandAncestorsForNetDebt(b.line_code, next)
      }
      return next
    })
  }, [bullets, dataKey])

  const toggle = useCallback((id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const openChartForRow = useCallback(
    (row: NetDebtRow) => {
      if (!isDrillableRow(row)) return
      setHighlightId(row.id)
      onSelectAccount?.(row)
    },
    [onSelectAccount],
  )

  const onBulletSelect = useCallback(
    (bullet: PlNarrativeBullet) => {
      if (!bullet.line_code) return
      setHighlightId(bullet.line_code)
      setExpanded(prev => {
        const next = new Set(prev)
        expandAncestorsForNetDebt(bullet.line_code, next)
        return next
      })
      const row = rowById.get(bullet.line_code)
      if (row && isDrillableRow(row)) {
        onSelectAccount?.(row)
      }
    },
    [onSelectAccount, rowById],
  )

  const handleRowActivate = useCallback(
    (row: FlatRow) => {
      if (isExpandableRow(row)) {
        toggle(row.id)
        return
      }
      if (isDrillableRow(row)) {
        openChartForRow(row)
        return
      }
      if (markerMap[row.id]) {
        setHighlightId(row.id)
      }
    },
    [markerMap, openChartForRow, toggle],
  )

  const flat = useMemo(() => flattenRows(data.rows, 0, expanded), [data.rows, expanded])

  useEffect(() => {
    const el = tableWrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setTableHeightPx(el.getBoundingClientRect().height))
    ro.observe(el)
    setTableHeightPx(el.getBoundingClientRect().height)
    return () => ro.disconnect()
  }, [data, keys, expanded, chartOpen])

  const tableHeading = `Net debt — ${anchorLabel}`

  return (
    <div className={FIN_REPORT_SPLIT_GRID} style={{ alignItems: 'stretch' }}>
      <div className="min-w-0" ref={tableWrapRef}>
        <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs" style={{ minWidth: 640 }}>
            <thead>
              <tr style={{ borderBottom: `2px solid ${FA_BORDER}`, background: FA_HEADER_BG }}>
                <th
                  className="px-2 py-1.5 text-left w-8"
                  style={{ color: '#475569', fontSize: '0.68rem', fontWeight: 600 }}
                >
                  #
                </th>
                <th
                  className="px-2 py-1.5 text-left"
                  style={{ color: '#475569', fontSize: FIN_TABLE_VALUE_FONT, fontWeight: 600 }}
                >
                  kEUR
                </th>
                {hasMarkers && (
                  <th
                    className="px-0 py-1.5 text-center font-semibold align-middle"
                    style={{ width: 20, minWidth: 20, maxWidth: 20, fontSize: '0.62rem' }}
                  >
                    #
                  </th>
                )}
                {keys.map(k => (
                  <th
                    key={k}
                    className="px-2 py-1.5 text-right whitespace-nowrap"
                    style={{
                      color: k === anchorKey ? FA_NAVY : '#475569',
                      fontWeight: k === anchorKey ? 700 : 600,
                      background: k === anchorKey ? FA_SNAPSHOT_COL_BG : FA_HEADER_BG,
                      fontSize: FIN_TABLE_VALUE_FONT,
                    }}
                  >
                    {colLabels[k] ?? k}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {flat.map(row => (
                <NetDebtRowView
                  key={row.id}
                  row={row}
                  keys={keys}
                  anchorKey={anchorKey}
                  expanded={expanded.has(row.id)}
                  marker={markerMap[row.id]}
                  showMarkerColumn={hasMarkers}
                  selected={
                    selectedChartRowId === row.id ||
                    (highlightId === row.id && !selectedChartRowId)
                  }
                  chartActive={selectedChartRowId === row.id}
                  onToggle={() => toggle(row.id)}
                  onActivate={() => handleRowActivate(row)}
                />
              ))}
            </tbody>
          </table>
        </div>

        {chartOpen && (
          <div
            className="mt-4 rounded-lg border border-slate-200 bg-slate-50/80 overflow-hidden"
            role="region"
            aria-label="Monthly payment timeline"
          >
            <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-200 bg-white">
              <span className="text-xs font-semibold" style={{ color: FA_NAVY }}>
                Monthly payments
                {selectedLoanLabel ? ` — ${selectedLoanLabel}` : ''}
              </span>
              <button
                type="button"
                onClick={() => onCloseChart?.()}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors"
                aria-label="Close payment chart"
              >
                <X size={14} aria-hidden />
                Close
              </button>
            </div>
            <div className="px-3 py-3">
              <NetDebtLoanTrendChart
                data={bookings ?? null}
                loading={bookingsLoading}
                loanLabel={selectedLoanLabel}
                compact
              />
              {bookings && bookings.entries.length > 0 && (
                <details className="mt-3">
                  <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-700">
                    Posting detail ({bookings.entries.length})
                  </summary>
                  <div className="mt-2">
                    <CashDebtBookingPanel data={bookings} />
                  </div>
                </details>
              )}
            </div>
          </div>
        )}

        {!chartOpen && (
          <p className="text-[10px] text-slate-400 mt-2">
            Click a loan or bank account row to view monthly payment seasonality.
          </p>
        )}
      </div>

      <div
        className="min-w-0 flex flex-col border-t lg:border-t-0 lg:border-l border-slate-100 pt-4 lg:pt-0 lg:pl-4"
        style={
          tableHeightPx != null && tableHeightPx > 120
            ? { maxHeight: tableHeightPx, overflowY: 'auto' }
            : undefined
        }
      >
        <StatementSectionHeading>{KEY_DRIVERS_HEADING}</StatementSectionHeading>
        <p className="text-[0.65rem] text-slate-500 mb-2 -mt-1">
          Largest liabilities, booking seasonality and contract partners — click a bullet to jump to the row
        </p>
        {hasNarrative ? (
          <StatementNarrativeList
            intro={narrative.intro}
            bullets={bullets}
            loading={false}
            onSelect={onBulletSelect}
          />
        ) : (
          <p className="text-xs leading-relaxed text-slate-500">
            No analytical comments for this period — check postings and filters.
          </p>
        )}
      </div>
    </div>
  )
}

function MarkerCell({ marker }: { marker?: ReportCommentMarkerMap[string] }) {
  return (
    <td className="px-0 py-1 text-center align-middle" style={{ width: 20 }}>
      {marker ? <PlCommentIndexBadge marker={marker} /> : null}
    </td>
  )
}

function NetDebtRowView({
  row,
  keys,
  anchorKey,
  expanded,
  marker,
  showMarkerColumn,
  selected,
  chartActive,
  onToggle,
  onActivate,
}: {
  row: FlatRow
  keys: string[]
  anchorKey: string
  expanded: boolean
  marker?: ReportCommentMarkerMap[string]
  showMarkerColumn: boolean
  selected: boolean
  chartActive: boolean
  onToggle: () => void
  onActivate: () => void
}) {
  const isGroup = row.row_kind === 'section_header' || row.row_kind === 'subtotal' || row.row_kind === 'total'
  const isTotal = row.row_kind === 'total'
  const isSubtotal = row.row_kind === 'subtotal' || row.row_kind === 'total'
  const expandable = isExpandableRow(row)
  const drillable = isDrillableRow(row)
  const indent = 8 + row.depth * 14
  const labelWeight = isSubtotal || (isGroup && row.row_kind !== 'account') ? 600 : 400
  const interactive = expandable || drillable || Boolean(marker)

  return (
    <tr
      style={{
        cursor: interactive ? 'pointer' : undefined,
        boxShadow: selected ? `inset 3px 0 0 ${FA_NAVY}` : undefined,
        background: chartActive ? 'rgba(30, 58, 95, 0.04)' : undefined,
        borderTop: isTotal ? `2px solid ${FA_BORDER}` : undefined,
        borderBottom: isTotal ? `2px solid ${FA_BORDER}` : undefined,
      }}
      onClick={onActivate}
    >
      <td className="px-2 py-1 text-slate-500 tabular-nums">{row.ref ?? ''}</td>
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-left`}
        style={{
          paddingLeft: indent,
          fontSize: FIN_TABLE_VALUE_FONT,
          fontWeight: labelWeight,
          color: isGroup ? FA_NAVY : '#334155',
        }}
      >
        <span className="inline-flex items-center gap-0.5">
          {expandable && <ExpandChevron open={expanded} onToggle={onToggle} />}
          {row.label}
        </span>
      </td>
      {showMarkerColumn && <MarkerCell marker={marker} />}
      {keys.map(k => {
        const isAnchor = k === anchorKey
        const isBold = isSubtotal || isAnchor
        return (
          <td
            key={k}
            className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
            style={{
              fontSize: FIN_TABLE_VALUE_FONT,
              fontWeight: isBold ? 600 : 400,
              color: '#111827',
              background: isAnchor ? FA_SNAPSHOT_COL_BG : undefined,
            }}
          >
            {fmtFaCell(cellValue(row, k, anchorKey))}
          </td>
        )
      })}
    </tr>
  )
}
