import type { XlsxRow } from '../exportXlsx'
import { buildFlatTablePptxConfig } from './buildPptxExportConfig'
import { exportFinssentialsPptx } from './pptx/exportFinssentialsPptx'

export type FlatTablePptxOpts = {
  fileName: string
  pageTitle: string
  tableHeading: string
  breadcrumbCurrent: string
  breadcrumbParent?: string
  footerRight: string
  headers: string[]
  columnKinds?: string[]
  rows: XlsxRow[]
}

export async function exportFlatTablePptx(opts: FlatTablePptxOpts): Promise<void> {
  await exportFinssentialsPptx(buildFlatTablePptxConfig(opts))
}
