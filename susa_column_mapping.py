"""
Shared SuSa column-mapping helpers (preview API + SuSabyYear config reader).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

EN_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

MONTH_NAME_PATTERNS: List[Tuple[int, List[str]]] = [
    (1, ["jan", "januar", "january", "janury", "ian"]),
    (2, ["feb", "februar", "february"]),
    (3, ["mar", "maerz", "mrz", "march", "märz"]),
    (4, ["apr", "april"]),
    (5, ["mai", "may"]),
    (6, ["jun", "juni", "june"]),
    (7, ["jul", "juli", "july"]),
    (8, ["aug", "august"]),
    (9, ["sep", "sept", "september"]),
    (10, ["okt", "oct", "oktober", "october"]),
    (11, ["nov", "november"]),
    (12, ["dez", "dec", "dezember", "december"]),
]


def excel_col_to_idx(col: str) -> int:
    idx = 0
    for c in col.strip().upper():
        idx = idx * 26 + (ord(c) - ord("A") + 1)
    return idx - 1


def idx_to_excel_col(idx: int) -> str:
    idx += 1
    letters = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def looks_like_account(x) -> bool:
    if pd.isna(x):
        return False
    return bool(re.fullmatch(r"\d{2,6}", str(x).replace(".0", "").strip()))


def _row_looks_like_period_header(row: pd.Series) -> bool:
    for v in row.values:
        if pd.isna(v):
            continue
        text = str(v).strip()
        if not text:
            continue
        month, _year = parse_month_year_from_label(text, None)
        if month is not None:
            return True
        low = text.lower()
        if low in ("konto", "konten", "account", "beschriftung", "bezeichnung"):
            return True
    return False


def find_header_row(df: pd.DataFrame) -> int:
    """
    Locate the column header row (Konto / Jan / Feb / …), not the first data row.

    SuSa exports often have a section row (e.g. 'AKTIVA …') between headers and accounts;
    using (first_account_row - 1) would pick that section row instead of real headers.
    """
    df = df.dropna(how="all").reset_index(drop=True)
    first_account_row: Optional[int] = None
    for i in range(len(df)):
        if any(looks_like_account(v) for v in df.iloc[i].values):
            first_account_row = i
            break
    if first_account_row is None:
        raise ValueError("Header row could not be detected")

    for j in range(first_account_row - 1, -1, -1):
        if _row_looks_like_period_header(df.iloc[j]):
            return j
    return max(first_account_row - 1, 0)


def profile_for_entity(config: dict, entity_name: str) -> dict:
    cm = config.get("column_mapping") or {}
    if isinstance(cm, str):
        import json
        try:
            cm = json.loads(cm)
        except json.JSONDecodeError:
            cm = {}
    default = dict(cm.get("default") or {})
    overrides = cm.get("entities") or {}
    if entity_name and entity_name in overrides:
        merged = {**default, **dict(overrides[entity_name])}
        return merged
    return default


def normalize_label_text(text: str) -> str:
    s = str(text or "").strip().lower()
    return (
        s.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def parse_month_year_from_label(
    text: str,
    default_year: Optional[int] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse month (1-12) and optional year from header cell text.
    Supports numeric (01-2022, 2022/01) and word forms (Jan, Jan22, Januar, …).
    """
    s = normalize_label_text(text)
    if not s:
        return None, default_year

    year: Optional[int] = default_year
    ym = re.search(r"(20\d{2}|19\d{2})", s)
    if ym:
        year = int(ym.group(1))

    y2 = re.search(r"(?<![0-9])(\d{2})(?![0-9])", s)
    if year is None and y2:
        yy = int(y2.group(1))
        year = 2000 + yy if yy < 70 else 1900 + yy

    for mm, keys in MONTH_NAME_PATTERNS:
        if any(k in s for k in keys):
            return mm, year

    m_num = re.search(
        r"(?:^|[^\d])(0?[1-9]|1[0-2])(?:\s*[-/.]\s*(?:0?[1-9]|1[0-2]))?(?:[^\d]|$)",
        s,
    )
    if not m_num:
        m_num = re.search(r"\b(0?[1-9]|1[0-2])\b", s)
    if m_num:
        return int(m_num.group(1)), year

    return None, year


def period_roles_in_order(sign_mode: str) -> List[str]:
    """Column order within one period block: amount first, then indicator(s)."""
    if sign_mode == "sh_two_columns":
        return ["amount", "sh_s", "sh_h"]
    if sign_mode == "sh_column":
        return ["amount", "sh"]
    return ["amount"]


def block_size_for_sign_mode(sign_mode: str) -> int:
    return len(period_roles_in_order(sign_mode))


