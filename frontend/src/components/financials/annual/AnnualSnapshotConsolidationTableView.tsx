/**
 * Annual BS entity-breakdown table view — Dec22 … Jul25 + deltas + CAGR per entity column.
 */
import { useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { ConsolidationResponse, ConsolidationRow } from '../../../lib/api'
import { fmtKpi, fmtPct } from '../../../lib/fmt'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../finReportLayout'
import { shouldDisplayConsolidationRow } from './annualRowVisibility'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import {
  buildAnnualSnapshotTableColumns,
  type AnnualSnapshotTableColDef,
} from './annualSnapshotConsolidationColumns'
import {
  snapshotConsolidationCellValue,
  snapColToPeriodKey,
  type SnapshotConsolTarget,
} from './snapshotConsolidationCellResolver'

const BS_KEEP_CLOSED = new Set(['Deferred tax assets', 'Prepaid expenses'])

type BsSnapCol = 'dec_py2' | 'fy_py' | 'fy' | 'cm_py' | 'cm'

function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}

function periodRange(year: number, month: number, col: BsSnapCol): { from: string; to: string } {
  switch (col) {
    case 'dec_py2': return { from: `${year - 3}-01-01`, to: lastDay(year - 3, 12) }
    case 'fy_py': return { from: `${year - 2}-01-01`, to: lastDay(year - 2, 12) }
    case 'fy': return { from: `${year - 1}-01-01`, to: lastDay(year - 1, 12) }
    case 'cm_py': return { from: `${year - 1}-01-01`, to: lastDay(year - 1, month) }
    case 'cm': return { from: `${year}-01-01`, to: lastDay(year, month) }
  }
}

type ColGroup = {
  key: string
  title: string
  target: SnapshotConsolTarget
  highlighted?: boolean
  muted?: boolean
}

type SubCol = {
  key: string
  def: AnnualSnapshotTableColDef
  target: SnapshotConsolTarget
}

function DeltaBar({ value, maxAbs }: { value: number; maxAbs: number }) {
  if (maxAbs === 0) return <span className="inline-block" style={{ width: 28 }} />
  const pct = Math.min((Math.abs(value) / maxAbs) * 100, 100)
  const isPos = value >= 0
  return (
    <span className="inline-block align-middle" style={{ width: 28, height: 6, background: '#F1F5F9', borderRadius: 2, overflow: 'hidden' }}>
      <span style={{ display: 'block', height: '100%', width: `${pct}%`, background: isPos ? '#10B981' : '#DC2626', borderRadius: 2 }} />
    </span>
  )
}

function NumCell({
  value,
  bold,
  highlighted,
  muted,
  isKpi,
  isDelta,
  isPct,
  maxDelta,
  onClick,
}: {
  value: number
  bold?: boolean
  highlighted?: boolean
  muted?: boolean
  isKpi?: boolean
  isDelta?: boolean
  isPct?: boolean
  maxDelta?: number
  onClick?: () => void
}) {
  let content: string
  if (isKpi || isPct) {
    content = fmtPct(value)
  } else if (isDelta) {
    const color = value === 0 ? '#94A3B8' : value > 0 ? '#10B981' : '#DC2626'
    content = fmtKpi(value)
    return (
      <td
        className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums ${onClick ? 'cursor-pointer hover:bg-slate-50' : ''}`}
        style={{
          background: highlighted ? 'rgba(30,58,95,0.04)' : undefined,
          fontSize: FIN_TABLE_VALUE_FONT,
        }}
        onClick={onClick}
      >
        <span className="flex items-center justify-end gap-1.5">
          <span style={{ color, fontWeight: 500, fontStyle: 'italic' }}>{content}</span>
          {maxDelta != null && maxDelta > 0 && <DeltaBar value={value} maxAbs={maxDelta} />}
        </span>
      </td>
    )
  } else {
    content = fmtKpi(value)
  }

  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums ${onClick ? 'cursor-pointer hover:bg-slate-50' : ''}`}
      style={{
        background: highlighted ? 'rgba(30,58,95,0.04)' : undefined,
        fontWeight: bold ? 600 : 400,
        fontSize: FIN_TABLE_VALUE_FONT,
        color: muted ? '#94A3B8' : isKpi ? '#475569' : '#111827',
        fontStyle: isKpi ? 'italic' : undefined,
      }}
      onClick={onClick}
    >
      {content}
    </td>
  )
}

