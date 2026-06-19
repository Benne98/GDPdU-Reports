/**
 * ValidationReport — human-friendly check results from POST /api/v1/ingest/validate.
 */

import { useEffect, useState } from "react";
import type { CheckOffender, CheckResult, ValidationResponse } from "../../lib/gdpduApi";
import S1IssueExplorer, { type ValidateContext } from "./S1IssueExplorer";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ValidationReportProps {
  report: ValidationResponse;
  onCommit: (confirmSoft: boolean) => void;
  committing: boolean;
  excluding?: boolean;
  onExcludeLines?: (lineIds: number[]) => void | Promise<void>;
  onClearExclusions?: () => void | Promise<void>;
  validateContext?: ValidateContext;
}

// ---------------------------------------------------------------------------
// Check catalogue (display order + grouping)
// ---------------------------------------------------------------------------

const CHECK_ORDER = ["S1", "S2", "B1", "B2", "B3", "Q2", "M1", "R1", "R2", "R3", "R4"] as const;

const CHECK_GROUPS: { title: string; subtitle: string; ids: string[] }[] = [
  {
    title: "Data completeness",
    subtitle: "Required fields and dates",
    ids: ["S1", "S2"],
  },
  {
    title: "Balance checks",
    subtitle: "Double-entry integrity per booking, entity, and month",
    ids: ["B1", "B2", "B3"],
  },
  {
    title: "Data quality",
    subtitle: "Uniqueness and chart mapping",
    ids: ["Q2", "M1"],
  },
  {
    title: "Reconciliation",
    subtitle: "Derived totals vs. ledger",
    ids: ["R1", "R2", "R3", "R4"],
  },
];

const COLUMN_LABELS: Record<string, string> = {
  field: "Field",
  field_label: "Field",
  issue: "Issue",
  journal_entry_number: "Booking no.",
  journal_entry_group_number: "Booking ID",
  fiscal_year: "Fiscal year",
  fiscal_period: "Month",
  line_count: "Lines in booking",
  sum: "Imbalance",
  booking_line_id: "Row no.",
  gl_account_id: "Account",
  account_number_group: "Account key",
  amount: "Amount",
  posting_date: "Posting date",
  posting_year: "Year from date",
  entity: "Entity",
  account: "Account",
};

const EXAMPLE_BATCH = 5;

/** Friendly labels for canonical / legacy field ids */
const FIELD_FRIENDLY: Record<string, string> = {
  account_number_group: "Group account",
  amount: "Amount",
  journal_entry_group_number: "Booking ID",
  fiscal_year: "Fiscal year",
  line_number: "Line number",
  booking_line_id: "Row number",
  posting_date: "Posting date",
};

