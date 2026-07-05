import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PersonnelAccountingResponse, PersonnelTableRow } from '../../../lib/api'
import { fmtChartKpi, fmtPct } from '../../../lib/fmt'
import { ExpandChevron } from '../../financials/pl-two-view/plTableCore'
import PlCommentIndexBadge from '../../financials/pl-two-view/PlCommentIndexBadge'
import type { PlNarrativeBullet } from '../../financials/pl-two-view/plNarrativeEngine'
import { KEY_DRIVERS_HEADING } from '../annual/annualReportSectionHeadings'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../statement-two-view/finReportLayout'
import StatementNarrativeList from '../statement-two-view/StatementNarrativeList'
import StatementSectionHeading from '../statement-two-view/StatementSectionHeading'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import { buildPayrollMarkerMap, formatPayrollValueCell, resolvePayrollNarrative } from './payrollNarrative'

const REPORT_SPLIT_GRID =
  'grid grid-cols-1 md:grid-cols-[minmax(0,1.05fr)_minmax(260px,0.95fr)] gap-x-4 gap-y-4 items-start'

const HEADER_BG = '#F8FAFC'
const SUBTOTAL_BG = '#F8FAFC'
const HIGHLIGHT_BG = 'rgba(30,58,95,0.04)'
const SELECTED_ROW_BG = 'rgba(30,58,95,0.06)'
const KPI_ITALIC = '#64748B'
const BORDER = '#E2E8F0'

type Props = {
  data: PersonnelAccountingResponse
  visibleColKeys: string[]
  loading?: boolean
}

function formatCell(value: number | null | undefined, unit: string): string {
  if (value == null || Number.isNaN(value)) return '—'
  if (unit === 'pct') return fmtPct(value)
  if (unit === 'count') return Math.round(value).toLocaleString('de-DE')
  return fmtChartKpi(value)
}

