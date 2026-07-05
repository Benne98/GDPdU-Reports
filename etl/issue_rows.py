"""Fetch all source rows for a validation issue (S1 field failures, B1 bookings).

Rows are projected from the CANONICAL/mapped ``linked`` frame's OWN real column
values.  This is robust to ANY ``booking_line_id`` — it is a 19-digit BLAKE2b
hash masked to 62 bits (see ``etl.mapping._deterministic_booking_line_id``), NOT a
1-based row position, so it must never be used to index into ``raw_df``.
"""
from __future__ import annotations

import pandas as pd

from etl.checks import _FIELD_LABELS, s1_empty_mask

#: Canonical/mapped column order for the issue-row projection.  Mirrors the
#: "combined GL lines" preview ordering where practical (the mapping.py canonical
#: frame order), led by a plain human-readable ``reference`` column.  Only columns
#: actually present in the ``linked`` frame are emitted, so this is safe across
#: profiles / linking strategies.
_CANONICAL_ORDER: list[str] = [
    "reference",
    "journal_entry_group_number",
    "fiscal_year",
    "fiscal_period",
    "line_number",
    "account_number_group",
    "gl_account_id",
    "amount",
    "vat_amount",
    "posting_date",
    "document_date",
    "document_type_code",
    "reference_document_number",
    "currency_code",
    "posting_type",
    "customer_id",
    "supplier_id",
    "line_note",
    "header_note",
    "entry_type",
    "account_class",
    "source_system",
    "booking_line_id",
]

#: Display labels (English) for the projected columns.
_COLUMN_LABELS: dict[str, str] = {
    **_FIELD_LABELS,
    "reference": "Reference",
    "fiscal_period": "Period",
    "gl_account_id": "GL account",
    "vat_amount": "VAT amount",
    "document_date": "Document date",
    "document_type_code": "Document type",
    "reference_document_number": "Reference document",
    "currency_code": "Currency",
    "posting_type": "Posting type",
    "customer_id": "Customer",
    "supplier_id": "Supplier",
    "line_note": "Line note",
    "header_note": "Header note",
    "entry_type": "Entry type",
    "account_class": "Account class",
    "source_system": "Source system",
}


def _serialize_cell(value) -> str | int | float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        try:
            return str(value.date()) if hasattr(value, "date") else str(value)
        except Exception:
            return str(value)
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value).strip()


def _booking_line_id_at(linked: pd.DataFrame, idx) -> str | None:
    """Return the booking_line_id for ``idx`` as an exact STRING (or None).

    booking_line_id is a 62-bit BLAKE2b hash (> 2**53). It is returned as a string
    so the exact digit sequence round-trips through JSON -> JavaScript float64
    without precision loss (a JSON number would be silently rounded by the browser).
    """
    if "booking_line_id" not in linked.columns:
        return None
    val = linked.at[idx, "booking_line_id"]
    if pd.isna(val):
        return None
    try:
        return str(int(val))
    except (TypeError, ValueError):
        return None


def _reference_value(linked: pd.DataFrame, idx) -> str | None:
    """Plain, human-readable reference for display (NOT the huge id hash).

    Prefers ``journal_entry_group_number`` + ``line_number``; falls back to the
    booking_line_id as a string so a reference is always present.
    """
    parts: list[str] = []
    if "journal_entry_group_number" in linked.columns:
        jegn = _serialize_cell(linked.at[idx, "journal_entry_group_number"])
        if jegn not in (None, ""):
            parts.append(str(jegn))
    if "line_number" in linked.columns:
        ln = _serialize_cell(linked.at[idx, "line_number"])
        if ln not in (None, ""):
            parts.append(f"#{ln}")
    if parts:
        return " ".join(parts)
    bid = _booking_line_id_at(linked, idx)
    return str(bid) if bid is not None else None


def projected_columns(linked: pd.DataFrame) -> list[str]:
    """Ordered list of canonical columns to project (present columns only).

    Always includes ``reference`` (synthetic display column) and, when available,
    ``booking_line_id`` (needed by the UI exclusion checkbox).
    """
    cols = [
        c
        for c in _CANONICAL_ORDER
        if c == "reference" or c in linked.columns
    ]
    return cols


def column_labels_for(columns: list[str]) -> dict[str, str]:
    """English display labels for the subset of columns being returned."""
    return {c: _COLUMN_LABELS[c] for c in columns if c in _COLUMN_LABELS}


def _project_row(linked: pd.DataFrame, idx, columns: list[str]) -> dict:
    """Project ONE canonical row onto ``columns`` from its OWN real values.

    Robust to huge / non-sequential / missing booking_line_ids and to row
    filtering — never keys into a positional source frame.
    """
    row: dict = {}
    for c in columns:
        if c == "reference":
            row[c] = _reference_value(linked, idx)
        elif c == "booking_line_id":
            row[c] = _booking_line_id_at(linked, idx)
        else:
            row[c] = _serialize_cell(linked.at[idx, c])
    return row