/** Display names when the API still returns legacy labels */
const CHECK_DISPLAY: Record<string, { name: string; severity?: "HARD" | "SOFT" }> = {
  S1: { name: "Required fields filled" },
  S2: { name: "Posting year matches booking date" },
  B1: { name: "Each booking balances to zero", severity: "SOFT" },
  B2: { name: "Whole ledger balances per entity" },
  B3: { name: "Monthly movements balance per entity" },
  Q2: { name: "Row numbers are unique" },
  M1: { name: "All accounts mapped in chart", severity: "SOFT" },
  R1: { name: "Trade receivables" },
  R2: { name: "Trade payables" },
  R3: { name: "Gross sales" },
  R4: { name: "Cost of materials" },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type WarningAction = "acknowledged" | "ignored";

function fmt(n: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(n);
}

function parseLegacyMissingFields(detail: string): string[] | null {
  const m = detail.match(/missing\/empty:\s*\[([^\]]*)\]/i);
  if (!m) return null;
  return m[1]
    .split(",")
    .map((s) => s.trim().replace(/['"]/g, ""))
    .filter(Boolean);
}

function inferS1Fields(check: CheckResult): string[] {
  const fromDetail = parseLegacyMissingFields(check.detail);
  if (fromDetail?.length) return fromDetail;
  const offenders = normalizeOffenders(check.offenders);
  const fromOffenders = offenders
    .map((o) => o.field)
    .filter((f): f is string => Boolean(f));
  return [...new Set(fromOffenders)];
}

function humanizeDetail(check: CheckResult): { main: string; hint?: string } {
  if (check.passed) {
    if (check.detail === "ok") return { main: "All good." };
    return { main: check.detail };
  }

  if (check.id === "S1") {
    const fields = inferS1Fields(check);
    const friendly = fields.map((f) => FIELD_FRIENDLY[f] ?? f);
    if (friendly.length > 0) {
      const joined =
        friendly.length === 1
          ? friendly[0]
          : friendly.length === 2
          ? `${friendly[0]} and ${friendly[1]}`
          : `${friendly.slice(0, -1).join(", ")}, and ${friendly[friendly.length - 1]}`;
      return {
        main: "Some rows are missing required values.",
        hint: `The mapped source columns for ${joined} do not produce valid values in every row.`,
      };
    }
  }

  if (check.id === "B1" && /out-of-balance booking/i.test(check.detail)) {
    const count = check.offender_count ?? check.detail.match(/([\d,]+)/)?.[1];
    return {
      main: count
        ? `${Number(String(count).replace(/,/g, "")).toLocaleString("en-US")} booking(s) do not balance to zero.`
        : "Some bookings do not balance to zero.",
      hint: "For GoBD exports this is often acceptable — you can acknowledge the warning when importing.",
    };
  }

  if (check.detail === "ok") return { main: "All good." };
  return { main: check.detail };
}

function normalizeCheck(raw: CheckResult): CheckResult {
  const meta = CHECK_DISPLAY[raw.id];
  const severity = meta?.severity ?? raw.severity;
  return {
    ...raw,
    name: meta?.name ?? raw.name,
    severity,
  };
}

function buildEffectiveSummary(results: CheckResult[]) {
  const blocking = results.filter((r) => !r.passed && r.severity === "HARD").map((r) => r.id);
  const warnings = results.filter((r) => !r.passed && r.severity === "SOFT").map((r) => r.id);
  return {
    passed: blocking.length === 0,
    blocking,
    warnings,
  };
}

function normalizeOffenders(raw: CheckResult["offenders"]): CheckOffender[] {
  if (!raw?.length) return [];
  return raw.map((o) => {
    if (typeof o === "string") {
      return { field: o, issue: "missing" };
    }
    if (Array.isArray(o)) {
      return { field: String(o[0] ?? ""), issue: "legacy" };
    }
    return o as CheckOffender;
  });
}

function checkLabel(check: CheckResult): string {
  return check.name || check.id;
}

function describeS1Example(o: CheckOffender): string {
  if (o.issue === "column_missing") {
    return `The column “${o.field_label ?? o.field}” is missing from the transformed data.`;
  }
  const row = o.booking_line_id != null ? `Row ${o.booking_line_id}` : "A row";
  const booking = o.journal_entry_group_number
    ? `, booking ${String(o.journal_entry_number ?? o.journal_entry_group_number).replace(/^0+/, "") || o.journal_entry_group_number}`
    : "";
  if (o.field === "account_number_group") {
    const acct = o.gl_account_id ? ` (account ${o.gl_account_id})` : "";
    return `${row}${booking}: account number is empty in the source file${acct} — the system cannot build an account key (entity prefix + account).`;
  }
  if (o.field === "amount") {
    return `${row}${booking}: amount is missing.`;
  }
  const label = o.field_label ?? o.field ?? "field";
  return `${row}${booking}: “${label}” is empty.`;
}

function describeB1Example(o: CheckOffender): string {
  const no =
    o.journal_entry_number != null
      ? String(o.journal_entry_number).replace(/^0+/, "") || o.journal_entry_number
      : o.journal_entry_group_number ?? "—";
  const year = o.fiscal_year != null ? `, fiscal year ${o.fiscal_year}` : "";
  const lines =
    o.line_count != null
      ? o.line_count === 1
        ? "only 1 line"
        : `${o.line_count} lines`
      : "multiple lines";
  const imbalance = o.sum != null ? `, imbalance ${fmt(o.sum)}` : "";
  return `Booking ${no}${year}: ${lines}${imbalance}.`;
}

function describeGenericExample(o: CheckOffender): string {
  if (typeof o.account === "string") return `Account ${o.account} is not in the chart.`;
  const parts: string[] = [];
  for (const [key, label] of Object.entries(COLUMN_LABELS)) {
    if (o[key] != null && o[key] !== "") {
      const val = o[key];
      parts.push(`${label}: ${typeof val === "number" ? fmt(val) : String(val)}`);
    }
  }
  return parts.length > 0 ? parts.join(" · ") : "See details in the table below.";
}

function describeExample(check: CheckResult, o: CheckOffender): string {
  if (check.id === "S1") return describeS1Example(o);
  if (check.id === "B1") return describeB1Example(o);
  return describeGenericExample(o);
}

function offenderTableColumns(rows: CheckOffender[]): string[] {
  const preferred = [
    "journal_entry_number",
    "fiscal_year",
    "line_count",
    "sum",
    "booking_line_id",
    "gl_account_id",
    "account_number_group",
    "amount",
    "posting_date",
    "entity",
    "account",
  ];
  const keys = new Set<string>();
  for (const row of rows) Object.keys(row).forEach((k) => keys.add(k));
  return preferred.filter((k) => keys.has(k));
}

// ---------------------------------------------------------------------------
// Small presentational helpers
// ---------------------------------------------------------------------------

function PassIcon({ passed, size = "base" }: { passed: boolean; size?: "base" | "lg" }) {
  const cls = size === "lg" ? "text-xl leading-none" : "text-base leading-none";
  return passed ? (
    <span className={`text-emerald-500 ${cls}`} aria-label="passed">
      &#x2713;
    </span>
  ) : (
    <span className={`text-red-500 ${cls}`} aria-label="failed">
      &#x2715;
    </span>
  );
}

function WarnIcon({ size = "base" }: { size?: "base" | "lg" }) {
  const cls = size === "lg" ? "text-xl leading-none" : "text-base leading-none";
  return (
    <span className={`text-amber-500 ${cls}`} aria-label="warning">
      &#x26A0;
    </span>
  );
}

function StatusPill({
  check,
  warningAction,
}: {
  check: CheckResult;
  warningAction?: WarningAction | null;
}) {
  const isWarning = !check.passed && check.severity === "SOFT";
  if (check.passed) {
    return (
      <span className="inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800">
        Passed
      </span>
    );
  }
  if (isWarning && warningAction === "acknowledged") {
    return (
      <span className="inline-flex rounded-full bg-slate-200 px-2 py-0.5 text-[11px] font-medium text-slate-700">
        Acknowledged
      </span>
    );
  }
  if (isWarning && warningAction === "ignored") {
    return (
      <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">
        Ignored
      </span>
    );
  }
  if (isWarning) {
    return (
      <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-800">
        Warning
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-800">
      Must fix first
    </span>
  );
}

// ---------------------------------------------------------------------------
// Progressive examples
// ---------------------------------------------------------------------------

type ExampleView = "hidden" | 1 | "more" | "all";

function SourceRowTable({ row, lineNumber }: { row: Record<string, unknown>; lineNumber?: number }) {
  const entries = Object.entries(row);
  if (entries.length === 0) return null;

  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 overflow-hidden">
      <div className="px-3 py-1.5 border-b border-slate-200 bg-white text-xs font-medium text-slate-600">
        Source file{lineNumber != null ? ` · line ${lineNumber.toLocaleString("en-US")}` : ""}
        <span className="font-normal text-slate-400 ml-1">(all columns as uploaded)</span>
      </div>
      <div className="max-h-64 overflow-auto">
        <table className="w-full text-xs">
          <tbody>
            {entries.map(([col, val]) => (
              <tr key={col} className="border-b border-slate-100 last:border-0">
                <td className="py-1.5 px-3 font-medium text-slate-600 align-top whitespace-nowrap w-40">
                  {col}
                </td>
                <td className="py-1.5 px-3 font-mono text-slate-800 break-all">
                  {val === null || val === undefined || val === "" ? (
                    <span className="text-slate-400 italic">empty</span>
                  ) : (
                    String(val)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ExampleCard({
  check,
  offender,
  onExcludeLine,
  excluding,
}: {
  check: CheckResult;
  offender: CheckOffender;
  onExcludeLine?: (lineId: number) => void;
  excluding?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const sourceRow = offender.source_row;
  const hasSource = sourceRow != null && Object.keys(sourceRow).length > 0;
  const lineId =
    typeof offender.booking_line_id === "number" ? offender.booking_line_id : undefined;

  return (
    <li className="rounded-md border border-slate-200 bg-white overflow-hidden">
      <div className="px-3 py-2 text-sm text-slate-700">
        <p>{describeExample(check, offender)}</p>
        <div className="mt-1.5 flex flex-wrap gap-3">
          {hasSource && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="text-xs font-medium text-blue-600 hover:underline"
            >
              {expanded ? "Hide source columns" : "Show all source columns"}
            </button>
          )}
          {lineId != null && onExcludeLine && (
            <button
              type="button"
              disabled={excluding}
              onClick={() => onExcludeLine(lineId)}
              className="text-xs font-medium text-slate-600 hover:text-red-700 hover:underline disabled:opacity-50"
            >
              Exclude from import
            </button>
          )}
        </div>
      </div>
      {expanded && hasSource && (
        <div className="px-3 pb-3">
          <SourceRowTable
            row={sourceRow}
            lineNumber={
              typeof offender.source_line_number === "number"
                ? offender.source_line_number
                : typeof offender.booking_line_id === "number"
                ? offender.booking_line_id
                : undefined
            }
          />
        </div>
      )}
    </li>
  );
}

function ProgressiveExamples({
  check,
  onExcludeLine,
  excluding,
}: {
  check: CheckResult;
  onExcludeLine?: (lineId: number) => void;
  excluding?: boolean;
}) {
  const offenders = normalizeOffenders(check.offenders);
  const total = check.offender_count ?? offenders.length;
  const [view, setView] = useState<ExampleView>(offenders.length > 0 && !check.passed ? 1 : "hidden");

  if (offenders.length === 0 || view === "hidden") return null;

  const visibleCount =
    view === 1 ? 1 : view === "more" ? Math.min(EXAMPLE_BATCH, offenders.length) : offenders.length;
  const visible = offenders.slice(0, visibleCount);
  const canShowMore = visibleCount < offenders.length;
  const canShowAll = offenders.length > 1 && visibleCount < offenders.length;

  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs text-slate-500">
        {total > visibleCount
          ? `Showing ${visibleCount} of ${total.toLocaleString("en-US")} example(s)`
          : `${visibleCount} example(s)`}
      </p>

      <ul className="space-y-2">
        {visible.map((o, i) => (
          <ExampleCard
            key={`${o.booking_line_id ?? i}-${o.field ?? ""}`}
            check={check}
            offender={o}
            onExcludeLine={onExcludeLine}
            excluding={excluding}
          />
        ))}
      </ul>

      {check.id === "B1" && visible[0]?.line_count === 1 && (
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-md px-3 py-2">
          Most failing bookings have only one line. In the column mapping step, map{" "}
          <strong>Journal Entry Number</strong> to <strong>Transaction number</strong> (not document
          number or row id). For GoBD exports you may also proceed without fixing B1.
        </p>
      )}

      {check.id === "S1" && offenders.some((o) => o.field === "account_number_group") && (
        <p className="text-xs text-slate-600 bg-slate-50 border border-slate-100 rounded-md px-3 py-2">
          Account key = 2-digit entity prefix + 6-digit account number (e.g. entity 01 + account
          41100 → 01041100).
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        {view === 1 && canShowMore && (
          <button
            type="button"
            onClick={() => setView("more")}
            className="text-xs text-blue-600 hover:underline"
          >
            Show {Math.min(EXAMPLE_BATCH, offenders.length) - 1} more example(s)
          </button>
        )}
        {view !== "all" && canShowAll && offenders.length > EXAMPLE_BATCH && (
          <button
            type="button"
            onClick={() => setView("all")}
            className="text-xs text-blue-600 hover:underline"
          >
            Show all {offenders.length} loaded examples
          </button>
        )}
        {(view === "more" || view === "all") && (
          <button
            type="button"
            onClick={() => setView(1)}
            className="text-xs text-slate-500 hover:underline"
          >
            Collapse to one example
          </button>
        )}
        <button
          type="button"
          onClick={() => setView("hidden")}
          className="text-xs text-slate-500 hover:underline"
        >
          Hide examples
        </button>
      </div>

      {view === "all" && offenderTableColumns(visible).length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-200 bg-white">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200 text-left text-slate-500">
                {offenderTableColumns(visible).map((c) => (
                  <th key={c} className="py-1.5 px-2 font-semibold whitespace-nowrap">
                    {COLUMN_LABELS[c] ?? c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visible.map((row, i) => (
                <tr key={i} className="border-b border-slate-100 last:border-0">
                  {offenderTableColumns(visible).map((c) => (
                    <td key={c} className="py-1.5 px-2 font-mono text-slate-700 whitespace-nowrap">
                      {row[c] == null ? "—" : typeof row[c] === "number" ? fmt(row[c] as number) : String(row[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Check card
// ---------------------------------------------------------------------------

function CheckCard({
  check,
  warningAction,
  onExcludeLines,
  excluding,
  validateContext,
}: {
  check: CheckResult;
  warningAction: WarningAction | null;
  onExcludeLines?: (lineIds: number[]) => void | Promise<void>;
  excluding?: boolean;
  validateContext?: ValidateContext;
}) {
  const showDiff =
    check.diff !== undefined && check.diff !== null && !check.passed && check.id.startsWith("R");
  const { main, hint } = humanizeDetail(check);
  const isWarning = !check.passed && check.severity === "SOFT";
  const warningReviewed = isWarning && warningAction !== null;

  let cardClass = "border-slate-200 bg-white";
  if (check.passed) {
    cardClass = "border-slate-200 bg-white";
  } else if (isWarning && warningAction === "acknowledged") {
    cardClass = "border-slate-300 bg-slate-50";
  } else if (isWarning && warningAction === "ignored") {
    cardClass = "border-slate-200 bg-slate-50/80";
  } else if (isWarning) {
    cardClass = "border-amber-200 bg-amber-50/50";
  } else {
    cardClass = "border-red-200 bg-red-50/60";
  }

  return (
    <div className={`rounded-lg border px-4 py-3 ${cardClass}`}>
      <div className="flex items-start gap-3">
        <div className="pt-0.5">
          {isWarning && warningAction === "acknowledged" ? (
            <span className="text-slate-500 text-base leading-none" aria-label="acknowledged">
              &#x2713;
            </span>
          ) : isWarning && warningAction === "ignored" ? (
            <span className="text-slate-400 text-base leading-none" aria-label="ignored">
              &#x2013;
            </span>
          ) : isWarning ? (
            <WarnIcon />
          ) : (
            <PassIcon passed={check.passed} />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-slate-400">{check.id}</span>
            <h5 className="font-semibold text-slate-900">{checkLabel(check)}</h5>
            <StatusPill check={check} warningAction={warningAction} />
          </div>
          <p className="mt-1 text-sm text-slate-700">{main}</p>
          {hint && !warningReviewed && (
            <p className="mt-1 text-sm italic text-slate-600">{hint}</p>
          )}
          {isWarning && warningAction === "acknowledged" && (
            <p className="mt-1 text-xs text-slate-500">Acknowledged — you can still import.</p>
          )}
          {isWarning && warningAction === "ignored" && (
            <p className="mt-1 text-xs text-slate-400">Ignored for this import.</p>
          )}
          {showDiff && (
            <p className="mt-1 text-xs text-slate-600">
              Difference: <span className="font-mono font-semibold">{fmt(check.diff!)}</span>
            </p>
          )}
          {check.id === "S1" && !check.passed && validateContext ? (
            <S1IssueExplorer
              check={check}
              context={validateContext}
              excluding={excluding}
              onExcludeLines={onExcludeLines}
            />
          ) : (
            <ProgressiveExamples
              check={check}
              excluding={excluding}
              onExcludeLine={
                onExcludeLines
                  ? (lineId) => {
                      void onExcludeLines([lineId]);
                    }
                  : undefined
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Key preview
// ---------------------------------------------------------------------------

function KeyPreview({ rows }: { rows: Record<string, unknown>[] }) {
  if (rows.length === 0) return null;
  const cols = Object.keys(rows[0]);

  return (
    <div>
      <h4 className="text-sm font-semibold text-slate-700 mb-2">Preview of transformed rows</h4>
      <div className="overflow-x-auto rounded border border-slate-200">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200">
              {cols.map((c) => (
                <th key={c} className="py-1.5 px-2 text-left font-semibold text-slate-600 whitespace-nowrap">
                  {COLUMN_LABELS[c] ?? c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-slate-100 last:border-0">
                {cols.map((c) => (
                  <td key={c} className="py-1.5 px-2 font-mono text-slate-700 whitespace-nowrap">
                    {String(row[c] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Warning acknowledgment — inline in summary banner (no modal)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Main ValidationReport
// ---------------------------------------------------------------------------

export default function ValidationReport({
  report,
  onCommit,
  committing,
  excluding = false,
  onExcludeLines,
  onClearExclusions,
  validateContext,
}: ValidationReportProps) {
  const [warningAction, setWarningAction] = useState<WarningAction | null>(null);

  const exclusions = report.exclusions ?? {
    active_count: 0,
    active_line_ids: [],
    suggested: null,
  };

  const normalizedResults = report.results.map(normalizeCheck);
  const summary = buildEffectiveSummary(normalizedResults);
  const { key_preview, unmapped_accounts } = report;
  const hasBlocking = summary.blocking.length > 0;
  const hasSoftWarnings = summary.warnings.length > 0;

  useEffect(() => {
    setWarningAction(null);
  }, [report]);

  const byId = Object.fromEntries(normalizedResults.map((r) => [r.id, r]));

  const warningDetails = summary.warnings
    .map((id) => byId[id])
    .filter(Boolean)
    .map((r) => {
      const { main, hint } = humanizeDetail(r);
      return { id: r.id, title: checkLabel(r), detail: hint ? `${main} ${hint}` : main };
    });

  const blockingDetails = summary.blocking
    .map((id) => byId[id])
    .filter(Boolean)
    .map((r) => checkLabel(r));

  const warningsReviewed = hasSoftWarnings && warningAction !== null;
  const canImport = !hasBlocking && (!hasSoftWarnings || warningsReviewed);

  function handleCommitClick() {
    if (hasSoftWarnings && warningsReviewed) {
      onCommit(true);
    } else {
      onCommit(false);
    }
  }

  const summaryIcon = hasBlocking ? (
    <PassIcon passed={false} size="lg" />
  ) : hasSoftWarnings && !warningsReviewed ? (
    <WarnIcon size="lg" />
  ) : hasSoftWarnings && warningAction === "acknowledged" ? (
    <span className="text-xl leading-none text-slate-500" aria-label="acknowledged">&#x2713;</span>
  ) : hasSoftWarnings && warningAction === "ignored" ? (
    <span className="text-xl leading-none text-slate-400" aria-label="ignored">&#x2013;</span>
  ) : (
    <PassIcon passed size="lg" />
  );

  let bannerClass = "border-emerald-200 bg-emerald-50";
  if (hasBlocking) {
    bannerClass = "border-red-200 bg-red-50";
  } else if (hasSoftWarnings && warningAction === "acknowledged") {
    bannerClass = "border-slate-300 bg-slate-50";
  } else if (hasSoftWarnings && warningAction === "ignored") {
    bannerClass = "border-slate-200 bg-slate-50/90";
  } else if (hasSoftWarnings) {
    bannerClass = "border-amber-200 bg-amber-50";
  }

  return (
    <div className="space-y-6">
      {/* Summary banner */}
      <div className={`rounded-lg border px-4 py-3 ${bannerClass}`}>
        <div className="flex items-start gap-3">
          <div className="pt-0.5 shrink-0">{summaryIcon}</div>
          <div className="min-w-0 flex-1">
            <p className="font-semibold text-slate-900">
              {hasBlocking
                ? `${summary.blocking.length} issue(s) must be fixed before import`
                : hasSoftWarnings && warningAction === "acknowledged"
                ? "Ready to import — warnings acknowledged"
                : hasSoftWarnings && warningAction === "ignored"
                ? "Ready to import — warnings ignored"
                : hasSoftWarnings
                ? `${summary.warnings.length} warning(s) — review and acknowledge or ignore`
                : "All checks passed — ready to import"}
            </p>
            {hasBlocking && (
              <ul className="mt-2 space-y-1">
                {blockingDetails.map((title, i) => (
                  <li key={i} className="flex items-center gap-2 text-sm text-red-700">
                    <PassIcon passed={false} />
                    <span>{title}</span>
                  </li>
                ))}
              </ul>
            )}
            {hasSoftWarnings && (
              <ul className={`space-y-1 ${hasBlocking ? "mt-2" : "mt-2"}`}>
                {warningDetails.map((w) => (
                  <li
                    key={w.id}
                    className={`flex items-center gap-2 text-sm ${
                      warningAction === "acknowledged"
                        ? "text-slate-600"
                        : warningAction === "ignored"
                        ? "text-slate-500"
                        : "text-amber-800"
                    }`}
                  >
                    {warningAction === "acknowledged" ? (
                      <span className="text-slate-500 text-base leading-none">&#x2713;</span>
                    ) : warningAction === "ignored" ? (
                      <span className="text-slate-400 text-base leading-none">&#x2013;</span>
                    ) : (
                      <WarnIcon />
                    )}
                    <span>{w.title}</span>
                  </li>
                ))}
              </ul>
            )}
            {hasSoftWarnings && !hasBlocking && !warningsReviewed && (
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setWarningAction("acknowledged")}
                  className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
                >
                  Acknowledge
                </button>
                <button
                  type="button"
                  onClick={() => setWarningAction("ignored")}
                  className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  Ignore
                </button>
              </div>
            )}
            {hasSoftWarnings && !hasBlocking && warningsReviewed && (
              <button
                type="button"
                onClick={() => setWarningAction(null)}
                className="mt-2 text-xs text-slate-500 hover:text-slate-700 hover:underline"
              >
                Reset warning review
              </button>
            )}
          </div>
        </div>
      </div>

      {exclusions.active_count > 0 && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-slate-700">
            <span className="font-medium">{exclusions.active_count.toLocaleString("en-US")} row(s)</span>{" "}
            excluded from this import (not written to the database).
          </p>
          {onClearExclusions && (
            <button
              type="button"
              disabled={excluding}
              onClick={() => void onClearExclusions()}
              className="text-sm text-blue-600 hover:underline disabled:opacity-50"
            >
              Restore all excluded rows
            </button>
          )}
        </div>
      )}

      {/* Grouped checks */}
      <div className="space-y-8">
        {CHECK_GROUPS.map((group) => {
          const groupChecks = group.ids
            .map((id) => byId[id])
            .filter((c): c is CheckResult => Boolean(c));
          if (groupChecks.length === 0) return null;

          const failed = groupChecks.filter((c) => !c.passed).length;

          return (
            <section key={group.title}>
              <div className="mb-3">
                <h4 className="text-base font-semibold text-slate-900">{group.title}</h4>
                <p className="text-sm text-slate-500">
                  {group.subtitle}
                  {failed > 0 ? ` · ${failed} open` : " · all passed"}
                </p>
              </div>
              <div className="space-y-3">
                {groupChecks.map((c) => (
                  <CheckCard
                    key={c.id}
                    check={c}
                    warningAction={warningAction}
                    excluding={excluding}
                    onExcludeLines={onExcludeLines}
                    validateContext={validateContext}
                  />
                ))}
              </div>
            </section>
          );
        })}

        {/* Any checks not in groups (fallback) */}
        {normalizedResults
          .filter((r) => !CHECK_ORDER.includes(r.id as (typeof CHECK_ORDER)[number]))
          .map((c) => (
            <CheckCard
              key={c.id}
              check={c}
              warningAction={warningAction}
              excluding={excluding}
              onExcludeLines={onExcludeLines}
              validateContext={validateContext}
            />
          ))}
      </div>

      {/* Unmapped accounts */}
      {unmapped_accounts.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h4 className="text-sm font-semibold text-amber-900 mb-2">
            Accounts not in chart ({unmapped_accounts.length})
          </h4>
          <p className="text-xs text-amber-800 mb-3">
            These accounts from the file are not in the chart of accounts yet. Placeholder entries
            will be created — map them manually afterwards.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {unmapped_accounts.slice(0, 20).map((acc) => (
              <span
                key={acc}
                className="rounded bg-amber-100 border border-amber-200 px-2 py-0.5 text-xs font-mono text-amber-900"
              >
                {acc}
              </span>
            ))}
            {unmapped_accounts.length > 20 && (
              <span className="text-xs text-amber-700">
                + {unmapped_accounts.length - 20} more
              </span>
            )}
          </div>
        </div>
      )}

      {key_preview.length > 0 && <KeyPreview rows={key_preview} />}

      {/* Import action */}
      <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-3">
        <div className="text-sm text-slate-600">
          {hasBlocking
            ? hasSoftWarnings
              ? "Fix the required issues first. You can acknowledge warnings afterwards."
              : "Resolve the blocking issues above, then run checks again."
            : hasSoftWarnings && !warningsReviewed
            ? "Acknowledge or ignore the warnings above to enable import."
            : hasSoftWarnings
            ? "Import is enabled — click Import data below."
            : "All checks passed. You can import now."}
        </div>
        <button
          type="button"
          disabled={!canImport || committing}
          onClick={handleCommitClick}
          className={`rounded-md px-5 py-2 text-sm font-semibold transition ${
            !canImport || committing
              ? "bg-slate-200 text-slate-400 cursor-not-allowed"
              : "bg-blue-600 text-white hover:bg-blue-700 active:bg-blue-800"
          }`}
        >
          {committing ? "Importing… (large files may take several minutes)" : "Import data"}
        </button>
      </div>
    </div>
  );
}
