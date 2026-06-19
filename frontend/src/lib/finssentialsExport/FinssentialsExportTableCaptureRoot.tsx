import FinssentialsExportTableFrame, {
  type FinssentialsExportTableProps,
} from './FinssentialsExportTableFrame'

type Props = FinssentialsExportTableProps & {
  widthMm?: number
}

function mmToPx(mm: number): number {
  return Math.round(mm * (96 / 25.4))
}

/** Off-screen Finssentials export table for PDF/PPT html2canvas capture. */
export default function FinssentialsExportTableCaptureRoot({
  widthMm,
  widthPx: widthPxProp,
  ...tableProps
}: Props) {
  const widthPx = widthPxProp ?? (widthMm != null ? mmToPx(widthMm) : undefined)

  return (
    <div
      data-pl-export-table
      style={{
        width: widthPx,
        boxSizing: 'border-box',
        overflow: 'visible',
        background: '#FFFFFF',
      }}
    >
      <FinssentialsExportTableFrame {...tableProps} widthPx={widthPx} />
    </div>
  )
}
