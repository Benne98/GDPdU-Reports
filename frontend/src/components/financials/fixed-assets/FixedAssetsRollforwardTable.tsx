import { useCallback, useMemo, useState } from 'react'

import type { FixedAssetRollforwardResponse, FixedAssetTableRow } from '../../../lib/api'

import { ExpandChevron } from '../../financials/pl-two-view/plTableCore'

import { FA_BORDER } from './fixedAssetsTableTheme'

import {

  FaLabelCell,

  FaRollforwardHeader,

  FaValueCell,

  faGroupLabel,

} from './FixedAssetsRollforwardTableParts'



type Props = {

  data: FixedAssetRollforwardResponse

  visibleColKeys: string[]

}



export default function FixedAssetsRollforwardTable({ data, visibleColKeys }: Props) {

  const keys = data.col_keys.filter(k => visibleColKeys.includes(k))

  // Collapse all Bilanzposition sections on first load; user can expand individually.
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () => new Set(data.rows.filter(r => r.row_kind === 'section_header').map(r => r.id)),
  )



  const toggle = useCallback((id: string) => {

    setCollapsed(prev => {

      const next = new Set(prev)

      if (next.has(id)) next.delete(id)

      else next.add(id)

      return next

    })

  }, [])



  const hiddenIndices = useMemo(() => {

    const hidden = new Set<number>()

    for (let i = 0; i < data.rows.length; i++) {

      const row = data.rows[i]

      if (row.row_kind !== 'section_header' || !collapsed.has(row.id)) continue

      const d = row.depth ?? 0

      for (let j = i + 1; j < data.rows.length; j++) {

        const r = data.rows[j]

        if ((r.depth ?? 0) <= d) break

        hidden.add(j)

      }

    }

    return hidden

  }, [data.rows, collapsed])



  const visibleRows = useMemo(

    () => data.rows

      .map((row, i) => ({ row, i }))

      .filter(({ i, row }) => !hiddenIndices.has(i) && row.row_kind !== 'subtotal'),

    [data.rows, hiddenIndices],

  )



  return (

    <div className="overflow-x-auto">

      <table className="w-full border-collapse text-xs" style={{ minWidth: 720 }}>

        <FaRollforwardHeader colKeys={keys} colLabels={data.col_labels} />

        <tbody>

          {visibleRows.map(({ row }) => (

            <FaRow

              key={row.id}

              row={row}

              keys={keys}

              collapsed={collapsed.has(row.id)}

              onToggle={() => toggle(row.id)}

            />

          ))}

        </tbody>

      </table>

    </div>

  )

}



function FaRow({

  row,

  keys,

  collapsed,

  onToggle,

}: {

  row: FixedAssetTableRow

  keys: string[]

  collapsed: boolean

  onToggle: () => void

}) {

  const depth = row.depth ?? 0

  const isTotal = row.row_kind === 'total'

  const isSection = row.row_kind === 'section_header'

  const indent = 8 + depth * 14



  return (

    <tr

      style={{

        borderTop: isTotal ? `2px solid ${FA_BORDER}` : undefined,

        borderBottom: isTotal ? `2px solid ${FA_BORDER}` : undefined,

      }}

    >

      <FaLabelCell rowKind={row.row_kind} variant="table" indent={indent}>

        <span className="inline-flex items-center gap-0.5">

          {isSection && <ExpandChevron open={!collapsed} onToggle={onToggle} />}

          {faGroupLabel(row.label, row.row_kind)}

        </span>

      </FaLabelCell>

      {keys.map(k => (

        <FaValueCell

          key={k}

          value={row.amounts?.[k]}

          colKey={k}

          rowKind={row.row_kind}

          variant="table"

        />

      ))}

    </tr>

  )

}

