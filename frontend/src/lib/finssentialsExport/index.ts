export { PROJECT_NAME, C } from './designStyles'
export {
  flattenTreeForExport,
  type ExportFlatRow,
  type ExportRowKind,
  type TreeExportNode,
  type FlattenTreeOptions,
} from './flattenTreeForExport'
export { exportFinssentialsXlsx, type FinssentialsXlsxConfig } from './exportFinssentialsXlsx'
export { loadDesignWorkbook, DESIGN_SHEET_NAME } from './loadDesignWorkbook'
export { buildExportCheckOpen } from './buildExportCheckOpen'
export {
  buildStatementReportPptxConfig,
  buildStatementTablePptxConfig,
  buildMonthlyPptxConfig,
  buildConsolidationPptxConfig,
  buildFlatTablePptxConfig,
} from './buildPptxExportConfig'
export {
  exportFinssentialsPptx,
  addFinssentialsPptxSlide,
  type FinssentialsPptxConfig,
} from './pptx/exportFinssentialsPptx'
export { exportFlatTablePptx, type FlatTablePptxOpts } from './exportFlatTablePptx'
export {
  PPT_COLORS,
  PPT_CONTENT_BOTTOM,
  PPT_CONTENT_TOP,
  PPT_SECTION_LABEL_Y,
  PPT_NARRATIVE_FONT_PT,
  PPT_NARRATIVE_W,
  PPT_NARRATIVE_X,
  PPT_TABLE_W,
  PPT_TABLE_X,
  addFinssentialsPptxFooter,
  addFinssentialsPptxHeader,
  addPptxSectionLabel,
  pptGeneratedLabel,
} from './reportSlideLayout'
