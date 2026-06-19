import { useCallback, useEffect, useState } from "react";
import DataUpdateNav from "../components/ingest/DataUpdateNav";
import PageShell from "../components/ui/PageShell";
import SoftSegment from "../components/ui/SoftSegment";
import {
  getMappingFiscalYears,
  getMappingStructure,
  listMappingAccounts,
  patchMappingAccounts,
  reorderMappingStructure,
  type ApplyScope,
  type MappingAccountRow,
  type MappingStatement,
  type StructureNode,
} from "../lib/mappingEditorApi";

type SubTab = "accounts" | "structure";

const NAVY = "#1E3A5F";

const BASE_LEVELS = [
  { key: "level_0", label: "Level 1" },
  { key: "level_1", label: "Level 2" },
  { key: "level_2", label: "Level 3" },
  { key: "level_3", label: "Level 4" },
] as const;

const OPTIONAL_LEVELS = [
  { key: "level_4", label: "Level 5" },
  { key: "l4_sub", label: "Level 6" },
] as const;

const REORDERABLE = new Set(["level_1", "level_2", "level_3"]);

function pillStyle(active: boolean) {
  return {
    background: active ? "rgba(30,58,95,0.1)" : "#F4F6F9",
    color: active ? NAVY : "#475569",
    border: `1px solid ${active ? "rgba(30,58,95,0.25)" : "#E2E8F0"}`,
  };
}

