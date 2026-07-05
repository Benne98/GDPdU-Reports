/**
 * MergedValidationReport — aggregate validation view for format groups with
 * multiple member entities.
 *
 * Rendered by GlGroupConfigPanel when validationStage + onMemberValidated props
 * are present (wizard mode only).  IngestionPage is unaffected.
 *
 * Behaviour:
 *  - Single-entity group: no Entity column; visually identical to today's
 *    per-entity ValidierungStep ValidationReport.
 *  - Multi-entity group: one card per check id; offender tables gain a leading
 *    "Entity" column; S1 gets one S1IssueExplorer per failing member with its
 *    own ValidateContext so exclusions remain per-file.
 */

import { useState } from 'react'
import type { CheckOffender, CheckResult, IssueRowsResponse, ValidationResponse } from '../../lib/gdpduApi'
import { fetchIssueRows } from '../../lib/gdpduApi'
import S1IssueExplorer, { type ValidateContext } from './S1IssueExplorer'
import B1BookingLinesPanel from './B1BookingLinesPanel'

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface MemberValidationData {
  entityCode: string
  entityIndex: number
  result: ValidationResponse
  context: ValidateContext
}

interface MergedValidationReportProps {
  members: MemberValidationData[]
  /** Per-entity excluding flag (true = API call in flight for that entity). */
  excluding: Record<number, boolean>
  /** Called when S1 explorer excludes lines for a specific member entity. */
  onExcludeLines: (entityIndex: number, lineIds: string[]) => Promise<void>
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CHECK_GROUPS: { title: string; subtitle: string; ids: string[] }[] = [
  {
    title: 'Data completeness',
    subtitle: 'Required fields and dates',
    ids: ['S1', 'S2'],
  },
  {
    title: 'Balance checks',
    subtitle: 'Double-entry integrity per booking, entity, and month',
    ids: ['B1', 'B2', 'B3'],
  },
  {
    title: 'Data quality',
    subtitle: 'Uniqueness and chart mapping',
    ids: ['Q2', 'M1'],
  },
  {
    title: 'Reconciliation',
    subtitle: 'Derived totals vs. ledger',
    ids: ['R1', 'R2', 'R3', 'R4'],
  },
]

const CHECK_NAME: Record<string, string> = {
  S1: 'Required fields filled',
  S2: 'Posting year matches booking date',
  B1: 'Each booking balances to zero',
  B2: 'Whole ledger balances per entity',
  B3: 'Monthly movements balance per entity',
  Q2: 'Row numbers are unique',
  M1: 'All accounts mapped in chart',
  R1: 'Trade receivables',
  R2: 'Trade payables',
  R3: 'Gross sales',
  R4: 'Cost of materials',
}

const CHECK_SOFT: Record<string, boolean> = { B1: true, M1: true }

const COLUMN_LABELS: Record<string, string> = {
  entity: 'Entity',
  journal_entry_number: 'Booking no.',
  journal_entry_group_number: 'Booking ID',
  fiscal_year: 'Fiscal year',
  fiscal_period: 'Month',
  line_count: 'Lines in booking',
  sum: 'Imbalance',
  booking_line_id: 'Row no.',
  gl_account_id: 'Account',
  account_number_group: 'Account key',
  amount: 'Amount',
  posting_date: 'Posting date',
  account: 'Account',
}

const PREFERRED_COLS = [
  'entity',
  'journal_entry_number',
  'fiscal_year',
  'line_count',
  'sum',
  'booking_line_id',
  'gl_account_id',
  'account_number_group',
  'amount',
  'posting_date',
  'account',
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(n)
}

function checkName(id: string, check?: CheckResult): string {
  return CHECK_NAME[id] ?? check?.name ?? id
}

function offenderColumns(rows: CheckOffender[], includeEntity: boolean): string[] {
  const keys = new Set<string>()
  for (const row of rows) Object.keys(row).forEach(k => keys.add(k))
  const preferred = includeEntity ? PREFERRED_COLS : PREFERRED_COLS.filter(c => c !== 'entity')
  return preferred.filter(c => keys.has(c))
}

// ---------------------------------------------------------------------------
// Per-check aggregate
// ---------------------------------------------------------------------------

interface MemberCheck {
  member: MemberValidationData
  check: CheckResult
}

function collectMemberChecks(checkId: string, members: MemberValidationData[]): MemberCheck[] {
  return members
    .map(m => ({ member: m, check: m.result.results.find(r => r.id === checkId) }))
    .filter((x): x is { member: MemberValidationData; check: CheckResult } => x.check !== undefined)
}

function aggPassed(memberChecks: MemberCheck[]): boolean {
  return memberChecks.every(mc => mc.check.passed)
}

function aggSeverity(memberChecks: MemberCheck[]): 'HARD' | 'SOFT' {
  if (memberChecks.some(mc => !mc.check.passed && mc.check.severity === 'HARD')) return 'HARD'
  return 'SOFT'
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function PassIcon({ passed }: { passed: boolean }) {
  return passed
    ? <span className="text-emerald-500 text-base leading-none" aria-label="passed">&#x2713;</span>
    : <span className="text-red-500 text-base leading-none" aria-label="failed">&#x2715;</span>
}

function WarnIcon() {
  return <span className="text-amber-500 text-base leading-none" aria-label="warning">&#x26A0;</span>
}

// ---------------------------------------------------------------------------
// Aggregate summary banner
// ---------------------------------------------------------------------------

function SummaryBanner({ members }: { members: MemberValidationData[] }) {
  const allBlockingIds = [...new Set(members.flatMap(m => m.result.summary.blocking))]
  const allWarningIds = [...new Set(
    members.flatMap(m => m.result.summary.warnings).filter(id => !allBlockingIds.includes(id))
  )]
  const allPassed = allBlockingIds.length === 0 && allWarningIds.length === 0

  const bannerClass = allBlockingIds.length > 0
    ? 'border-red-200 bg-red-50'
    : allWarningIds.length > 0
    ? 'border-amber-200 bg-amber-50'
    : 'border-emerald-200 bg-emerald-50'

  const icon = allBlockingIds.length > 0
    ? <PassIcon passed={false} />
    : allWarningIds.length > 0
    ? <WarnIcon />
    : <PassIcon passed />

  return (
    <div className={`rounded-lg border px-4 py-3 ${bannerClass}`}>
      <div className="flex items-start gap-3">
        <div className="pt-0.5 shrink-0 text-xl">{icon}</div>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-slate-900">
            {allPassed
              ? 'All checks passed — all entities are ready to commit'
              : allBlockingIds.length > 0
              ? `${allBlockingIds.length} issue type(s) must be fixed before commit`
              : `${allWarningIds.length} warning type(s) — review before committing`}
          </p>
          {members.length > 1 && (
            <p className="mt-1 text-xs text-slate-600">
              {members.length} entities ·{' '}
              {members.map(m => (
                <span key={m.entityIndex} className={`mr-2 ${m.result.summary.passed ? 'text-emerald-700' : 'text-amber-700'}`}>
                  {m.entityCode}: {m.result.summary.passed ? 'passed' : `${m.result.summary.blocking.length} blocking, ${m.result.summary.warnings.length} warning(s)`}
                </span>
              ))}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Offender table for non-S1 checks
// ---------------------------------------------------------------------------

function MergedOffenderTable({
  rows,
  includeEntity,
}: {
  rows: CheckOffender[]
  includeEntity: boolean
}) {
  if (rows.length === 0) return null
  const cols = offenderColumns(rows, includeEntity)
  if (cols.length === 0) return null

  return (
    <div className="overflow-x-auto rounded border border-slate-200 bg-white mt-2">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-slate-50 border-b border-slate-200 text-left text-slate-500">
            {cols.map(c => (
              <th key={c} className="py-1.5 px-2 font-semibold whitespace-nowrap">
                {COLUMN_LABELS[c] ?? c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 20).map((row, i) => (
            <tr key={i} className="border-b border-slate-100 last:border-0">
              {cols.map(c => (
                <td key={c} className="py-1.5 px-2 font-mono text-slate-700 whitespace-nowrap">
                  {row[c] == null
                    ? '—'
                    : typeof row[c] === 'number'
                    ? fmt(row[c] as number)
                    : String(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 20 && (
        <p className="px-3 py-2 text-xs text-slate-500">
          Showing 20 of {rows.length.toLocaleString('en-US')} offenders.
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// B1 per-offender drill-down row (merged view)
// ---------------------------------------------------------------------------

/** Mirrors describeB1Example from ValidationReport.tsx for consistent wording. */
function describeB1Offender(o: CheckOffender): string {
  const no =
    o.journal_entry_number != null
      ? String(o.journal_entry_number).replace(/^0+/, '') || String(o.journal_entry_number)
      : o.journal_entry_group_number ?? '—'
  const year = o.fiscal_year != null ? `, fiscal year ${o.fiscal_year}` : ''
  const lines =
    o.line_count != null
      ? o.line_count === 1
        ? 'only 1 line'
        : `${o.line_count} lines`
      : 'multiple lines'
  const imbalance = o.sum != null ? `, imbalance ${fmt(o.sum)}` : ''
  return `Booking ${no}${year}: ${lines}${imbalance}.`
}

/**
 * One expandable offender row for B1 in the merged view.
 * Manages its own fetch state so each booking can be independently drilled.
 * Uses B1BookingLinesPanel for the table + "Amount sum (should be 0)" tfoot.
 */
function B1OffenderRow({
  offender,
  context,
  entityCode,
  isMultiEntity,
}: {
  offender: CheckOffender
  context: ValidateContext
  entityCode: string
  isMultiEntity: boolean
}) {
  const [showLines, setShowLines] = useState(false)
  const [lines, setLines] = useState<IssueRowsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canDrill =
    typeof offender.journal_entry_group_number === 'string' &&
    offender.journal_entry_group_number.length > 0

  async function loadLines() {
    if (!canDrill) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetchIssueRows({
        check_id: 'B1',
        file_id: context.file_id,
        sheet: context.sheet,
        profile: context.profile,
        exclude_line_ids: context.exclude_line_ids,
        journal_entry_group_number: offender.journal_entry_group_number,
        ...(typeof offender.fiscal_year === 'number' ? { fiscal_year: offender.fiscal_year } : {}),
      })
      setLines(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load booking lines')
    } finally {
      setLoading(false)
    }
  }

  function toggle() {
    if (!showLines && !lines && !loading) {
      void loadLines()
    }
    setShowLines(v => !v)
  }

  return (
    <div className="rounded-md border border-slate-200 bg-white overflow-hidden">
      <div className="px-3 py-2 text-sm text-slate-700">
        {isMultiEntity && (
          <span className="inline-block text-xs font-semibold text-slate-500 mr-2">
            {entityCode}
          </span>
        )}
        <span>{describeB1Offender(offender)}</span>
        {canDrill && (
          <button
            type="button"
            disabled={loading}
            onClick={toggle}
            className="ml-3 text-xs font-medium text-blue-600 hover:underline disabled:opacity-50"
          >
            {loading ? 'Loading…' : showLines ? 'Hide booking lines' : 'View booking lines'}
          </button>
        )}
      </div>
      {showLines && (
        <B1BookingLinesPanel
          offender={offender}
          data={lines}
          error={error}
          loading={loading}
          onRetry={() => void loadLines()}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Single merged check card
// ---------------------------------------------------------------------------

function MergedCheckCard({
  checkId,
  memberChecks,
  isMultiEntity,
  excluding,
  onExcludeLines,
}: {
  checkId: string
  memberChecks: MemberCheck[]
  isMultiEntity: boolean
  excluding: Record<number, boolean>
  onExcludeLines: (entityIndex: number, lineIds: string[]) => Promise<void>
}) {
  const passed = aggPassed(memberChecks)
  const severity = aggSeverity(memberChecks)
  const isSoft = CHECK_SOFT[checkId] ?? false
  const isWarn = !passed && (severity === 'SOFT' || isSoft)
  const displayCheck = memberChecks.find(mc => !mc.check.passed)?.check ?? memberChecks[0].check

  let cardClass = 'border-slate-200 bg-white'
  if (!passed) {
    if (isWarn) cardClass = 'border-amber-200 bg-amber-50/50'
    else cardClass = 'border-red-200 bg-red-50/60'
  }

  const failingMembers = memberChecks.filter(mc => !mc.check.passed)

  // Merge offenders across members, tagging with entityCode for multi-entity
  const mergedOffenders: CheckOffender[] = memberChecks.flatMap(mc =>
    (mc.check.offenders ?? []).map(o =>
      isMultiEntity ? { ...o, entity: mc.member.entityCode } : o
    )
  )
  const totalOffenderCount = memberChecks.reduce(
    (sum, mc) => sum + (mc.check.offender_count ?? mc.check.offenders?.length ?? 0),
    0
  )

  const detail = displayCheck.detail === 'ok' ? 'All good.' : displayCheck.detail

  return (
    <div className={`rounded-lg border px-4 py-3 ${cardClass}`}>
      <div className="flex items-start gap-3">
        <div className="pt-0.5">
          {passed ? <PassIcon passed /> : isWarn ? <WarnIcon /> : <PassIcon passed={false} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-slate-400">{checkId}</span>
            <h5 className="font-semibold text-slate-900">{checkName(checkId, displayCheck)}</h5>
            {passed ? (
              <span className="inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800">
                Passed
              </span>
            ) : isWarn ? (
              <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                Warning
              </span>
            ) : (
              <span className="inline-flex rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-800">
                Must fix first
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-slate-700">{detail}</p>
          {!passed && totalOffenderCount > 0 && (
            <p className="mt-0.5 text-xs text-slate-500">
              {totalOffenderCount.toLocaleString('en-US')} issue(s) across{' '}
              {failingMembers.length === 1 ? '1 entity' : `${failingMembers.length} entities`}
            </p>
          )}

          {/* S1: one explorer per failing member (per-entity context = per-file exclusions) */}
          {checkId === 'S1' && !passed && (
            <div className="mt-3 space-y-4">
              {failingMembers.map(mc => (
                <div key={mc.member.entityIndex}>
                  {isMultiEntity && (
                    <p className="text-xs font-semibold text-slate-700 mb-1">
                      {mc.member.entityCode}
                    </p>
                  )}
                  <S1IssueExplorer
                    check={mc.check}
                    context={mc.member.context}
                    excluding={excluding[mc.member.entityIndex] ?? false}
                    onExcludeLines={(ids) => onExcludeLines(mc.member.entityIndex, ids)}
                  />
                </div>
              ))}
            </div>
          )}

          {/* B1: per-offender drill-down with per-member context */}
          {checkId === 'B1' && !passed && (
            <div className="mt-3 space-y-2">
              {failingMembers.flatMap((mc, mci) =>
                (mc.check.offenders ?? []).map((o, oi) => (
                  <B1OffenderRow
                    key={`${mci}-${oi}`}
                    offender={o}
                    context={mc.member.context}
                    entityCode={mc.member.entityCode}
                    isMultiEntity={isMultiEntity}
                  />
                ))
              )}
            </div>
          )}

          {/* Non-S1, non-B1: merged offender table with optional Entity column */}
          {checkId !== 'S1' && checkId !== 'B1' && !passed && mergedOffenders.length > 0 && (
            <MergedOffenderTable rows={mergedOffenders} includeEntity={isMultiEntity} />
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main export
// ---------------------------------------------------------------------------

export default function MergedValidationReport({
  members,
  excluding,
  onExcludeLines,
}: MergedValidationReportProps) {
  const isMultiEntity = members.length > 1

  // Collect all check ids present across all members
  const allCheckIds = new Set(members.flatMap(m => m.result.results.map(r => r.id)))

  return (
    <div className="space-y-6">
      <SummaryBanner members={members} />

      <div className="space-y-8">
        {CHECK_GROUPS.map(group => {
          // Determine which check ids in this group have any results
          const activeIds = group.ids.filter(id => allCheckIds.has(id))
          if (activeIds.length === 0) return null

          const groupHasFailed = activeIds.some(id => {
            const mc = collectMemberChecks(id, members)
            return mc.length > 0 && !aggPassed(mc)
          })

          return (
            <section key={group.title}>
              <div className="mb-3">
                <h4 className="text-base font-semibold text-slate-900">{group.title}</h4>
                <p className="text-sm text-slate-500">
                  {group.subtitle}
                  {groupHasFailed ? ' · issues found' : ' · all passed'}
                </p>
              </div>
              <div className="space-y-3">
                {activeIds.map(id => {
                  const memberChecks = collectMemberChecks(id, members)
                  if (memberChecks.length === 0) return null
                  return (
                    <MergedCheckCard
                      key={id}
                      checkId={id}
                      memberChecks={memberChecks}
                      isMultiEntity={isMultiEntity}
                      excluding={excluding}
                      onExcludeLines={onExcludeLines}
                    />
                  )
                })}
              </div>
            </section>
          )
        })}
      </div>
    </div>
  )
}
