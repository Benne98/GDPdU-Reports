/**
 * S1IssueExplorer — one line per issue type, flat source table with stats + row exclusion.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CheckOffender,
  CheckResult,
  IssueRowsResponse,
  Profile,
  S1IssueGroup,
} from "../../lib/gdpduApi";
import { fetchIssueRows } from "../../lib/gdpduApi";

export interface ValidateContext {
  file_id: string;
  sheet?: string;
  profile: Profile;
  exclude_line_ids: string[];
}

function fmt(n: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(n);
}

/**
 * Render a single cell value.
 * Only the "amount" column gets thousands-separator formatting.
 * All other columns (ids, account numbers, document codes, dates, text) render
 * as plain strings to avoid formatting 19-digit booking_line_id values.
 */
function renderCell(col: string, value: string | number | boolean | null) {
  if (value == null || value === "") {
    return <span className="text-slate-400 italic">empty</span>;
  }
  if (col === "amount" && typeof value === "number") {
    return fmt(value);
  }
  return String(value);
}

function describeIssueGroup(group: S1IssueGroup): string {
  const label = group.field_label ?? group.field;
  if (group.issue === "column_missing") {
    return `Column “${label}” is missing from the transformed data.`;
  }
  if (group.field === "account_number_group") {
    return `${group.count.toLocaleString("en-US")} row(s): account number empty in source — cannot build account key (entity prefix + account).`;
  }
  if (group.field === "amount") {
    return `${group.count.toLocaleString("en-US")} row(s): amount is missing.`;
  }
  return `${group.count.toLocaleString("en-US")} row(s): ${label} is missing.`;
}

function describeSample(group: S1IssueGroup): string | null {
  const s = group.sample;
  if (!s?.booking_line_id) return null;
  const row = `Row ${s.booking_line_id}`;
  const booking = s.journal_entry_group_number
    ? `, booking ${String(s.journal_entry_number ?? s.journal_entry_group_number).replace(/^0+/, "") || s.journal_entry_group_number}`
    : "";
  if (group.field === "account_number_group") {
    return `${row}${booking}`;
  }
  if (group.field === "amount") {
    return `${row}${booking}`;
  }
  return `${row}${booking}`;
}

function fallbackIssueGroups(check: CheckResult): S1IssueGroup[] {
  if (check.issue_groups?.length) return check.issue_groups;
  const byField = new Map<string, S1IssueGroup>();
  for (const o of check.offenders ?? []) {
    const off = o as CheckOffender;
    const field = off.field ?? "unknown";
    if (!byField.has(field)) {
      byField.set(field, {
        field,
        field_label: typeof off.field_label === "string" ? off.field_label : undefined,
        count: 0,
        issue: off.issue ?? "empty",
        sample: off,
      });
    }
    const g = byField.get(field)!;
    g.count += 1;
  }
  return [...byField.values()];
}

