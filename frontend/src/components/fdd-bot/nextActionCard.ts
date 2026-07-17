import type { AdaptiveCardPayload } from './useFddBot'

const NEXT_ACTION_OPTIONS = [
  { label: 'Create another output', value: 'create_another' },
  { label: 'Change settings', value: 'change_settings' },
  { label: 'Restart the full guided conversation', value: 'restart' },
  { label: 'Change only the filters', value: 'change_filters' },
] as const

const SCRIPT_LABELS: Record<string, string> = {
  gst: 'General Sales Table',
  pvm: 'PVM Analysis',
  top: 'TOP Report',
  opos: 'Creditor / Debitor Aging',
  fixed_assets_rollf: 'Fixed Assets Rollforward',
  fte_payroll: 'FTE Development',
  churn: 'Churn / ARR Bridge',
  bubble: 'Bubble Scatter Plot',
  working_capital: 'Working capital',
  cashflow: 'Cash flow',
  fast_track: 'Fast Track',
  fast_track_pdf: 'Fast Track PDF Report',
}

export function scriptDisplayLabel(scriptKey: string): string {
  return SCRIPT_LABELS[scriptKey] ?? 'Output'
}

export function buildBuildDatabookCard(): AdaptiveCardPayload {
  return {
    type: 'adaptive_card',
    card: 'build_databook',
    title: 'Select Output',
    subtitle: 'What would you like to build?',
    inputs: [
      {
        id: 'output_type',
        type: 'radio',
        label: 'Choose output',
        options: [
          { label: 'Databook', value: 'databook' },
          { label: 'Revenue Databook', value: 'revenue_databook' },
          { label: 'Creditor / Debitor Aging', value: 'creditor_debitor_aging' },
          { label: 'Fixed Assets Rollforward', value: 'fixed_assets_rollforward' },
          { label: 'FTE Development', value: 'fte_development' },
        ],
      },
    ],
    submit_label: 'Continue',
  }
}

const BUILD_DATABOOK_AFTER_SCRIPTS = new Set([
  'opos',
  'fixed_assets_rollf',
  'fte_payroll',
])

export function shouldShowBuildDatabookAfterScript(scriptKey: string): boolean {
  return BUILD_DATABOOK_AFTER_SCRIPTS.has(scriptKey)
}

export function buildOposPostOutputCard(multipleSnapshots: boolean): AdaptiveCardPayload {
  const periodLabel = multipleSnapshots
    ? 'Change periods and sort order'
    : 'Change periods'
  const options: { label: string; value: string }[] = [
    { label: 'Another output', value: 'another_output' },
    { label: periodLabel, value: 'change_periods' },
    { label: 'Change filters', value: 'change_filters' },
  ]

  return {
    type: 'adaptive_card',
    card: 'opos_post_output',
    title: 'Aging output complete',
    subtitle: 'What would you like to do next?',
    inputs: [
      {
        id: 'opos_post_output_choice',
        type: 'radio',
        label: 'Choose next step',
        options,
      },
    ],
    submit_label: 'Continue',
  }
}

export function buildStrandPostOutputCard(scriptKey: 'fixed_assets_rollf' | 'fte_payroll'): AdaptiveCardPayload {
  const title =
    scriptKey === 'fixed_assets_rollf' ? 'Fixed assets output complete' : 'FTE output complete'
  return {
    type: 'adaptive_card',
    card: 'strand_post_output',
    title,
    subtitle: 'What would you like to do next?',
    inputs: [
      {
        id: 'script_key',
        type: 'text',
        label: '',
        default: scriptKey,
        required: false,
        hidden: true,
      },
      {
        id: 'strand_post_output_choice',
        type: 'radio',
        label: 'Choose next step',
        options: [
          { label: 'Another output', value: 'another_output' },
          { label: 'Change filters', value: 'change_filters' },
        ],
      },
    ],
    submit_label: 'Continue',
    script_key: scriptKey,
  }
}

export function buildNextActionCard(subtitle: string): AdaptiveCardPayload {
  return {
    type: 'adaptive_card',
    card: 'next_action',
    title: 'Output Complete',
    subtitle,
    inputs: [
      {
        id: 'next_action',
        type: 'radio',
        label: 'What would you like to do next?',
        options: [...NEXT_ACTION_OPTIONS],
      },
    ],
    submit_label: 'Continue',
  }
}

export function buildFileAttachmentCard(
  outputFile: string,
  outputFilename: string,
  options?: { notification?: boolean; title?: string },
): AdaptiveCardPayload {
  return {
    type: 'adaptive_card',
    card: 'file_attachment',
    title: options?.title ?? (options?.notification ? 'Output ready' : 'Output Excel'),
    filename: outputFilename,
    download_url: `/api/v1/fdd/download?path=${encodeURIComponent(outputFile)}`,
    notification: options?.notification,
    inputs: [],
  }
}

export function buildPdfReportPromptCard(workbookPath: string): AdaptiveCardPayload {
  const filename = workbookPath ? workbookPath.split(/[/\\]/).pop() ?? '' : ''
  return {
    type: 'adaptive_card',
    card: 'pdf_report_prompt',
    title: 'PDF report',
    subtitle: filename
      ? `Would you also like to create a PDF report from ${filename}?`
      : 'Would you also like to create a PDF report from your Fast Track Excel?',
    metadata: { workbook_path: workbookPath },
    inputs: [
      {
        id: 'pdf_report_choice',
        type: 'radio',
        label: 'Create PDF report?',
        options: [
          { label: 'Yes', value: 'Y' },
          { label: 'No', value: 'N' },
        ],
        default: 'Y',
      },
    ],
    submit_label: 'Continue',
  }
}
