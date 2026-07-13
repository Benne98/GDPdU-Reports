import { useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type {
  ConsolidationResponse,
  ConsolidationRow,
  FinancialStatementResponse,
  MonthlyResponse,
} from '../../../lib/api'
import { fmtDays, fmtKpi, fmtPct } from '../../../lib/fmt'
import { formatConsolidationPeriodLabel } from '../../../lib/periodColumnLabels'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../finReportLayout'
import { shouldDisplayConsolidationRow } from '../annual/annualRowVisibility'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import type { PlConsolidationColumnDef } from './plConsolidationColumnRegistry'
import {
  resolveConsolidationCell,
  targetFromColumnDef,
  type ConsolidationCellTarget,
} from './plConsolidationCellResolver'
import { buildPlanMapFromStatement } from './plPlanMap'
import type { PlPlanMap } from './usePlStatementData'
import { periodRange } from './plTableCore'

const BS_KEEP_CLOSED = new Set(['Deferred tax assets', 'Prepaid expenses'])

type SubCol = {
  key: string
  label: string
  col?: PlConsolidationColumnDef
  target: ConsolidationCellTarget
}

type ColGroup = {
  key: string
  title: string
  subCols: SubCol[]
  highlighted?: boolean
  muted?: boolean
}

function NumCell({
  value,
  bold,
  highlighted,
  muted,
  isKpi,
  isDays,
  onClick,
}: {
  value: number
  bold?: boolean
  highlighted?: boolean
  muted?: boolean
  isKpi?: boolean
  isDays?: boolean
  onClick?: () => void
}) {
  const content = isKpi ? (isDays ? fmtDays(value) : fmtPct(value)) : fmtKpi(value)
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
  extraColumns: PlConsolidationColumnDef[]
  statementByEntity: Map<string, FinancialStatementResponse>
  groupStatement: FinancialStatementResponse | null
  monthly?: MonthlyResponse | null
  onDrill: (d: FinancialsDrillOpen) => void
  onRegisterCheckOpen?: (checkOpen: (id: string) => boolean) => void
  /** Override sub-column header (e.g. YTDJul25A for annual entity breakdown). */
  periodColumnLabel?: string
}

export default function PlConsolidationTableView({
  data,
  year,
  month,
  extraColumns,
  statementByEntity,
  groupStatement,
  monthly,
  onDrill,
  onRegisterCheckOpen,
  periodColumnLabel,
}: Props) {
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())

  const cmLabel = periodColumnLabel ?? formatConsolidationPeriodLabel(data.col_label)

  const entityPlanMaps = useMemo(() => {
    const m = new Map<string, PlPlanMap>()
    for (const [code, stmt] of statementByEntity) {
      m.set(code, buildPlanMapFromStatement(stmt))
    }
    return m
  }, [statementByEntity])

  const groupPlanMap = useMemo(
    () => (groupStatement ? buildPlanMapFromStatement(groupStatement) : {}),
    [groupStatement],
  )

  const colGroups: ColGroup[] = useMemo(() => {
    const aggExtras = extraColumns.filter(c => c.target === 'aggregated')
    const conExtras = extraColumns.filter(c => c.target === 'consolidation')

    const entityGroups: ColGroup[] = data.entities.map(e => {
      const extras = extraColumns.filter(
        c => c.target === 'single_entity' && c.entityCode === e.code,
      )
      const subCols: SubCol[] = [
        {
          key: `${e.code}:cm`,
          label: cmLabel,
          target: { kind: 'entity', code: e.code },
        },
        ...extras.map(c => ({
          key: c.id,
          label: c.labelLine1,
          col: c,
          target: targetFromColumnDef(c),
        })),
      ]
      return { key: e.code, title: e.label, subCols }
    })

    return [
      ...entityGroups,
      {
        key: '__aggregated__',
        title: 'Aggregated',
        highlighted: true,
        subCols: [
          { key: 'agg:cm', label: cmLabel, target: { kind: 'aggregated' } },
          ...aggExtras.map(c => ({
            key: c.id,
            label: c.labelLine1,
            col: c,
            target: targetFromColumnDef(c),
          })),
        ],
      },
      {
        key: '__ic__',
        title: 'IC Elim.',
        muted: true,
        subCols: [{ key: 'ic:cm', label: cmLabel, target: { kind: 'ic' } }],
      },
      {
        key: '__consolidation__',
        title: 'Consolidation',
        highlighted: true,
        subCols: [
          { key: 'con:cm', label: cmLabel, target: { kind: 'consolidation' } },
          ...conExtras.map(c => ({
            key: c.id,
            label: c.labelLine1,
            col: c,
            target: targetFromColumnDef(c),
          })),
        ],
      },
    ]
  }, [data.entities, extraColumns, cmLabel])

  const twoRowHeader = colGroups.some(g => g.subCols.length > 1)
  const totalCols = 1 + colGroups.reduce((s, g) => s + g.subCols.length, 0)
  const entityCodes = data.entities.map(e => e.code)

  const autoExpandedIds = useMemo(() => {
    if (!data?.rows) return new Set<string>()
    const stmt = data.statement
    const maxDepth = stmt === 'bs' ? 2 : stmt === 'wc' ? 1 : 0
    if (maxDepth === 0) return new Set<string>()
    function collectIds(rows: ConsolidationRow[], depth: number): string[] {
      if (depth >= maxDepth) return []
      const ids: string[] = []
      for (const row of rows) {
        if (row.children?.length) {
          if (stmt === 'bs' && BS_KEEP_CLOSED.has(row.label)) continue
          ids.push(row.id)
          ids.push(...collectIds(row.children, depth + 1))
        }
      }
      return ids
    }
    return new Set(collectIds(data.rows, 0))
  }, [data])

  /** True when every non-KPI row has ic_eliminations === 0 — hatch the IC column instead of showing zeros. */
  const allIcZero = useMemo(() => {
    if (!data.rows.length) return false
    function walk(rows: ConsolidationRow[]): boolean {
      for (const row of rows) {
        if (row.row_kind !== 'kpi' && row.row_kind !== 'kpi_header') {
          if (Number(row.ic_eliminations ?? 0) !== 0) return false
        }
        if (row.children?.length && !walk(row.children)) return false
      }
      return true
    }
    return walk(data.rows)
  }, [data.rows])

  function checkOpen(id: string): boolean {
    return autoExpandedIds.has(id) !== userToggles.has(id)
  }

  useEffect(() => {
    onRegisterCheckOpen?.(checkOpen)
  }, [checkOpen, onRegisterCheckOpen, userToggles, autoExpandedIds])

  function toggle(id: string) {
    setUserToggles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function planForTarget(target: ConsolidationCellTarget): PlPlanMap {
    if (target.kind === 'entity') {
      return entityPlanMaps.get(target.code) ?? {}
    }
    return groupPlanMap
  }

  function cellValue(row: ConsolidationRow, sub: SubCol): number | null {
    const plan = planForTarget(sub.target)
    return resolveConsolidationCell(
      row,
      sub.target,
      sub.col ?? null,
      statementByEntity,
      groupStatement,
      plan,
      monthly,
    )
  }

  function entityLabelForDrill(target: ConsolidationCellTarget): string | undefined {
    if (target.kind === 'entity') return target.code
    return undefined
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
          {colGroups.flatMap(g => g.subCols.map(s => <td key={`${row.id}-${s.key}`} style={{ background: '#F8FAFC' }} />))}
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
          {colGroups.flatMap(g =>
            g.subCols.map(sub => {
              if (isKpi && sub.col) {
                return <td key={sub.key} />
              }
              if (allIcZero && sub.target.kind === 'ic' && !isKpi) {
                return (
                  <td
                    key={sub.key}
                    className={FIN_TABLE_CELL_CLASS}
                    style={{
                      backgroundImage: 'repeating-linear-gradient(45deg, transparent, transparent 4px, #E2E8F0 4px, #E2E8F0 5px)',
                    }}
                  />
                )
              }
              const v = cellValue(row, sub)
              if (isKpi && !sub.col) {
                if (v == null || v === 0) {
                  return (
                    <td key={sub.key} className="px-2.5 py-2 text-right text-xs text-slate-300">
                      —
                    </td>
                  )
                }
                return (
                  <NumCell
                    key={sub.key}
                    value={v}
                    isKpi
                    isDays={data.statement === 'wc'}
                    highlighted={g.highlighted}
                    muted={g.muted}
                  />
                )
              }
              if (v == null) {
                return (
                  <td
                    key={sub.key}
                    className="px-2.5 py-2 text-right text-xs text-slate-300"
                    style={{ background: g.highlighted ? 'rgba(30,58,95,0.04)' : undefined }}
                  >
                    —
                  </td>
                )
              }
              const entCode = entityLabelForDrill(sub.target)
              const ent = entCode ? data.entities.find(e => e.code === entCode) : null
              const drillTitle = ent
                ? `${row.label} — ${ent.label} — ${sub.col?.labelLine1 ?? cmLabel}`
                : `${row.label} — ${g.title} — ${sub.col?.labelLine1 ?? cmLabel}`
              return (
                <NumCell
                  key={sub.key}
                  value={v}
                  bold={row.is_bold}
                  highlighted={g.highlighted}
                  muted={g.muted}
                  isKpi={isKpi}
                  isDays={isKpi && data.statement === 'wc'}
                  onClick={
                    !isKpi && sub.target.kind === 'entity'
                      ? () => {
                          const { from, to } = periodRange(year, month, 'cm')
                          onDrill({
                            dateFrom: from,
                            dateTo: to,
                            title: drillTitle,
                            entityOverride: entCode,
                          })
                        }
                      : undefined
                  }
                />
              )
            }),
          )}
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
          {twoRowHeader ? (
            <>
              <tr style={{ borderBottom: '1px solid #E2E8F0', background: '#F8FAFC' }}>
                <th rowSpan={2} className="px-3 py-2 text-left font-semibold align-bottom" style={{ color: '#475569' }}>
                  EURk
                </th>
                {colGroups.map(g => (
                  <th
                    key={g.key}
                    colSpan={g.subCols.length}
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
                  g.subCols.map(sub => (
                    <th
                      key={sub.key}
                      className="px-2 py-1.5 text-right font-medium text-[0.65rem] whitespace-nowrap border-l border-slate-100"
                      style={{
                        color: '#64748B',
                        background: g.highlighted ? 'rgba(30,58,95,0.04)' : undefined,
                        backgroundImage: g.key === '__ic__' && allIcZero ? 'repeating-linear-gradient(45deg, transparent, transparent 4px, #E2E8F0 4px, #E2E8F0 5px)' : undefined,
                      }}
                    >
                      {sub.label}
                    </th>
                  )),
                )}
              </tr>
            </>
          ) : (
            <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
              <th className="px-3 py-2 text-left font-semibold" style={{ color: '#475569' }}>
                EURk
              </th>
              {colGroups.map(g => {
                const sub = g.subCols[0]
                return (
                  <th
                    key={g.key}
                    className="px-2.5 py-2 text-right font-semibold whitespace-nowrap border-l border-slate-200"
                    style={{
                      color: g.muted ? '#94A3B8' : '#1E3A5F',
                      background: g.highlighted ? 'rgba(30,58,95,0.04)' : undefined,
                      backgroundImage: g.key === '__ic__' && allIcZero ? 'repeating-linear-gradient(45deg, transparent, transparent 4px, #E2E8F0 4px, #E2E8F0 5px)' : undefined,
                    }}
                  >
                    <div>{g.title}</div>
                    <div className="text-[0.65rem] font-medium mt-0.5" style={{ color: '#64748B' }}>
                      {sub.label}
                    </div>
                  </th>
                )
              })}
            </tr>
          )}
        </thead>
        <tbody>{data.rows.flatMap(r => renderRow(r, 0))}</tbody>
      </table>
    </div>
  )
}
