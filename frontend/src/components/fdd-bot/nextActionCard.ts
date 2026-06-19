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
  bubble: 'Bubble Scatter Plot',
}

export function scriptDisplayLabel(scriptKey: string): string {
  return SCRIPT_LABELS[scriptKey] ?? 'Output'
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