def collect_s1_issue_rows(
    linked: pd.DataFrame,
    raw_df: pd.DataFrame,
    field: str,
    exclude_line_ids: list[str | int] | set[str | int] | None = None,
    search: str | None = None,
    limit: int = 10_000,
    offset: int = 0,
    reference_limit: int = 5,
    *,
    required_fields: list[str] | None = None,
) -> dict:
    """Return canonical source rows + stats for one S1 failing field.

    Rows (failing AND reference) are projected from the canonical ``linked`` frame's
    own real column values (``raw_df`` is accepted for signature compatibility but is
    NOT keyed by booking_line_id).  ``columns`` is multi-column (the mapped canonical
    columns in a sensible order, led by a plain ``reference`` column) and always keeps
    a ``booking_line_id`` key per row for the UI exclusion checkbox.  ``failing_field``
    carries the requested field so the UI can highlight that column.

    Also returns up to ``reference_limit`` reference rows (``reference_rows``),
    projected onto the same ``columns``, as a positive reference. Reference selection
    has a 3-tier fallback so a reference is shown whenever ANY candidate exists in the
    dataset (``reference_kind``):

    * ``'same_field'`` — rows that PASS the specific failing ``field`` (primary).
    * ``'overall'``    — when no same-field passing row exists (e.g. the field is
      entirely empty): rows passing ALL S1 ``required_fields`` if any, else the
      best-populated rows (most non-empty required fields) as a last resort.
    * ``'none'``       — the dataset has no usable candidate row at all.

    ``offender_ids`` / ``total_offenders`` carry the FULL offender set for this
    field across all pages (honouring ``exclude_line_ids``), so the caller can
    exclude every offender in one click regardless of pagination/search.
    """
    # Exclusions match by STRING comparison on the exact digit sequence so a
    # 62-bit hash id (> 2**53) is never routed through float64 / pd.to_numeric.
    excluded: set[str] = set()
    if exclude_line_ids and "booking_line_id" in linked.columns:
        excluded = {str(x) for x in exclude_line_ids}

    has_bid = "booking_line_id" in linked.columns
    not_excluded = pd.Series(True, index=linked.index)
    if excluded and has_bid:
        not_excluded &= ~linked["booking_line_id"].astype("int64").astype(str).isin(excluded)

    mask = s1_empty_mask(linked, field) & not_excluded

    failing = linked.loc[mask]
    total = int(len(failing))

    # Full offender set for this field across ALL pages (search-independent).
    #
    # booking_line_id is a 62-bit BLAKE2b hash (> 2**53). It MUST NOT be routed
    # through float64 (e.g. via pd.to_numeric), which silently rounds large ids to
    # the WRONG integer (2003686083711855000 -> 2003686083711855104). These ids are
    # fed back as exclude_line_ids, so a corrupted id would exclude the wrong row.
    # They are emitted as exact STRINGS so the digit sequence round-trips through
    # JSON -> JavaScript without float64 precision loss.
    offender_ids: list[str] = []
    if has_bid and not failing.empty:
        col = failing["booking_line_id"]
        if pd.api.types.is_integer_dtype(col.dtype):
            # Already an integer dtype (e.g. int64) — read ids straight from the
            # column with NO float round-trip; exact for huge hash ids.
            offender_ids = [str(int(v)) for v in col.tolist()]
        else:
            # Non-integer dtype (float/object with NaN). Precision may already be
            # lost on a true float64 column; do the safest possible — drop NaN and
            # cast each surviving ORIGINAL value to int directly (object-dtype
            # Python ints stay exact), avoiding any further float64 step.
            offender_ids = [str(int(v)) for v in col.dropna().tolist()]
    total_offenders = len(offender_ids)

    amount_sum: float | None = None
    if "amount" in failing.columns and not failing.empty:
        amount_sum = round(
            float(pd.to_numeric(failing["amount"], errors="coerce").fillna(0.0).sum()),
            2,
        )

    fiscal_years: list[int] = []
    if "fiscal_year" in failing.columns and not failing.empty:
        fiscal_years = sorted(
            int(x)
            for x in pd.to_numeric(failing["fiscal_year"], errors="coerce").dropna().unique()
        )

    # Canonical column projection — identical for failing AND reference rows.
    columns = projected_columns(linked)

    rows: list[dict] = [_project_row(linked, idx, columns) for idx in failing.index]

    if search:
        q = search.strip().lower()
        if q:
            rows = [
                r
                for r in rows
                if any(q in str(v).lower() for v in r.values() if v is not None and v != "")
            ]

    filtered_total = len(rows)
    page = rows[offset : offset + limit]

    # --- Reference (positive) rows with a 3-tier fallback --------------------- #
    # All tiers honour the same line-id exclusions and project onto the SAME
    # canonical columns, so reference rows still render when there are no failing
    # rows to show.
    present_required = [
        c for c in (required_fields or []) if c in linked.columns
    ]
    ref_idx: list = []
    reference_kind = "none"
    if reference_limit > 0:
        # PRIMARY: rows passing the specific failing field.
        pass_mask = (~s1_empty_mask(linked, field)) & not_excluded
        ref_idx = list(linked.loc[pass_mask].index[:reference_limit])
        if ref_idx:
            reference_kind = "same_field"

        # FALLBACK: rows passing ALL S1 required fields (fully-valid rows).
        if not ref_idx and present_required:
            overall = not_excluded.copy()
            for col in present_required:
                overall &= ~s1_empty_mask(linked, col)
            ref_idx = list(linked.loc[overall].index[:reference_limit])
            if ref_idx:
                reference_kind = "overall"

        # LAST RESORT: best-populated rows (most non-empty required fields).
        if not ref_idx and present_required:
            populated = pd.Series(0, index=linked.index)
            for col in present_required:
                populated += (~s1_empty_mask(linked, col)).astype(int)
            candidates = populated.loc[not_excluded]
            if not candidates.empty:
                ranked = candidates.sort_values(ascending=False, kind="stable")
                ref_idx = list(ranked.index[:reference_limit])
                if ref_idx:
                    reference_kind = "overall"

    reference_rows: list[dict] = [_project_row(linked, idx, columns) for idx in ref_idx]

    return {
        "stats": {
            "row_count": total,
            "amount_sum": amount_sum,
            "fiscal_years": fiscal_years,
        },
        "columns": columns,
        "column_labels": column_labels_for(columns),
        "failing_field": field,
        "rows": page,
        "total": filtered_total,
        "reference_rows": reference_rows,
        "reference_kind": reference_kind,
        "offender_ids": offender_ids,
        "total_offenders": total_offenders,
    }


