/**
 * PositionGranularityPicker — granularity step UI for Budget Planning Chat.
 *
 * Data source: GET /api/v1/financials/budget/granularity-view
 *   grain = draft.viewMode === 'monthly' ? 'month' : 'year'
 *   statement = 'PL' | 'BS' (fetched separately when Both)
 *
 * Renders the FULL statement structure in order:
 *   - subtotal / grandtotal rows → bold, indented, period values, NO buttons
 *   - line (mapping) rows → indented, period values, sticky Granularity buttons
 *
 * Columns:
 *   1. Position — indented label + chevron (sticky left)
 *   2..N. Period value columns — one per periods[] entry (scrollable, kEUR)
 *   N+1. Granularity — [L3] [L4?] [By customers/suppliers?] buttons (sticky right)
 *
 * Features:
 *   - Multi-period columns scroll horizontally; Position + Granularity columns sticky
 *   - L4 button: sets granularity='L4' AND auto-expands L4 children + adds badge
 *   - Partner buttons: only on is_partner_driven rows; partner_kind drives label
 *   - Lazy account expansion: account rows only mounted when node is expanded
 *   - Both statements shown with a small gap between PL and BS sections
 */

import {
  useEffect,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from 'react';
import { ChevronRight } from 'lucide-react';
import {
  getBudgetGranularityView,
  type BudgetGranularityViewResponse,
  type BudgetGranularityRow,
  type BudgetGranularityChild,
  type BudgetGranularityAccount,
} from '../../../lib/gdpduApi';
import { fmtKpi } from '../../../lib/fmt';
import type {
  BudgetDraft,
  PositionGranularity,
  PositionKey,
} from '../../../lib/budgetChatFlow';
import { positionKey } from '../../../lib/budgetChatFlow';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface PositionGranularityPickerProps {
  draft: BudgetDraft;
  /** Called on each toggle (no advance). */
  onSelect: (map: Record<PositionKey, PositionGranularity>) => void;
  /** Called to advance past this step. */
  onAnswer: (map: Record<PositionKey, PositionGranularity>) => void;
}

// ---------------------------------------------------------------------------
// Granularity button group for a single mapping (line) row
// ---------------------------------------------------------------------------

interface GranularityButtonGroupProps {
  posKey: PositionKey;
  current: PositionGranularity;
  hasL4: boolean;
  partnerKind: 'customer' | 'supplier' | null;
  onChange: (key: PositionKey, val: PositionGranularity) => void;
}

function GranularityButtonGroup({
  posKey,
  current,
  hasL4,
  partnerKind,
  onChange,
}: GranularityButtonGroupProps) {
  const buttons: Array<{ value: PositionGranularity; label: string }> = [
    { value: 'L3', label: 'L3' },
  ];
  if (hasL4) {
    buttons.push({ value: 'L4', label: 'L4' });
  }
  if (partnerKind === 'customer') {
    buttons.push({ value: 'customers', label: 'Customers' });
  } else if (partnerKind === 'supplier') {
    buttons.push({ value: 'suppliers', label: 'Suppliers' });
  }

  return (
    <div
      className="inline-flex items-center rounded-lg border border-[#E2E8F0] overflow-hidden"
      onClick={(e) => e.stopPropagation()}
    >
      {buttons.map((btn, idx) => {
        const isActive = current === btn.value;
        return (
          <button
            key={btn.value}
            type="button"
            onClick={() => onChange(posKey, btn.value)}
            className={[
              'px-2 py-0.5 text-xs font-medium transition-colors whitespace-nowrap',
              idx > 0 ? 'border-l border-[#E2E8F0]' : '',
              isActive
                ? 'bg-[#1E3A5F] text-white'
                : 'bg-white text-slate-600 hover:bg-slate-50',
            ].join(' ')}
          >
            {btn.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Granularity badge (shown inline on line label when L4/partner selected)
// ---------------------------------------------------------------------------

function GranularityBadge({ value }: { value: PositionGranularity }) {
  if (value === 'L3') return null;
  const label =
    value === 'L4'
      ? 'L4'
      : value === 'customers'
      ? 'Customers'
      : 'Suppliers';
  return (
    <span
      className="ml-1.5 inline-flex items-center rounded text-[9px] font-bold px-1 py-0.5"
      style={{ background: '#1E3A5F', color: '#fff', verticalAlign: 'middle' }}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Shared sticky cell styles
// ---------------------------------------------------------------------------

const STICKY_LEFT_STYLE: React.CSSProperties = {
  position: 'sticky',
  left: 0,
  zIndex: 2,
  background: '#fff',
};

const STICKY_RIGHT_STYLE: React.CSSProperties = {
  position: 'sticky',
  right: 0,
  zIndex: 2,
  background: '#fff',
  borderLeft: '1px solid #E2E8F0',
  boxShadow: '-3px 0 6px -2px rgba(0,0,0,0.06)',
};

const STICKY_RIGHT_HEADER_STYLE: React.CSSProperties = {
  ...STICKY_RIGHT_STYLE,
  background: '#F8FAFC',
};

// kEUR formatter: divide raw EUR value by 1000
function fmtKeur(v: number | undefined): string {
  if (v === undefined || v === null) return '—';
  return fmtKpi(v / 1000);
}

// ---------------------------------------------------------------------------
// Multi-period full-structure statement table
// ---------------------------------------------------------------------------

interface FullStructureStatementTableProps {
  title: string;
  statementId: 'PL' | 'BS';
  viewData: BudgetGranularityViewResponse | null;
  loading: boolean;
  error: string | null;
  granularityMap: Record<PositionKey, PositionGranularity>;
  onChange: (key: PositionKey, val: PositionGranularity) => void;
  onL4Selected?: (lineCode: string) => void;
  externallyExpanded?: Set<string>;
}

function FullStructureStatementTable({
  title,
  statementId,
  viewData,
  loading,
  error,
  granularityMap,
  onChange,
  onL4Selected,
  externallyExpanded,
}: FullStructureStatementTableProps) {
  // Per-node expand state keyed by row.id or child expand key
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  // Merge externally-triggered expansions (e.g. auto-expand on L4 select)
  const prevExternal = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!externallyExpanded) return;
    const added: string[] = [];
    for (const id of externallyExpanded) {
      if (!prevExternal.current.has(id)) added.push(id);
    }
    if (added.length > 0) {
      setExpanded((prev) => {
        const next = new Set(prev);
        for (const id of added) next.add(id);
        return next;
      });
    }
    prevExternal.current = new Set(externallyExpanded);
  }, [externallyExpanded]);

  const toggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }, []);

  const handleGranularityChange = useCallback(
    (key: PositionKey, val: PositionGranularity) => {
      onChange(key, val);
      if (val === 'L4' && onL4Selected) {
        const lineCode = key.split('|')[1] ?? '';
        onL4Selected(lineCode);
      }
    },
    [onChange, onL4Selected],
  );

  if (!viewData) {
    return (
      <div>
        <div
          className="px-3 py-2 text-xs font-bold uppercase tracking-wide border-b border-[#E2E8F0]"
          style={{ background: '#F8FAFC', color: '#1E3A5F' }}
        >
          {title}
        </div>
        {loading && (
          <div className="px-4 py-5 text-sm text-slate-400 text-center">Loading…</div>
        )}
        {error && !loading && (
          <div className="px-4 py-3 text-sm text-red-600">{error}</div>
        )}
        {!loading && !error && (
          <div className="px-4 py-5 text-sm text-slate-400 text-center">
            No data available.
          </div>
        )}
      </div>
    );
  }

  const periods = viewData.periods;

  // Resolve rows: prefer new `rows` field; fall back to old `positions` array
  // wrapped as synthetic 'line' rows for backward compat.
  const rows: BudgetGranularityRow[] = viewData.rows ?? (
    (viewData.positions ?? []).map((pos) => ({
      id: pos.line_code,
      line_code: pos.line_code,
      label: pos.label,
      kind: 'line' as const,
      indent: 0,
      is_bold: false,
      values: pos.values ?? pos.series ?? [],
      plannable: true,
      is_partner_driven: pos.is_partner_driven,
      partner_kind: pos.partner_kind,
      has_l4: pos.has_l4,
      children: pos.children,
      accounts: pos.accounts,
    }))
  );

  // ---------------------------------------------------------------------------
  // Sub-row renderers (L4 children and GL accounts) — unchanged from prior impl
  // ---------------------------------------------------------------------------

  function renderAccountRow(
    acc: BudgetGranularityAccount,
    depth: number,
  ): ReactNode {
    const pad = 10 + depth * 14;
    return (
      <tr
        key={`acc-${acc.gl_account_id}`}
        style={{ borderBottom: '1px solid #F1F5F9' }}
      >
        <td
          className="py-1 text-left align-middle"
          style={{ ...STICKY_LEFT_STYLE, paddingLeft: pad, paddingRight: 8, minWidth: 180 }}
        >
          <div className="flex items-center gap-0.5">
            <span style={{ width: 20, flexShrink: 0 }} />
            <span className="text-xs" style={{ color: '#6B7280', fontStyle: 'italic' }}>
              {acc.label}
            </span>
          </div>
        </td>
        {periods.map((p, i) => (
          <td
            key={p.key}
            className="py-1 text-right tabular-nums whitespace-nowrap"
            style={{
              paddingLeft: 8,
              paddingRight: 8,
              fontSize: '0.7rem',
              color: '#9CA3AF',
              minWidth: 72,
            }}
          >
            {acc.values[i] !== undefined ? fmtKeur(acc.values[i]) : '—'}
          </td>
        ))}
        <td className="py-1 pr-3" style={STICKY_RIGHT_STYLE} />
      </tr>
    );
  }

  function renderL4Row(
    child: BudgetGranularityChild,
    lineCode: string,
    depth: number,
  ): ReactNode[] {
    const expandId = `l4:${lineCode}:${child.level_4}`;
    const isOpen = expanded.has(expandId);
    const hasAccounts = child.accounts && child.accounts.length > 0;
    const pad = 10 + depth * 14;

    const childRows: ReactNode[] = [];
    childRows.push(
      <tr key={expandId} style={{ borderBottom: '1px solid #E2E8F0' }}>
        <td
          className="py-1.5 text-left align-middle"
          style={{ ...STICKY_LEFT_STYLE, paddingLeft: pad, paddingRight: 8, minWidth: 180 }}
        >
          <div className="flex items-center gap-0.5">
            {hasAccounts ? (
              <button
                type="button"
                onClick={() => toggle(expandId)}
                className="p-0.5 rounded shrink-0"
                style={{ color: '#64748B' }}
                aria-expanded={isOpen}
              >
                <ChevronRight
                  size={12}
                  style={{
                    transform: isOpen ? 'rotate(90deg)' : 'none',
                    transition: 'transform 0.15s',
                  }}
                />
              </button>
            ) : (
              <span style={{ width: 20, flexShrink: 0 }} />
            )}
            <span className="text-xs" style={{ color: '#374151' }}>
              {child.label}
            </span>
          </div>
        </td>
        {periods.map((p, i) => (
          <td
            key={p.key}
            className="py-1.5 text-right tabular-nums whitespace-nowrap"
            style={{
              paddingLeft: 8,
              paddingRight: 8,
              fontSize: '0.75rem',
              color: '#374151',
              minWidth: 72,
            }}
          >
            {child.values[i] !== undefined ? fmtKeur(child.values[i]) : '—'}
          </td>
        ))}
        <td className="py-1.5 pr-3" style={STICKY_RIGHT_STYLE} />
      </tr>,
    );

    if (isOpen && hasAccounts) {
      for (const acc of child.accounts) {
        childRows.push(renderAccountRow(acc, depth + 1));
      }
    }

    return childRows;
  }

  // ---------------------------------------------------------------------------
  // Row renderer — handles all three kinds
  // ---------------------------------------------------------------------------

  function renderRow(row: BudgetGranularityRow): ReactNode[] {
    const pad = 10 + row.indent * 14;
    const isStructural = row.kind === 'subtotal' || row.kind === 'grandtotal';

    // ── Structural rows (subtotal / grandtotal): bold, no buttons ──
    if (isStructural) {
      const bgColor = row.kind === 'grandtotal' ? '#F0F4F8' : '#F8FAFC';
      const textColor = row.kind === 'grandtotal' ? '#1E3A5F' : '#374151';
      return [
        <tr
          key={row.id}
          style={{
            borderBottom: '1px solid #E2E8F0',
            background: bgColor,
          }}
        >
          {/* Label — sticky left */}
          <td
            className="py-2 text-left align-middle"
            style={{
              ...STICKY_LEFT_STYLE,
              background: bgColor,
              paddingLeft: pad,
              paddingRight: 8,
              minWidth: 180,
            }}
          >
            <span
              className="text-xs"
              style={{ fontWeight: row.is_bold ? 700 : 600, color: textColor }}
            >
              {row.label}
            </span>
          </td>
          {/* Period values */}
          {periods.map((p, i) => (
            <td
              key={p.key}
              className="py-2 text-right tabular-nums whitespace-nowrap"
              style={{
                paddingLeft: 8,
                paddingRight: 8,
                fontSize: '0.75rem',
                fontWeight: row.is_bold ? 700 : 600,
                color: textColor,
                minWidth: 72,
              }}
            >
              {row.values[i] !== undefined ? fmtKeur(row.values[i]) : '—'}
            </td>
          ))}
          {/* Granularity cell — empty for structural rows */}
          <td
            className="py-2 pr-3"
            style={{ ...STICKY_RIGHT_HEADER_STYLE, background: bgColor }}
          />
        </tr>,
      ];
    }

    // ── Line (mapping) rows: granularity buttons ──
    const expandId = `row:${row.id}`;
    const isOpen = expanded.has(expandId);
    const hasChildren = (row.has_l4 ?? false) && (row.children?.length ?? 0) > 0;
    const hasAccounts = (row.accounts?.length ?? 0) > 0;
    const hasExpandable = hasChildren || hasAccounts;

    const pKey = positionKey(statementId, row.line_code);
    const currentGranularity = granularityMap[pKey] ?? 'L3';

    const rowNodes: ReactNode[] = [];

    rowNodes.push(
      <tr key={expandId} style={{ borderBottom: '1px solid #E2E8F0' }}>
        {/* Label — sticky left */}
        <td
          className="py-1.5 text-left align-middle"
          style={{ ...STICKY_LEFT_STYLE, paddingLeft: pad, paddingRight: 8, minWidth: 180 }}
        >
          <div className="flex items-center gap-0.5">
            {hasExpandable ? (
              <button
                type="button"
                onClick={() => toggle(expandId)}
                className="p-0.5 rounded shrink-0"
                style={{ color: '#1E3A5F' }}
                aria-expanded={isOpen}
              >
                <ChevronRight
                  size={13}
                  style={{
                    transform: isOpen ? 'rotate(90deg)' : 'none',
                    transition: 'transform 0.15s',
                  }}
                />
              </button>
            ) : (
              <span style={{ width: 20, flexShrink: 0 }} />
            )}
            <span
              className="text-xs"
              style={{
                fontWeight: row.is_bold ? 700 : 500,
                color: '#111827',
              }}
            >
              {row.label}
            </span>
            <GranularityBadge value={currentGranularity} />
          </div>
        </td>
        {/* Period values */}
        {periods.map((p, i) => (
          <td
            key={p.key}
            className="py-1.5 text-right tabular-nums whitespace-nowrap"
            style={{
              paddingLeft: 8,
              paddingRight: 8,
              fontSize: '0.75rem',
              fontWeight: 500,
              color: '#374151',
              minWidth: 72,
            }}
          >
            {row.values[i] !== undefined ? fmtKeur(row.values[i]) : '—'}
          </td>
        ))}
        {/* Granularity buttons — sticky right */}
        <td
          className="py-1.5 pr-3 align-middle text-right"
          style={{ ...STICKY_RIGHT_STYLE, whiteSpace: 'nowrap' }}
        >
          <GranularityButtonGroup
            posKey={pKey}
            current={currentGranularity}
            hasL4={row.has_l4 ?? false}
            partnerKind={(row.is_partner_driven ?? false) ? (row.partner_kind ?? null) : null}
            onChange={handleGranularityChange}
          />
        </td>
      </tr>,
    );

    // Show L4 children when granularity is 'L4' (auto) or row is expanded (manual)
    const showChildren = isOpen || currentGranularity === 'L4';

    if (showChildren && hasChildren && row.children) {
      for (const child of row.children) {
        rowNodes.push(...renderL4Row(child, row.line_code, row.indent + 1));
      }
    }

    // Show accounts only when manually expanded and no L4 children shown
    if (isOpen && !hasChildren && hasAccounts && row.accounts) {
      for (const acc of row.accounts) {
        rowNodes.push(renderAccountRow(acc, row.indent + 1));
      }
    }

    return rowNodes;
  }

  return (
    <div>
      {/* Section header */}
      <div
        className="px-3 py-2 text-xs font-bold uppercase tracking-wide border-b border-[#E2E8F0]"
        style={{ background: '#F8FAFC', color: '#1E3A5F' }}
      >
        {title}
      </div>

      {loading && (
        <div className="px-4 py-5 text-sm text-slate-400 text-center">Loading…</div>
      )}

      {error && !loading && (
        <div className="px-4 py-3 text-sm text-red-600">{error}</div>
      )}

      {!loading && !error && rows.length === 0 && (
        <div className="px-4 py-5 text-sm text-slate-400 text-center">
          No plannable positions found.
        </div>
      )}

      {!loading && !error && rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs" style={{ tableLayout: 'auto' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
                {/* Position header — sticky left */}
                <th
                  className="px-3 py-2 text-left font-semibold text-slate-500 whitespace-nowrap"
                  style={{ ...STICKY_LEFT_STYLE, background: '#F8FAFC', minWidth: 180 }}
                >
                  Position (kEUR)
                </th>
                {/* Period headers */}
                {periods.map((p) => (
                  <th
                    key={p.key}
                    className="py-2 text-right font-semibold whitespace-nowrap"
                    style={{
                      paddingLeft: 8,
                      paddingRight: 8,
                      color: '#1E3A5F',
                      minWidth: 72,
                    }}
                  >
                    {p.label}
                  </th>
                ))}
                {/* Granularity header — sticky right */}
                <th
                  className="py-2 pr-3 text-right font-semibold text-slate-500 whitespace-nowrap"
                  style={STICKY_RIGHT_HEADER_STYLE}
                >
                  Granularity
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.flatMap((row) => renderRow(row))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PositionGranularityPicker
// ---------------------------------------------------------------------------

export default function PositionGranularityPicker({
  draft,
  onSelect,
  onAnswer,
}: PositionGranularityPickerProps) {
  const hasPL = draft.statements.includes('PL');
  const hasBS = draft.statements.includes('BS');
  const grain = draft.viewMode === 'monthly' ? 'month' : 'year';
  const entity = draft.entities[0] ?? '';

  // Granularity view data per statement
  const [plData, setPlData] = useState<BudgetGranularityViewResponse | null>(null);
  const [bsData, setBsData] = useState<BudgetGranularityViewResponse | null>(null);
  const [plLoading, setPlLoading] = useState(false);
  const [bsLoading, setBsLoading] = useState(false);
  const [plError, setPlError] = useState<string | null>(null);
  const [bsError, setBsError] = useState<string | null>(null);

  // Local granularity map
  const [localMap, setLocalMap] = useState<Record<PositionKey, PositionGranularity>>(
    () => ({ ...draft.granularityByPosition }),
  );

  // Track which positions should be auto-expanded when L4 is selected
  const [autoExpandedPL, setAutoExpandedPL] = useState<Set<string>>(() => new Set());
  const [autoExpandedBS, setAutoExpandedBS] = useState<Set<string>>(() => new Set());

  // Helper: seed defaults for all 'line' rows in a response
  function seedDefaults(
    res: BudgetGranularityViewResponse,
    stmt: 'PL' | 'BS',
    prev: Record<PositionKey, PositionGranularity>,
  ): Record<PositionKey, PositionGranularity> {
    const next = { ...prev };
    // Prefer new rows; fall back to old positions
    const lineRows = res.rows
      ? res.rows.filter((r) => r.kind === 'line')
      : (res.positions ?? []).map((p) => ({ line_code: p.line_code }));
    for (const row of lineRows) {
      const key = positionKey(stmt, row.line_code);
      if (!(key in next)) next[key] = 'L3';
    }
    return next;
  }

  // Fetch data when grain / entity / statements change
  useEffect(() => {
    if (hasPL) {
      setPlLoading(true);
      setPlError(null);
      getBudgetGranularityView({ statement: 'PL', grain, entity })
        .then((res) => {
          setPlData(res);
          setLocalMap((prev) => seedDefaults(res, 'PL', prev));
        })
        .catch((err) =>
          setPlError(err instanceof Error ? err.message : 'Failed to load P&L data'),
        )
        .finally(() => setPlLoading(false));
    }

    if (hasBS) {
      setBsLoading(true);
      setBsError(null);
      getBudgetGranularityView({ statement: 'BS', grain, entity })
        .then((res) => {
          setBsData(res);
          setLocalMap((prev) => seedDefaults(res, 'BS', prev));
        })
        .catch((err) =>
          setBsError(err instanceof Error ? err.message : 'Failed to load Balance Sheet data'),
        )
        .finally(() => setBsLoading(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grain, entity, hasPL, hasBS]);

  const handleChange = useCallback(
    (key: PositionKey, val: PositionGranularity) => {
      setLocalMap((prev) => {
        const next = { ...prev, [key]: val };
        onSelect(next);
        return next;
      });
    },
    [onSelect],
  );

  const handleL4SelectedPL = useCallback((lineCode: string) => {
    setAutoExpandedPL((prev) => {
      const next = new Set(prev);
      next.add(`row:${lineCode}`);
      return next;
    });
  }, []);

  const handleL4SelectedBS = useCallback((lineCode: string) => {
    setAutoExpandedBS((prev) => {
      const next = new Set(prev);
      next.add(`row:${lineCode}`);
      return next;
    });
  }, []);

  const isLoading = plLoading || bsLoading;

  if (isLoading && !plData && !bsData) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-slate-500">
        <svg
          className="animate-spin h-4 w-4 text-[#1E3A5F]"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
        </svg>
        Loading statement structure…
      </div>
    );
  }

  // Summary counts
  const counts: Record<PositionGranularity, number> = {
    L3: 0,
    L4: 0,
    customers: 0,
    suppliers: 0,
  };
  for (const v of Object.values(localMap)) counts[v] = (counts[v] ?? 0) + 1;
  const summaryParts: string[] = [];
  if (counts.L3) summaryParts.push(`${counts.L3} at L3`);
  if (counts.L4) summaryParts.push(`${counts.L4} at L4`);
  if (counts.customers) summaryParts.push(`${counts.customers} by customers`);
  if (counts.suppliers) summaryParts.push(`${counts.suppliers} by suppliers`);

  const hasAnyData =
    (plData && (plData.rows?.length ?? plData.positions?.length ?? 0) > 0) ||
    (bsData && (bsData.rows?.length ?? bsData.positions?.length ?? 0) > 0);

  return (
    <div className="flex flex-col gap-4 max-w-5xl">
      {/* Legend */}
      <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500 px-1">
        <div className="flex items-center gap-1.5">
          <span
            className="inline-flex w-7 h-5 rounded text-[9px] font-bold text-white items-center justify-center"
            style={{ background: '#1E3A5F' }}
          >
            L3
          </span>
          <span>Plan as a single position total</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="inline-flex w-7 h-5 rounded text-[9px] font-bold items-center justify-center border border-slate-300 text-slate-600">
            L4
          </span>
          <span>Plan each sub-line separately</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[#1E3A5F] font-medium">By partner</span>
          <span>Plan by top customer / supplier</span>
        </div>
        <div className="text-slate-400 italic">
          Bold rows (subtotals) are structural — no granularity choice needed.
          Expand rows with ▸ to inspect sub-lines and accounts.
        </div>
      </div>

      {/* No data fallback */}
      {!isLoading && !hasAnyData && (
        <div className="rounded-xl border border-[#E2E8F0] bg-white px-6 py-8 text-center text-sm text-slate-400">
          No positions found. Make sure GL data is loaded and the entity / year is valid.
        </div>
      )}

      {/* Income Statement */}
      {hasPL && (
        <div className="rounded-xl border border-[#E2E8F0] bg-white overflow-hidden">
          <FullStructureStatementTable
            title="Income statement"
            statementId="PL"
            viewData={plData}
            loading={plLoading}
            error={plError}
            granularityMap={localMap}
            onChange={handleChange}
            onL4Selected={handleL4SelectedPL}
            externallyExpanded={autoExpandedPL}
          />
        </div>
      )}

      {/* Balance Sheet — spaced below Income Statement */}
      {hasBS && (
        <div className={`rounded-xl border border-[#E2E8F0] bg-white overflow-hidden${hasPL ? ' mt-2' : ''}`}>
          <FullStructureStatementTable
            title="Balance sheet"
            statementId="BS"
            viewData={bsData}
            loading={bsLoading}
            error={bsError}
            granularityMap={localMap}
            onChange={handleChange}
            onL4Selected={handleL4SelectedBS}
            externallyExpanded={autoExpandedBS}
          />
        </div>
      )}

      {/* Live summary chip */}
      {summaryParts.length > 0 && (
        <div className="px-1 text-xs text-slate-400">
          {summaryParts.join(' · ')}
        </div>
      )}

      {/* Continue button */}
      <button
        type="button"
        onClick={() => onAnswer(localMap)}
        className="self-start rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors hover:opacity-90"
        style={{ background: '#1E3A5F' }}
      >
        Continue
      </button>
    </div>
  );
}
