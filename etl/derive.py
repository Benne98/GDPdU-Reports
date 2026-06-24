"""Derive AR / AP / Sales / CoM from canonical GL lines (P1, methods A+B linking).

Input `lines` DataFrame requires at least:
  journal_entry_group_number, fiscal_year, amount, account_class,
  customer_id, supplier_id
where `account_class` ∈ {'revenue','material','receivable','payable','other'}
(derived from the account mapping via `classify`).

Sign convention (canonical): amount + = debit, - = credit.
  gross_sales       = -amount   (revenue is booked as credit)
  cost_of_materials =  amount    (material is booked as debit)

Linking strategies:
  'txn'        -> propagate_partners (method A, GoBD transaction-number based)
  'gegenkonto' -> propagate_partners_gegenkonto (method B, DATEV counter-account)
  'none'       -> no partner attribution
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd


@dataclass(frozen=True)
class AccountClasses:
    """level_3 labels that define each economic class (from the account mapping)."""

    revenue: frozenset[str] = field(default_factory=frozenset)
    material: frozenset[str] = field(default_factory=frozenset)
    receivable: frozenset[str] = field(default_factory=frozenset)
    payable: frozenset[str] = field(default_factory=frozenset)


def classify(level_3: pd.Series, classes: AccountClasses) -> pd.Series:
    """Map each line's level_3 label to its economic class (else 'other').

    Backward-compatible single-column classifier.  Prefer ``classify_with_rules``
    when different classes use different level columns (DF2 multi-column rules).
    """
    lookup: dict[str, str] = {}
    for cls in ("revenue", "material", "receivable", "payable"):
        for label in getattr(classes, cls):
            lookup[label] = cls
    return level_3.astype("string").fillna("").str.strip().map(lambda v: lookup.get(v, "other"))


def classify_with_rules(lines: pd.DataFrame, rules: "Any") -> pd.Series:
    """Map each GL line to its economic class using per-class level columns.

    Unlike ``classify``, which applies a single column, this function reads the
    configured ``level_column`` for each class from a ``ClassificationRules``
    instance and applies them independently, then merges the results.

    Priority: revenue > material > receivable > payable > 'other'
    (first matching class wins; a line should only ever match one class).

    Parameters
    ----------
    lines : pd.DataFrame
        Canonical GL lines.  Must contain the level columns referenced by ``rules``.
    rules : etl.classify_config.ClassificationRules
        Rule set (e.g. ``DEFAULT_RULES`` or a source-system override).

    Returns
    -------
    pd.Series
        String series with values in {revenue, material, receivable, payable, other}.
    """
    result = pd.Series("other", index=lines.index, dtype="string")

    # Apply in reverse priority so highest-priority class overwrites lower ones
    for cls in reversed(("revenue", "material", "receivable", "payable")):
        rule = getattr(rules, cls)
        col = rule.level_column
        if col not in lines.columns:
            continue
        label_series = lines[col].astype("string").fillna("").str.strip()
        matches = label_series.isin(rule.labels)
        result = result.where(~matches, other=cls)

    return result


def propagate_partners(lines: pd.DataFrame) -> pd.DataFrame:
    """Method A: within each booking, copy the (unique) partner from the personal
    account line onto the matching P&L lines.

    - customer_id: receivable line -> revenue lines
    - supplier_id: payable line    -> material lines
    Ambiguous bookings (>1 distinct partner of that class) are left unlinked
    (`link_method` stays 'none').
    """
    df = lines.copy()
    if "link_method" not in df.columns:
        df["link_method"] = "none"

    for partner_col, src_class, tgt_class in (
        ("customer_id", "receivable", "revenue"),
        ("supplier_id", "payable", "material"),
    ):
        src = df[df["account_class"] == src_class]
        if src.empty:
            continue
        nunique = src.groupby("journal_entry_group_number")[partner_col].nunique(dropna=True)
        unambiguous = nunique[nunique == 1].index
        partner = (
            src[src["journal_entry_group_number"].isin(unambiguous)]
            .groupby("journal_entry_group_number")[partner_col]
            .first()
        )
        mask = (df["account_class"] == tgt_class) & df["journal_entry_group_number"].isin(partner.index)
        df.loc[mask, partner_col] = df.loc[mask, "journal_entry_group_number"].map(partner)
        df.loc[mask, "link_method"] = "txn"
    return df


def propagate_partners_gegenkonto(lines: pd.DataFrame) -> pd.DataFrame:
    """Method B (DATEV Konto/Gegenkonto): attribute the partner from this row's
    counter-account directly onto the same row — no cross-row grouping needed.

    Contract / pre-conditions:
      - Each row may carry two extra optional columns (both or neither may be absent):
          * `counter_account_class`  — the economic class of the *counter* account
                                       for this row ('receivable' or 'payable').
          * `counter_partner_id`     — the already-built customer_id / supplier_id
                                       from the counter account on the same DATEV line.
      - The *current* row's `account_class` determines where the partner is stored:
          * row is 'revenue'  and counter is 'receivable'  -> set customer_id
          * row is 'material' and counter is 'payable'     -> set supplier_id
        All other combinations are left untouched (link_method stays 'none').
      - If either column is absent the function is a no-op (returns unchanged copy).
      - Existing non-null customer_id / supplier_id on a row are **not** overwritten
        (caller pre-populate from direct source_no mapping if present).

    This method is the in-row counterpart to propagate_partners (method A). It
    handles DATEV-style canonical lines where both the P&L account and the
    personal (partner) account appear on the *same* row via Konto/Gegenkonto.
    """
    df = lines.copy()
    if "link_method" not in df.columns:
        df["link_method"] = "none"

    if "counter_account_class" not in df.columns or "counter_partner_id" not in df.columns:
        # columns not present -> strategy B not applicable, return unchanged
        return df

    cac = df["counter_account_class"].astype("string").str.strip()
    cpid = df["counter_partner_id"]

    # revenue line whose counter is a receivable: attribute customer
    rev_mask = (
        (df["account_class"] == "revenue")
        & (cac == "receivable")
        & cpid.notna()
        & df["customer_id"].isna()
    )
    df.loc[rev_mask, "customer_id"] = cpid[rev_mask]
    df.loc[rev_mask, "link_method"] = "gegenkonto"

    # material line whose counter is a payable: attribute supplier
    mat_mask = (
        (df["account_class"] == "material")
        & (cac == "payable")
        & cpid.notna()
        & df["supplier_id"].isna()
    )
    df.loc[mat_mask, "supplier_id"] = cpid[mat_mask]
    df.loc[mat_mask, "link_method"] = "gegenkonto"

    return df


def link_partners(lines: pd.DataFrame, strategy: str) -> pd.DataFrame:
    """Dispatcher: choose the partner-linking strategy.

    strategy:
      'txn'        -> Method A (GoBD transaction-number propagation)
      'gegenkonto' -> Method B (DATEV in-row counter-account attribution)
      'none'       -> no-op; returns a copy with link_method='none'
    """
    if strategy == "txn":
        return propagate_partners(lines)
    if strategy == "gegenkonto":
        return propagate_partners_gegenkonto(lines)
    if strategy == "none":
        df = lines.copy()
        if "link_method" not in df.columns:
            df["link_method"] = "none"
        return df
    raise ValueError(f"unknown linking strategy {strategy!r}; choose 'txn', 'gegenkonto', or 'none'")


def derive_sales(lines: pd.DataFrame) -> pd.DataFrame:
    """fact_sales: revenue lines, gross_sales = -amount."""
    out = lines[lines["account_class"] == "revenue"].copy()
    out["gross_sales"] = -out["amount"]
    return out


def derive_com(lines: pd.DataFrame) -> pd.DataFrame:
    """fact_com: material lines, cost_of_materials = amount."""
    out = lines[lines["account_class"] == "material"].copy()
    out["cost_of_materials"] = out["amount"]
    return out


def derive_ar(lines: pd.DataFrame) -> pd.DataFrame:
    """fact_ar: receivable lines (open items). Passes through `due_date` if present."""
    return lines[lines["account_class"] == "receivable"].copy()


def derive_ap(lines: pd.DataFrame) -> pd.DataFrame:
    """fact_ap: payable lines (open items). Passes through `due_date` if present."""
    return lines[lines["account_class"] == "payable"].copy()


# --------------------------------------------------------------------------- #
# DF3 — Partner extraction (pure, DB-free)
# --------------------------------------------------------------------------- #

#: Optional attribute columns carried on canonical lines that enrich the dims.
_CUSTOMER_ATTR_COLS = (
    "customer_name",
    "source_no",
    "country_code",
    "region_code",
    "city",
    "postal_code",
    "default_currency",
    "source_system",
)

_SUPPLIER_ATTR_COLS = (
    "supplier_name",
    "source_no",
    "country_code",
    "region_code",
    "city",
    "postal_code",
    "default_currency",
    "source_system",
)


def _strip_entity_prefix(id_series: pd.Series) -> pd.Series:
    """Remove the leading 2-character entity prefix from a partner-id series.

    Example: '01100' -> '100',  '01200' -> '200'.
    Returns a string series; null/empty values propagate as None.
    """
    return id_series.where(id_series.isna(), id_series.str[2:])


def extract_partners(
    lines: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract distinct customer and supplier rows from canonical GL lines.

    Scans the ``customer_id`` and ``supplier_id`` columns in *lines* and
    returns one row per distinct partner id, together with any optional
    attribute columns that happen to be present on the lines.

    Design rules:
    - Pure / DB-free — safe to call with no database connection.
    - Lines that carry no partner (NaN customer_id / supplier_id) are
      silently ignored (cash sales, VAT lines, etc.).
    - When multiple rows share the same partner id, attributes are taken
      from the **first** non-null value in each column (coalesce over the
      group).  This is identical to the COALESCE-on-update strategy used
      in the DB upsert.
    - ``debtor_number`` / ``creditor_number`` default to the id with the
      2-character entity prefix stripped, unless an explicit ``source_no``
      column is present on the lines.

    Parameters
    ----------
    lines : pd.DataFrame
        Canonical GL lines (post-link_partners).  Must contain
        ``customer_id`` and/or ``supplier_id`` columns.

    Returns
    -------
    customers : pd.DataFrame
        Columns: customer_id (str), debtor_number (str), and any of
        {name_line_1, country_code, region_code, city, postal_code,
        default_currency, source_system} that are present on *lines*.
        May be empty when no customer_id values are present.

    suppliers : pd.DataFrame
        Columns: supplier_id (str), creditor_number (str), and any of
        {name_line_1, country_code, region_code, city, postal_code,
        default_currency, source_system} that are present on *lines*.
        May be empty when no supplier_id values are present.
    """
    customers = _extract_one_side(
        lines,
        id_col="customer_id",
        number_col="debtor_number",
        name_src_col="customer_name",
        attr_cols=_CUSTOMER_ATTR_COLS,
    )
    suppliers = _extract_one_side(
        lines,
        id_col="supplier_id",
        number_col="creditor_number",
        name_src_col="supplier_name",
        attr_cols=_SUPPLIER_ATTR_COLS,
    )
    return customers, suppliers