def header_labels_for_column(raw: pd.DataFrame, header_row: int, col_idx: int, lookback: int = 3) -> str:
    parts: List[str] = []
    for r in range(max(0, header_row - lookback), min(header_row + 1, len(raw))):
        v = raw.iloc[r, col_idx] if col_idx < raw.shape[1] else None
        if pd.notna(v) and str(v).strip():
            parts.append(str(v).strip())
    return " ".join(parts)


def resolve_header_row(raw: pd.DataFrame, profile: dict) -> int:
    """Prefer detected Konto/month header row; ignore mapper row if it is a section/data row."""
    df = raw.dropna(how="all").reset_index(drop=True)
    detected = find_header_row(df)
    mapped = profile.get("header_row")
    if mapped is None or mapped == "":
        return detected
    try:
        mapped_i = int(mapped)
    except (TypeError, ValueError):
        return detected
    if 0 <= mapped_i < len(df) and _row_looks_like_period_header(df.iloc[mapped_i]):
        return mapped_i
    return detected


def build_period_blocks_from_profile(
    raw: pd.DataFrame,
    profile: dict,
    sign_mode: str,
    default_year: int,
) -> List[dict]:
    """
    Build period_blocks with month/year from header labels (not assumed Jan..Dec order).
    """
    df = raw.dropna(how="all").reset_index(drop=True)
    header_row = resolve_header_row(raw, profile)
    ncols = df.shape[1]
    roles = period_roles_in_order(sign_mode)
    block_size = len(roles)

    repeating = profile.get("repeating") or {}
    explicit = list(profile.get("period_blocks") or [])
    use_explicit = False
    if explicit and all(b.get("month") for b in explicit) and not repeating.get("enabled"):
        use_explicit = True
    elif explicit and all(b.get("month") for b in explicit) and repeating.get("enabled"):
        months = {int(b["month"]) for b in explicit if b.get("month")}
        years = [int(b.get("year") or default_year) for b in explicit]
        plausible_years = all(1990 <= y <= default_year + 1 for y in years)
        if len(months) >= 10 and plausible_years:
            use_explicit = True
    if use_explicit:
        blocks = sorted(
            explicit,
            key=lambda b: (int(b.get("year") or default_year), int(b.get("month") or 99)),
        )
        return finalize_period_blocks(blocks, df, header_row, default_year)
    start_col = 0
    if repeating.get("enabled"):
        start_col = excel_col_to_idx(str(repeating.get("from_col") or "A"))
        block_size = int(repeating.get("block_size") or block_size)
    elif explicit:
        first = explicit[0]
        amount_letter = first.get("amount") or first.get("soll")
        if amount_letter:
            start_col = excel_col_to_idx(amount_letter)
    else:
        return []

    blocks: List[dict] = []
    col = start_col
    while col + block_size - 1 < ncols:
        block: dict = {}
        for j, role in enumerate(roles):
            block[role] = idx_to_excel_col(col + j)
        label = header_labels_for_column(df, header_row, col)
        if is_summary_total_label(label):
            col += block_size
            continue
        month, yr = parse_month_year_from_label(label, default_year)
        if month is not None:
            block["month"] = month
            block["year"] = int(yr or default_year)
        blocks.append(block)
        col += block_size

    def sort_key(b: dict) -> Tuple[int, int, int]:
        m = int(b.get("month") or 99)
        y = int(b.get("year") or default_year)
        return (y, m, excel_col_to_idx(str(b.get("amount") or b.get("soll") or "A")))

    blocks = sorted(blocks, key=sort_key)
    for i, b in enumerate(blocks):
        if not b.get("month"):
            b["month"] = i + 1
            b["year"] = default_year
    return finalize_period_blocks(blocks, df, header_row, default_year)


def expand_period_blocks(profile: dict, ncols: int, sign_mode: str, default_year: int = 0) -> List[dict]:
    """Legacy expander when raw matrix is unavailable — prefers explicit period_blocks."""
    blocks = list(profile.get("period_blocks") or [])
    if blocks:
        return blocks
    repeating = profile.get("repeating")
    if not repeating or not repeating.get("enabled"):
        return blocks
    roles = period_roles_in_order(sign_mode)
    block_size = int(repeating.get("block_size") or len(roles))
    start = excel_col_to_idx(str(repeating.get("from_col") or "A"))
    out: List[dict] = []
    col = start
    seq = 1
    while col + block_size - 1 < ncols and seq <= 12:
        block: dict = {"month": seq, "year": default_year}
        for j, role in enumerate(roles):
            block[role] = idx_to_excel_col(col + j)
        out.append(block)
        col += block_size
        seq += 1
    return out


