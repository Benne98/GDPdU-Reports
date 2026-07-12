/**
 * Poll async FDD script jobs and invoke callbacks for UI updates.
 */

import { getApiBaseUrl } from '../../lib/api'
import {
  buildBuildDatabookCard,
  buildFileAttachmentCard,
  buildNextActionCard,
  buildOposPostOutputCard,
  buildStrandPostOutputCard,
  scriptDisplayLabel,
} from './nextActionCard'
import type { ChatMessage } from './useFddBot'

export interface ScriptJobPayload {
  run_id: string
  session_id: string
  script_key: string
  title?: string
}

export interface RunStatusResponse {
  status: 'running' | 'done' | 'failed'
  run_id: string
  session_id: string
  script_key?: string
  output_file?: string
  output_filename?: string
  output_path?: string
  message?: string
}

const INITIAL_WAIT_MS = 60_000
const POLL_INTERVAL_MS = 3_000
const MAX_STATUS_NULL_POLLS = 10
const RASA_URL = (import.meta.env.VITE_RASA_URL as string | undefined)?.trim() || '/rasa'

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function oposHasMultipleSnapshots(sessionId: string): Promise<boolean> {
  try {
    const resp = await fetch(`${RASA_URL}/conversations/${encodeURIComponent(sessionId)}/tracker`)
    if (!resp.ok) return false
    const data = (await resp.json()) as { slots?: Record<string, unknown> }
    const slots = data.slots ?? {}
    let raw = slots.opos_snapshots_json
    if (raw !== null && typeof raw === 'object' && 'value' in (raw as object)) {
      raw = (raw as { value: unknown }).value
    }
    if (typeof raw !== 'string' || !raw.trim()) return false
    const parsed = JSON.parse(raw) as unknown
    return Array.isArray(parsed) && parsed.length > 1
  } catch {
    return false
  }
}

function postScriptNextCard(job: ScriptJobPayload, multipleSnapshots?: boolean) {
  if (job.script_key === 'opos') {
    return buildOposPostOutputCard(Boolean(multipleSnapshots))
  }
  if (job.script_key === 'fixed_assets_rollf' || job.script_key === 'fte_payroll') {
    return buildStrandPostOutputCard(job.script_key)
  }
  return buildNextActionCard(
    'You can start another output while the current run finishes in the background.',
  )
}

async function fetchRunStatus(sessionId: string, runId: string): Promise<RunStatusResponse | null> {
  try {
    const url = `${getApiBaseUrl()}/api/v1/fdd/run/status?session_id=${encodeURIComponent(sessionId)}&run_id=${encodeURIComponent(runId)}`
    const resp = await fetch(url)
    if (!resp.ok) return null
    return (await resp.json()) as RunStatusResponse
  } catch {
    return null
  }
}

export interface ScriptJobCallbacks {
  addMessage: (msg: Omit<ChatMessage, 'id' | 'timestamp'> & { id?: string }) => void
  setScriptJobLoading: (loading: boolean) => void
}

function completionSubtitle(job: ScriptJobPayload, _status: RunStatusResponse): string {
  const label = job.title ?? scriptDisplayLabel(job.script_key)
  return `${label} created successfully. Use the download button below to save your file.`
}

function longerRunningText(job: ScriptJobPayload): string {
  const label = job.title ?? scriptDisplayLabel(job.script_key)
  return (
    `The ${label} is still running (Excel formulas can take several minutes). `
    + 'You can continue configuring the next output below — a download will appear here when ready.'
  )
}

/**
 * Poll until done/failed. Calls onInitialPhaseEnd when the 60s window closes while still running.
 */
export async function pollScriptJob(
  job: ScriptJobPayload,
  callbacks: ScriptJobCallbacks,
): Promise<void> {
  const { addMessage, setScriptJobLoading } = callbacks
  const deadline = Date.now() + INITIAL_WAIT_MS
  let releasedEarly = false
  let nullPolls = 0

  setScriptJobLoading(true)

  while (true) {
    const status = await fetchRunStatus(job.session_id, job.run_id)

    if (status === null) {
      nullPolls += 1
      if (nullPolls >= MAX_STATUS_NULL_POLLS) {
        addMessage({
          role: 'bot',
          text:
            'Could not reach the analysis server to check job status. '
            + 'Ensure the backend is running and try Proceed again.',
        })
        setScriptJobLoading(false)
        return
      }
    } else {
      nullPolls = 0
    }

    if (status?.status === 'done' && status.output_file) {
      const multipleSnapshots =
        job.script_key === 'opos' ? await oposHasMultipleSnapshots(job.session_id) : false
      if (releasedEarly) {
        addMessage({
          role: 'bot',
          custom: buildFileAttachmentCard(
            status.output_file,
            status.output_filename ?? 'Output.xlsx',
            { notification: true, title: `${scriptDisplayLabel(job.script_key)} ready` },
          ),
        })
      } else {
        addMessage({
          role: 'bot',
          custom: buildFileAttachmentCard(
            status.output_file,
            status.output_filename ?? 'Output.xlsx',
          ),
        })
        addMessage({
          role: 'bot',
          custom: postScriptNextCard(job, multipleSnapshots),
        })
      }
      setScriptJobLoading(false)
      return
    }

    if (status?.status === 'failed') {
      const msg = status.message ?? 'Unknown error'
      const timeoutHint = msg.toLowerCase().includes('timed out')
        ? '\n\nThe output file may be incomplete (hardcoded values, no formulas). Do not use it — try again or use a smaller file.'
        : ''
      addMessage({
        role: 'bot',
        text: `The script ran into an error:\n${msg}${timeoutHint}\n\nPlease check your settings and try again.`,
      })
      setScriptJobLoading(false)
      return
    }

    if (!releasedEarly && Date.now() >= deadline) {
      releasedEarly = true
      addMessage({ role: 'bot', text: longerRunningText(job) })
      const multipleSnapshots =
        job.script_key === 'opos' ? await oposHasMultipleSnapshots(job.session_id) : false
      addMessage({
        role: 'bot',
        custom: postScriptNextCard(job, multipleSnapshots),
      })
      setScriptJobLoading(false)
    }

    await sleep(POLL_INTERVAL_MS)
  }
}
