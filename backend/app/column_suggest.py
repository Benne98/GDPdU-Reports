"""Headerless GL detection + synthetic-header suggestion helpers.

Real client GL exports for a single (entity, year) are frequently delivered with
NO header row — the first physical line is already data.  When pandas reads such a
file it promotes that first DATA row to column names, so two same-layout files end
up with DIFFERENT column names (their respective first rows) and `/gl/combine`
rejects them with "Column mismatch".

This module provides three pure, DB-free building blocks used by the ingest
router:

  * ``detect_has_header(rows)`` — decide whether the first parsed row is a real
    header (mostly distinct non-numeric label strings) or just data.
  * ``synthetic_headers(n)`` — positional names ``"Column 1" .. "Column N"``.
  * ``suggest_column_names(sample_rows, columns)`` — content heuristic mapping each
    column to a GoBD label (aligned to the frontend ``GOBD_GL_DEFAULTS`` strings)
    so the later column-mapping step auto-fills.

All functions operate on plain Python values (strings / None) so they are trivially
unit-testable with synthetic fixtures and never touch the DB or the filesystem.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Canonical GoBD labels — MUST match the frontend GOBD_GL_DEFAULTS source strings
# (frontend/src/components/ingest/GlEntityCard.tsx) so suggestGlMapping auto-fills
# the column mapping once the user confirms these headers.
LABEL_POSTING_DATE = "Posting date"
LABEL_DOCUMENT_DATE = "Document date"
LABEL_AMOUNT = "Amount"
LABEL_VAT_AMOUNT = "VAT amount"
LABEL_ACCOUNT_NUMBER = "Account number"
LABEL_BOOKING_TEXT = "Booking text"
LABEL_SOURCE_NO = "Source No."
LABEL_SOURCE_TYPE = "Source type"

# A column whose content matches no heuristic keeps its synthetic positional name.
_SYNTHETIC_PREFIX = "Column "

# pandas emits "Unnamed: N" for blank header cells; a numeric-looking column name
# (e.g. an account code "4219405" or amount "-15.895,29") is a strong tell that the
# first row was DATA, not a header.
_RE_UNNAMED = re.compile(r"^Unnamed:\s*\d+$")

# German (1.234,56 / -15.895,29) and plain (1234.56 / -1234) signed decimals.
_RE_GERMAN_DECIMAL = re.compile(r"^[+-]?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^[+-]?\d+,\d+$")
_RE_PLAIN_DECIMAL = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
# Dates: dd.mm.yyyy / dd/mm/yyyy / yyyy-mm-dd (the formats real GL exports use).
_RE_DATE = re.compile(
    r"^\s*(?:\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})\s*$"
)
_RE_ALL_DIGITS = re.compile(r"^\d+$")
# Partner-like source tokens, e.g. "BU9038316-10", "Kreditor", "Debitor".
_RE_PARTNER_TOKEN = re.compile(r"^[A-Za-z]{1,4}\d{3,}(?:-\d+)?$")
_PARTNER_TYPE_WORDS = {"kreditor", "debitor", "creditor", "debtor"}


def _norm(value: Any) -> str:
    """Normalise a cell to a trimmed string ('' for None / NaN)."""
    if value is None:
        return ""
    # pandas NaN (float) — never equal to itself.
    if isinstance(value, float) and value != value:
        return ""
    return str(value).strip()


def _is_number(text: str) -> bool:
    return bool(_RE_GERMAN_DECIMAL.match(text) or _RE_PLAIN_DECIMAL.match(text))


def _is_signed_decimal(text: str) -> bool:
    """A decimal that carries a fractional part or an explicit sign (an amount)."""
    if not _is_number(text):
        return False
    return ("," in text) or ("." in text) or text.startswith(("+", "-"))


def _is_date(text: str) -> bool:
    return bool(_RE_DATE.match(text))


def _magnitude(text: str) -> float:
    """Best-effort absolute magnitude of a German/plain decimal (0.0 on failure)."""
    t = text.strip().replace(" ", "")
    if _RE_GERMAN_DECIMAL.match(t) and ("," in t):
        t = t.replace(".", "").replace(",", ".")
    try:
        return abs(float(t))
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------- #
# Header detection
# --------------------------------------------------------------------------- #
def looks_like_data_row(cells: Iterable[Any]) -> bool:
    """True if a row of cells looks like DATA (numbers / dates / codes), not labels."""
    values = [_norm(c) for c in cells]
    non_empty = [v for v in values if v]
    if not non_empty:
        return False
    datalike = sum(
        1
        for v in non_empty
        if _is_number(v) or _is_date(v) or _RE_PARTNER_TOKEN.match(v)
    )
    return datalike >= max(1, len(non_empty) // 2)


def detect_has_header(
    first_row: Iterable[Any],
    body_rows: list[list[Any]] | None = None,
    column_names: Iterable[Any] | None = None,
) -> bool:
    """Decide whether ``first_row`` is a REAL header row.

    Heuristic (any DATA signal wins -> headerless):
      * If pandas produced ``Unnamed: N`` or purely numeric-looking column NAMES,
        the source had no header -> ``False``.
      * If the first row itself looks like data (mostly numeric / date / code) ->
        ``False``.
      * Otherwise compare the first row's "shape" to the body: a header is mostly
        distinct non-numeric label strings, so if the first row is clearly less
        data-like than the body it is a header -> ``True``.

    Returns ``True`` when the first row is a genuine header, ``False`` when the file
    is headerless and synthetic positional names should be assigned.
    """
    # Signal 1: pandas-assigned column names betray a missing header.
    if column_names is not None:
        names = [_norm(c) for c in column_names]
        if names:
            # A genuine header row essentially NEVER contains parseable dates or
            # several pure numbers — these are dead giveaways of headerless data
            # (the first DATA row got promoted to column names). Short-circuit on
            # them so borderline GL exports are caught reliably.
            n_dates = sum(1 for n in names if _is_date(n))
            n_numbers = sum(1 for n in names if _is_number(n))
            if n_dates >= 2 or n_numbers >= 3:
                return False
            # Otherwise count all data-like artefacts: Unnamed:N, numbers, dates,
            # empty/whitespace cells, and pandas dedup suffixes (".1"/".2" appended
            # to DUPLICATE values — duplicates indicate data, not a header).
            betray = sum(
                1
                for n in names
                if (
                    _RE_UNNAMED.match(n)
                    or _is_number(n)
                    or _is_date(n)
                    or not n.strip()
                    or re.search(r"\.\d+$", n)
                )
            )
            if betray >= max(2, round(len(names) * 0.30)):
                return False

    # Signal 2: the first row itself is data-shaped.
    if looks_like_data_row(first_row):
        return False

    # Signal 3: compare to body. If the body is data but the first row is not,
    # the first row is a header.  If we have no body to compare, default to "the
    # first row is a header" (it did not look like data above).
    if body_rows:
        body_datalike = sum(1 for r in body_rows if looks_like_data_row(r))
        if body_datalike >= max(1, len(body_rows) // 2):
            return True
        # Body is also non-data-shaped (e.g. all text): treat the first row as a
        # header only if it is distinct from the body rows.
        return True
    return True


def synthetic_headers(n: int) -> list[str]:
    """Positional synthetic column names ``["Column 1", ..., "Column N"]``."""
    if n < 0:
        raise ValueError("column count must be non-negative")
    return [f"{_SYNTHETIC_PREFIX}{i}" for i in range(1, n + 1)]


def is_synthetic_header(name: str) -> bool:
    return bool(re.match(rf"^{re.escape(_SYNTHETIC_PREFIX)}\d+$", str(name).strip()))


# --------------------------------------------------------------------------- #
# Column-name suggestion
# --------------------------------------------------------------------------- #
def _column_profile(values: list[str]) -> dict[str, Any]:
    """Summarise a column's sample values for the suggestion heuristic."""
    non_empty = [v for v in values if v]
    n = len(non_empty)
    if n == 0:
        return {"n": 0}
    dates = sum(1 for v in non_empty if _is_date(v))
    signed = sum(1 for v in non_empty if _is_signed_decimal(v))
    numbers = sum(1 for v in non_empty if _is_number(v))
    all_digit = [v for v in non_empty if _RE_ALL_DIGITS.match(v)]
    partner_tok = sum(1 for v in non_empty if _RE_PARTNER_TOKEN.match(v))
    type_words = sum(1 for v in non_empty if v.lower() in _PARTNER_TYPE_WORDS)
    avg_len = sum(len(v) for v in non_empty) / n
    max_mag = max((_magnitude(v) for v in non_empty if _is_number(v)), default=0.0)
    avg_digit_len = (
        sum(len(v) for v in all_digit) / len(all_digit) if all_digit else 0.0
    )
    return {
        "n": n,
        "frac_date": dates / n,
        "frac_signed": signed / n,
        "frac_number": numbers / n,
        "frac_all_digit": len(all_digit) / n,
        "frac_partner_tok": partner_tok / n,
        "frac_type_word": type_words / n,
        "avg_len": avg_len,
        "avg_digit_len": avg_digit_len,
        "max_magnitude": max_mag,
    }