function EditorToolbar({
  statement,
  onStatementChange,
  subTab,
  onSubTabChange,
  applyScope,
  onApplyScopeChange,
  fiscalYear,
  onFiscalYearChange,
  fiscalYears,
}: {
  statement: MappingStatement;
  onStatementChange: (s: MappingStatement) => void;
  subTab: SubTab;
  onSubTabChange: (t: SubTab) => void;
  applyScope: ApplyScope;
  onApplyScopeChange: (s: ApplyScope) => void;
  fiscalYear: number;
  onFiscalYearChange: (y: number) => void;
  fiscalYears: number[];
}) {
  const years = fiscalYears.length > 0 ? fiscalYears : [fiscalYear];

  return (
    <div
      className="mb-6 rounded-xl overflow-hidden"
      style={{
        background: "#FFFFFF",
        border: "1px solid #E2E8F0",
        boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
      }}
    >
      <div
        className="px-5 py-4 flex flex-wrap items-end justify-between gap-4"
        style={{ borderBottom: "1px solid #F1F5F9", background: "#FAFBFC" }}
      >
        <div className="flex flex-wrap items-end gap-6">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
              Statement
            </p>
            <SoftSegment
              value={statement}
              onChange={(v) => onStatementChange(v as MappingStatement)}
              options={[
                { value: "BS", label: "Balance sheet" },
                { value: "PL", label: "Income statement" },
              ]}
            />
          </div>
          <div className="hidden sm:block w-px h-8 bg-slate-200" />
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400 mb-2">
              View
            </p>
            <SoftSegment
              value={subTab}
              onChange={(v) => onSubTabChange(v as SubTab)}
              options={[
                { value: "accounts", label: "Accounts" },
                { value: "structure", label: "Position order" },
              ]}
            />
          </div>
        </div>
      </div>

      <div className="px-5 py-3.5 flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-slate-500 shrink-0">Apply to</span>
          <div className="flex gap-1">
            {(
              [
                ["all_years", "All fiscal years"],
                ["single_year", "Single year"],
              ] as [ApplyScope, string][]
            ).map(([val, label]) => (
              <button
                key={val}
                type="button"
                onClick={() => onApplyScopeChange(val)}
                className="px-2.5 py-1.5 rounded-md text-xs font-medium transition-all"
                style={pillStyle(applyScope === val)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="w-px h-4 bg-slate-200 hidden sm:block" />

        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-slate-500 shrink-0">
            {applyScope === "all_years" ? "Preview year" : "Fiscal year"}
          </label>
          <select
            value={fiscalYear}
            onChange={(e) => onFiscalYearChange(parseInt(e.target.value, 10))}
            className="rounded-md px-2.5 py-1.5 text-xs font-medium outline-none cursor-pointer"
            style={{ background: "#F4F6F9", border: "1px solid #E2E8F0", color: "#111827" }}
          >
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </div>

        {applyScope === "all_years" && (
          <p className="text-xs text-slate-400">
            Preview only — saves and reorders affect all fiscal years.
          </p>
        )}
      </div>
    </div>
  );
}

function SortableSiblingList({
  nodes,
  parentPath,
  statement,
  fiscalYear,
  applyScope,
  onReordered,
}: {
  nodes: StructureNode[];
  parentPath: Record<string, string>;
  statement: MappingStatement;
  fiscalYear: number;
  applyScope: ApplyScope;
  onReordered: () => void;
}) {
  const [items, setItems] = useState(nodes);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems(nodes);
  }, [nodes]);

  if (items.length === 0) return null;

  const levelKey = items[0].level_key;
  const canDrag = REORDERABLE.has(levelKey);

  async function persistOrder(next: StructureNode[]) {
    if (!canDrag) return;
    setSaving(true);
    setError(null);
    try {
      await reorderMappingStructure({
        fiscal_year: fiscalYear,
        apply_scope: applyScope,
        statement,
        parent_path: parentPath,
        level_key: levelKey,
        ordered_labels: next.map((n) => n.label),
      });
      onReordered();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reorder failed");
      setItems(nodes);
    } finally {
      setSaving(false);
    }
  }

  function onDrop(targetIdx: number) {
    if (dragIdx === null || dragIdx === targetIdx || !canDrag) return;
    const next = [...items];
    const [moved] = next.splice(dragIdx, 1);
    next.splice(targetIdx, 0, moved);
    setItems(next);
    setDragIdx(null);
    void persistOrder(next);
  }

  return (
    <div className="space-y-1">
      {error && <p className="text-xs text-red-600 mb-1">{error}</p>}
      {items.map((node, idx) => (
        <div key={node.id}>
          <div
            draggable={canDrag && !saving}
            onDragStart={() => setDragIdx(idx)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => onDrop(idx)}
            className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${
              canDrag
                ? "border-slate-200 bg-white cursor-grab active:cursor-grabbing hover:border-slate-300 hover:bg-slate-50/50"
                : "border-slate-100 bg-slate-50"
            }`}
          >
            {canDrag && (
              <span className="text-slate-300 select-none" aria-hidden>
                ⠿
              </span>
            )}
            <span className="text-xs font-medium text-slate-400 w-16">{node.ui_level}</span>
            <span className="flex-1 font-medium text-slate-800">{node.label}</span>
            <span className="text-xs text-slate-400">
              {node.account_count} acct{node.account_count === 1 ? "" : "s"}
            </span>
            {node.sort_order != null && (
              <span className="text-xs text-slate-300 font-mono">#{node.sort_order}</span>
            )}
          </div>
          {node.children.length > 0 && (
            <div className="ml-6 mt-1 border-l border-slate-100 pl-3">
              <SortableSiblingList
                nodes={node.children}
                parentPath={node.path}
                statement={statement}
                fiscalYear={fiscalYear}
                applyScope={applyScope}
                onReordered={onReordered}
              />
            </div>
          )}
        </div>
      ))}
      {saving && <p className="text-xs text-slate-400">Saving order…</p>}
    </div>
  );
}

function StructurePanel({
  statement,
  fiscalYear,
  applyScope,
}: {
  statement: MappingStatement;
  fiscalYear: number;
  applyScope: ApplyScope;
}) {
  const [nodes, setNodes] = useState<StructureNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setNodes(await getMappingStructure(fiscalYear, statement));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load structure");
    } finally {
      setLoading(false);
    }
  }, [fiscalYear, statement]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm overflow-hidden">
      <div className="px-5 py-3.5 border-b border-slate-100">
        <p className="text-xs text-slate-400">
          Drag positions to reorder within the same parent level.
        </p>
      </div>
      <div className="p-5">
        {loading && <p className="text-sm text-slate-500">Loading structure…</p>}
        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
        {!loading && !error && (
          <>
            {nodes.length === 0 ? (
              <p className="text-sm text-slate-500">No accounts found.</p>
            ) : (
              <SortableSiblingList
                nodes={nodes}
                parentPath={{ level_0: statement }}
                statement={statement}
                fiscalYear={fiscalYear}
                applyScope={applyScope}
                onReordered={load}
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

function AccountsPanel({
  statement,
  fiscalYear,
  applyScope,
}: {
  statement: MappingStatement;
  fiscalYear: number;
  applyScope: ApplyScope;
}) {
  const [rows, setRows] = useState<MappingAccountRow[]>([]);
  const [draft, setDraft] = useState<Record<string, MappingAccountRow>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showLevel5, setShowLevel5] = useState(false);
  const [showLevel6, setShowLevel6] = useState(false);

  const levelColumns = [
    ...BASE_LEVELS,
    ...(showLevel5 ? [OPTIONAL_LEVELS[0]] : []),
    ...(showLevel6 ? [OPTIONAL_LEVELS[1]] : []),
  ];

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listMappingAccounts(fiscalYear, statement);
      setRows(data);
      setDraft(Object.fromEntries(data.map((r) => [r.account_number_group, { ...r }])));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load accounts");
    } finally {
      setLoading(false);
    }
  }, [fiscalYear, statement]);

  useEffect(() => {
    void load();
  }, [load]);

  function setCell(ang: string, key: keyof MappingAccountRow, value: string) {
    setDraft((prev) => ({
      ...prev,
      [ang]: { ...prev[ang], [key]: value },
    }));
  }

  async function save() {
    setSaving(true);
    setError(null);
    setMessage(null);
    const fieldKeys = ["account_name", ...levelColumns.map((l) => l.key)];
    const updates = rows
      .map((orig) => {
        const cur = draft[orig.account_number_group];
        if (!cur) return null;
        const patch: Record<string, string> = {
          account_number_group: orig.account_number_group,
        };
        for (const k of fieldKeys) {
          const key = k as keyof MappingAccountRow;
          if (String(cur[key] ?? "") !== String(orig[key] ?? "")) {
            patch[k] = String(cur[key] ?? "");
          }
        }
        return Object.keys(patch).length > 1 ? patch : null;
      })
      .filter(Boolean) as Array<{
      account_number_group: string;
      account_name?: string;
      level_0?: string;
      level_1?: string;
      level_2?: string;
      level_3?: string;
      level_4?: string;
      l4_sub?: string;
    }>;

    if (updates.length === 0) {
      setMessage("No changes to save.");
      setSaving(false);
      return;
    }

    try {
      const n = await patchMappingAccounts(fiscalYear, updates, applyScope);
      setMessage(
        applyScope === "all_years"
          ? `Saved ${n} row(s) across all fiscal years.`
          : `Saved ${n} row(s) for fiscal year ${fiscalYear}.`
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const display = rows.map((r) => draft[r.account_number_group] ?? r);

  return (
    <div className="rounded-xl border border-slate-200/80 bg-white shadow-sm overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 py-3.5 border-b border-slate-100">
        <p className="text-xs text-slate-400">
          {display.length.toLocaleString("en-US")} accounts
          {applyScope === "all_years"
            ? ` · preview ${fiscalYear}`
            : ` · fiscal year ${fiscalYear}`}
          {" · "}
          Level 5/6 via + in table header
        </p>
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving || loading}
          className="rounded-md bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-40 shrink-0"
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>

      {message && (
        <p className="px-5 pt-3 text-xs text-emerald-700">{message}</p>
      )}
      {error && (
        <div className="mx-5 mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <p className="p-5 text-sm text-slate-500">Loading accounts…</p>
      ) : (
        <div className="max-h-[560px] overflow-auto">
          <table className="w-full text-xs text-left">
            <thead className="bg-slate-50/90 sticky top-0 text-slate-500 border-b border-slate-200">
              <tr>
                <th className="px-3 py-2 font-semibold">Account</th>
                <th className="px-3 py-2 font-semibold">Name</th>
                {levelColumns.map((l) => (
                  <th key={l.key} className="px-3 py-2 font-semibold whitespace-nowrap">
                    {l.label}
                  </th>
                ))}
                <th className="px-3 py-2 w-10">
                  {!showLevel5 && (
                    <button
                      type="button"
                      title="Add Level 5 column"
                      onClick={() => setShowLevel5(true)}
                      className="flex h-6 w-6 items-center justify-center rounded border border-dashed border-slate-300 text-slate-400 hover:border-slate-400 hover:text-slate-600"
                    >
                      +
                    </button>
                  )}
                  {showLevel5 && !showLevel6 && (
                    <button
                      type="button"
                      title="Add Level 6 column"
                      onClick={() => setShowLevel6(true)}
                      className="flex h-6 w-6 items-center justify-center rounded border border-dashed border-slate-300 text-slate-400 hover:border-slate-400 hover:text-slate-600"
                    >
                      +
                    </button>
                  )}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {display.map((row) => (
                <tr key={row.account_number_group} className="hover:bg-slate-50/60">
                  <td className="px-3 py-1.5 font-mono text-slate-600 whitespace-nowrap">
                    {row.gl_account_id}
                  </td>
                  <td className="px-3 py-1.5">
                    <input
                      type="text"
                      value={row.account_name}
                      onChange={(e) =>
                        setCell(row.account_number_group, "account_name", e.target.value)
                      }
                      className="w-full min-w-[120px] rounded border border-slate-200 bg-white px-2 py-1 focus:border-slate-300 focus:outline-none"
                    />
                  </td>
                  {levelColumns.map((l) => (
                    <td key={l.key} className="px-3 py-1.5">
                      <input
                        type="text"
                        value={String(row[l.key as keyof MappingAccountRow] ?? "")}
                        onChange={(e) =>
                          setCell(
                            row.account_number_group,
                            l.key as keyof MappingAccountRow,
                            e.target.value
                          )
                        }
                        className="w-full min-w-[90px] rounded border border-slate-200 bg-white px-2 py-1 focus:border-slate-300 focus:outline-none"
                      />
                    </td>
                  ))}
                  <td />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function contextTitle(statement: MappingStatement, subTab: SubTab): string {
  const stmt = statement === "BS" ? "Balance sheet" : "Income statement";
  const view = subTab === "accounts" ? "accounts" : "position order";
  return `${stmt} · ${view}`;
}

export default function MappingEditorPage() {
  const [statement, setStatement] = useState<MappingStatement>("BS");
  const [subTab, setSubTab] = useState<SubTab>("accounts");
  const [applyScope, setApplyScope] = useState<ApplyScope>("all_years");
  const [fiscalYears, setFiscalYears] = useState<number[]>([]);
  const [fiscalYear, setFiscalYear] = useState<number>(2024);
  const [loadingFy, setLoadingFy] = useState(true);

  useEffect(() => {
    getMappingFiscalYears()
      .then((years) => {
        setFiscalYears(years);
        if (years.length > 0) setFiscalYear(years[years.length - 1]);
      })
      .catch(() => setFiscalYears([]))
      .finally(() => setLoadingFy(false));
  }, []);

  return (
    <PageShell loading={loadingFy} message="Loading mapping editor…">
      <div>
        <DataUpdateNav topMode="account-mapping" accountView="editor" />

        <p className="mb-5 text-sm text-slate-500">{contextTitle(statement, subTab)}</p>

        <EditorToolbar
          statement={statement}
          onStatementChange={setStatement}
          subTab={subTab}
          onSubTabChange={setSubTab}
          applyScope={applyScope}
          onApplyScopeChange={setApplyScope}
          fiscalYear={fiscalYear}
          onFiscalYearChange={setFiscalYear}
          fiscalYears={fiscalYears}
        />

        {subTab === "accounts" && (
          <AccountsPanel
            statement={statement}
            fiscalYear={fiscalYear}
            applyScope={applyScope}
          />
        )}
        {subTab === "structure" && (
          <StructurePanel
            statement={statement}
            fiscalYear={fiscalYear}
            applyScope={applyScope}
          />
        )}
      </div>
    </PageShell>
  );
}
