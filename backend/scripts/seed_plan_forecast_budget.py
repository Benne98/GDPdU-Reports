r"""Seed the plan / forecast / budget layer into the DB (IDEMPOTENT).

Lights up the Forecast + Coverage columns of the two-view statements by
materialising BOTH plan grains from the ACTUALS already loaded in the target DB:

  1. fact_gl_plan (+ fact_sales_plan, fact_com_plan) — account / customer /
     supplier grain, scenarios ``forecast`` and ``plan``.  Drives P&L coverage
     (``fin_compat_pl._load_plan_map`` budget→forecast→plan fallback) and the
     annual ``fy_f`` forecast (``_apply_annual_fy_forecast`` needs ``ytg`` from
     these rows).  Produced by the PURE, deterministic pipeline
     ``etl.plan_synth.generate_plan`` and UPSERTed by ``etl.load.load_plan``
     (``ON CONFLICT DO UPDATE``).  Actuals are read with the SAME queries the
     ``POST /api/v1/plan/generate`` router uses (``app.routers.plan._fetch_*``).

  2. fact_position_plan (scenario ``budget``) — position grain.  REQUIRED for
     BS / WC / CF two-view coverage because ``load_position_plan_map`` reads ONLY
     this table.  Materialised per entity + statement (PL, BS) via
     ``app.services.budget_service.seed_budget`` (idempotent ``_upsert_rows``
     ON CONFLICT; honours the SKIP-ZERO rule and the ``present_to_stored`` sign
     flip).  The consolidated view is the Σ of the per-entity rows
     (``position_plan_grain_sql``), so seeding every entity lights up both the
     per-entity and the consolidated scope.

Fiscal year(s) and entities are DISCOVERED from the actuals in the target DB
(``fact_gl_line`` × ``fact_gl_entry`` and ``dim_legal_entity``), so nothing is
hardcoded.  Safe to re-run — every write path is an ON CONFLICT upsert.

USAGE (PowerShell — password supplied via env, NEVER stored here):
  $env:DB_NAME='finssentials_v2'; $env:DB_PASSWORD='<secret>'
  backend\.venv\Scripts\python.exe backend\scripts\seed_plan_forecast_budget.py

OPTIONS:
  --year <FY>            budget fiscal year (default: the discovered forecast year)
  --entities 01,02       comma list of entity_prefixes to budget (default: all real)
  --skip-gl-plan         skip the fact_gl_plan/sales/com plan+forecast generation
  --skip-budget          skip the fact_position_plan budget seed
  --top-n 20             Top-N partners per partner-driven position (budget seed)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# backend/ on the path for ``app.*`` imports; repo root for ``etl.*`` imports
# (etl/ lives one level above backend/ — same order plan.py uses).
_BACKEND = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND.parent
for _p in (str(_BACKEND), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlalchemy import text
from sqlalchemy.orm import Session as SASession

from app.db import engine
from app.routers.plan import (
    _fetch_com_actuals,
    _fetch_gl_actuals,
    _fetch_sales_actuals,
)
from app.services import budget_service
from app.services import forecast_engine
from app.services.fin_compat_cf import _norm_cf_key
from etl.load import load_plan
from etl.plan_synth import generate_plan

_BUDGET_STATEMENTS = ("PL", "BS", "CF", "WC")
_HORIZON_YEARS = 4
_GROWTH_RATE = 0.05
_FORECAST_GROWTH_RATE = 0.0
_BUDGET_GROWTH_PCT = 0.05
_SEED_ACTOR = "seed_plan_forecast_budget.py"

# Forecast band (scenario='forecast') — position-grain, per the LOCKED methodology
# in app.services.forecast_engine.  Days basis for the DSO/DPO/DIO activity ratios.
_FORECAST_DAYS = 360.0
_FORECAST_GROWTH_G = 0.0
# The reporting positions that play the driver / retained-earnings / cash roles in the
# BS roll-forward (line_codes in dim_pl_structure — confirmed against finssentials_v2).
_BS_ROLES = {"AR": "AR", "INV": "INVENTORY", "AP": "AP", "RE": "EQUITY", "CASH": "CASH"}
# The equity line that absorbs the P7 balancing residual (the not-yet-GL-booked YTD
# net profit in report_inject mode) so the starting BS balances and the CF cash tie
# holds.  It is carried FLAT thereafter (NI_f rolls into RE).
_BS_NET_PROFIT_CODE = "BS_NET_PROFIT"
# Partner-driven PL positions whose level_3 seeds revenue / COGS.
_PL_REVENUE_CODE = "NET_SALES"
_PL_COGS_CODE = "COST_OF_MATERIALS"


# --------------------------------------------------------------------------- #
# Discovery — everything derived from the actuals present in the DB
# --------------------------------------------------------------------------- #
def discover_fiscal_anchor(session: SASession) -> tuple[int, int, int]:
    """Return ``(base_fy, current_fy, last_closed_period)`` from GL actuals.

    * ``current_fy`` = the latest year with actuals; if that year is only
      partially booked (max period < 12) it is the in-progress forecast year and
      ``last_closed_period`` = its max booked period.  If the latest year is fully
      booked (period 12 present) there is no in-progress year, so ``current_fy`` is
      rolled forward by one and ``last_closed_period`` = 0 (the whole year is open
      → forecast covers all 12 periods).
    * ``base_fy`` = the last FULLY booked year (period 12 present) at or below
      ``current_fy - 1``; falls back to ``current_fy - 1``.
    """
    rows = session.execute(
        text(
            "SELECT l.fiscal_year AS fy, MAX(e.fiscal_period) AS maxp "
            "FROM fact_gl_line l "
            "JOIN fact_gl_entry e "
            "  ON e.journal_entry_group_number = l.journal_entry_group_number "
            " AND e.fiscal_year = l.fiscal_year "
            "WHERE e.fiscal_period BETWEEN 1 AND 12 "
            "GROUP BY l.fiscal_year ORDER BY l.fiscal_year"
        )
    ).fetchall()
    if not rows:
        raise SystemExit(
            "No GL actuals found (fact_gl_line × fact_gl_entry). Load GL data first."
        )
    max_by_fy = {int(r[0]): int(r[1]) for r in rows}
    latest = max(max_by_fy)
    full_years = [fy for fy, mp in max_by_fy.items() if mp >= 12]

    if max_by_fy[latest] >= 12:
        current_fy = latest + 1
        last_closed_period = 0
    else:
        current_fy = latest
        last_closed_period = max_by_fy[latest]

    candidate_base = [fy for fy in full_years if fy <= current_fy - 1]
    base_fy = max(candidate_base) if candidate_base else current_fy - 1
    return base_fy, current_fy, last_closed_period


def discover_entity_prefixes(session: SASession) -> list[str]:
    """Real (non-consolidation) entity prefixes present in ``dim_legal_entity``."""
    rows = session.execute(
        text(
            "SELECT DISTINCT entity_prefix FROM dim_legal_entity "
            "WHERE COALESCE(is_consolidation, FALSE) = FALSE "
            "  AND entity_prefix IS NOT NULL AND entity_prefix <> '' "
            "ORDER BY entity_prefix"
        )
    ).fetchall()
    return [str(r[0]).strip()[:2] for r in rows if r and r[0] is not None]


# --------------------------------------------------------------------------- #
# Seeders
# --------------------------------------------------------------------------- #
def seed_gl_plan(session: SASession, base_fy: int, current_fy: int, last_closed_period: int) -> dict[str, int]:
    """Read actuals, generate forecast+plan, UPSERT into fact_gl/sales/com_plan."""
    gl_actuals = _fetch_gl_actuals(session, base_fy, None)
    if gl_actuals.empty:
        raise SystemExit(f"No GL actuals for base_fy={base_fy} or surrounding years.")
    sales_actuals = _fetch_sales_actuals(session, base_fy)
    com_actuals = _fetch_com_actuals(session, base_fy)

    gl_plan_df, sales_plan_df, com_plan_df = generate_plan(
        gl_actuals,
        sales_actuals,
        com_actuals,
        base_fy=base_fy,
        current_fy=current_fy,
        last_closed_period=last_closed_period,
        horizon_years=_HORIZON_YEARS,
        growth_rate=_GROWTH_RATE,
        forecast_growth_rate=_FORECAST_GROWTH_RATE,
    )
    # load_plan commits (and rolls back on error) itself, mirroring the router.
    return load_plan(session, gl_plan_df, sales_plan_df, com_plan_df)


def seed_position_budget(
    session: SASession, fiscal_year: int, entity_prefixes: list[str], top_n: int
) -> dict[str, int]:
    """Seed the synthetic budget per entity × (PL, BS) into fact_position_plan.

    ``seed_budget`` commits per call (idempotent ON CONFLICT upsert)."""
    seeded: dict[str, int] = {}
    for ep in entity_prefixes:
        for stmt in _BUDGET_STATEMENTS:
            # materialize_suggestion=True writes the prior-year 'suggestion' profile
            # (full base-year seasonality × growth) for ALL 12 months, so forecast
            # periods P8-12 are non-zero (the legacy actual-profile seed left them 0
            # because the forecast year is only booked through last_closed_period).
            try:
                res = budget_service.seed_budget(
                    session,
                    statement=stmt,
                    fiscal_year=fiscal_year,
                    entity=ep,
                    top_n=top_n,
                    updated_by=_SEED_ACTOR,
                    materialize_suggestion=True,
                    heuristic="prior_year",
                    growth_pct=_BUDGET_GROWTH_PCT,
                )
                seeded[f"{ep}/{stmt}"] = int(res.get("rows_seeded", 0))
            except Exception as e:  # noqa: BLE001 — a statement without a structure (e.g. CF/WC) must not abort the rest
                session.rollback()
                seeded[f"{ep}/{stmt}"] = -1
                print(f"            {ep}/{stmt:2s} SKIPPED: {str(e)[:90]}")
    return seeded


# --------------------------------------------------------------------------- #
# Forecast band (scenario='forecast') — position-grain PL / BS / CF
# --------------------------------------------------------------------------- #
def _structure_maps(session: SASession, statement: str) -> tuple[dict[str, str], dict[str, str]]:
    """(l3_to_code, code_to_side) for a statement's mapping positions.

    ``l3_to_code`` : {level_3 label -> line_code} to aggregate GL actuals by level_3
                     and key them back onto the reporting position (mirrors how
                     budget_service._load_positions aligns the grid to the structure).
    ``code_to_side``: {line_code -> 'asset'|'le'} for BS from kpi_code
                      ('BS:asset'->asset, 'BS:credit'->le); '' for non-BS.
    """
    if statement == "BS":
        where = "kpi_code LIKE 'BS:%'"
    else:
        where = "(kpi_code IS NULL OR kpi_code NOT LIKE 'BS:%') AND line_code NOT LIKE 'CF%'"
    rows = session.execute(text(
        f"SELECT line_code, COALESCE(TRIM(level_3),''), COALESCE(kpi_code,'') "
        f"FROM dim_pl_structure WHERE row_type='mapping' AND {where}"
    )).fetchall()
    l3_to_code: dict[str, str] = {}
    code_to_side: dict[str, str] = {}
    for lc, l3, kpi in rows:
        lc = str(lc)
        if l3:
            l3_to_code[str(l3)] = lc
        code_to_side[lc] = "le" if str(kpi).startswith("BS:credit") else "asset"
    return l3_to_code, code_to_side


def _pl_monthly_presented(
    session: SASession, ep: str, fy: int, l3_to_code: dict[str, str]
) -> dict[str, dict[int, float]]:
    """{line_code: {period(1..12): PRESENTED movement}} of PL actuals for (ep, fy).

    PL presentation is the single ``amount * -1`` flip (income +, expense −); we route
    it through the sanctioned ``budget_service.stored_to_present`` helper (no new sign
    literal).  Keyed by level_3 -> reporting-position line_code.
    """
    rows = session.execute(text(
        "SELECT TRIM(a.level_3) AS l3, e.fiscal_period AS p, SUM(l.amount) AS amt "
        "FROM fact_gl_line l "
        "JOIN fact_gl_entry e ON e.journal_entry_group_number = l.journal_entry_group_number "
        " AND e.fiscal_year = l.fiscal_year "
        "JOIN dim_gl_account a ON a.account_number_group = l.account_number_group "
        " AND a.fiscal_year = l.fiscal_year "
        "WHERE a.level_0 = 'PL' AND e.fiscal_year = :fy AND l.entity_prefix = :ep "
        "  AND e.fiscal_period BETWEEN 1 AND 12 "
        "GROUP BY TRIM(a.level_3), e.fiscal_period"
    ), {"fy": fy, "ep": ep}).fetchall()
    out: dict[str, dict[int, float]] = {}
    for l3, p, amt in rows:
        code = l3_to_code.get(str(l3 or "").strip())
        if not code:
            continue
        pres = budget_service.stored_to_present(float(amt or 0.0), "PL", code)
        out.setdefault(code, {})[int(p)] = pres
    return out


def _bs_closing_natural(
    session: SASession, ep: str, fy: int, cutoff_period: int,
    l3_to_code: dict[str, str], code_to_side: dict[str, str],
) -> dict[str, float]:
    """{line_code: NATURAL closing balance} at end of ``cutoff_period`` in ``fy``.

    FY-scoped stock (mirrors fin_compat_bs_sql._bal_amount_expr_fy): the Jan-1
    opening_balance (fiscal_period=0) of ``fy`` PLUS all non-OB movements p1..cutoff.
    Converted from the RAW stored sign (+debit assets / −credit L&E) to NATURAL
    magnitudes: asset -> +stored, L&E -> −stored (from the structure's asset/credit
    side).  So both assets and L&E come out as positive book values.
    """
    rows = session.execute(text(
        "SELECT TRIM(a.level_3) AS l3, SUM(l.amount) AS amt "
        "FROM fact_gl_line l "
        "JOIN fact_gl_entry e ON e.journal_entry_group_number = l.journal_entry_group_number "
        " AND e.fiscal_year = l.fiscal_year "
        "JOIN dim_gl_account a ON a.account_number_group = l.account_number_group "
        " AND a.fiscal_year = l.fiscal_year "
        "WHERE a.level_0 = 'BS' AND l.entity_prefix = :ep AND l.fiscal_year = :fy "
        "  AND ( (e.entry_type = 'opening_balance' AND e.fiscal_period = 0) "
        "        OR (COALESCE(e.entry_type,'') <> 'opening_balance' "
        "            AND e.fiscal_period BETWEEN 1 AND :cut) ) "
        "GROUP BY TRIM(a.level_3)"
    ), {"ep": ep, "fy": fy, "cut": cutoff_period}).fetchall()
    out: dict[str, float] = {}
    for l3, amt in rows:
        code = l3_to_code.get(str(l3 or "").strip())
        if not code:
            continue
        raw = float(amt or 0.0)
        out[code] = raw if code_to_side.get(code) == "asset" else -raw
    return out


def _cf_leaf_map(session: SASession) -> dict[str, str]:
    """{normalised CF leaf label -> CF structure line_code} for the CF mapping rows."""
    rows = session.execute(text(
        "SELECT line_code, COALESCE(balance_title,'') FROM dim_pl_structure "
        "WHERE row_type = 'mapping' AND line_code LIKE 'CF%'"
    )).fetchall()
    return {_norm_cf_key(bt): str(lc) for lc, bt in rows if str(bt).strip()}


def _bs_feed_value(nat: float, side: str, line_code: str) -> float:
    """Value to pass to ``present_to_stored(v,'BS',line_code)`` so the STORED sign is the
    economic GL sign (asset +debit / L&E −credit) for a NATURAL magnitude ``nat``.

    ``present_to_stored`` only treats the supplier/AP position as 'credit' (single
    flip); every other BS line it treats as 'asset' (no flip).  So to land the correct
    stored sign we pre-shape the fed value using the line's true asset/credit side:
      * present_to_stored will negate (AP) -> feed +nat  (stored = −nat, a credit).
      * present_to_stored passes through (asset-treated) -> feed +nat for an asset,
        −nat for an L&E line (stored = −nat, a credit).
    The reader (load_position_plan_map, BS) re-presents via stored_to_present with the
    SAME helper, so the forecast round-trips in exactly the budget's presented
    convention (assets +, AP +, other credits −); no sign convention is changed.
    """
    if budget_service._bs_side_for(line_code) == "credit":  # the AP position
        return nat
    return nat if side == "asset" else -nat


def _forecast_rows(
    statement: str, line_code: str, ep: str, fy: int,
    periods_presented: dict[int, float], *, partner_kind=None,
) -> list[dict]:
    """Stored-sign fact_position_plan row dicts for the given periods.  The value in
    ``periods_presented`` is fed through the sanctioned ``present_to_stored`` once."""
    rows: list[dict] = []
    for p, pv in periods_presented.items():
        rows.append({
            "statement": statement, "line_code": line_code, "entity_prefix": ep,
            "partner_id": "", "level_4": "", "partner_kind": partner_kind,
            "fiscal_year": fy, "fiscal_period": p,
            "amount": budget_service.present_to_stored(float(pv), statement, line_code),
        })
    return rows


def seed_position_forecast(
    session: SASession, base_fy: int, forecast_fy: int, last_closed_period: int,
    entity_prefixes: list[str],
) -> dict[str, int]:
    """Materialise the scenario='forecast' band into fact_position_plan for PL / BS / CF
    per entity, via the pure calculators in ``app.services.forecast_engine``.

    Per entity:
      1. Read base-FY monthly PL actuals + current-FY YTD PL actuals (presented) and the
         BS P7 closing + base-FY BS Dec closing (natural).
      2. PL forecast: seasonal open-period plan (P8-12, 5 distinct base months, g=0).
      3. Derive drivers DSO/DPO/DIO from the base-FY BS Dec closing + base-FY revenue /
         COGS.  BS roll-forward P7->P12 with a balancing cash plug.  (The P7 residual —
         the unbooked YTD net profit in report_inject mode — is absorbed into the Net
         profit equity line so the starting BS balances and the CF cash tie holds.)
      4. CF indirect bridge; seed only the leaves that resolve to a CF structure row.
      5. assert_cross_ties, then present_to_stored on write.  Idempotent (ON CONFLICT).

    Consolidated ('') rows are NOT written — the consolidated view is Σ per-entity
    (position_plan_grain_sql), so seeding every entity lights up both scopes.
    """
    L = last_closed_period if last_closed_period and last_closed_period > 0 else 7
    open_periods = [p for p in range(1, 13) if p > L]

    pl_l3_to_code, _ = _structure_maps(session, "PL")
    bs_l3_to_code, bs_side = _structure_maps(session, "BS")
    cf_leaf_map = _cf_leaf_map(session)

    seeded: dict[str, int] = {}
    for ep in entity_prefixes:
        rows_all: list[dict] = []

        # ---- 1. reads -------------------------------------------------------
        base_pl = _pl_monthly_presented(session, ep, base_fy, pl_l3_to_code)
        ytd_pl = _pl_monthly_presented(session, ep, forecast_fy, pl_l3_to_code)
        bs_p7 = _bs_closing_natural(session, ep, forecast_fy, L, bs_l3_to_code, bs_side)
        bs_dec_base = _bs_closing_natural(session, ep, base_fy, 12, bs_l3_to_code, bs_side)

        # ---- 2. PL seasonal open-period forecast (presented) ---------------
        pl_plan = forecast_engine.forecast_pl_seasonal(base_pl, ytd_pl, L=L, g=_FORECAST_GROWTH_G)
        fy_f = forecast_engine.fy_forecast_by_line(base_pl, ytd_pl, L=L, g=_FORECAST_GROWTH_G)
        for code, months in pl_plan.items():
            rows_all += _forecast_rows("PL", code, ep, forecast_fy, months)

        revenue_f = float(fy_f.get(_PL_REVENUE_CODE, 0.0))
        cogs_f = -float(fy_f.get(_PL_COGS_CODE, 0.0))          # positive magnitude
        ni_f = float(sum(fy_f.values()))                       # net income (income +)

        # ---- 3. drivers + BS roll-forward (natural) ------------------------
        revenue_base = float(sum((base_pl.get(_PL_REVENUE_CODE) or {}).values()))
        cogs_base = -float(sum((base_pl.get(_PL_COGS_CODE) or {}).values()))
        ar_base = float(bs_dec_base.get(_BS_ROLES["AR"], 0.0))
        inv_base = float(bs_dec_base.get(_BS_ROLES["INV"], 0.0))
        ap_base = float(bs_dec_base.get(_BS_ROLES["AP"], 0.0))
        drivers = {
            "DSO": (ar_base / revenue_base * _FORECAST_DAYS) if abs(revenue_base) > 1e-6 else 0.0,
            "DIO": (inv_base / cogs_base * _FORECAST_DAYS) if abs(cogs_base) > 1e-6 else 0.0,
            "DPO": (ap_base / cogs_base * _FORECAST_DAYS) if abs(cogs_base) > 1e-6 else 0.0,
        }

        # Absorb the P7 balancing residual (unbooked YTD net profit) into the Net-profit
        # equity line so the starting BS balances -> the CF cash tie holds.
        for code in bs_side:
            bs_p7.setdefault(code, 0.0)
        assets_p7 = sum(v for c, v in bs_p7.items() if bs_side.get(c) == "asset")
        le_p7 = sum(v for c, v in bs_p7.items() if bs_side.get(c) == "le")
        bs_p7[_BS_NET_PROFIT_CODE] = bs_p7.get(_BS_NET_PROFIT_CODE, 0.0) + (assets_p7 - le_p7)

        pl_forecast = {"revenue_f": revenue_f, "COGS_f": cogs_f, "NI_f": ni_f}
        roll = forecast_engine.forecast_bs_rollforward(
            bs_p7, bs_side, _BS_ROLES, drivers, pl_forecast, days=_FORECAST_DAYS
        )
        closing = roll["closing"]
        path = roll["path"]

        # ---- 4. CF indirect bridge -----------------------------------------
        cf = forecast_engine.forecast_cf_indirect(
            ar7=bs_p7[_BS_ROLES["AR"]], inv7=bs_p7[_BS_ROLES["INV"]], ap7=bs_p7[_BS_ROLES["AP"]],
            ar12=closing[_BS_ROLES["AR"]], inv12=closing[_BS_ROLES["INV"]], ap12=closing[_BS_ROLES["AP"]],
            ni_f=ni_f, cash7=bs_p7[_BS_ROLES["CASH"]], da=0.0,
        )

        # ---- 5. cross-ties (full precision, before rounding on write) ------
        assets_p12 = sum(v for c, v in closing.items() if bs_side.get(c) == "asset")
        le_p12 = sum(v for c, v in closing.items() if bs_side.get(c) == "le")
        forecast_engine.assert_cross_ties(
            re_p7=bs_p7[_BS_ROLES["RE"]], re_p12=closing[_BS_ROLES["RE"]], ni_f=ni_f,
            cf_ending_cash=cf["Cash at end"], bs_cash_p12=closing[_BS_ROLES["CASH"]],
            assets_p12=assets_p12, le_p12=le_p12,
        )

        # ---- BS path rows (P8-12 natural -> feed -> present_to_stored) -----
        for code, per in path.items():
            side = bs_side.get(code, "asset")
            months_fed = {p: _bs_feed_value(per[p], side, code) for p in open_periods}
            rows_all += _forecast_rows("BS", code, ep, forecast_fy, months_fed)

        # ---- CF leaf rows (annual presented spread uniformly over P8-12) ---
        n_open = len(open_periods) or 1
        for semantic, label in forecast_engine.CF_SEMANTIC_LEAF_LABELS.items():
            leaf_code = cf_leaf_map.get(_norm_cf_key(label))
            if not leaf_code:
                continue
            per_month = float(cf.get(semantic, 0.0)) / n_open
            months = {p: per_month for p in open_periods}
            rows_all += _forecast_rows("CF", leaf_code, ep, forecast_fy, months)

        # ---- write (idempotent UPSERT into the 'forecast' band) ------------
        try:
            n = budget_service._upsert_rows(
                session, rows_all, updated_by=_SEED_ACTOR, is_synthetic=True,
                scenario="forecast",
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        seeded[ep] = int(n)
    return seeded


def _forecast_period_report(session: SASession, fiscal_year: int) -> None:
    """Per-statement/period row-count + Σ(amount) for scenario='forecast' (acceptance)."""
    rows = session.execute(text(
        "SELECT statement, fiscal_period, COUNT(*) AS n, "
        "       SUM(amount) AS s, SUM(ABS(amount)) AS sa, "
        "       COUNT(*) FILTER (WHERE ABS(amount) > 1e-6) AS nz "
        "FROM fact_position_plan WHERE scenario='forecast' AND fiscal_year=:fy "
        "GROUP BY statement, fiscal_period ORDER BY statement, fiscal_period"
    ), {"fy": fiscal_year}).fetchall()
    print(f"\n=== scenario='forecast' rows by statement/period (fy={fiscal_year}) ===")
    print(f"  (BS SUM(amount)==0 is the balance identity A==L+E in stored sign;")
    print(f"   SUM(ABS) / non-zero counts show the band is populated with real values)")
    print(f"  {'stmt':4} {'period':>6} {'count':>7} {'sum(amount)':>18} {'sum(abs)':>18} {'nonzero':>8}")
    for st, p, n, s, sa, nz in rows:
        print(f"  {str(st):4} {int(p):>6} {int(n):>7} {float(s or 0.0):>18.2f} "
              f"{float(sa or 0.0):>18.2f} {int(nz or 0):>8}")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _scenario_counts(session: SASession, table: str) -> list[tuple[str, int]]:
    try:
        rows = session.execute(
            text(f"SELECT scenario, COUNT(*) FROM {table} GROUP BY scenario ORDER BY scenario")
        ).fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]
    except Exception:  # noqa: BLE001 — table may be absent on a lean profile
        session.rollback()
        return []


def print_summary(session: SASession) -> None:
    print("\n=== fact table row counts by scenario ===")
    for table in ("fact_gl_plan", "fact_sales_plan", "fact_com_plan", "fact_position_plan"):
        counts = _scenario_counts(session, table)
        if not counts:
            print(f"  {table:22s} (none / absent)")
            continue
        for sc, n in counts:
            print(f"  {table:22s} {sc:10s} {n:>10d}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Seed plan/forecast/budget layer (idempotent).")
    parser.add_argument("--year", type=int, default=None,
                        help="budget fiscal year (default: discovered forecast year)")
    parser.add_argument("--entities", type=str, default=None,
                        help="comma list of entity_prefixes to budget (default: all real)")
    parser.add_argument("--skip-gl-plan", action="store_true")
    parser.add_argument("--skip-budget", action="store_true")
    parser.add_argument("--skip-forecast", action="store_true",
                        help="skip the fact_position_plan scenario='forecast' band seed")
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()

    with SASession(engine) as session:
        base_fy, current_fy, last_closed_period = discover_fiscal_anchor(session)
        budget_year = args.year if args.year is not None else current_fy
        print(
            f"Discovered anchor: base_fy={base_fy}  current_fy(forecast)={current_fy}  "
            f"last_closed_period={last_closed_period}"
        )
        print(f"Budget fiscal year: {budget_year}")

        # ------------------------------------------------- GL plan / forecast
        if args.skip_gl_plan:
            print("\n[gl-plan] skipped (--skip-gl-plan)")
        else:
            counts = seed_gl_plan(session, base_fy, current_fy, last_closed_period)
            print(
                f"\n[gl-plan] upserted: gl_plan={counts.get('gl_plan', 0)} "
                f"sales_plan={counts.get('sales_plan', 0)} com_plan={counts.get('com_plan', 0)}"
            )

        # ------------------------------------------------- position budget
        if args.skip_budget:
            print("[budget]  skipped (--skip-budget)")
        else:
            if args.entities:
                entity_prefixes = [e.strip()[:2] for e in args.entities.split(",") if e.strip()]
            else:
                entity_prefixes = discover_entity_prefixes(session)
            if not entity_prefixes:
                print("[budget]  no real entities found — nothing to seed")
            else:
                print(f"[budget]  entities: {', '.join(entity_prefixes)}  statements: {', '.join(_BUDGET_STATEMENTS)}")
                seeded = seed_position_budget(session, budget_year, entity_prefixes, args.top_n)
                total = sum(seeded.values())
                print(f"[budget]  position rows seeded: {total}")
                for scope, n in seeded.items():
                    print(f"            {scope:10s} {n:>8d}")

        # ------------------------------------------------- position forecast band
        if args.skip_forecast:
            print("[forecast] skipped (--skip-forecast)")
        else:
            if args.entities:
                fc_prefixes = [e.strip()[:2] for e in args.entities.split(",") if e.strip()]
            else:
                fc_prefixes = discover_entity_prefixes(session)
            if not fc_prefixes:
                print("[forecast] no real entities found — nothing to seed")
            else:
                print(f"[forecast] entities: {', '.join(fc_prefixes)}  statements: PL, BS, CF")
                fseeded = seed_position_forecast(
                    session, base_fy, current_fy, last_closed_period, fc_prefixes
                )
                print(f"[forecast] forecast rows seeded: {sum(fseeded.values())}")
                for scope, n in fseeded.items():
                    print(f"            {scope:10s} {n:>8d}")
                _forecast_period_report(session, current_fy)

        print_summary(session)

    return 0


if __name__ == "__main__":
    sys.exit(main())
