"""Derive databook Master sheets (Master_BS / Master_PL) from loaded GDPdU GL data.

This is the "pipeline data" path: instead of re-running ``SuSabyYear`` on uploaded
SuSa workbooks, we **reshape** the already-tested trial-balance services
(:func:`build_pl_trial_balance` / :func:`build_bs_trial_balance`) directly into the
pivoted Master format that ``SuSabyYear.build_final_output`` would otherwise produce.

Why reshape (Option B) and NOT ``build_final_output``:
  ``build_final_output`` consumes *raw long-format* with its own presentation sign
  flip (``amount * -1`` for PL), EB column and net-income injection. The trial-balance
  services already emit *presented* values (PL revenue +, expense −) and *cumulative*
  BS closing balances. Feeding those into ``build_final_output`` would flip PL signs a
  second time. So we treat ``SuSabyYear`` only as the FORMAT REFERENCE.

Sign convention (NOT re-flipped here — inherited from the TB services):
  PL — presented flow: revenue +, expense −  (``build_pl_trial_balance``).
  BS — cumulative month-end closing balance, raw stored sign (``build_bs_trial_balance``).
  Net income BS equity row — sum of presented PL movements (revenue +, expense −),
  matching SuSabyYear ``build_net_income_bs_rows``.

L-hierarchy mapping (DB ``dim_gl_account.level_*`` → Master ``L*`` columns):
  Both PL & BS:  ``L1 - BS/PL`` = "PL"/"BS"  (constant per statement)
                 ``L2`` = DB ``level_2``  (PL: Income/Expense; BS: Assets/E&L)
                 ``L3`` = DB ``level_3``  (Position)
                 ``L4`` = DB ``level_4``  (Item)
                 ``L5`` = ""              (synthesized; no DB source)
                 ``L6`` = "Reported"      (synthesized; no DB source)
  BS only:       ``NA`` = DB ``level_1``  (the Assets / Equity & liabilities split;
                                           no Master L-slot — informational, recon
                                           does not key on NA)
  Recon keys: PL recon matches on (L3, L4); BS recon matches on (L2, L3, L4)
  (see ``recon_mapping_loader.py``), so the mapping above keeps recon/lead non-empty.

Account column: the BARE ``gl_account_id`` (NOT the "gid | name" ``account`` field
from the TB rows) so the numeric sort in the Master succeeds.

EB column: omitted on purpose. BS values are already cumulative month-end balances,
so there is no separate opening-balance (EB-{year}) period. Verified: no downstream
script (Consolidation / Adjustments / Recon / Lead) references an EB column — the EB
label exists only inside ``SuSabyYear.py``. The recon period regexes
(``^FY\\d{2}A$`` for FY, ``^[A-Za-z]{3}-\\d{4}$`` for months) do not match ``EB-...``,
so even if present it would be filtered out downstream.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.services.fin_compat_trial_balance import (
    build_bs_trial_balance,
    build_pl_trial_balance,
)

# SuSabyYear + susa_column_mapping live at repo root (same as the FDD script cwd).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from susa_column_mapping import EN_MONTH_ABBR  # noqa: E402

# Master column layout — mirrors SuSabyYear META_COLS_* (format reference).
_META_COLS_BASE = [
    "Entity",
    "Account",
    "Account description",
    "L1 - BS/PL",
    "L2",
    "L3",
    "L4",
    "L5",
    "L6",
]
META_COLS_PL = _META_COLS_BASE + ["Comments", "Q&A", "Answer of Target"]
META_COLS_BS = _META_COLS_BASE + ["NA", "Comments", "Q&A", "Answer of Target"]

# Net-income equity row — verbatim labels the recon/lead taxonomy matches on
# (SuSabyYear.build_net_income_bs_rows).
NET_INCOME_ACCOUNT = "2869"
NET_INCOME_DESCRIPTION = "Jahresüberschuss/Jahresfehlbetrag"
NET_INCOME_L2 = "Equity"
NET_INCOME_L3 = "Net retained profits"
NET_INCOME_L4 = "Net income"


def _fy_master_label(year: int) -> str:
    """DB-year → Master FY column label (e.g. 2024 → 'FY24A')."""
    return f"FY{str(year)[-2:]}A"


def _month_master_label(year: int, month: int) -> str:
    """Calendar (year, month) → Master monthly column label (e.g. (2024, 1) → 'Jan-2024').

    Matches ``databook_periods.make_period_str`` / ``is_month_period_column`` so the
    downstream consolidation/recon period filters pick these columns up.
    """
    return f"{EN_MONTH_ABBR[month - 1]}-{year}"


def _anchor_from_fiscal_years(fiscal_years: list[int], fy_end_month: int) -> tuple[int, int]:
    """Trial-balance anchor (year, month) from the requested fiscal-year set.

    The TB services span Jan(year−3) .. anchor month and expose FY{y−3..y−1}+YTD{y}.
    We anchor on the latest requested FY at its fiscal year-end month so that every
    requested FY is covered by an FY/month column.
    """
    if not fiscal_years:
        raise ValueError("fiscal_years must be a non-empty list of integers")
    year = max(int(y) for y in fiscal_years)
    month = 12 if not fy_end_month else int(fy_end_month)
    if not 1 <= month <= 12:
        raise ValueError(f"fy_end_month out of range: {fy_end_month!r}")
    return year, month


def _tb_period_keys(year: int, month: int) -> list[tuple[str, str]]:
    """(tb_amount_key, master_label) for monthly columns Jan(year−3)..anchor month."""
    out: list[tuple[str, str]] = []
    for y in range(year - 3, year + 1):
        end_m = month if y == year else 12
        for m in range(1, end_m + 1):
            out.append((f"{y:04d}-{m:02d}", _month_master_label(y, m)))
    return out


def _pl_fy_key_map(year: int, fiscal_years: list[int]) -> list[tuple[str, str]]:
    """(tb_summary_key, master_fy_label) for the requested PL FY columns.

    The PL TB exposes summary keys FY{year-3}, FY{year-2}, FY{year-1} (full-year sums)
    and YTD{year} (the partial latest year). We map each requested fiscal year to its
    TB key: a completed prior year → FY{y}; the anchor year → its YTD column.
    """
    want = {int(y) for y in fiscal_years}
    out: list[tuple[str, str]] = []
    for y in sorted(want):
        if y == year:
            out.append((f"YTD{year}", _fy_master_label(y)))
        elif year - 3 <= y <= year - 1:
            out.append((f"FY{y}", _fy_master_label(y)))
        # years outside the TB span are silently skipped (not derivable from this anchor)
    return out


def _bs_fy_key_map(year: int, month: int, fiscal_years: list[int]) -> list[tuple[str, str]]:
    """(tb_summary_key, master_fy_label) for the requested BS FY columns.

    The BS TB exposes DEC{year-3..year-1} (Dec-31 year-end balances) and
    CM{year}-{month} (the anchor month-end balance). FY-end balance for the anchor
    year is the cumulative balance at the anchor month (= fiscal year-end).
    """
    want = {int(y) for y in fiscal_years}
    out: list[tuple[str, str]] = []
    for y in sorted(want):
        if y == year:
            out.append((f"CM{year}-{month:02d}", _fy_master_label(y)))
        elif year - 3 <= y <= year - 1:
            out.append((f"DEC{y}", _fy_master_label(y)))
    return out


def _reshape_statement(
    tb: dict[str, Any],
    *,
    statement: str,
    fy_key_map: list[tuple[str, str]],
    period_key_map: list[tuple[str, str]],
    meta_cols: list[str],
) -> pd.DataFrame:
    """Reshape a trial-balance dict into a Master frame (one row per account).

    Pure function: no DB access, no sign flip. Values are copied verbatim from the
    TB ``amounts`` map under the requested keys.
    """
    fy_labels = [lbl for _, lbl in fy_key_map]
    period_labels = [lbl for _, lbl in period_key_map]
    columns = meta_cols + fy_labels + period_labels

    rows: list[dict[str, Any]] = []
    for r in tb.get("rows", []):
        amounts = r.get("amounts", {}) or {}
        gid = str(r.get("gl_account_id") or "").strip()
        row: dict[str, Any] = {
            "Entity": r.get("entity") or "—",
            "Account": gid,  # bare gl_account_id (numeric sort), NOT "gid | name"
            "Account description": r.get("account_name") or "",
            "L1 - BS/PL": statement,
            "L2": r.get("level_2"),
            "L3": r.get("level_3"),
            "L4": r.get("level_4"),
            "L5": "",
            "L6": "Reported",
            "Comments": None,
            "Q&A": None,
            "Answer of Target": None,
        }
        if "NA" in meta_cols:
            # BS only: DB level_1 (Assets / Equity & liabilities split) has no L-slot.
            row["NA"] = r.get("level_1")
        for tb_key, label in fy_key_map:
            row[label] = float(amounts.get(tb_key, 0.0) or 0.0)
        for tb_key, label in period_key_map:
            row[label] = float(amounts.get(tb_key, 0.0) or 0.0)
        rows.append(row)

    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    return df


def _build_net_income_rows(
    pl_df: pd.DataFrame,
    *,
    fy_labels: list[str],
    period_labels: list[str],
) -> pd.DataFrame:
    """One BS equity 'Net income' row per entity (account 2869).

    The row's value in any column = the sum of the presented Master_PL values in that
    same column for that entity (revenue + minus expense −). This is the net result and
    closes the BS equity section. Injected exactly once per entity (no double count).
    """
    columns = META_COLS_BS + fy_labels + period_labels
    if pl_df.empty:
        return pd.DataFrame(columns=columns)

    value_cols = fy_labels + period_labels
    grouped = pl_df.groupby("Entity", dropna=False)[value_cols].sum()

    rows: list[dict[str, Any]] = []
    for entity, sums in grouped.iterrows():
        row: dict[str, Any] = {
            "Entity": entity,
            "Account": NET_INCOME_ACCOUNT,
            "Account description": NET_INCOME_DESCRIPTION,
            "L1 - BS/PL": "BS",
            "L2": NET_INCOME_L2,
            "L3": NET_INCOME_L3,
            "L4": NET_INCOME_L4,
            "L5": "",
            "L6": "Reported",
            "NA": NET_INCOME_L2,
            "Comments": None,
            "Q&A": None,
            "Answer of Target": None,
        }
        for col in value_cols:
            row[col] = float(sums.get(col, 0.0) or 0.0)
        rows.append(row)

    return pd.DataFrame(rows, columns=columns)


def _sort_master(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by Entity then numeric Account (mirrors SuSabyYear final sort)."""
    if df.empty:
        return df
    out = df.copy()
    out["__sort"] = pd.to_numeric(out["Account"], errors="coerce")
    out = out.sort_values(["Entity", "__sort", "Account"], kind="stable")
    out = out.drop(columns="__sort").reset_index(drop=True)
    return out