def _extract_one_side(
    lines: pd.DataFrame,
    id_col: str,
    number_col: str,
    name_src_col: str,
    attr_cols: tuple[str, ...],
) -> pd.DataFrame:
    """Internal helper that extracts one partner side (customer or supplier).

    Parameters
    ----------
    id_col       Column holding the full partner id  (e.g. 'customer_id').
    number_col   Target column name for the numeric part (e.g. 'debtor_number').
    name_src_col Column on *lines* that holds the partner name  ('customer_name').
    attr_cols    Optional attribute columns from *lines* to carry forward.
    """
    if id_col not in lines.columns:
        # Return empty frame with the minimum expected schema.
        return pd.DataFrame(columns=[id_col, number_col])

    present = lines[lines[id_col].notna() & (lines[id_col].astype(str).str.strip() != "")]
    if present.empty:
        return pd.DataFrame(columns=[id_col, number_col])

    # Collect which optional columns actually exist on the lines.
    available_attr: list[str] = [c for c in attr_cols if c in lines.columns]

    # Build a sub-frame of [id_col] + available attrs.
    sub = present[[id_col] + available_attr].copy()

    # Coalesce per partner id: first non-null value wins.
    def _first_non_null(s: pd.Series):
        return s.dropna().iloc[0] if s.notna().any() else None

    if available_attr:
        grouped = sub.groupby(id_col, sort=False).agg(
            {col: _first_non_null for col in available_attr}
        ).reset_index()
    else:
        grouped = sub[[id_col]].drop_duplicates(subset=[id_col]).reset_index(drop=True)

    # Derive the numeric account number from the id (strip 2-char entity prefix),
    # unless an explicit source_no column was available.
    if "source_no" in grouped.columns:
        # source_no is explicit — use it as the debtor/creditor number where present.
        grouped[number_col] = grouped["source_no"].where(
            grouped["source_no"].notna(),
            _strip_entity_prefix(grouped[id_col]),
        )
        # Drop source_no from output (it was an intermediate from lines).
        grouped = grouped.drop(columns=["source_no"])
    else:
        grouped[number_col] = _strip_entity_prefix(grouped[id_col])

    # Rename name column if present.
    if name_src_col in grouped.columns:
        grouped = grouped.rename(columns={name_src_col: "name_line_1"})

    # Ensure id and number_col come first.
    first_cols = [id_col, number_col]
    rest = [c for c in grouped.columns if c not in first_cols]
    return grouped[first_cols + rest].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Stored-partner backfill (DB-side method-A propagation)
