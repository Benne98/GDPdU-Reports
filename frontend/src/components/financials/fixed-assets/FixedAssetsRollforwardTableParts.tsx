import type { CSSProperties, ReactNode } from 'react'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from '../statement-two-view/finReportLayout'
import { fmtFaCell } from './fixedAssetsTableFormat'
import {
  FA_BORDER,
  FA_HEADER_BG,
  FA_NAVY,
  type FaTableVariant,
  faGroupLabel,
  faHeaderCellStyle,
  faLabelCellBg,
  faLabelFontWeight,
  faValueCellBg,
  faValueFontWeight,
} from './fixedAssetsTableTheme'

export function FaRollforwardHeader({
  colKeys,
  colLabels,
  showMarkerColumn,
}: {
  colKeys: string[]
  colLabels: Record<string, string>
  showMarkerColumn?: boolean
}) {
  return (
    <thead>
      <tr style={{ borderBottom: `2px solid ${FA_BORDER}`, background: FA_HEADER_BG }}>
        <th
          className="px-2 py-2 text-left whitespace-nowrap text-xs font-semibold"
          style={{ ...faHeaderCellStyle(), fontSize: FIN_TABLE_VALUE_FONT }}
        >
          kEUR
        </th>
        {showMarkerColumn && (
          <th
            className="px-0 py-2 text-center font-semibold align-middle"
            style={{ ...faHeaderCellStyle(), width: 20, minWidth: 20, maxWidth: 20, fontSize: '0.62rem' }}
          >
            #
          </th>
        )}
        {colKeys.map(k => (
          <th
            key={k}
            className="px-2 py-2 text-right whitespace-nowrap text-xs font-semibold"
            style={{
              ...faHeaderCellStyle(k),
              fontSize: FIN_TABLE_VALUE_FONT,
            }}
          >
            {colLabels[k] ?? k}
          </th>
        ))}
      </tr>
    </thead>
  )
}

export function FaLabelCell({
  children,
  rowKind,
  variant,
  indent,
  style,
}: {
  children: ReactNode
  rowKind: string
  variant: FaTableVariant
  indent?: number
  style?: CSSProperties
}) {
  const isGroup = rowKind === 'section_header' || rowKind === 'subtotal' || rowKind === 'total'
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-left`}
      style={{
        paddingLeft: indent ?? 8,
        fontSize: FIN_TABLE_VALUE_FONT,
        fontWeight: faLabelFontWeight(rowKind),
        color: isGroup ? FA_NAVY : '#334155',
        background: faLabelCellBg(rowKind, variant),
        ...style,
      }}
    >
      {children}
    </td>
  )
}

export function FaValueCell({
  value,
  colKey,
  rowKind,
  variant: _variant,
  bold,
}: {
  value: number | null | undefined
  colKey: string
  rowKind: string
  variant: FaTableVariant
  bold?: boolean
}) {
  const isBold = Boolean(bold) || faValueFontWeight(rowKind) >= 600
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right tabular-nums whitespace-nowrap`}
      style={{
        fontSize: FIN_TABLE_VALUE_FONT,
        fontWeight: isBold ? 600 : 400,
        color: '#111827',
        background: faValueCellBg(colKey),
      }}
    >
      {fmtFaCell(value)}
    </td>
  )
}

export { faGroupLabel }
