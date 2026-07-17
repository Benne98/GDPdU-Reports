export interface AuditColumnEntry {
  role: string
  header: string
  letter?: string
  issue: string
  count: number
}

export interface AuditFileEntry {
  label: string
  file_path?: string
  sheet_name?: string
  excluded_rows?: number
  columns?: AuditColumnEntry[]
  notes?: string[]
}

export interface SourceDataAuditResult {
  total_issues?: number
  total_excluded?: number
  total_invalid?: number
  files?: AuditFileEntry[]
}

const ISSUE_LABELS: Record<string, string> = {
  missing_or_invalid: 'missing or invalid',
  non_numeric: 'non-numeric',
  empty_grouped_as_na: 'empty (aggregated as n/a)',
}

function auditFiles(result: SourceDataAuditResult): AuditFileEntry[] {
  return result.files ?? []
}

export function hasAuditContent(result: SourceDataAuditResult | null): boolean {
  if (!result) return false
  const files = auditFiles(result)
  return files.some(
    f =>
      (f.columns?.length ?? 0) > 0 ||
      (f.notes?.length ?? 0) > 0 ||
      Number(f.excluded_rows ?? 0) > 0,
  )
}

interface Props {
  result: SourceDataAuditResult
  summary: string
  blockingCount?: number
  onContinue?: () => void
  onReupload?: () => void
  submitting?: boolean
  disabled?: boolean
}

export default function SourceDataAuditWarning({
  result,
  summary,
  blockingCount = 0,
  onContinue,
  onReupload,
  submitting,
  disabled,
}: Props) {
  const files = auditFiles(result)
  const showActions = blockingCount > 0 && (onContinue || onReupload)

  return (
    <div
      className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-950"
      role="alert"
    >
      <p className="font-medium">{summary}</p>

      {files.length > 0 && (
        <ul className="mt-2 space-y-2 text-amber-900">
          {files.map(file => (
            <li key={`${file.label}-${file.sheet_name ?? ''}`}>
              <p className="font-medium">
                {file.label}
                {file.sheet_name ? ` · ${file.sheet_name}` : ''}
              </p>
              {(file.columns?.length ?? 0) > 0 && (
                <ul className="mt-1 list-disc pl-4 space-y-0.5">
                  {file.columns!.map(col => (
                    <li key={`${col.role}-${col.header}-${col.issue}`}>
                      {col.count.toLocaleString('en-US')} row{col.count === 1 ? '' : 's'} —{' '}
                      <span className="font-medium">{col.header}</span>
                      {col.letter ? ` (${col.letter})` : ''}:{' '}
                      {ISSUE_LABELS[col.issue] ?? col.issue}
                    </li>
                  ))}
                </ul>
              )}
              {(file.notes?.length ?? 0) > 0 && (
                <ul className="mt-1 list-disc pl-4 space-y-0.5 text-amber-800">
                  {file.notes!.map((note, i) => (
                    <li key={i}>{note}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}

      {showActions && (
        <>
          <p className="mt-2 text-amber-900">
            Do you want to continue anyway{onReupload ? ', or correct the file and re-upload' : ''}?
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {onContinue && (
              <button
                type="button"
                disabled={disabled || submitting}
                onClick={onContinue}
                className="rounded-md bg-amber-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-800 disabled:opacity-50"
              >
                Continue anyway
              </button>
            )}
            {onReupload && (
              <button
                type="button"
                disabled={disabled || submitting}
                onClick={onReupload}
                className="rounded-md border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-900 hover:bg-amber-100 disabled:opacity-50"
              >
                Re-upload files
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}