# --------------------------------------------------------------------------- #
#
# Moved here verbatim from backend/scripts/derive_facts.py so the reporting-v2
# rebuild (etl/rebuild.py) and the legacy derive_facts.py CLI share one
# implementation.  This is the on-DB equivalent of ``propagate_partners`` above:
# it copies the (unambiguous) customer/supplier from the personal-account line
# (receivable / payable) onto the matching P&L lines (revenue / material) within
# the same journal entry, but operates on already-stored ``fact_gl_line`` rows
# (used when the original GL load skipped classification before link_partners,
# e.g. the Decidra reference path).


def backfill_partner_links_on_gl_lines(session) -> dict[str, int]:
    """Method-A backfill on stored fact_gl_line: receivable→revenue, payable→material.

    Classification uses the same level-label sets as the derived-fact SQL
    (``etl.derive_facts_sql``).  Returns the number of income/material lines linked.
    Idempotent: re-running only fills lines that are still NULL/blank.
    """
    from sqlalchemy import text

    from etl.derive_facts_sql import (
        MATERIAL_L3,
        PAYABLE_L3,
        RECEIVABLE_L3,
        REVENUE_L2,
        REVENUE_L3,
        _quote,
    )

    rev_l3 = _quote(REVENUE_L3)
    rev_l2 = _quote(REVENUE_L2)
    mat_l3 = _quote(MATERIAL_L3)
    ar_l3 = _quote(RECEIVABLE_L3)
    ap_l3 = _quote(PAYABLE_L3)

    cust_result = session.execute(
        text(f"""
            WITH recv AS (
                SELECT
                    r.journal_entry_group_number,
                    r.fiscal_year,
                    MAX(r.customer_id) AS customer_id
                FROM fact_gl_line r
                JOIN dim_gl_account ar
                  ON ar.account_number_group = r.account_number_group
                 AND ar.fiscal_year          = r.fiscal_year
                WHERE ar.level_3 IN ({ar_l3})
                  AND r.customer_id IS NOT NULL
                  AND TRIM(r.customer_id) <> ''
                GROUP BY r.journal_entry_group_number, r.fiscal_year
                HAVING COUNT(DISTINCT r.customer_id) = 1
            ),
            targets AS (
                SELECT income.booking_line_id, recv.customer_id
                FROM fact_gl_line income
                JOIN recv
                  ON recv.journal_entry_group_number = income.journal_entry_group_number
                 AND recv.fiscal_year = income.fiscal_year
                JOIN dim_gl_account ai
                  ON ai.account_number_group = income.account_number_group
                 AND ai.fiscal_year          = income.fiscal_year
                WHERE (ai.level_3 IN ({rev_l3}) OR ai.level_2 IN ({rev_l2}))
                  AND (income.customer_id IS NULL OR TRIM(income.customer_id) = '')
            )
            UPDATE fact_gl_line l
            SET customer_id = t.customer_id
            FROM targets t
            WHERE l.booking_line_id = t.booking_line_id
        """)
    )

    supp_result = session.execute(
        text(f"""
            WITH pay AS (
                SELECT
                    p.journal_entry_group_number,
                    p.fiscal_year,
                    MAX(p.supplier_id) AS supplier_id
                FROM fact_gl_line p
                JOIN dim_gl_account ap
                  ON ap.account_number_group = p.account_number_group
                 AND ap.fiscal_year          = p.fiscal_year
                WHERE ap.level_3 IN ({ap_l3})
                  AND p.supplier_id IS NOT NULL
                  AND TRIM(p.supplier_id) <> ''
                GROUP BY p.journal_entry_group_number, p.fiscal_year
                HAVING COUNT(DISTINCT p.supplier_id) = 1
            ),
            targets AS (
                SELECT mat.booking_line_id, pay.supplier_id
                FROM fact_gl_line mat
                JOIN pay
                  ON pay.journal_entry_group_number = mat.journal_entry_group_number
                 AND pay.fiscal_year = mat.fiscal_year
                JOIN dim_gl_account am
                  ON am.account_number_group = mat.account_number_group
                 AND am.fiscal_year          = mat.fiscal_year
                WHERE am.level_3 IN ({mat_l3})
                  AND (mat.supplier_id IS NULL OR TRIM(mat.supplier_id) = '')
            )
            UPDATE fact_gl_line l
            SET supplier_id = t.supplier_id
            FROM targets t
            WHERE l.booking_line_id = t.booking_line_id
        """)
    )

    return {
        "income_customer_linked": int(cust_result.rowcount or 0),
        "material_supplier_linked": int(supp_result.rowcount or 0),
    }
