/**
 * S1IssueExplorer — one line per issue type, flat source table with stats + row exclusion.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
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
  exclude_line_ids: number[];
}

function fmt(n: number): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(n);
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
  selected: Set<number>;
  onToggle: (lineId: number) => void;
  onToggleAll: (lineIds: number[], checked: boolean) => void;
}) {
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
    .filter((id): id is number => typeof id === "number");
  const allSelected = lineIds.length > 0 && lineIds.every((id) => selected.has(id));

  if (data.columns.length === 0) {
    return <p className="text-sm text-slate-500">No rows to display.</p>;
  }

  return (
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
            {data.columns.map((c) => (
              <th key={c} className="py-1.5 px-2 text-left font-semibold text-slate-600 whitespace-nowrap">
                {c === "booking_line_id" ? "Line" : c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const lineId = typeof row.booking_line_id === "number" ? row.booking_line_id : null;
            return (
              <tr key={lineId ?? i} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/80">
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
                {data.columns.map((c) => (
                  <td key={c} className="py-1.5 px-2 font-mono text-slate-700 whitespace-nowrap">
                    {row[c] == null || row[c] === "" ? (
                      <span className="text-slate-400 italic">empty</span>
                    ) : typeof row[c] === "number" ? (
                      fmt(row[c] as number)
                    ) : (
                      String(row[c])
                    )}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      {rows.length === 0 && (
        <p className="px-3 py-4 text-sm text-slate-500">No rows match your filter.</p>
      )}
    </div>
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
  onExcludeLines?: (lineIds: number[]) => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<IssueRowsResponse | null>(null);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());

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

  function toggleLine(lineId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(lineId)) next.delete(lineId);
      else next.add(lineId);
      return next;
    });
  }

  function toggleAll(lineIds: number[], checked: boolean) {
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
              </div>

              <IssueRowTable
                data={data}
                filter={filter}
                selected={selected}
                onToggle={toggleLine}
                onToggleAll={toggleAll}
              />
              <p className="text-[11px] text-slate-500">
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
  onExcludeLines?: (lineIds: number[]) => void | Promise<void>;
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
