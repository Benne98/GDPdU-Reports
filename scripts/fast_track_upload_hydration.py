"""Recover Fast Track OPOS/FTE/FA sections from session uploads when mapper state was lost."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts.revenue_column_matching import _normalize_header, _score_header

STRAND_ROLE_ALIASES: dict[str, list[str]] = {
    "opos_partner": ["debitor", "kreditor", "partner", "konto", "account", "customer", "text"],
    "opos_amount": [
        "betrag in hauswahrung",
        "betrag in hauswährung",
        "amount",
        "balance",
        "saldo",
        "open amount",
    ],
    "opos_due_date": [
        "nettofalligkeit",
        "nettofälligkeit",
        "due date",
        "falligkeit",
        "fälligkeit",
        "faelligkeit",
    ],
    "fte_employment": ["beschaftigungsgrad", "beschäftigungsgrad", "employment rate", "employment"],
    "fte_months_sum": ["summe", "annual working time", "months sum", "working time months"],
    "fte_payroll": ["gesamtsumme", "payroll", "lohn", "gehalt", "personnel cost"],
    "fte_group": ["bereich", "kostenstelle", "cost center", "department", "org unit"],
    "fa_opening": ["buchwert gj-beg", "buchwert gj beg", "ahk gj-beg", "opening", "opening balance"],
    "fa_additions": ["zugang", "additions", "addition"],
    "fa_disposals": ["abgang", "disposals", "disposal"],
    "fa_depreciation": ["afa des jahres", "afa des jahres", "depreciation", "afa"],
    "fa_group": ["bilanzposition", "anlagenklasse", "asset class", "category"],
}


def coerce_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _session_dir(session_id: str, upload_base: str | Path) -> Path:
    return Path(upload_base).expanduser() / str(session_id or "").strip()


def _file_id_from_path(path: Path) -> str:
    return path.stem.split("_", 1)[0]


def _year_from_stem(stem: str) -> int | None:
    match = re.search(r"(?:^|_)(\d{4})(?:_|$)", stem)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:^|_)(\d{2})(?:_|$)", stem)
    if match:
        return 2000 + int(match.group(1))
    return None


def _label_from_year(year: int, *, latest_year: int | None) -> str:
    if latest_year is not None and year == latest_year and year >= 2020:
        return f"YTD{year}"
    return f"FY{year}"


def _read_headers(file_path: Path, sheet_name: str = "") -> tuple[str, list[str]]:
    try:
        import pandas as pd
    except ImportError:
        return "", []
    try:
        xl = pd.ExcelFile(file_path)
        sheet = sheet_name.strip() or (xl.sheet_names[0] if xl.sheet_names else "")
        if not sheet:
            return "", []
        df = pd.read_excel(file_path, sheet_name=sheet, nrows=0)
        headers = [str(c).strip() for c in df.columns if str(c).strip()]
        return sheet, headers
    except Exception:
        return "", []


def _suggest_header(headers: list[str], role: str, *, used: set[str] | None = None) -> str:
    used = used or set()
    if role == "fa_opening":
        for header in headers:
            if header in used:
                continue
            norm = _normalize_header(header)
            if "buchwert" in norm and "gj" in norm:
                return header
    aliases = STRAND_ROLE_ALIASES.get(role, [])
    best_header = ""
    best_score = 0
    for header in headers:
        if header in used:
            continue
        score = max(_score_header(header, alias) for alias in aliases) if aliases else 0
        if score > best_score:
            best_score = score
            best_header = header
    if best_header and best_score >= 72:
        return best_header
    return ""


def _suggest_payroll_cols(headers: list[str], *, used: set[str]) -> list[str]:
    preferred = _suggest_header(headers, "fte_payroll", used=used)
    if preferred:
        return [preferred]
    month_tokens = {
        "januar", "februar", "marz", "märz", "april", "mai", "juni", "juli",
        "august", "september", "oktober", "november", "dezember",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    }
    month_cols = [
        h for h in headers
        if h not in used and _normalize_header(h).split()[0] in month_tokens
    ]
    return month_cols[:12]


def _suggest_depreciation_cols(headers: list[str], *, used: set[str]) -> list[str]:
    cols: list[str] = []
    for role in ("fa_depreciation",):
        hit = _suggest_header(headers, role, used=used | set(cols))
        if hit:
            cols.append(hit)
    for header in headers:
        norm = _normalize_header(header)
        if header in used or header in cols:
            continue
        if "abgang" in norm and "afa" in norm:
            cols.append(header)
    return cols or ([_suggest_header(headers, "fa_depreciation", used=used)] if headers else [])


def _discover_side_files(session_dir: Path, side: str) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for path in sorted(session_dir.glob(f"*_{side}_*.xlsx")):
        if not path.is_file():
            continue
        year = _year_from_stem(path.stem)
        if year is None:
            continue
        out[year] = path
    return out


def _discover_period_files(session_dir: Path, token: str) -> dict[int, Path]:
    out: dict[int, Path] = {}
    pattern = re.compile(rf"_{re.escape(token)}_(\d{{4}})\.xlsx$", re.IGNORECASE)
    short_pattern = re.compile(rf"_{re.escape(token)}_(\d{{2}})\.xlsx$", re.IGNORECASE)
    for path in sorted(session_dir.glob("*.xlsx")):
        if not path.is_file():
            continue
        stem = path.stem.lower()
        if token not in stem:
            continue
        match = pattern.search(path.name) or short_pattern.search(path.name)
        if not match:
            continue
        raw = match.group(1)
        year = int(raw) if len(raw) == 4 else 2000 + int(raw)
        out[year] = path
    return out


def _section_skipped(section: Any) -> bool:
    if section is False:
        return True
    return isinstance(section, dict) and section.get("included") is False


def _section_has_user_mapping(name: str, section: dict[str, Any]) -> bool:
    if section.get("mapper_confirmed"):
        return True
    cols = section.get("columns") if isinstance(section.get("columns"), dict) else {}
    group_cols = section.get("group_cols") if isinstance(section.get("group_cols"), list) else []
    if name == "opos":
        return bool(str(cols.get("partner_id") or "").strip())
    if name == "fte":
        return bool(
            str(cols.get("employment") or "").strip()
            and str(cols.get("months_sum") or "").strip()
            and cols.get("payroll_cols")
            and any(str(g).strip() for g in group_cols)
        )
    if name == "fa":
        return bool(
            str(cols.get("opening") or "").strip()
            and any(str(g).strip() for g in group_cols)
        )
    return False


def _section_complete(name: str, section: dict[str, Any]) -> bool:
    if name == "opos":
        cols = section.get("columns") if isinstance(section.get("columns"), dict) else {}
        side_blocks = section.get("sides") if isinstance(section.get("sides"), dict) else {}
        if side_blocks:
            deb = (side_blocks.get("debitor") or {}).get("snapshots") if isinstance(side_blocks.get("debitor"), dict) else None
            kred = (side_blocks.get("kreditor") or {}).get("snapshots") if isinstance(side_blocks.get("kreditor"), dict) else None
            return bool(
                isinstance(deb, list)
                and deb
                and isinstance(kred, list)
                and kred
                and cols.get("partner_id")
                and cols.get("amount")
            )
        if section.get("sides"):
            return bool(cols.get("partner_id") or cols.get("amount"))
        snaps = section.get("snapshots")
        return bool(isinstance(snaps, list) and snaps and cols.get("partner_id") and cols.get("amount"))
    if name in ("fte", "fa"):
        periods = section.get("periods")
        cols = section.get("columns") if isinstance(section.get("columns"), dict) else {}
        if not isinstance(periods, list) or not periods:
            return False
        if name == "fte":
            group_cols = section.get("group_cols") if isinstance(section.get("group_cols"), list) else []
            return bool(
                cols.get("employment")
                and cols.get("months_sum")
                and cols.get("payroll_cols")
                and any(str(g).strip() for g in group_cols)
            )
        return bool(
            cols.get("opening")
            and cols.get("additions")
            and cols.get("disposals")
            and cols.get("depreciation_cols")
        )
    return False


def _letters_for_headers(
    headers: list[str],
    *,
    partner: str,
    amount: str,
    due_date: str,
) -> dict[str, str]:
    from openpyxl.utils import get_column_letter

    def letter_for(name: str) -> str:
        for idx, header in enumerate(headers):
            if header == name:
                return get_column_letter(idx + 1)
        return ""

    return {
        "partner": letter_for(partner),
        "amount": letter_for(amount),
        "due_date": letter_for(due_date),
    }


def _build_opos_section(
    session_dir: Path,
    *,
    fy_end_month: int,
    fy_end_day: int,
) -> dict[str, Any] | None:
    debitors = _discover_side_files(session_dir, "debitor")
    creditors = _discover_side_files(session_dir, "kreditor")
    if not debitors or not creditors:
        return None
    snapshots: list[dict[str, Any]] = []
    sides: dict[str, dict[str, list[dict[str, Any]]]] = {
        "debitor": {"snapshots": []},
        "kreditor": {"snapshots": []},
    }
    columns: dict[str, str] = {}
    column_letters: dict[str, str] = {}
    for year in sorted(set(debitors) & set(creditors)):
        deb_path = debitors[year]
        kred_path = creditors[year]
        deb_sheet, deb_headers = _read_headers(deb_path)
        if not deb_headers:
            return None
        used: set[str] = set()
        partner = ""
        for header in deb_headers:
            if _normalize_header(header) == "debitor":
                partner = header
                break
        if not partner:
            partner = _suggest_header(deb_headers, "opos_partner", used=used)
        amount = _suggest_header(deb_headers, "opos_amount", used=used | {partner})
        due_date = _suggest_header(deb_headers, "opos_due_date", used=used | {partner, amount})
        if not all((partner, amount, due_date)):
            return None
        as_of = f"{year:04d}-{fy_end_month:02d}-{fy_end_day:02d}"
        kred_sheet, _kred_headers = _read_headers(kred_path)
        deb_snap = {
            "as_of": as_of,
            "file_id": _file_id_from_path(deb_path),
            "file_path": str(deb_path.resolve()),
            "sheet_name": deb_sheet,
        }
        kred_snap = {
            "as_of": as_of,
            "file_id": _file_id_from_path(kred_path),
            "file_path": str(kred_path.resolve()),
            "sheet_name": kred_sheet,
        }
        columns = {
            "partner_id": partner,
            "partner_name": partner,
            "amount": amount,
            "due_date": due_date,
        }
        column_letters = _letters_for_headers(
            deb_headers,
            partner=partner,
            amount=amount,
            due_date=due_date,
        )
        if not all(column_letters.values()):
            return None
        sides = {
            "debitor": {"snapshots": [deb_snap]},
            "kreditor": {"snapshots": [kred_snap]},
        }
        snapshots.append({"as_of": as_of, "debitor": deb_snap, "kreditor": kred_snap})
    if not snapshots:
        return None
    return {
        "included": True,
        "snapshots": snapshots,
        "sides": sides,
        "columns": columns,
        "column_letters": column_letters,
        "aging_buckets": {"ranges": [[1, 30], [31, 60], [61, 90], [91, 180]]},
        "sort": {"basis": "most_recent", "metric": "total"},
        "top_bucket": {
            "enabled": True,
            "cumulative_thresholds": [0.2, 0.5, 0.8],
            "create_other_bucket": True,
        },
    }


def _build_period_section(
    session_dir: Path,
    token: str,
    *,
    kind: str,
) -> dict[str, Any] | None:
    files = _discover_period_files(session_dir, token)
    if not files:
        return None
    latest_year = max(files)
    periods: list[dict[str, str]] = []
    columns: dict[str, Any] | None = None
    group_cols: list[str] = []
    for year in sorted(files):
        path = files[year]
        sheet, headers = _read_headers(path)
        if not headers:
            return None
        used: set[str] = set()
        if kind == "fte":
            employment = _suggest_header(headers, "fte_employment", used=used)
            months_sum = _suggest_header(headers, "fte_months_sum", used=used | {employment})
            payroll_cols = _suggest_payroll_cols(headers, used=used | {employment, months_sum})
            grouping = _suggest_header(headers, "fte_group", used=used | {employment, months_sum, *payroll_cols})
            if not all((employment, months_sum, payroll_cols, grouping)):
                return None
            columns = {
                "employment": employment,
                "months_sum": months_sum,
                "payroll_cols": payroll_cols,
            }
            group_cols = [grouping]
        else:
            opening = _suggest_header(headers, "fa_opening", used=used)
            additions = _suggest_header(headers, "fa_additions", used=used | {opening})
            disposals = _suggest_header(headers, "fa_disposals", used=used | {opening, additions})
            dep_cols = _suggest_depreciation_cols(headers, used=used | {opening, additions, disposals})
            dep_cols = [c for c in dep_cols if c]
            grouping = _suggest_header(
                headers,
                "fa_group",
                used=used | {opening, additions, disposals, *dep_cols},
            )
            if not all((opening, additions, disposals, dep_cols, grouping)):
                return None
            columns = {
                "opening": opening,
                "additions": additions,
                "disposals": disposals,
                "depreciation_cols": dep_cols,
            }
            group_cols = [grouping]
        periods.append(
            {
                "label": _label_from_year(year, latest_year=latest_year),
                "file_id": _file_id_from_path(path),
                "file_path": str(path.resolve()),
                "sheet_name": sheet,
            }
        )
    if not periods or not columns:
        return None
    out: dict[str, Any] = {"included": True, "periods": periods, "columns": columns}
    if group_cols:
        out["group_cols"] = group_cols
    return out


def hydrate_strand_sections_from_uploads(
    session_id: str,
    upload_base: str | Path,
    sections: dict[str, Any],
    *,
    dates: dict[str, Any] | None = None,
) -> list[str]:
    """Fill missing OPOS/FTE/FA configs from uploaded session files. Returns hydrated section names."""
    sid = str(session_id or "").strip()
    if not sid:
        return []
    session_dir = _session_dir(sid, upload_base)
    if not session_dir.is_dir():
        return []

    dates = dict(dates or {})
    try:
        fy_end_month = int(dates.get("fy_end_month") or 12)
    except (TypeError, ValueError):
        fy_end_month = 12
    try:
        fy_end_day = int(dates.get("fy_end_day") or 31)
    except (TypeError, ValueError):
        fy_end_day = 31

    hydrated: list[str] = []
    builders = (
        ("opos", lambda: _build_opos_section(session_dir, fy_end_month=fy_end_month, fy_end_day=fy_end_day)),
        ("fte", lambda: _build_period_section(session_dir, "personaltable", kind="fte")),
        ("fa", lambda: _build_period_section(session_dir, "anlagengitter", kind="fa")),
    )
    for name, builder in builders:
        current = sections.get(name)
        if _section_skipped(current):
            continue
        if isinstance(current, dict) and (
            _section_has_user_mapping(name, current) or _section_complete(name, current)
        ):
            continue
        built = builder()
        if not built:
            continue
        merged = dict(built)
        if isinstance(current, dict):
            for key, value in current.items():
                if value in (None, "", [], {}):
                    continue
                if key == "columns" and isinstance(value, dict):
                    base_cols = merged.get("columns") if isinstance(merged.get("columns"), dict) else {}
                    merged["columns"] = {**base_cols, **value}
                else:
                    merged[key] = value
        if _section_complete(name, merged):
            merged.setdefault("included", True)
            sections[name] = merged
            hydrated.append(name)

    return hydrated
