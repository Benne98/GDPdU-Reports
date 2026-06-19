"""P5 / Phase C6 — Entity consolidation / breakdown.

Returns any of the four statement kinds (pl|bs|wc|cf) BROKEN DOWN by legal entity
PLUS a consolidated total = the per-line, per-column SUM across entities.

The whole module reuses the EXISTING per-kind services verbatim — it never forks a
sign convention or a period bucket.  For each entity we call the kind's own builder
(``build_pl_statement`` / ``build_bs_statement`` / ``build_wc_statement`` /
``build_cf_statement``) with ``entity_prefix=<that entity>``, then the PURE core
``consolidate_statements`` sums the resulting cells column-by-column, line-by-line.

======================================================================= CONSOLIDATION FORMULA (pure, golden-tested)
For each statement line L and each period column C:

    consolidated(L, C) = Σ over entities e of  value_e(L, C)

where ``value_e(L, C)`` is the (already sign-presented) cell from entity e's own
statement for the SAME kind.  Because every per-entity service has already applied
its kind's sign convention (P&L _present = −amount; BS _present_bs by side), the
consolidated value is a straight additive roll-up — no per-entity sign juggling.

  Worked example (2 entities, kind=pl, FY column):
    Atlas    (entity 01) REVENUE presented = +3000 ; COGS = −500
    Meridian (entity 02) REVENUE presented = +2000 ; COGS = −300
    → consolidated REVENUE = 3000 + 2000 = 5000
    → consolidated COGS    = (−500) + (−300) = −800

  P&L SUBTOTAL/CALC lines (TOTAL_OUTPUT, GROSS_PROFIT, EBITDA, EBIT, EBT,
  NET_PROFIT, …) are NOT summed across entities either — summing per-entity
  subtotals would double-count their member mapping lines.  They are RECOMPUTED as
  the running cumulative sum of the consolidated MAPPING lines above them, exactly
  like statements.aggregate_pl.  Only *_PCT ratio lines use a ratio formula.  This
  guarantees consolidated NET_PROFIT == Σ entity NET_PROFIT == Σ consolidated
  mapping lines.

RATIO LINES ARE NOT SUMMED.  A sum of ratios (margin %, DSO days, …) is
meaningless.  For the consolidated column, ratio lines are RECOMPUTED from the
consolidated base lines using each kind's OWN pure recompute (no forked formula):
  • pl: GROSS_PROFIT = consREVENUE + consCOGS ; GROSS_MARGIN_PCT = GP/REV×100
        (None when consolidated REVENUE == 0) ; EBITDA = Σ consolidated mapping lines.
  • wc: NWC = consAR + consINV − consAP ; DSO/DIO/DPO/CCC recomputed via
        working_capital._ratio_days from the consolidated AR/INV/AP/REV/COGS.
  • bs/cf: every line is additive (no ratio lines), so the plain sum is correct;
        bs imbalance and cf tieout_residual are summed too (each entity ties out to
        0 ⇒ consolidated also 0 absent intercompany).

  INTERCOMPANY ELIMINATION IS OUT OF SCOPE (P10).  This roll-up is a GROSS sum:
  intercompany revenue/cost/AR/AP between the consolidated entities is NOT removed.
  This is documented on the response (``intercompany_eliminated = False``).

======================================================================= EDGE CASES
  • Entity with no GL rows in a period → that entity's cell is 0.0 (its service
    returns 0 for an empty window) → it contributes 0 to the consolidated sum.
  • Single entity → consolidated == that entity (Σ of one term).  Asserted in test.
  • Consolidated ratio with consolidated REVENUE/denominator == 0 → None (no
    div-by-zero), exactly like the single-entity services.
  • A cell value of None (undefined ratio) is treated as 0.0 ONLY for additive
    lines; ratio lines are recomputed, never summed, so None never propagates wrongly.
  • Entities are taken from dim_legal_entity (is_consolidation = FALSE) so the
    pre-existing consolidation pseudo-entity is never double-counted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.statements import StatementCell, StatementLine

# Line codes whose values are RATIOS (days / percentages) — never summed across
# entities; recomputed from consolidated base lines instead.
_WC_RATIO_CODES = {"DSO", "DIO", "DPO", "CCC"}
_WC_DERIVED_CODES = {"NWC", "DSO", "DIO", "DPO", "CCC"}
# NB: P&L subtotal/calc rows are NOT identified by a fixed code set anymore — they
# are driven by row_type/kpi_code (running cumulative sum, ratios via *_PCT), exactly
# like statements.aggregate_pl.  See consolidate_statements.


@dataclass
class EntityBlock:
    """One legal entity's full statement (the kind's normal line set)."""

    entity_prefix: str
    entity_name: str
    lines: list[StatementLine]


@dataclass
class ConsolidationResult:
    kind: str
    view_mode: str
    coverage: float
    column_keys: list[str]
    column_labels: dict[str, str]
    entities: list[EntityBlock]
    consolidated: list[StatementLine]
    intercompany_eliminated: bool  # always False here (P10) — documented


# --------------------------------------------------------------------------- #
# Pure cell helpers
# --------------------------------------------------------------------------- #
def _cell(line: StatementLine, column_key: str) -> Optional[float]:
    for c in line.cells:
        if c.column_key == column_key:
            return c.value
    return None


def _cell_in(lines: list[StatementLine], code: str, column_key: str) -> Optional[float]:
    for ln in lines:
        if ln.line_code == code:
            return _cell(ln, column_key)
    return None


def _sum_line_across(
    statements_lines: list[list[StatementLine]],
    code: str,
    column_keys: list[str],
) -> dict[str, float]:
    """Additive roll-up of one line code across entities: Σ value_e(code, col).

    None cells are treated as 0.0 (an additive line with no data contributes 0).
    """
    out: dict[str, float] = {}
    for col in column_keys:
        total = 0.0
        for lines in statements_lines:
            v = _cell_in(lines, code, col)
            if v is not None:
                total += v
        out[col] = total
    return out


# --------------------------------------------------------------------------- #
# Pure core
# --------------------------------------------------------------------------- #
def consolidate_statements(
    kind: str,
    per_entity: list[tuple[str, str, Any]],  # (entity_prefix, entity_name, Statement)
    column_keys: list[str],
    column_labels: dict[str, str],
    view_mode: str,
    coverage: float,
) -> ConsolidationResult:
    """Build the per-entity blocks + the consolidated total.  PURE — no DB.

    ``per_entity`` carries each entity's already-built statement (the kind's normal
    object with ``.lines``).  Additive lines are summed; ratio/derived lines are
    recomputed from the consolidated base lines so the consolidated column is
    internally consistent (e.g. consolidated margin is GP/REV of the consolidated
    figures, not an average of the per-entity margins).
    """
    entity_lines: list[list[StatementLine]] = [stmt.lines for (_, _, stmt) in per_entity]

    # Template = the line metadata (code/label/row_type/is_bold/kpi) of the FIRST
    # entity; all entities share the same structure for a given kind.
    template: list[StatementLine] = entity_lines[0] if entity_lines else []

    consolidated: list[StatementLine] = []
    # Pre-roll every line additively; ratios/derived get overwritten below.
    rolled: dict[str, dict[str, float]] = {
        ln.line_code: _sum_line_across(entity_lines, ln.line_code, column_keys)
        for ln in template
    }

    def _consolidated_value(code: str, col: str) -> Optional[float]:
        return rolled.get(code, {}).get(col)

    # For P&L, non-mapping rows are RUNNING cumulative sums of the consolidated
    # MAPPING lines above them (mirrors statements.aggregate_pl) — NOT the additive
    # roll-up of per-entity subtotals (that would double-count member lines).  Track
    # a per-column running sum of consolidated mapping lines as we walk the template.
    pl_running: dict[str, float] = {col: 0.0 for col in column_keys}
    # Map line_code → its consolidated value (for ratio recompute references).
    cons_emitted: dict[str, dict[str, Optional[float]]] = {col: {} for col in column_keys}

    def _is_pct(tmpl: StatementLine) -> bool:
        return bool(tmpl.kpi_code) and tmpl.kpi_code.upper().endswith("_PCT")

    for tmpl in template:
        code = tmpl.line_code
        cells: list[StatementCell] = []
        for col in column_keys:
            value: Optional[float]
            if kind == "pl":
                if tmpl.row_type == "mapping":
                    value = rolled[code][col]  # additive sum of entity mapping lines
                    pl_running[col] += value or 0.0
                elif tmpl.row_type in ("subtotal", "calc"):
                    if tmpl.row_type == "calc" and _is_pct(tmpl):
                        value = _recompute_pl_ratio(code, cons_emitted[col])
                    else:
                        value = pl_running[col]  # running cumulative sum
                else:
                    value = rolled[code][col]
            elif kind == "wc" and code in _WC_DERIVED_CODES:
                value = _recompute_wc(code, _consolidated_value, col)
            else:
                value = rolled[code][col]  # additive
            cons_emitted[col][code] = value
            cells.append(StatementCell(column_key=col, value=value))
        consolidated.append(
            StatementLine(
                line_code=code,
                label=tmpl.label,
                row_type=tmpl.row_type,
                is_bold=tmpl.is_bold,
                kpi_code=tmpl.kpi_code,
                cells=cells,
            )
        )

    entities = [
        EntityBlock(entity_prefix=pfx, entity_name=name, lines=stmt.lines)
        for (pfx, name, stmt) in per_entity
    ]

    return ConsolidationResult(
        kind=kind,
        view_mode=view_mode,
        coverage=coverage,
        column_keys=column_keys,
        column_labels=column_labels,
        entities=entities,
        consolidated=consolidated,
        intercompany_eliminated=False,
    )


def _recompute_pl_ratio(code: str, col_values: dict[str, Optional[float]]) -> Optional[float]:
    """Recompute a consolidated P&L *_PCT ratio from the consolidated base lines.

    Mirrors statements._compute_ratio EXACTLY (same operand resolution) so the
    consolidated ratio is GP/revenue of the consolidated figures, not an average of
    per-entity ratios.  Non-ratio subtotals/calcs are running sums (see
    consolidate_statements) — they never reach here.
    """
    from app.services.statements import _compute_ratio

    return _compute_ratio(code, col_values)


def _recompute_wc(code: str, cons: Any, col: str) -> Optional[float]:
    """Recompute the consolidated NWC line from the consolidated AR/INV/AP bases.

    NWC = AR + INVENTORY − AP (same formula as working_capital.compute_working_capital).
    The DSO/DIO/DPO/CCC ratio lines need days + revenue/COGS that the WC statement
    does NOT carry, so they are recomputed by the ORCHESTRATOR
    (``_apply_wc_consolidated_ratios``) which has the PL flows; the pure core leaves
    them as the (placeholder) additive sum, overwritten afterwards.
    """
    ar = cons("AR", col) or 0.0
    inv = cons("INVENTORY", col) or 0.0
    ap = cons("AP", col) or 0.0
    if code == "NWC":
        return ar + inv - ap
    # Ratio lines: leave as additive placeholder; orchestrator overwrites them.
    return cons(code, col)


# --------------------------------------------------------------------------- #
# DB helper: list the legal entities to consolidate
# --------------------------------------------------------------------------- #
def fetch_entities(session: Any) -> list[tuple[str, str]]:
    """Return [(entity_prefix, entity_name), …] for the NON-consolidation entities,
    ordered by prefix.  The pre-existing consolidation pseudo-entity (if any) is
    excluded so it is never double-counted.
    """
    from sqlalchemy import text

    rows = session.execute(
        text(
            "SELECT entity_prefix, entity_name FROM dim_legal_entity "
            "WHERE is_consolidation = FALSE ORDER BY entity_prefix"
        )
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


# --------------------------------------------------------------------------- #
# Orchestration (thin; reuses each kind's own builder per entity)
# --------------------------------------------------------------------------- #
def build_consolidation(
    session: Any,
    *,
    kind: str,
    view_mode: str,
    current_fy: int,
    last_closed_period: int,
    scenario: Optional[str] = None,
    fy_start_month: int = 1,
) -> ConsolidationResult:
    """Build the per-entity statements (one per legal entity) + the consolidated total.

    For each entity, the kind's OWN builder is called with that entity_prefix; the
    pure ``consolidate_statements`` then sums them.  ``kind`` ∈ {pl, bs, wc, cf}.
    """
    from app.services.balance_sheet import build_bs_statement
    from app.services.cash_flow import build_cf_statement
    from app.services.statements import build_pl_statement
    from app.services.working_capital import build_wc_statement

    builders = {
        "pl": build_pl_statement,
        "bs": build_bs_statement,
        "wc": build_wc_statement,
        "cf": build_cf_statement,
    }
    if kind not in builders:
        raise ValueError(f"unknown statement kind {kind!r}; expected pl|bs|wc|cf")
    builder = builders[kind]

    entities = fetch_entities(session)
    if not entities:
        raise ValueError("no legal entities found to consolidate")

    per_entity: list[tuple[str, str, Any]] = []
    column_keys: list[str] = []
    column_labels: dict[str, str] = {}
    view_mode_out = view_mode
    coverage_out = 0.0
    # WC needs the days/rev/cogs maps to recompute consolidated ratios; collect them.
    wc_days: list[dict[str, float]] = []
    wc_rev: list[dict[str, Optional[float]]] = []
    wc_cogs_mag: list[dict[str, Optional[float]]] = []

    for pfx, name in entities:
        stmt = builder(
            session,
            view_mode=view_mode,
            current_fy=current_fy,
            last_closed_period=last_closed_period,
            entity_prefix=pfx,
            scenario=scenario,
            fy_start_month=fy_start_month,
        )
        per_entity.append((pfx, name, stmt))
        column_keys = stmt.column_keys
        column_labels = stmt.column_labels
        view_mode_out = stmt.view_mode
        coverage_out = stmt.coverage
        if kind == "wc":
            wc_days.append(getattr(stmt, "days_in_period", {}) or {})
            # Re-fetch the entity's REV / COGS magnitude from a PL build so consolidated
            # DSO/DIO/DPO use the consolidated flows (not a sum of ratios).
            pl = build_pl_statement(
                session,
                view_mode=view_mode,
                current_fy=current_fy,
                last_closed_period=last_closed_period,
                entity_prefix=pfx,
                scenario=scenario,
                fy_start_month=fy_start_month,
            )
            rev_map: dict[str, Optional[float]] = {}
            cogs_map: dict[str, Optional[float]] = {}
            for col in column_keys:
                # Candidate codes: real seeded P&L codes first, then synthetic-fixture
                # codes — same resolution as working_capital.compute_working_capital.
                rev_map[col] = (
                    _cell_in(pl.lines, "NET_SALES", col)
                    if _cell_in(pl.lines, "NET_SALES", col) is not None
                    else _cell_in(pl.lines, "REVENUE", col)
                )
                c_pres = _cell_in(pl.lines, "COST_OF_MATERIALS", col)
                if c_pres is None:
                    c_pres = _cell_in(pl.lines, "COGS", col)
                cogs_map[col] = (-(c_pres)) if c_pres is not None else None
            wc_rev.append(rev_map)
            wc_cogs_mag.append(cogs_map)

    result = consolidate_statements(
        kind,
        per_entity,
        column_keys,
        column_labels,
        view_mode_out,
        coverage_out,
    )

    # For WC, the ratio recompute in the pure core needs consolidated days/rev/cogs;
    # inject them by recomputing the WC derived lines here with the gathered maps.
    if kind == "wc":
        cons_days = {c: (wc_days[0].get(c, 0.0) if wc_days else 0.0) for c in column_keys}
        cons_rev = {
            c: sum((m.get(c) or 0.0) for m in wc_rev) if wc_rev else None
            for c in column_keys
        }
        cons_cogs = {
            c: sum((m.get(c) or 0.0) for m in wc_cogs_mag) if wc_cogs_mag else None
            for c in column_keys
        }
        _apply_wc_consolidated_ratios(result, cons_days, cons_rev, cons_cogs)

    return result


def _apply_wc_consolidated_ratios(
    result: ConsolidationResult,
    days: dict[str, float],
    rev: dict[str, Optional[float]],
    cogs_mag: dict[str, Optional[float]],
) -> None:
    """Overwrite the consolidated WC ratio cells with values recomputed from the
    consolidated AR/INV/AP base lines + consolidated days/rev/cogs (not a sum of
    per-entity ratios).  Mutates ``result.consolidated`` in place.
    """
    from app.services.working_capital import _ratio_days

    def base(code: str, col: str) -> float:
        for ln in result.consolidated:
            if ln.line_code == code:
                v = _cell(ln, col)
                return v if v is not None else 0.0
        return 0.0

    for ln in result.consolidated:
        if ln.line_code not in _WC_RATIO_CODES:
            continue
        for cell in ln.cells:
            col = cell.column_key
            d = days.get(col, 0.0)
            ar = base("AR", col)
            inv = base("INVENTORY", col)
            ap = base("AP", col)
            r = rev.get(col)
            cm = cogs_mag.get(col)
            if ln.line_code == "DSO":
                cell.value = _ratio_days(ar, r, d)
            elif ln.line_code == "DIO":
                cell.value = _ratio_days(inv, cm, d)
            elif ln.line_code == "DPO":
                cell.value = _ratio_days(ap, cm, d)
            elif ln.line_code == "CCC":
                dso = _ratio_days(ar, r, d)
                dio = _ratio_days(inv, cm, d)
                dpo = _ratio_days(ap, cm, d)
                cell.value = (
                    None if (dso is None or dio is None or dpo is None) else dso + dio - dpo
                )
