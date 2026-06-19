import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import {
  getVersion,
  listVersions,
  restoreVersion,
  type VersionDetail,
  type VersionRecord,
} from "../../lib/gdpduApi";

function formatScope(v: VersionRecord): string {
  const pfx = v.scope_entity_prefixes.length
    ? v.scope_entity_prefixes.join(", ")
    : "—";
  const yrs = v.scope_fiscal_years.length
    ? v.scope_fiscal_years.join(", ")
    : "—";
  return `${pfx} · FY ${yrs}`;
}

function datasetLabel(dataset: string): string {
  if (dataset === "gl") return "GL";
  if (dataset === "mapping") return "Mapping";
  if (dataset === "restore") return "Restore";
  return dataset;
}

function canRestore(v: VersionRecord): boolean {
  return v.dataset !== "restore" && v.snapshot_captured;
}

function friendlyLoadError(message: string): string {
  if (/timed out/i.test(message)) {
    return "Could not load version history — the API did not respond. Upload and validation still work.";
  }
  if (/failed to fetch|network/i.test(message)) {
    return "Could not reach the API. Check that the backend is running.";
  }
  return message;
}

export default function VersionHistoryPanel() {
  const { loading: authLoading } = useAuth();
  const [versions, setVersions] = useState<VersionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restoringId, setRestoringId] = useState<number | null>(null);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listVersions(100);
      setVersions(rows);
      if (selectedId != null && !rows.some((r) => r.load_id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
        setConfirmRestore(false);
      }
    } catch (e) {
      setVersions([]);
      setError(
        friendlyLoadError(e instanceof Error ? e.message : "Failed to load version history")
      );
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    if (authLoading) return;
    void refresh();
  }, [authLoading, refresh]);

  async function selectVersion(loadId: number) {
    const row = versions.find((v) => v.load_id === loadId);
    if (!row || !canRestore(row)) return;

    setSelectedId(loadId);
    setConfirmRestore(false);
    setDetailLoading(true);
    setError(null);
    try {
      const d = await getVersion(loadId);
      setDetail(d);
    } catch (e) {
      setDetail(null);
      setError(
        friendlyLoadError(e instanceof Error ? e.message : "Failed to load version details")
      );
    } finally {
      setDetailLoading(false);
    }
  }

  async function handleRestore(loadId: number) {
    setRestoringId(loadId);
    setError(null);
    setSuccessMsg(null);
    try {
      const r = await restoreVersion(loadId, true);
      setSuccessMsg(
        `Database restored to the state after load #${r.restored_from_load_id} (audit load #${r.load_id}).`
      );
      setConfirmRestore(false);
      setSelectedId(null);
      setDetail(null);
      await refresh();
    } catch (e) {
      setError(
        friendlyLoadError(e instanceof Error ? e.message : "Restore failed")
      );
    } finally {
      setRestoringId(null);
    }
  }

  const selected = versions.find((v) => v.load_id === selectedId);

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm mt-8">
      <div className="mb-3">
        <div className="flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            className="flex items-center gap-2 text-left group min-w-0"
          >
            <span className="text-slate-400 text-sm shrink-0" aria-hidden>
              {collapsed ? "▸" : "▾"}
            </span>
            <h2 className="text-lg font-semibold text-slate-900 group-hover:text-slate-700">
              Version history
            </h2>
          </button>
          {!collapsed && (
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={loading || authLoading}
              className="shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          )}
        </div>
        <p className="text-sm text-slate-500 mt-2 w-full">
          Each successful commit after data versioning creates a restorable snapshot. Click a row
          with snapshot <strong className="font-semibold text-slate-600">Yes</strong>, then restore
          the database scope to that point.
        </p>
      </div>

      {!collapsed && error && (
        <p className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-md px-3 py-2 mb-3">
          {error}
        </p>
      )}
      {!collapsed && successMsg && (
        <p className="text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-md px-3 py-2 mb-3">
          {successMsg}
        </p>
      )}

      {!collapsed && (authLoading || (loading && versions.length === 0)) ? (
        <p className="text-sm text-slate-500">Loading version history…</p>
      ) : null}

      {!collapsed && !authLoading && !loading && versions.length === 0 && !error ? (
        <p className="text-sm text-slate-500">No loads recorded yet.</p>
      ) : null}

      {!collapsed && versions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-4">Load</th>
                <th className="py-2 pr-4">Type</th>
                <th className="py-2 pr-4">Time</th>
                <th className="py-2 pr-4">Scope</th>
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4 text-right">Rows</th>
                <th className="py-2 pr-4">Snapshot</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => {
                const restorable = canRestore(v);
                const isSelected = selectedId === v.load_id;
                return (
                  <tr
                    key={v.load_id}
                    onClick={() => restorable && void selectVersion(v.load_id)}
                    className={`border-b border-slate-100 transition-colors ${
                      restorable
                        ? "cursor-pointer hover:bg-blue-50/60"
                        : "cursor-default"
                    } ${isSelected ? "bg-blue-50 ring-1 ring-inset ring-blue-200" : ""}`}
                  >
                    <td className="py-2.5 pr-4 font-mono text-slate-800">
                      #{v.load_id}
                      {restorable && (
                        <span className="ml-2 text-[10px] font-medium text-blue-600 uppercase">
                          Select
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-4">{datasetLabel(v.dataset)}</td>
                    <td className="py-2.5 pr-4 whitespace-nowrap text-slate-600">
                      {new Date(v.loaded_at).toLocaleString()}
                    </td>
                    <td
                      className="py-2.5 pr-4 text-slate-600 max-w-xs truncate"
                      title={formatScope(v)}
                    >
                      {formatScope(v)}
                    </td>
                    <td className="py-2.5 pr-4 text-slate-600">{v.commit_mode ?? "—"}</td>
                    <td className="py-2.5 pr-4 text-right tabular-nums">
                      {v.row_count != null ? v.row_count.toLocaleString("en-US") : "—"}
                    </td>
                    <td className="py-2.5 pr-4">
                      {v.snapshot_captured ? (
                        <span className="text-emerald-700 font-medium">Yes</span>
                      ) : (
                        <span className="text-slate-400" title="No snapshot stored for this load">
                          No
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!collapsed && selected && (
        <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50/40 p-4 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="font-semibold text-slate-900">
                Load #{selected.load_id} — {datasetLabel(selected.dataset)}
              </h3>
              <p className="text-sm text-slate-600 mt-0.5">
                {formatScope(selected)} · {new Date(selected.loaded_at).toLocaleString()}
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setSelectedId(null);
                setDetail(null);
                setConfirmRestore(false);
              }}
              className="text-xs text-slate-500 hover:text-slate-700 hover:underline"
            >
              Clear selection
            </button>
          </div>

          {detailLoading && (
            <p className="text-sm text-slate-500">Loading snapshot details…</p>
          )}

          {detail && !detailLoading && Object.keys(detail.snapshot_counts).length > 0 && (
            <div className="flex flex-wrap gap-2 text-xs">
              {Object.entries(detail.snapshot_counts).map(([table, count]) => (
                <span
                  key={table}
                  className="rounded bg-white border border-slate-200 px-2 py-1 font-mono text-slate-700"
                >
                  {table}: {count.toLocaleString("en-US")}
                </span>
              ))}
            </div>
          )}

          <p className="text-sm text-slate-700">
            Restoring replaces the live data in this scope with the snapshot taken after this
            commit. Later commits for the same scope are undone.
          </p>

          {!confirmRestore ? (
            <button
              type="button"
              onClick={() => setConfirmRestore(true)}
              disabled={restoringId === selected.load_id}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Restore to this version
            </button>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-amber-900 font-medium">
                Confirm restore to load #{selected.load_id}?
              </p>
              <button
                type="button"
                onClick={() => void handleRestore(selected.load_id)}
                disabled={restoringId === selected.load_id}
                className="rounded-md bg-amber-600 px-4 py-2 text-sm font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
              >
                {restoringId === selected.load_id ? "Restoring…" : "Yes, restore"}
              </button>
              <button
                type="button"
                onClick={() => setConfirmRestore(false)}
                className="rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