function IssueRowTable({
  data,
  filter,
  selected,
  onToggle,
  onToggleAll,
}: {
  data: IssueRowsResponse;
  filter: string;
  selected: Set<string>;
  onToggle: (lineId: string) => void;
  onToggleAll: (lineIds: string[], checked: boolean) => void;
}) {
  // booking_line_id is the internal checkbox key — never display it as a column.
  // The human-readable "reference" column (e.g. "01B200 #1") is shown instead.
  // When data.columns contains no usable columns (stale/degenerate API response)
  // but rows are present, fall back to deriving display columns from the union of
  // keys found across the row objects, preserving first-seen order.
  const { displayCols, isDegenerate } = useMemo(() => {
    const fromColumns = data.columns.filter((c) => c !== "booking_line_id");
    if (fromColumns.length > 0 || data.rows.length === 0) {
      return { displayCols: fromColumns, isDegenerate: false };
    }
    // Stale/degenerate response: derive columns from the union of row keys.
    const seen = new Set<string>();
    const derived: string[] = [];
    for (const row of data.rows) {
      for (const key of Object.keys(row)) {
        if (key !== "booking_line_id" && !seen.has(key)) {
          seen.add(key);
          derived.push(key);
        }
      }
    }
    return { displayCols: derived, isDegenerate: true };
  }, [data.columns, data.rows]);

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return data.rows;
    return data.rows.filter((row) =>
      Object.values(row).some(
        (v) => v != null && v !== "" && String(v).toLowerCase().includes(q)
      )
    );
  }, [data.rows, filter]);

  const lineIds = rows
    .map((r) => r.booking_line_id)
    .filter((id): id is string => typeof id === "string");
  const allSelected = lineIds.length > 0 && lineIds.every((id) => selected.has(id));

  if (data.rows.length === 0) {
    return <p className="text-sm text-slate-500">No rows to display.</p>;
  }

  return (
    <>
      {isDegenerate && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-md px-3 py-2">
          This validation result looks out of date (the backend returned only an internal id
          column). Restart the API / refresh to see full row details.
        </p>
      )}
      <div className="overflow-x-auto rounded border border-slate-200 bg-white max-h-96 overflow-y-auto">
      <table className="w-full text-xs">
        <thead className="sticky top-0 z-10 bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="py-1.5 px-2 w-8">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={(e) => onToggleAll(lineIds, e.target.checked)}
                aria-label="Select all visible rows"
              />
            </th>
            {displayCols.map((c) => {
              const isFailing = c === data.failing_field;
              return (
                <th
                  key={c}
                  className={`py-1.5 px-2 text-left font-semibold whitespace-nowrap ${
                    isFailing ? "bg-amber-50 text-amber-800" : "text-slate-600"
                  }`}
                >
                  {data.column_labels?.[c] ?? c}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const lineId =
              typeof row.booking_line_id === "string" ? row.booking_line_id : null;
            return (
              <tr
                key={lineId ?? i}
                className="border-b border-slate-100 last:border-0 hover:bg-slate-50/80"
              >
                <td className="py-1.5 px-2">
                  {lineId != null && (
                    <input
                      type="checkbox"
                      checked={selected.has(lineId)}
                      onChange={() => onToggle(lineId)}
                      aria-label={`Select row ${lineId}`}
                    />
                  )}
                </td>
                {displayCols.map((c) => {
                  const isFailing = c === data.failing_field;
                  return (
                    <td
                      key={c}
                      className={`py-1.5 px-2 font-mono whitespace-nowrap ${
                        isFailing ? "bg-amber-50/60 text-amber-900" : "text-slate-700"
                      }`}
                    >
                      {renderCell(c, row[c] as string | number | boolean | null)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length === 0 && (
        <p className="px-3 py-4 text-sm text-slate-500">No rows match your filter.</p>
      )}
    </div>
    </>
  );
}

function IssueGroupPanel({
  group,
  context,
  excluding,
  onExcludeLines,
}: {
  group: S1IssueGroup;
  context: ValidateContext;
  excluding?: boolean;
  onExcludeLines?: (lineIds: string[]) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<IssueRowsResponse | null>(null);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // F-2: Reset loaded data + checkbox selection whenever the exclude_line_ids context
  // changes (i.e. after an exclusion round-trip).  The existing open+!data effect then
  // re-fetches automatically.  isFirstExclude skips the reset on initial mount.
  const isFirstExclude = useRef(true);
  const excludeKey = context.exclude_line_ids.join(",");
  useEffect(() => {
    if (isFirstExclude.current) { isFirstExclude.current = false; return; }
    setData(null);
    setSelected(new Set());
  }, [excludeKey]); // excludeKey is a primitive string — stable comparison

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchIssueRows({
        file_id: context.file_id,
        sheet: context.sheet,
        profile: context.profile,
        exclude_line_ids: context.exclude_line_ids,
        check_id: "S1",
        field: group.field,
      });
      setData(res);
      setSelected(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load rows");
    } finally {
      setLoading(false);
    }
  }, [context, group.field]);

  useEffect(() => {
    if (open && !data && !loading) {
      void load();
    }
  }, [open, data, loading, load]);

  function toggleLine(lineId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  }

  function toggleAll(lineIds: string[], checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of lineIds) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  const sample = describeSample(group);

  return (
    <li className="rounded-md border border-slate-200 bg-white overflow-hidden">
      <div className="px-3 py-2.5 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-slate-800">{describeIssueGroup(group)}</p>
          {sample && (
            <p className="mt-0.5 text-xs text-slate-500">Example: {sample}</p>
          )}
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="shrink-0 rounded-md border border-blue-200 bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-800 hover:bg-blue-100"
        >
          {open ? "Hide source rows" : "View affected rows"}
        </button>
      </div>

      {open && (
        <div className="border-t border-slate-200 bg-slate-50 px-3 py-3 space-y-3">
          {loading && (
            <p className="text-sm text-slate-500">
              Loading source rows… large files can take up to a minute after the first check.
            </p>
          )}
          {error && (
            <div className="text-sm text-red-700">
              {error}{" "}
              <button type="button" onClick={() => void load()} className="underline">
                Retry
              </button>
            </div>
          )}
          {data && (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Rows</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {data.stats.row_count.toLocaleString("en-US")}
                  </div>
                </div>
                <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Amount sum</div>
                  <div className="text-lg font-semibold text-slate-900">
                    {data.stats.amount_sum != null ? fmt(data.stats.amount_sum) : "—"}
                  </div>
                </div>
                <div className="rounded-md border border-slate-200 bg-white px-3 py-2 sm:col-span-2">
                  <div className="text-[10px] uppercase tracking-wide text-slate-500">Fiscal years</div>
                  <div className="text-sm font-semibold text-slate-900">
                    {data.stats.fiscal_years.length > 0
                      ? data.stats.fiscal_years.join(", ")
                      : "—"}
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="search"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  placeholder="Filter rows…"
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm min-w-[12rem] flex-1"
                />
                {onExcludeLines && (
                  <button
                    type="button"
                    disabled={excluding || selected.size === 0}
                    onClick={() => void onExcludeLines([...selected])}
                    className="rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-800 hover:bg-red-100 disabled:opacity-50"
                  >
                    {excluding
                      ? "Updating…"
                      : `Exclude selected (${selected.size.toLocaleString("en-US")})`}
                  </button>
                )}
                {onExcludeLines && (data.offender_ids?.length ?? 0) > 0 && (
                  <button
                    type="button"
                    disabled={excluding}
                    onClick={() => void onExcludeLines(data.offender_ids!)}
                    className="rounded-md border border-red-300 bg-red-100 px-3 py-1.5 text-sm font-medium text-red-900 hover:bg-red-200 disabled:opacity-50"
                  >
                    {excluding
                      ? "Updating…"
                      : `Exclude all ${(data.total_offenders ?? data.offender_ids!.length).toLocaleString("en-US")} offending rows`}
                  </button>
                )}
              </div>

              {/* F-5: Reference rows — always-visible block ABOVE the failing-rows
                  scroll container so they are never hidden inside the 384px clip.
                  Label is driven by reference_kind so the user understands provenance.
                  Uses the same display columns (booking_line_id excluded) so columns
                  line up column-for-column with the failing-rows table below. */}
              {(data.reference_rows?.length ?? 0) > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-semibold text-emerald-700">
                    {data.reference_kind === "same_field"
                      ? "Valid examples for this field"
                      : data.reference_kind === "overall"
                      ? "Valid example rows (for comparison)"
                      : "Reference rows (valid examples)"}
                  </p>
                  <div className="overflow-x-auto rounded border border-emerald-200">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="bg-emerald-50 border-b border-emerald-200">
                          <th className="py-1 px-2 w-8" />
                          {data.columns
                            .filter((c) => c !== "booking_line_id")
                            .map((c) => {
                              const isFailing = c === data.failing_field;
                              return (
                                <th
                                  key={c}
                                  className={`py-1.5 px-2 text-left font-semibold whitespace-nowrap ${
                                    isFailing
                                      ? "bg-amber-50 text-amber-800"
                                      : "text-emerald-700"
                                  }`}
                                >
                                  {data.column_labels?.[c] ?? c}
                                </th>
                              );
                            })}
                        </tr>
                      </thead>
                      <tbody>
                        {data.reference_rows!.slice(0, 5).map((row, i) => (
                          <tr
                            key={`ref-${i}`}
                            className="border-b border-emerald-100 last:border-0 bg-emerald-50/60"
                          >
                            <td className="py-1.5 px-2" />
                            {data.columns
                              .filter((c) => c !== "booking_line_id")
                              .map((c) => {
                                const isFailing = c === data.failing_field;
                                return (
                                  <td
                                    key={c}
                                    className={`py-1.5 px-2 font-mono whitespace-nowrap ${
                                      isFailing
                                        ? "bg-amber-50/60 text-amber-700"
                                        : "text-emerald-700"
                                    }`}
                                  >
                                    {renderCell(
                                      c,
                                      row[c] as string | number | boolean | null
                                    )}
                                  </td>
                                );
                              })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              {(data.reference_kind === "none" ||
                (data.reference_kind === undefined && data.reference_available === false)) && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-md px-3 py-2">
                  No valid rows to compare — every row fails this field.
                </p>
              )}

              <IssueRowTable
                data={data}
                filter={filter}
                selected={selected}
                onToggle={toggleLine}
                onToggleAll={toggleAll}
              />
              <p className="text-[12px] text-slate-500">
                Source file columns as uploaded. Select rows to exclude from this import, then re-run
                checks.
              </p>
            </>
          )}
        </div>
      )}
    </li>
  );
}

export default function S1IssueExplorer({
  check,
  context,
  excluding,
  onExcludeLines,
}: {
  check: CheckResult;
  context: ValidateContext;
  excluding?: boolean;
  onExcludeLines?: (lineIds: string[]) => void | Promise<void>;
}) {
  const groups = fallbackIssueGroups(check);
  if (groups.length === 0) return null;

  return (
    <div className="mt-3 space-y-2">
      <p className="text-xs text-slate-500">
        {groups.length} issue type(s) — open a row to inspect and exclude source lines.
      </p>
      <ul className="space-y-2">
        {groups.map((g) => (
          <IssueGroupPanel
            key={g.field}
            group={g}
            context={context}
            excluding={excluding}
            onExcludeLines={onExcludeLines}
          />
        ))}
      </ul>
      {groups.some((g) => g.field === "account_number_group") && (
        <p className="text-xs text-slate-600 bg-slate-50 border border-slate-100 rounded-md px-3 py-2">
          Account key = 2-digit entity prefix + 6-digit account number (e.g. entity 01 + account 41100
          → 01041100).
        </p>
      )}
    </div>
  );
}
