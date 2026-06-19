/**
 * Mapping editor API — chart of accounts hierarchy editing.
 */

const TOKEN_KEY = "gdpdu_access_token";

export type ApplyScope = "all_years" | "single_year";
export type MappingStatement = "BS" | "PL";

async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(init.headers as HeadersInit | undefined);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    window.location.href = "/login";
  }
  return res;
}

async function throwApiError(res: Response): Promise<never> {
  const text = await res.text();
  let detail = `API ${res.status}`;
  if (text) {
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } else if (typeof body === "string") {
        detail = body;
      }
    } catch {
      detail = text;
    }
  }
  throw new Error(detail);
}

export interface MappingAccountRow {
  account_number_group: string;
  fiscal_year: number;
  gl_account_id: string;
  account_name: string;
  level_0: string;
  level_1: string;
  level_2: string;
  level_3: string;
  level_4: string;
  l4_sub?: string;
  level_1_sort: number | null;
  level_2_sort: number | null;
  level_3_sort: number | null;
  level_4_sort: number | null;
}

export interface StructureNode {
  id: string;
  label: string;
  level_key: string;
  ui_level: string;
  path: Record<string, string>;
  sort_order: number | null;
  account_count: number;
  children: StructureNode[];
}

export async function listMappingAccounts(
  fiscalYear: number,
  statement: MappingStatement
): Promise<MappingAccountRow[]> {
  const q = new URLSearchParams({
    fiscal_year: String(fiscalYear),
    statement,
  });
  const res = await apiFetch(`/api/v1/mapping-editor/accounts?${q}`);
  if (!res.ok) await throwApiError(res);
  const data = await res.json();
  return data.accounts as MappingAccountRow[];
}

export async function patchMappingAccounts(
  fiscalYear: number,
  updates: Array<{
    account_number_group: string;
    account_name?: string;
    level_0?: string;
    level_1?: string;
    level_2?: string;
    level_3?: string;
    level_4?: string;
    l4_sub?: string;
  }>,
  applyScope: ApplyScope = "all_years"
): Promise<number> {
  const res = await apiFetch("/api/v1/mapping-editor/accounts", {
    method: "PATCH",
    body: JSON.stringify({
      fiscal_year: fiscalYear,
      apply_scope: applyScope,
      updates,
    }),
  });
  if (!res.ok) await throwApiError(res);
  const data = await res.json();
  return data.updated as number;
}

export async function getMappingStructure(
  fiscalYear: number,
  statement: MappingStatement
): Promise<StructureNode[]> {
  const q = new URLSearchParams({
    fiscal_year: String(fiscalYear),
    statement,
  });
  const res = await apiFetch(`/api/v1/mapping-editor/structure?${q}`);
  if (!res.ok) await throwApiError(res);
  const data = await res.json();
  return data.nodes as StructureNode[];
}

export async function reorderMappingStructure(payload: {
  fiscal_year: number;
  apply_scope?: ApplyScope;
  statement: MappingStatement;
  parent_path: Record<string, string>;
  level_key: string;
  ordered_labels: string[];
}): Promise<number> {
  const res = await apiFetch("/api/v1/mapping-editor/structure/reorder", {
    method: "POST",
    body: JSON.stringify({
      apply_scope: "all_years",
      ...payload,
    }),
  });
  if (!res.ok) await throwApiError(res);
  const data = await res.json();
  return data.updated_accounts as number;
}

export async function getMappingFiscalYears(): Promise<number[]> {
  const res = await apiFetch("/api/v1/ingest/fiscal-years");
  if (!res.ok) await throwApiError(res);
  const data = await res.json();
  return data.fiscal_years as number[];
}