def build_master_frames_from_gl(
    session: Session,
    entity_codes: Optional[list[str]],
    fiscal_years: list[int],
    fy_end_month: int = 12,
    fiscal_start_month: Optional[int] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build (Master_BS, Master_PL) DataFrames from loaded GL data.

    Reshapes ``build_pl_trial_balance`` / ``build_bs_trial_balance`` output into the
    SuSabyYear Master format. Returns frames whose columns match META_COLS_* + FY
    columns + monthly period columns.

    Args:
        session: SQLAlchemy session (read-only).
        entity_codes: legal_entity_code values to include; None/empty = all entities.
        fiscal_years: fiscal years to expose as FY columns (max FY = anchor year).
        fy_end_month: fiscal year-end month (1-12); the anchor month.
        fiscal_start_month: unused for the reshape (BS already cumulative); accepted
            for signature parity with the upload path.

    Returns:
        (df_bs, df_pl) — Master_BS includes one injected net-income row per entity.
    """
    _ = fiscal_start_month  # signature parity; reshape needs no opening-balance logic
    year, month = _anchor_from_fiscal_years(fiscal_years, fy_end_month)

    # SECURITY (multi-tenant isolation): the TB services accept a SINGLE
    # legal_entity_code and resolve it to one entity_prefix. A comma-joined string
    # silently resolves to None (= no entity filter), which would leak every
    # tenant's GL into the Master. So when an explicit allow-list is given we call
    # the TB services ONCE PER entity_code and concatenate the row sets; the result
    # is strictly limited to the requested+allowed entities.
    if not entity_codes:
        # None/empty = all entities (admin path) — single unfiltered call.
        pl_tb = build_pl_trial_balance(session, year, month, None)
        bs_tb = build_bs_trial_balance(session, year, month, None)
    else:
        pl_rows: list[dict[str, Any]] = []
        bs_rows: list[dict[str, Any]] = []
        for code in entity_codes:
            code_s = str(code).strip()
            if not code_s:
                continue
            pl_one = build_pl_trial_balance(session, year, month, code_s)
            bs_one = build_bs_trial_balance(session, year, month, code_s)
            pl_rows.extend(pl_one.get("rows", []) or [])
            bs_rows.extend(bs_one.get("rows", []) or [])
        pl_tb = {"rows": pl_rows}
        bs_tb = {"rows": bs_rows}

    period_key_map = _tb_period_keys(year, month)
    pl_fy_map = _pl_fy_key_map(year, fiscal_years)
    bs_fy_map = _bs_fy_key_map(year, month, fiscal_years)

    df_pl = _reshape_statement(
        pl_tb,
        statement="PL",
        fy_key_map=pl_fy_map,
        period_key_map=period_key_map,
        meta_cols=META_COLS_PL,
    )
    df_bs = _reshape_statement(
        bs_tb,
        statement="BS",
        fy_key_map=bs_fy_map,
        period_key_map=period_key_map,
        meta_cols=META_COLS_BS,
    )

    # Net income: BS FY/period labels are the canonical column order for the row.
    net_income = _build_net_income_rows(
        df_pl,
        fy_labels=[lbl for _, lbl in bs_fy_map],
        period_labels=[lbl for _, lbl in period_key_map],
    )
    if not net_income.empty:
        df_bs = pd.concat([df_bs, net_income], ignore_index=True)

    df_pl = _sort_master(df_pl)
    df_bs = _sort_master(df_bs)
    return df_bs, df_pl


def write_master_workbook(
    df_bs: pd.DataFrame,
    df_pl: pd.DataFrame,
    output_path: str,
    config: Optional[dict] = None,
) -> str:
    """Write Master_BS / Master_PL to an .xlsx structurally identical to the upload path.

    Reuses ``SuSabyYear.write_output`` (the same function the upload path calls) so the
    sheet names, META column order, L5/L6 synthesis and Check row are byte-identical to
    the subprocess output. Downstream recon re-reads this workbook from disk per step.

    Returns the output path.
    """
    import os

    from SuSabyYear import write_output

    cfg = dict(config or {})
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    write_output(df_bs, df_pl, output_path, cfg)
    return output_path
