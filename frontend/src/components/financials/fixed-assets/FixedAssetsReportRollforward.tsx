import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { FixedAssetPositionAsset, FixedAssetReportDetailResponse } from '../../../lib/api'

import { ExpandChevron } from '../../financials/pl-two-view/plTableCore'
import PlCommentIndexBadge from '../../financials/pl-two-view/PlCommentIndexBadge'
import type { PlNarrativeBullet } from '../../financials/pl-two-view/plNarrativeEngine'
import { KEY_DRIVERS_HEADING } from '../annual/annualReportSectionHeadings'
import { FIN_REPORT_SPLIT_GRID } from '../statement-two-view/finReportLayout'
import StatementNarrativeList from '../statement-two-view/StatementNarrativeList'
import StatementSectionHeading from '../statement-two-view/StatementSectionHeading'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

import {
  FaLabelCell,
  FaRollforwardHeader,
  FaValueCell,
} from './FixedAssetsRollforwardTableParts'
import { buildFaMarkerMap, mapFaNarrativeBullets } from './faRollforwardNarrative'
import { FA_BORDER, FA_NAVY } from './fixedAssetsTableTheme'

import { formatAssetDescription } from './formatAssetDescription'

type Props = {
  data: FixedAssetReportDetailResponse
  visibleColKeys: string[]
  loading?: boolean
}

function assetDisplayLabel(asset: FixedAssetPositionAsset): string {
  return formatAssetDescription(asset.asset_id, asset.asset_label, asset.label)
}

function hasNonZeroAmounts(
  amounts: Record<string, number | null | undefined> | undefined,
  keys: string[],
): boolean {
  return keys.some(k => {
    const v = amounts?.[k]
    return v != null && !Number.isNaN(v) && Math.abs(v) > 0.0001
  })
}

export default function FixedAssetsReportRollforward({ data, visibleColKeys, loading }: Props) {
  const keys = data.col_keys.filter(k => visibleColKeys.includes(k))

  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const tableWrapRef = useRef<HTMLDivElement>(null)
  const [tableHeightPx, setTableHeightPx] = useState<number | null>(null)

  const bullets = useMemo(() => mapFaNarrativeBullets(data), [data])
  const markerMap = useMemo(() => buildFaMarkerMap(bullets), [bullets])
  const hasMarkers = bullets.length > 0

  useEffect(() => {
    if (!selectedId && bullets.length) {
      setSelectedId(bullets[0].line_code)
    }
  }, [bullets, selectedId])

  const toggle = useCallback((id: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const onBulletSelect = useCallback((bullet: PlNarrativeBullet) => {
    setSelectedId(bullet.line_code)
  }, [])

  const totalAmounts = useMemo(() => {
    if (data.total?.amounts) return data.total.amounts
    const sums: Record<string, number> = {}
    for (const k of keys) {
      let s = 0
      for (const pos of data.positions) {
        const v = pos.amounts?.[k]
        if (v != null && !Number.isNaN(v)) s += v
      }
      sums[k] = s
    }
    return sums
  }, [data.total?.amounts, data.positions, keys])

  const anchorLabel = data.col_labels[data.anchor_date] ?? data.anchor_date
  const tableHeading = `Fixed assets rollforward — ${anchorLabel}`

  useEffect(() => {
    const el = tableWrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      setTableHeightPx(el.getBoundingClientRect().height)
    })
    ro.observe(el)
    setTableHeightPx(el.getBoundingClientRect().height)
    return () => ro.disconnect()
  }, [data, keys, expanded])

  if (loading) {
    return <p className="text-sm text-slate-500 px-4 py-6">Loading report…</p>
  }

  return (
    <div className="px-4 pt-5 pb-6">
      <div className={FIN_REPORT_SPLIT_GRID} style={{ alignItems: 'stretch' }}>
        <div className="min-w-0" ref={tableWrapRef}>
          <StatementSectionHeading>{tableHeading}</StatementSectionHeading>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-xs" style={{ minWidth: 640 }}>
              <FaRollforwardHeader colKeys={keys} colLabels={data.col_labels} showMarkerColumn={hasMarkers} />
              <tbody>
                {data.positions.map(pos => {
                  const visibleAssets = pos.assets.filter(a => hasNonZeroAmounts(a.amounts, keys))
                  const open = expanded.has(pos.id)
                  return (
                    <PositionBlock
                      key={pos.id}
                      pos={pos}
                      assets={visibleAssets}
                      keys={keys}
                      open={open}
                      selected={selectedId === pos.id}
                      marker={markerMap[pos.id]}
                      showMarkerColumn={hasMarkers}
                      onToggle={() => toggle(pos.id)}
                      onSelect={() => setSelectedId(pos.id)}
                    />
                  )
                })}
                <tr style={{ borderTop: `2px solid ${FA_BORDER}`, borderBottom: `2px solid ${FA_BORDER}` }}>
                  <FaLabelCell rowKind="total" variant="report">
                    {data.total?.label ?? 'Fixed assets'}
                  </FaLabelCell>
                  {hasMarkers && <FaMarkerCell />}
                  {keys.map(k => (
                    <FaValueCell
                      key={k}
                      value={totalAmounts[k]}
                      colKey={k}
                      rowKind="total"
                      variant="report"
                    />
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
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
            Key drivers with rollforward table — click a bullet for detail
          </p>
          <StatementNarrativeList
            intro={data.narrative?.intro ?? data.intro}
            bullets={bullets}
            loading={false}
            onSelect={onBulletSelect}
          />
        </div>
      </div>
    </div>
  )
}

function FaMarkerCell({ marker }: { marker?: ReportCommentMarkerMap[string] }) {
  return (
    <td className="px-0 py-1 text-center align-middle" style={{ width: 20 }}>
      {marker ? <PlCommentIndexBadge marker={marker} /> : null}
    </td>
  )
}

function PositionBlock({
  pos,
  assets,
  keys,
  open,
  selected,
  marker,
  showMarkerColumn,
  onToggle,
  onSelect,
}: {
  pos: FixedAssetReportDetailResponse['positions'][number]
  assets: FixedAssetPositionAsset[]
  keys: string[]
  open: boolean
  selected: boolean
  marker?: ReportCommentMarkerMap[string]
  showMarkerColumn: boolean
  onToggle: () => void
  onSelect: () => void
}) {
  return (
    <>
      <tr
        style={{
          cursor: 'pointer',
          boxShadow: selected ? `inset 3px 0 0 ${FA_NAVY}` : undefined,
        }}
        onClick={onSelect}
      >
        <FaLabelCell rowKind="section_header" variant="report" indent={8}>
          <span className="inline-flex items-center gap-1" onClick={e => e.stopPropagation()}>
            <ExpandChevron open={open} onToggle={onToggle} />
            {pos.bilanzposition}
          </span>
        </FaLabelCell>
        {showMarkerColumn && <FaMarkerCell marker={marker} />}
        {keys.map(k => (
          <FaValueCell
            key={k}
            value={pos.amounts?.[k]}
            colKey={k}
            rowKind="line"
            variant="report"
          />
        ))}
      </tr>
      {open &&
        assets.map(asset => (
          <tr key={`${pos.id}-${asset.asset_id}`}>
            <FaLabelCell rowKind="line" variant="report" indent={28}>
              {assetDisplayLabel(asset)}
            </FaLabelCell>
            {showMarkerColumn && <FaMarkerCell />}
            {keys.map(k => (
              <FaValueCell
                key={k}
                value={asset.amounts?.[k]}
                colKey={k}
                rowKind="line"
                variant="report"
              />
            ))}
          </tr>
        ))}
    </>
  )
}