type Props = {
  data: ConsolidationResponse
  year: number
  month: number
  onDrill: (d: FinancialsDrillOpen) => void
  onRegisterCheckOpen?: (checkOpen: (id: string) => boolean) => void
}

export default function AnnualSnapshotConsolidationTableView({
  data,
  year,
  month,
  onDrill,
  onRegisterCheckOpen,
}: Props) {
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())

  const valueCols = useMemo(
    () => buildAnnualSnapshotTableColumns(year, month, data.col_labels),
    [year, month, data.col_labels],
  )

  const colGroups: ColGroup[] = useMemo(() => {
    const groups: ColGroup[] = data.entities.map(e => ({
      key: e.code,
      title: e.label,
      target: { kind: 'entity', code: e.code },
    }))
    groups.push(
      { key: '__aggregated__', title: 'Aggregated', target: { kind: 'aggregated' }, highlighted: true },
      { key: '__ic__', title: 'IC Elim.', target: { kind: 'ic' }, muted: true },
      { key: '__consolidation__', title: 'Consolidation', target: { kind: 'consolidation' }, highlighted: true },
    )
    return groups
  }, [data.entities])

  const subCols: SubCol[] = useMemo(
    () =>
      colGroups.flatMap(g =>
        valueCols.map(def => ({
          key: `${g.key}:${def.id}`,
          def,
          target: g.target,
        })),
      ),
    [colGroups, valueCols],
  )

  const entityCodes = data.entities.map(e => e.code)
  const totalCols = 1 + subCols.length
  const colsPerGroup = valueCols.length

  const maxDeltas = useMemo(() => {
    let maxFy = 0
    let maxCm = 0
    function walk(rows: ConsolidationRow[]) {
      for (const row of rows) {
        if (row.row_kind === 'kpi' || row.row_kind === 'kpi_header' || row.row_kind === 'title') {
          walk(row.children ?? [])
          continue
        }
        for (const g of colGroups) {
          const df = snapshotConsolidationCellValue(row, g.target, 'delta_fy')
          const dc = snapshotConsolidationCellValue(row, g.target, 'delta_cm')
          if (df != null) maxFy = Math.max(maxFy, Math.abs(df))
          if (dc != null) maxCm = Math.max(maxCm, Math.abs(dc))
        }
        walk(row.children ?? [])
      }
    }
    walk(data.rows)
    return { maxFy, maxCm }
  }, [data.rows, colGroups])

  const autoExpandedIds = useMemo(() => {
    const maxDepth = 2
    function collectIds(rows: ConsolidationRow[], depth: number): string[] {
      if (depth >= maxDepth) return []
      const ids: string[] = []
      for (const row of rows) {
        if (row.children?.length) {
          if (BS_KEEP_CLOSED.has(row.label)) continue
          ids.push(row.id)
          ids.push(...collectIds(row.children, depth + 1))
        }
      }
      return ids
    }
    return new Set(collectIds(data.rows, 0))
  }, [data.rows])

  function checkOpen(id: string): boolean {
    return autoExpandedIds.has(id) !== userToggles.has(id)
  }

  useEffect(() => {
    onRegisterCheckOpen?.(checkOpen)
  }, [onRegisterCheckOpen, userToggles, autoExpandedIds])

  function toggle(id: string) {
    setUserToggles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function renderRow(row: ConsolidationRow, depth: number): JSX.Element[] {
    const pad = 12 + depth * 14
    const isTitle = row.row_kind === 'title'
    const isKpiHeader = row.row_kind === 'kpi_header'
    const isKpi = row.row_kind === 'kpi'
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0
    const nodes: JSX.Element[] = []

    if (!shouldDisplayConsolidationRow(row, entityCodes)) return nodes

    if (isTitle) {
      nodes.push(
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
          <td colSpan={totalCols} className="px-3 py-2 text-xs font-bold uppercase tracking-wide" style={{ color: '#1E3A5F' }}>
            {row.label}
          </td>
        </tr>,
      )
    } else if (isKpiHeader) {
      nodes.push(
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
          <td className="px-3 py-2 text-xs font-semibold italic" style={{ color: '#1E3A5F' }}>{row.label}</td>
          {subCols.map(s => <td key={`${row.id}-${s.key}`} style={{ background: '#F8FAFC' }} />)}
        </tr>,
      )
    } else {
      nodes.push(
        <tr
          key={row.id}
          style={{
            borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
            borderTop: row.row_kind === 'subtotal' && depth === 0 ? '2px solid #E2E8F0' : undefined,
            background: isKpi ? '#F8FAFC' : undefined,
          }}
        >
          <td className="py-2 text-left whitespace-nowrap" style={{ minWidth: 200, paddingLeft: pad, paddingRight: 12 }}>
            <div className="flex items-center gap-0.5">
              {showChevron ? (
                <button type="button" onClick={() => toggle(row.id)} className="p-0.5 rounded shrink-0" style={{ color: '#1E3A5F' }}>
                  <ChevronRight size={14} style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }} />
                </button>
              ) : (
                <span style={{ width: 22 }} />
              )}
              <span className="text-xs" style={{ fontWeight: row.is_bold ? 600 : 500, color: isKpi ? '#64748B' : '#111827', fontStyle: isKpi ? 'italic' : undefined }}>
                {row.label}
              </span>
            </div>
          </td>
          {subCols.map(sub => {
            const group = colGroups.find(g => g.key === sub.key.split(':')[0])!
            const v = snapshotConsolidationCellValue(row, sub.target, sub.def.id)
            if (v == null) {
              return (
                <td
                  key={sub.key}
                  className="px-2.5 py-2 text-right text-xs text-slate-300"
                  style={{ background: group.highlighted && sub.def.highlighted ? 'rgba(30,58,95,0.04)' : undefined }}
                >
                  —
                </td>
              )
            }
            const entCode = sub.target.kind === 'entity' ? sub.target.code : undefined
            const ent = entCode ? data.entities.find(e => e.code === entCode) : null
            const drillCol = snapColToPeriodKey(sub.def.id)
            const canDrill = !isKpi && sub.target.kind === 'entity' && !sub.def.isDelta && sub.def.id !== 'cagr'
            return (
              <NumCell
                key={sub.key}
                value={v}
                bold={row.is_bold}
                highlighted={group.highlighted && sub.def.highlighted}
                muted={group.muted}
                isKpi={isKpi}
                isDelta={sub.def.isDelta}
                isPct={sub.def.isPct}
                maxDelta={sub.def.id === 'delta_fy' ? maxDeltas.maxFy : sub.def.id === 'delta_cm' ? maxDeltas.maxCm : undefined}
                onClick={
                  canDrill
                    ? () => {
                        const { from, to } = periodRange(year, month, drillCol as BsSnapCol)
                        onDrill({
                          dateFrom: from,
                          dateTo: to,
                          title: `${row.label} — ${ent?.label ?? entCode} — ${sub.def.labelLine1}`,
                          entityOverride: entCode,
                        })
                      }
                    : undefined
                }
              />
            )
          })}
        </tr>,
      )
    }

    if (!checkOpen(row.id)) return nodes
    if (row.children?.length) {
      for (const ch of row.children) nodes.push(...renderRow(ch, depth + 1))
    }
    return nodes
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '1px solid #E2E8F0', background: '#F8FAFC' }}>
            <th rowSpan={2} className="px-3 py-2 text-left font-semibold align-bottom" style={{ color: '#475569' }}>
              EURk
            </th>
            {colGroups.map(g => (
              <th
                key={g.key}
                colSpan={colsPerGroup}
                className="px-2 py-2 text-center font-semibold whitespace-nowrap border-l border-slate-200"
                style={{
                  color: g.muted ? '#94A3B8' : '#1E3A5F',
                  background: g.highlighted ? 'rgba(30,58,95,0.04)' : undefined,
                }}
              >
                {g.title}
              </th>
            ))}
          </tr>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#FAFBFC' }}>
            {colGroups.flatMap(g =>
              valueCols.map(def => (
                <th
                  key={`${g.key}-${def.id}`}
                  className="px-2 py-1.5 text-right font-medium text-[0.65rem] whitespace-nowrap border-l border-slate-100"
                  style={{
                    color: '#64748B',
                    background: g.highlighted && def.highlighted ? 'rgba(30,58,95,0.04)' : undefined,
                  }}
                >
                  <div>{def.labelLine1}</div>
                </th>
              )),
            )}
          </tr>
        </thead>
        <tbody>{data.rows.flatMap(r => renderRow(r, 0))}</tbody>
      </table>
    </div>
  )
}