def collect_b1_issue_rows(
    linked: pd.DataFrame,
    raw_df: pd.DataFrame,
    journal_entry_group_number: str | None,
    fiscal_year: int | None,
    exclude_line_ids: list[str | int] | set[str | int] | None = None,
    limit: int = 10_000,
    offset: int = 0,
) -> dict:
    """Return all source rows for ONE booking (a B1 offender) + its net balance.

    Selects every line of the ``(journal_entry_group_number, fiscal_year)`` booking
    — the same key ``etl.checks.check_booking_balance`` groups on — honouring the
    same ``exclude_line_ids`` as :func:`collect_s1_issue_rows`, projects them onto
    the canonical columns (from the lines' OWN values), and reports
    ``summary_row={"amount": <signed sum>}``.

    The amount is a raw arithmetic sum of the signed ``amount`` column (the same
    column B1 sums). For a balanced booking it nets to 0; for a B1 offender it
    equals the (non-zero) imbalance.
    """
    # Exclusions match by STRING comparison (see collect_s1_issue_rows): the
    # 62-bit hash booking_line_id is never coerced through float64.
    excluded: set[str] = set()
    if exclude_line_ids and "booking_line_id" in linked.columns:
        excluded = {str(x) for x in exclude_line_ids}

    if "journal_entry_group_number" in linked.columns and journal_entry_group_number is not None:
        jegn = linked["journal_entry_group_number"].astype("string").str.strip()
        mask = jegn.eq(str(journal_entry_group_number).strip())
    else:
        mask = pd.Series(False, index=linked.index)

    if fiscal_year is not None and "fiscal_year" in linked.columns:
        fy = pd.to_numeric(linked["fiscal_year"], errors="coerce")
        mask &= fy.eq(int(fiscal_year))

    if excluded and "booking_line_id" in linked.columns:
        mask &= ~linked["booking_line_id"].astype("int64").astype(str).isin(excluded)

    booking = linked.loc[mask]
    total = int(len(booking))

    # Per-booking balance: raw signed sum of the amount column (matches B1).
    amount_sum: float = 0.0
    if "amount" in booking.columns and not booking.empty:
        amount_sum = round(
            float(pd.to_numeric(booking["amount"], errors="coerce").fillna(0.0).sum()),
            2,
        )

    fiscal_years: list[int] = []
    if "fiscal_year" in booking.columns and not booking.empty:
        fiscal_years = sorted(
            int(x)
            for x in pd.to_numeric(booking["fiscal_year"], errors="coerce").dropna().unique()
        )

    columns = projected_columns(linked)
    rows: list[dict] = [_project_row(linked, idx, columns) for idx in booking.index]

    filtered_total = len(rows)
    page = rows[offset : offset + limit]

    return {
        "stats": {
            "row_count": total,
            "amount_sum": amount_sum,
            "fiscal_years": fiscal_years,
        },
        "columns": columns,
        "column_labels": column_labels_for(columns),
        # No single failing field for a balance drill-down.
        "failing_field": None,
        "rows": page,
        "total": filtered_total,
        # Reference (passing) rows are not meaningful for a single booking's lines.
        "reference_rows": [],
        "summary_row": {"amount": amount_sum},
    }