def build_preview(raw: pd.DataFrame, max_rows: int = 25) -> dict:
    """Return header_row_index, column letters with samples, and preview rows."""
    df = raw.dropna(how="all").reset_index(drop=True)
    header_row = find_header_row(df)
    ncols = df.shape[1]
    columns = []
    for i in range(ncols):
        letter = idx_to_excel_col(i)
        samples = []
        for r in range(header_row + 1, min(header_row + 8, len(df))):
            v = df.iloc[r, i]
            if pd.notna(v) and str(v).strip():
                samples.append(str(v).strip()[:40])
        columns.append({"letter": letter, "sample_values": samples[:5]})
    rows: List[List[str]] = []
    for r in range(header_row, min(header_row + max_rows, len(df))):
        rows.append(["" if pd.isna(v) else str(v) for v in df.iloc[r].tolist()])
    return {
        "header_row_index": int(header_row),
        "columns": columns,
        "rows": rows,
    }


def col_series(df: pd.DataFrame, letter: Optional[str]) -> Optional[pd.Series]:
    if not letter:
        return None
    idx = excel_col_to_idx(letter)
    if idx >= df.shape[1]:
        raise ValueError(f"Column {letter} out of range (width {df.shape[1]}).")
    return df.iloc[:, idx]


def parse_number_de(x):
    if pd.isna(x):
        return pd.NA
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s == "":
        return pd.NA
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "")
    s = s.replace("€", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return pd.NA


def apply_sign_indicator(amount_raw, indicator_raw) -> float:
    """Apply S/H/X indicator to an amount (number always in amount column).

  Soll (S) → positive magnitude; Haben (H) → negative magnitude.
  """
    amt = parse_number_de(amount_raw)
    if pd.isna(amt):
        return float("nan")
    val = float(amt)
    ind = str(indicator_raw).strip().upper() if pd.notna(indicator_raw) else ""
    if ind in ("S", "SOLL", "D"):
        return abs(val)
    if ind in ("H", "HABEN", "C"):
        return -abs(val)
    if ind in ("X", "", "—", "-"):
        return val
    return val


def is_summary_total_label(label: str) -> bool:
    """True for Gesamtsaldo / year-total columns that are not a monthly period."""
    s = normalize_label_text(label)
    if not s:
        return False
    return any(
        k in s
        for k in (
            "gesamtsaldo",
            "summensaldo",
            "jahressaldo",
            "jahres saldo",
            "summe gesamt",
        )
    )


def finalize_period_blocks(
    blocks: List[dict],
    raw: pd.DataFrame,
    header_row: int,
    default_year: int,
) -> List[dict]:
    """Drop Gesamtsaldo columns; keep one block per month (prefer FY + header match)."""
    df = raw.dropna(how="all").reset_index(drop=True)
    best: Dict[int, Tuple[int, dict]] = {}
    for b in blocks:
        m = int(b.get("month") or 0)
        if m < 1 or m > 12:
            continue
        amount_letter = str(b.get("amount") or b.get("soll") or "A")
        col_idx = excel_col_to_idx(amount_letter)
        label = header_labels_for_column(df, header_row, col_idx)
        if is_summary_total_label(label):
            continue
        y = int(b.get("year") or default_year)
        score = 0
        if y == default_year:
            score += 4
        elif abs(y - default_year) <= 1:
            score += 1
        parsed_m, _ = parse_month_year_from_label(label, default_year)
        if parsed_m == m:
            score += 2
        prev = best.get(m)
        if prev is None or score > prev[0]:
            kept = dict(b)
            kept["month"] = m
            kept["year"] = default_year
            best[m] = (score, kept)
    return [best[m][1] for m in sorted(best)]


def signed_amount(
    amount_raw,
    sign_mode: str,
    sh_val=None,
    soll_raw=None,
    haben_raw=None,
) -> float:
    amt = parse_number_de(amount_raw)
    if pd.isna(amt):
        return float("nan")
    mode = sign_mode.strip()
    if mode == "sh_two_columns":
        soll = parse_number_de(soll_raw)
        haben = parse_number_de(haben_raw)
        s = 0.0 if pd.isna(soll) else float(soll)
        h = 0.0 if pd.isna(haben) else float(haben)
        return s - h
    if mode == "sh_column" and sh_val is not None:
        return apply_sign_indicator(amt, sh_val)
    return float(amt)


def filter_ap_ar_by_length(df: pd.DataFrame, length: Optional[int]) -> pd.DataFrame:
    if not length or length < 1:
        return df
    acc = df["Account"].astype(str).str.replace(".0", "", regex=False).str.strip()
    drop = acc.str.len() == int(length)
    return df[~drop].copy()