def suggest_column_names(
    sample_rows: list[dict[str, Any]] | list[list[Any]],
    columns: list[str],
) -> dict[str, str]:
    """Suggest a GoBD label per column from a content heuristic over the sample.

    ``sample_rows`` may be a list of dicts keyed by ``columns`` (the upload/combine
    sample shape) or a list of positional row lists.  ``columns`` is the current
    (synthetic or real) column order.

    Returns ``{column: suggested_name}`` covering EVERY input column — a column with
    no confident guess maps to its existing name (so the caller can keep the
    synthetic positional name).  Labels align to the frontend ``GOBD_GL_DEFAULTS``
    so the column-mapping step auto-fills.

    Disambiguation across the whole frame:
      * dates       -> first becomes "Posting date", the rest "Document date".
      * amounts     -> largest-magnitude / most-signed -> "Amount", a smaller signed
                       decimal column -> "VAT amount".
    """
    # Build per-column value lists.
    per_col: dict[str, list[str]] = {c: [] for c in columns}
    for row in sample_rows:
        if isinstance(row, dict):
            for c in columns:
                per_col[c].append(_norm(row.get(c)))
        else:
            for i, c in enumerate(columns):
                per_col[c].append(_norm(row[i]) if i < len(row) else "")

    profiles = {c: _column_profile(per_col[c]) for c in columns}
    out: dict[str, str] = {c: c for c in columns}  # default: keep current name
    used: set[str] = set()

    # A column that already carries a meaningful (non-synthetic, non-"Unnamed")
    # name is authoritative — keep it and exclude it from the heuristic so e.g. a
    # constant "fiscal_year" column added by /gl/combine is never relabelled.
    for c in columns:
        name = str(c).strip()
        if name and not is_synthetic_header(name) and not _RE_UNNAMED.match(name):
            used.add(c)

    # --- dates: posting (first) then document (subsequent) -----------------
    date_cols = [
        c
        for c in columns
        if c not in used and profiles[c].get("n") and profiles[c]["frac_date"] >= 0.6
    ]
    for idx, c in enumerate(date_cols):
        label = LABEL_POSTING_DATE if idx == 0 else LABEL_DOCUMENT_DATE
        out[c] = label
        used.add(c)

    # --- amounts: signed decimals; largest magnitude -> Amount, next -> VAT --
    amount_cols = [
        c
        for c in columns
        if c not in used
        and profiles[c].get("n")
        and profiles[c]["frac_signed"] >= 0.5
    ]
    # Rank by (most-signed, largest magnitude) so the booking amount beats VAT.
    amount_cols.sort(
        key=lambda c: (profiles[c]["frac_signed"], profiles[c]["max_magnitude"]),
        reverse=True,
    )
    for idx, c in enumerate(amount_cols):
        out[c] = LABEL_AMOUNT if idx == 0 else LABEL_VAT_AMOUNT
        used.add(c)

    # --- remaining columns: codes / partners / text ------------------------
    for c in columns:
        if c in used:
            continue
        p = profiles[c]
        if not p.get("n"):
            continue  # empty column -> keep synthetic name

        # Partner type words ("Kreditor"/"Debitor") -> Source type.
        if p["frac_type_word"] >= 0.4:
            out[c] = LABEL_SOURCE_TYPE
            used.add(c)
            continue
        # Partner-like tokens ("BU9038316-10") -> Source No.
        if p["frac_partner_tok"] >= 0.4:
            out[c] = LABEL_SOURCE_NO
            used.add(c)
            continue
        # Short all-digit codes -> Account number.
        if p["frac_all_digit"] >= 0.6 and 2 <= p["avg_digit_len"] <= 10:
            out[c] = LABEL_ACCOUNT_NUMBER
            used.add(c)
            continue
        # Longer free text -> Booking text.
        if p["frac_number"] < 0.3 and p["avg_len"] >= 4:
            out[c] = LABEL_BOOKING_TEXT
            used.add(c)
            continue
        # else: keep the existing (synthetic) name.

    return out