export default function PayrollAccountingReport({ data, visibleColKeys, loading }: Props) {
  const keys = useMemo(() => {
    const all = data.col_keys ?? data.col_dates ?? []
    const filtered = all.filter(k => visibleColKeys.includes(k))
    return filtered.length ? filtered : all
  }, [data.col_keys, data.col_dates, visibleColKeys])
  const anchorKey = keys[keys.length - 1]
  const hasGroups = keys.some(k => data.col_groups?.[k])

  const rowIds = useMemo(() => new Set(data.rows.map(r => r.id)), [data.rows])
  const narrative = useMemo(() => resolvePayrollNarrative(data), [data])
  const bullets = narrative.bullets
  const markerMap = useMemo(() => buildPayrollMarkerMap(bullets, rowIds), [bullets, rowIds])
  const hasMarkers = Object.keys(markerMap).length > 0
  const hasNarrative = bullets.length > 0

  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const tableWrapRef = useRef<HTMLDivElement>(null)
  const [tableHeightPx, setTableHeightPx] = useState<number | null>(null)

  useEffect(() => {
    if (!selectedId && bullets.length) setSelectedId(bullets[0].line_code)
  }, [bullets, selectedId])

  const toggle = useCallback((id: string) => {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const onBulletSelect = useCallback((bullet: PlNarrativeBullet) => {
    setSelectedId(bullet.line_code)
  }, [])

  const hiddenIndices = useMemo(() => {
    const hidden = new Set<number>()
    for (let i = 0; i < data.rows.length; i++) {
      const row = data.rows[i]
      if (row.row_kind !== 'section_header' || !collapsed.has(row.id)) continue
      const d = row.depth ?? 0
      for (let j = i + 1; j < data.rows.length; j++) {
        const r = data.rows[j]
        if (r.row_kind === 'subtotal' && (r.depth ?? 0) === d) break
        hidden.add(j)
      }
    }
    return hidden
  }, [data.rows, collapsed])

  const visibleRows = useMemo(
    () => data.rows.map((row, i) => ({ row, i })).filter(({ i }) => !hiddenIndices.has(i)),
    [data.rows, hiddenIndices],
  )

  const groupSpans = useMemo(() => {
    if (!hasGroups) return []
    const spans: Array<{ label: string; span: number }> = []
    let i = 0
    while (i < keys.length) {
      const g = data.col_groups?.[keys[i]]
      let span = 1
      while (i + span < keys.length && data.col_groups?.[keys[i + span]] === g) span++
      spans.push({ label: g ?? '', span })
      i += span
    }
    return spans
  }, [keys, data.col_groups, hasGroups])

  const anchorLabel = data.col_labels[data.anchor_date] ?? data.anchor_date
  const tableHeading = `Payroll accounting — ${anchorLabel}`

  useEffect(() => {
    const el = tableWrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setTableHeightPx(el.getBoundingClientRect().height))
    ro.observe(el)
    setTableHeightPx(el.getBoundingClientRect().height)
    return () => ro.disconnect()
  }, [data, keys, collapsed])

  if (loading) {
    return <p className="text-sm text-slate-500 px-4 py-6">Loading report…</p>
  }

  return (
    <div className="px-4 pt-5 pb-6">
      <div className={REPORT_SPLIT_GRID} style={{ alignItems: 'stretch' }}>
        <div className="min-w-0" ref={tableWrapRef}>
          <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs" style={{ minWidth: 520, maxWidth: 640 }}>
              <thead>
                {hasGroups && (
                  <tr style={{ borderBottom: `1px solid ${BORDER}`, background: HEADER_BG }}>
                    <th className="px-2 py-1" />
                    {hasMarkers && <th className="px-0 py-1" style={{ width: 20 }} />}
                    {groupSpans.map((g, i) => (
                      <th
                        key={`${g.label}-${i}`}
                        colSpan={g.span}
                        className="px-2 py-1 text-right font-semibold text-xs"
                        style={{ color: '#1E3A5F' }}
                      >
                        {g.label}
                      </th>
                    ))}
                  </tr>
                )}
                <tr style={{ borderBottom: `2px solid ${BORDER}`, background: HEADER_BG }}>
                  <th
                    className="px-2 py-2 text-left font-semibold whitespace-nowrap"
                    style={{ color: '#475569', fontSize: FIN_TABLE_VALUE_FONT }}
                  >
                    EURk
                  </th>
                  {hasMarkers && (
                    <th
                      className="px-0 py-2 text-center font-semibold align-middle"
                      style={{ width: 20, minWidth: 20, maxWidth: 20, color: '#475569', fontSize: '0.62rem' }}
                    >
                      #
                    </th>
                  )}
                  {keys.map(k => (
                    <th
                      key={k}
                      className="px-2 py-2 text-right font-semibold whitespace-nowrap"
                      style={{
                        color: k === anchorKey ? '#1E3A5F' : '#475569',
                        fontWeight: k === anchorKey ? 700 : 600,
                        background: k === anchorKey ? HIGHLIGHT_BG : HEADER_BG,
                        fontSize: FIN_TABLE_VALUE_FONT,
                      }}
                    >
                      {data.col_labels[k] ?? k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map(({ row }) => (
                  <ReportRow
                    key={row.id}
                    row={row}
                    keys={keys}
                    anchorKey={anchorKey}
                    collapsed={collapsed.has(row.id)}
                    marker={markerMap[row.id]}
                    showMarkerColumn={hasMarkers}
                    selected={selectedId === row.id}
                    onToggle={() => toggle(row.id)}
                    onSelect={() => setSelectedId(row.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div
          className="min-w-0 flex flex-col border-t md:border-t-0 md:border-l border-slate-100 pt-4 md:pt-0 md:pl-4"
          style={
            tableHeightPx != null && tableHeightPx > 120
              ? { maxHeight: tableHeightPx, overflowY: 'auto' }
              : undefined
          }
        >
          <StatementSectionHeading>{KEY_DRIVERS_HEADING}</StatementSectionHeading>
          {hasNarrative ? (
            <StatementNarrativeList
              bullets={bullets}
              loading={false}
              onSelect={onBulletSelect}
            />
          ) : (
            <p className="text-xs leading-relaxed" style={{ color: '#64748B' }}>
              No payroll accounting comments for this period — check postings and filters.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

function MarkerCell({
  marker,
  background,
}: {
  marker?: ReportCommentMarkerMap[string]
  background?: string
}) {
  return (
    <td className="px-0 py-1 text-center align-middle" style={{ width: 20, background }}>
      {marker ? <PlCommentIndexBadge marker={marker} /> : null}
    </td>
  )
}

function ReportRow({
  row,
  keys,
  anchorKey,
  collapsed,
  marker,
  showMarkerColumn,
  selected,
  onToggle,
  onSelect,
}: {
  row: PersonnelTableRow
  keys: string[]
  anchorKey: string | undefined
  collapsed: boolean
  marker?: ReportCommentMarkerMap[string]
  showMarkerColumn: boolean
  selected: boolean
  onToggle: () => void
  onSelect: () => void
}) {
  const isSection = row.row_kind === 'section_header'
  const isSubtotal = row.row_kind === 'subtotal'
  const isTotal = row.row_kind === 'total'
  const isKpiHeader = row.row_kind === 'kpi_header'
  const isKpi = row.row_kind === 'kpi'
  const depth = row.depth ?? 0
  const indent = 8 + depth * 16

  if (isKpiHeader) {
    return (
      <tr style={{ background: HEADER_BG }}>
        <td
          className={`${FIN_TABLE_CELL_CLASS} text-left`}
          style={{
            color: '#1E3A5F',
            fontStyle: 'italic',
            fontWeight: 600,
            fontSize: FIN_TABLE_VALUE_FONT,
            background: HEADER_BG,
          }}
        >
          {row.label}
        </td>
        {showMarkerColumn && <td style={{ background: HEADER_BG }} />}
        {keys.map(k => (
          <td
            key={k}
            className={FIN_TABLE_CELL_CLASS}
            style={{ background: k === anchorKey ? HIGHLIGHT_BG : HEADER_BG }}
          />
        ))}
      </tr>
    )
  }

  const bg = selected
    ? SELECTED_ROW_BG
    : isSubtotal
      ? SUBTOTAL_BG
      : isKpi
        ? HEADER_BG
        : '#fff'
  const italic = isKpi
  const labelColor = isKpi ? KPI_ITALIC : isSection || isTotal ? '#1E3A5F' : isSubtotal ? '#0F172A' : '#475569'

  return (
    <tr
      style={{ background: bg }}
      onClick={marker ? onSelect : undefined}
    >
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-left`}
        style={{
          paddingLeft: indent,
          fontWeight: isTotal || isSubtotal || (isSection && depth != null) ? 600 : 400,
          fontStyle: italic ? 'italic' : undefined,
          color: labelColor,
          fontSize: FIN_TABLE_VALUE_FONT,
          cursor: marker ? 'pointer' : undefined,
          background: bg,
        }}
      >
        <span className="inline-flex items-center gap-0.5">
          {isSection && row.depth != null && <ExpandChevron open={!collapsed} onToggle={onToggle} />}
          {row.label}
        </span>
      </td>
      {showMarkerColumn && <MarkerCell marker={marker} background={bg} />}
      {keys.map(k => (
        <td
          key={k}
          className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
          style={{
            fontSize: FIN_TABLE_VALUE_FONT,
            fontWeight: isTotal || isSubtotal ? 600 : 400,
            fontStyle: italic ? 'italic' : undefined,
            color: isKpi ? KPI_ITALIC : '#111827',
            background: k === anchorKey ? HIGHLIGHT_BG : bg,
          }}
        >
          {formatPayrollValueCell(row, row.amounts?.[k], row.unit, formatCell)}
        </td>
      ))}
    </tr>
  )
}
