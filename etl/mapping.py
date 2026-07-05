"""etl/mapping.py — Turn a raw uploaded DataFrame + a MappingProfile into a
canonical GL lines DataFrame (P1b/P1f).

No DB, no I/O; operates on pandas DataFrames so every step is unit-testable.

MappingProfile drives:
  - entity resolution (fixed value or column)
  - fiscal_year resolution (fixed, column, or derived from posting_date)
  - sign logic  (signed / soll_haben / amount_dc) via transform.signed_amount
  - decimal / thousands / date parsing locale
  - column mapping (source column names -> canonical target fields)
  - linking strategy ('txn' | 'gegenkonto' | 'none')
  - entry_type default ('actual')

Output canonical columns (all present, nullable where schema allows):
  journal_entry_group_number, fiscal_year, fiscal_period, line_number,
  booking_line_id, account_number_group, gl_account_id, amount, vat_amount,
  line_note, customer_id, supplier_id, posting_type, posting_date,
  document_date, document_type_code, reference_document_number, currency_code,
  header_note, entry_type, account_class, source_system
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from etl import transform as T

# booking_line_id is a BIGINT (signed 64-bit) UNIQUE/PK across fact_gl_line and the
# derived fact_ar/ap/sales/com tables.  We mask the hash to 62 bits so the value is
# always a *positive* signed bigint (max 2**62-1 ≈ 4.6e18 < 9.2e18 bigint ceiling),
# leaving headroom and never producing a negative key.
_BLID_MASK = (1 << 62) - 1


def _deterministic_booking_line_id(
    jegn: pd.Series,
    fiscal_year: pd.Series,
    line_number: pd.Series,
) -> pd.Series:
    """Globally-unique, deterministic booking_line_id per booking line.

    Derived from ``(journal_entry_group_number, fiscal_year, line_number)``.  The
    journal_entry_group_number already embeds the 2-char entity_prefix
    (``build_journal_entry_group_number``), so the triple is unique across
    distinct entities *and* across separate per-(entity, year) GL files — two files
    can never collide on the same id.  The mapping is a pure function of its inputs,
    so a ``commit_mode='replace'`` re-run of the same scope reproduces identical ids
    (idempotent: no duplicate/blowup of fact rows).
    """
    def _one(jegn_v: object, fy_v: object, ln_v: object) -> int:
        key = f"{jegn_v}|{fy_v}|{ln_v}".encode("utf-8")
        digest = hashlib.blake2b(key, digest_size=8).digest()
        return int.from_bytes(digest, "big") & _BLID_MASK

    return pd.Series(
        [
            _one(j, fy, ln)
            for j, fy, ln in zip(
                jegn.astype("string").tolist(),
                fiscal_year.tolist(),
                line_number.tolist(),
            )
        ],
        index=jegn.index,
        dtype="int64",
    )


# --------------------------------------------------------------------------- #
# MappingProfile dataclass
# --------------------------------------------------------------------------- #
@dataclass
class MappingProfile:
    """Portable description of how a source file maps to the canonical schema.

    Serialisable to/from JSON via profile_to_dict / profile_from_dict.

    Fields
    ------
    entity : dict
        {'mode': 'fixed', 'value': '01'}  — fixed entity_prefix for the whole file.
        {'mode': 'column', 'value': '<col_name>'} — read entity_prefix per row from
        a source column (must be 1-2 digit numeric after normalization).

    fiscal_year : dict
        {'mode': 'fixed',    'value': 2024}
        {'mode': 'column',   'value': '<col_name>'}
        {'mode': 'from_date', 'value': None}  — derive from posting_date.year

    sign : dict
        Describes how to compute the signed `amount` (+ = debit, - = credit).
        mode 'signed':     {'mode': 'signed',     'amount': '<col>'}
        mode 'soll_haben': {'mode': 'soll_haben',  'soll': '<col>', 'haben': '<col>'}
        mode 'amount_dc':  {'mode': 'amount_dc',   'amount': '<col>', 'dc_flag': '<col>',
                            'debit_value': 'S'}  # debit_value default 'S'

    decimal : str
        Decimal separator in source numbers, e.g. '.' or ','.  Default '.'.
    thousands : str | None
        Thousands separator, e.g. ',' or '.'.  Default ','.  None = no stripping.
    date_dayfirst : bool
        Passed to pandas parse_date; True for DD.MM.YYYY (DATEV).  Default True.

    columns : dict[str, str | None]
        Maps each canonical *optional* target field name to the source column name.
        Keys: journal_entry_number, account_number, booking_line_id, vat_amount,
              line_note, source_type, source_no, posting_type, posting_date,
              document_date, document_type, reference_document_number, header_note,
              currency.  ``booking_line_id`` is optional: when mapped and present the
              source value is PRESERVED; otherwise a deterministic globally-unique id
              is derived from (jegn, fiscal_year, line_number).
        Value None means field is not mapped (column will be null in output).

    linking_strategy : str
        'txn' | 'gegenkonto' | 'none'.  Default 'txn'.

    entry_type : str
        Default entry_type for all rows.  Default 'actual'.

    source_system : str
        Identifies the source ERP / file type (stored on every row).  Default 'unknown'.

    entity_assignments : dict[str, str] | None
        Optional mapping of source entity label → 2-char prefix, confirmed in the wizard
        when labels are not yet in dim_legal_entity.
    """

    entity: dict = field(default_factory=lambda: {"mode": "fixed", "value": "01"})
    fiscal_year: dict = field(default_factory=lambda: {"mode": "from_date", "value": None})
    sign: dict = field(default_factory=lambda: {"mode": "signed", "amount": None})
    decimal: str = "."
    thousands: str | None = ","
    date_dayfirst: bool = True
    columns: dict[str, str | None] = field(default_factory=dict)
    linking_strategy: str = "txn"
    entry_type: str = "actual"
    source_system: str = "unknown"
    entity_assignments: dict[str, str] | None = None


def profile_from_dict(d: dict) -> MappingProfile:
    """Deserialise a JSON-compatible dict to a MappingProfile.

    Unknown keys are silently ignored so older serialised profiles can be loaded
    against a newer MappingProfile definition.
    """
    p = MappingProfile()
    if "entity" in d:
        p.entity = d["entity"]
    if "fiscal_year" in d:
        p.fiscal_year = d["fiscal_year"]
    if "sign" in d:
        p.sign = d["sign"]
    if "decimal" in d:
        p.decimal = d["decimal"]
    if "thousands" in d:
        p.thousands = d["thousands"]
    if "date_dayfirst" in d:
        p.date_dayfirst = bool(d["date_dayfirst"])
    if "columns" in d:
        p.columns = d["columns"]
    if "linking_strategy" in d:
        p.linking_strategy = d["linking_strategy"]
    if "entry_type" in d:
        p.entry_type = d["entry_type"]
    if "source_system" in d:
        p.source_system = d["source_system"]
    if "entity_assignments" in d:
        p.entity_assignments = d["entity_assignments"]
    return p


def profile_to_dict(p: MappingProfile) -> dict:
    """Serialise a MappingProfile to a JSON-compatible dict."""
    return {
        "entity": p.entity,
        "fiscal_year": p.fiscal_year,
        "sign": p.sign,
        "decimal": p.decimal,
        "thousands": p.thousands,
        "date_dayfirst": p.date_dayfirst,
        "columns": p.columns,
        "linking_strategy": p.linking_strategy,
        "entry_type": p.entry_type,
        "source_system": p.source_system,
        "entity_assignments": p.entity_assignments,
    }


# --------------------------------------------------------------------------- #
# Decimal-separator sniffing (money-column oriented)
# --------------------------------------------------------------------------- #
def sniff_decimal_separator(values: Iterable[str]) -> tuple[str, str] | None:
    """Infer ``(decimal, thousands)`` from a sample of amount-column strings.

    Pure, side-effect-free helper for locale-robust money parsing. The amount
    column is the reliable decimal signal (delimiter-based detection alone is
    unreliable: a comma-delimited file can still use German decimals, and
    vice-versa). Returns the complementary pair ``('.', ',')`` or ``(',', '.')``,
    or ``None`` when the sample is inconclusive (caller keeps its default).

    Per-value heuristic (after stripping whitespace, currency symbols and sign):
      * BOTH ``.`` and ``,`` present  -> the separator whose LAST occurrence is
        rightmost is the DECIMAL; the other is thousands.
      * Only ONE separator type present:
          - occurs >1 time            -> thousands grouping only (no decimal vote).
          - occurs exactly once, trailing-digit count != 3 (1, 2, or >3 digits)
                                      -> that char is the DECIMAL.
          - occurs exactly once, EXACTLY 3 trailing digits -> AMBIGUOUS (no vote).
      * No separator                  -> no vote.
    Votes are aggregated across the whole sample; the strict majority wins. A tie
    or an all-abstain sample is inconclusive -> ``None``.

    Worked examples::

        52803.84       -> decimal '.'   (single '.', 2 trailing digits)
        52803.840000000004 -> decimal '.'  (single '.', >3 trailing digits)
        52.803,84      -> decimal ','   (',' is rightmost)
        1.234.567,89   -> decimal ','   (',' is rightmost)
        1,234,567.89   -> decimal '.'   ('.' is rightmost)
        1,234          -> abstains      (single ',', exactly 3 trailing digits)
    """
    dot_votes = 0
    comma_votes = 0
    for raw in values:
        if raw is None:
            continue
        s = str(raw).strip()
        if not s:
            continue
        # Keep only digits and the two separator candidates; this drops
        # whitespace, currency symbols (€ $ £ …) and any leading/trailing sign.
        s = re.sub(r"[^0-9.,]", "", s)
        if not s:
            continue
        has_dot = "." in s
        has_comma = "," in s
        if has_dot and has_comma:
            # Rightmost last-occurrence is the decimal separator.
            if s.rfind(".") > s.rfind(","):
                dot_votes += 1
            else:
                comma_votes += 1
        elif has_dot:
            if s.count(".") > 1:
                continue  # grouping only -> no decimal vote
            if len(s.rsplit(".", 1)[1]) != 3:
                dot_votes += 1
            # exactly 3 trailing digits -> ambiguous -> abstain
        elif has_comma:
            if s.count(",") > 1:
                continue
            if len(s.rsplit(",", 1)[1]) != 3:
                comma_votes += 1
        # else: no separator -> no vote

    if dot_votes == comma_votes:
        return None  # tie or all-abstain -> inconclusive
    if dot_votes > comma_votes:
        return (".", ",")
    return (",", ".")


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _col(raw: pd.DataFrame, col_name: str | None) -> pd.Series | None:
    """Return a Series for col_name if it is non-None and present in raw, else None."""
    if col_name is None or col_name not in raw.columns:
        return None
    return raw[col_name]


def _nullable(raw: pd.DataFrame, col_name: str | None) -> pd.Series:
    """Return a string Series for col_name, or a null-filled Series if absent."""
    s = _col(raw, col_name)
    if s is None:
        return pd.Series([pd.NA] * len(raw), index=raw.index, dtype="string")
    return T.normalize_token(s)


# --------------------------------------------------------------------------- #
# apply_profile
# --------------------------------------------------------------------------- #
def apply_profile(
    raw_df: pd.DataFrame,
    profile: MappingProfile,
    account_class_lookup: dict[str, str] | None = None,
    entity_lookup: dict[str, str] | None = None,
    *,
    opening_balance: bool = False,
    drop_unknown_entities: bool = False,
) -> pd.DataFrame:
    """Apply a MappingProfile to a raw uploaded DataFrame.

    Parameters
    ----------
    raw_df : pd.DataFrame
        The raw source data as loaded from a CSV/XLSX upload.  Column names are
        those in the original file; no pre-processing expected.
    profile : MappingProfile
        Fully configured mapping profile.
    account_class_lookup : dict[str, str] | None
        Optional mapping {gl_account_id -> account_class}.  If provided, the
        canonical `account_class` column is populated by looking up the derived
        `gl_account_id`.  Falls back to None/'other' for unknown accounts.
    opening_balance : bool, keyword-only, default False
        Opening-balance path. OB files carry only account number + amount (no
        posting date, no transaction number). When True, an unmapped/missing
        `posting_date` yields an all-NaT column (Jan-1 synthesis happens later in
        ``opening_balance_commit``) and an unmapped/missing
        `journal_entry_number` yields an all-NA column (re-minted downstream).
        `fiscal_year` mode 'from_date' is rejected (no date to derive from). When
        False (GL/CoA/Partner default) the function behaves byte-identically to
        before: posting_date and journal_entry_number stay strictly required.

    Returns
    -------
    pd.DataFrame
        Canonical lines DataFrame with all columns listed in the module docstring.
        Rows retain the same order as raw_df.  Dtypes are best-effort (strings as
        StringDtype, numerics as float64, dates as datetime64).

    Raises
    ------
    KeyError
        If a mandatory source column named in the profile is not found in raw_df.
        Optional columns (columns dict values) silently yield nulls if absent.
    ValueError
        If entity mode / fiscal_year mode / sign mode is invalid.
    """
    cols = profile.columns
    n = len(raw_df)
    idx = raw_df.index

    # ------------------------------------------------------------------ entity prefix
    from etl.entity_resolve import prefix_series_from_entity_config

    lookup = entity_lookup if entity_lookup is not None else {}
    prefix_s = prefix_series_from_entity_config(
        raw_df,
        profile.entity,
        lookup,
        profile.entity_assignments,
        drop_unknown=drop_unknown_entities,
    )

    # When dropping unknown entities (OB path), unresolved rows carry NA in
    # prefix_s. Filter them out of the working frame BEFORE the downstream
    # group-number builders run (they choke on NA prefixes). The dropped raw
    # entity labels are surfaced to the caller via canonical.attrs below.
    dropped_entity_labels: list[str] = []
    if drop_unknown_entities and prefix_s.isna().any():
        mask = prefix_s.notna()
        entity_cfg = profile.entity
        if entity_cfg.get("mode") == "column":
            ent_col = entity_cfg.get("value")
            if ent_col in raw_df.columns:
                dropped_entity_labels = sorted(
                    {
                        str(v)
                        for v in raw_df.loc[~mask, ent_col].dropna().tolist()
                    }
                )
        raw_df = raw_df.loc[mask]
        idx = raw_df.index
        n = len(raw_df)
        prefix_s = prefix_s.loc[mask]

    # ------------------------------------------------------------------ posting_date (required for GL; optional for OB)
    pd_col = cols.get("posting_date")
    if pd_col is None or pd_col not in raw_df.columns:
        if opening_balance:
            # OB files carry no posting date; synthesis (Jan-1 of the fiscal year)
            # happens later in opening_balance_commit. Leave it all-NaT here.
            posting_date = pd.Series(pd.NaT, index=idx, dtype="datetime64[ns]")
        else:
            raise KeyError(
                "The 'Posting date' column is not mapped. Assign a source column as "
                "'Posting date' in the GL header step, then re-validate."
            )
    else:
        posting_date = T.parse_date(raw_df[pd_col], dayfirst=profile.date_dayfirst)

    # ------------------------------------------------------------------ fiscal_year
    fy_cfg = profile.fiscal_year
    if fy_cfg["mode"] == "fixed":
        fiscal_year_s = pd.Series([int(fy_cfg["value"])] * n, index=idx)
    elif fy_cfg["mode"] == "column":
        src_col = fy_cfg["value"]
        if src_col not in raw_df.columns:
            raise KeyError(f"fiscal_year column {src_col!r} not found in source file")
        fiscal_year_s = pd.to_numeric(raw_df[src_col], errors="coerce").astype("Int64")
    elif fy_cfg["mode"] == "from_date":
        if opening_balance:
            raise ValueError(
                "Opening-balance files carry no posting date, so the fiscal year "
                "can't be derived from it. Select a fiscal year for the project "
                "(first-year mode) or map a fiscal-year column (all-years mode), "
                "then retry."
            )
        fiscal_year_s = posting_date.dt.year.astype("Int64")
    else:
        raise ValueError(f"unknown fiscal_year mode {fy_cfg['mode']!r}")

    # ------------------------------------------------------------------ journal_entry_number (required for GL; optional for OB)
    jen_col = cols.get("journal_entry_number")
    if jen_col is None or jen_col not in raw_df.columns:
        if opening_balance:
            # OB files carry no transaction number; opening_balance_commit re-mints a
            # synthetic group number (entity prefix + '9' + account). Null here.
            jegn = pd.Series(pd.NA, index=idx, dtype="string")
        else:
            raise KeyError(
                "The 'Transaction/journal number' column is not mapped. Assign it in "
                "the GL header step, then re-validate."
            )
    else:
        jegn = T.build_journal_entry_group_number(prefix_s, raw_df[jen_col])

    # ------------------------------------------------------------------ account_number (required)
    acct_col = cols.get("account_number")
    if acct_col is None or acct_col not in raw_df.columns:
        raise KeyError(
            "The 'Account number' column is not mapped. Assign it in the GL header "
            "step, then re-validate."
        )
    ang = T.build_account_number_group(prefix_s, raw_df[acct_col])
    gl_account_id = T.normalize_token(raw_df[acct_col])

    # ------------------------------------------------------------------ amount (signed)
    sign_cfg = profile.sign
    s_mode = sign_cfg.get("mode", "signed")
    def _sign_col(key: str, label: str):
        """Resolve a sign-related source column, with a clear error when it is
        unmapped/missing (otherwise raw_df[''] raises a cryptic KeyError('')
        that surfaced to the user as just \"''\")."""
        name = sign_cfg.get(key, "")
        if not name or name not in raw_df.columns:
            raise ValueError(
                f"The '{label}' column is not mapped. Select it in the Options "
                "step, then re-validate."
            )
        return raw_df[name]

    if s_mode == "signed":
        amount_s = T.signed_amount(
            "signed",
            amount=_sign_col("amount", "Amount"),
            decimal=profile.decimal,
            thousands=profile.thousands,
        )
    elif s_mode == "soll_haben":
        amount_s = T.signed_amount(
            "soll_haben",
            soll=_sign_col("soll", "Debit (Soll)"),
            haben=_sign_col("haben", "Credit (Haben)"),
            decimal=profile.decimal,
            thousands=profile.thousands,
        )
    elif s_mode == "amount_dc":
        amount_s = T.signed_amount(
            "amount_dc",
            amount=_sign_col("amount", "Amount"),
            dc_flag=_sign_col("dc_flag", "Debit/Credit indicator"),
            debit_value=sign_cfg.get("debit_value", "S"),
            decimal=profile.decimal,
            thousands=profile.thousands,
        )
    else:
        raise ValueError(f"unknown sign mode {s_mode!r}")

    # ------------------------------------------------------------------ fiscal_period (from posting_date month)
    fiscal_period_s = posting_date.dt.month.astype("Int64")

    # ------------------------------------------------------------------ optional columns
    vat_amount_col = cols.get("vat_amount")
    vat_amount_s: pd.Series
    if vat_amount_col and vat_amount_col in raw_df.columns:
        vat_amount_s = T.parse_decimal(raw_df[vat_amount_col], profile.decimal, profile.thousands)
    else:
        vat_amount_s = pd.Series([pd.NA] * n, index=idx, dtype="Float64")

    document_date_col = cols.get("document_date")
    if document_date_col and document_date_col in raw_df.columns:
        document_date_s = T.parse_date(raw_df[document_date_col], dayfirst=profile.date_dayfirst)
    else:
        document_date_s = pd.Series([pd.NaT] * n, index=idx, dtype="datetime64[ns]")

    # ------------------------------------------------------------------ partner columns
    source_type_col = cols.get("source_type")
    source_no_col = cols.get("source_no")
    if source_type_col and source_no_col and \
            source_type_col in raw_df.columns and source_no_col in raw_df.columns:
        customer_id_s, supplier_id_s = T.derive_partner_ids(
            prefix_s,
            raw_df[source_type_col],
            raw_df[source_no_col],
        )
    else:
        customer_id_s = pd.Series([pd.NA] * n, index=idx, dtype="string")
        supplier_id_s = pd.Series([pd.NA] * n, index=idx, dtype="string")

    # ------------------------------------------------------------------ line_number within booking
    # Temporary frame for groupby; line_numbers assigned per (jegn) group in input order.
    _tmp = pd.DataFrame({"jegn": jegn}, index=idx)
    line_number_s = T.assign_line_numbers(_tmp, ["jegn"])

    # ------------------------------------------------------------------ booking_line_id (globally-unique, deterministic)
    # PRESERVE a source-provided booking_line_id when the column is mapped and present;
    # otherwise derive a deterministic, globally-unique id from
    # (journal_entry_group_number, fiscal_year, line_number) so two separate per-entity
    # GL files never collide on the UNIQUE(booking_line_id) constraint and a
    # commit_mode='replace' re-run reproduces the same ids (idempotent).
    blid_col = cols.get("booking_line_id")
    if blid_col and blid_col in raw_df.columns:
        booking_line_id_s = (
            pd.to_numeric(raw_df[blid_col], errors="coerce").astype("Int64")
        )
        # Fall back to the deterministic id for any row missing a source value.
        if booking_line_id_s.isna().any():
            derived = _deterministic_booking_line_id(jegn, fiscal_year_s, line_number_s)
            booking_line_id_s = booking_line_id_s.fillna(
                pd.Series(derived.values, index=idx, dtype="Int64")
            )
        booking_line_id_s = booking_line_id_s.astype("int64")
    else:
        booking_line_id_s = _deterministic_booking_line_id(
            jegn, fiscal_year_s, line_number_s
        )

    # ------------------------------------------------------------------ account_class
    if account_class_lookup:
        account_class_s = gl_account_id.map(lambda v: account_class_lookup.get(str(v), "other")).astype("string")
    else:
        account_class_s = pd.Series(["other"] * n, index=idx, dtype="string")

    # ------------------------------------------------------------------ assemble canonical frame
    out = pd.DataFrame(
        {
            "journal_entry_group_number": jegn,
            "fiscal_year": fiscal_year_s,
            "fiscal_period": fiscal_period_s,
            "line_number": line_number_s,
            "booking_line_id": booking_line_id_s,
            "account_number_group": ang,
            "gl_account_id": gl_account_id,
            "amount": amount_s,
            "vat_amount": vat_amount_s,
            "line_note": _nullable(raw_df, cols.get("line_note")),
            "customer_id": customer_id_s,
            "supplier_id": supplier_id_s,
            "posting_type": _nullable(raw_df, cols.get("posting_type")),
            "posting_date": posting_date,
            "document_date": document_date_s,
            "document_type_code": _nullable(raw_df, cols.get("document_type")),
            "reference_document_number": _nullable(raw_df, cols.get("reference_document_number")),
            "currency_code": _nullable(raw_df, cols.get("currency")).fillna("EUR"),
            "header_note": _nullable(raw_df, cols.get("header_note")),
            "entry_type": pd.Series([profile.entry_type] * n, index=idx, dtype="string"),
            "account_class": account_class_s,
            "source_system": pd.Series([profile.source_system] * n, index=idx, dtype="string"),
        },
        index=idx,
    )

    # Surface the entity labels dropped as "not in the project" (OB path only).
    out.attrs["ignored_entity_labels"] = dropped_entity_labels

    return out
