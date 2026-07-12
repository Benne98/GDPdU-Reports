"""Shared databook master workbook and mapping file paths."""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DESKTOP_DIR = PROJECT_ROOT / "Desktop"
SORT_FILES_DIR = DESKTOP_DIR / "Sort_files"
MAPPINGFILES_DIR = DESKTOP_DIR / "Mappingfiles"
BACKEND_DIR = PROJECT_ROOT / "backend"
TEMPLATES_FDD_DIR = BACKEND_DIR / "templates" / "fdd"

MASTER_WORKBOOK = DESKTOP_DIR / "BS_PL_Master.xlsx"
MASTER_WORKBOOK_STR = str(MASTER_WORKBOOK)

CF_NA_L3_ORDER_FILE = str(TEMPLATES_FDD_DIR / "CF_NA_L3_order.xlsx")


def _first_existing_path(*candidates: Path) -> str:
    for path in candidates:
        if path.is_file():
            return str(path)
    return str(candidates[-1])


PL_RECON_MAPPING_FILE = _first_existing_path(
    TEMPLATES_FDD_DIR / "PL_recon_Mapping.xlsx",
    SORT_FILES_DIR / "PL_recon_Mapping.xlsx",
    DESKTOP_DIR / "PL_recon_Mapping.xlsx",
)
BS_RECON_MAPPING_FILE = _first_existing_path(
    TEMPLATES_FDD_DIR / "BS_recon_Mapping.xlsx",
    SORT_FILES_DIR / "BS_recon_Mapping.xlsx",
    DESKTOP_DIR / "BS_recon_Mapping.xlsx",
)
# Cashflow L4 order under Financial result — PL recon mapping has L3+L4 (not SuSa Kontenmapping).
PL_KONTOMAPPING_FILE = PL_RECON_MAPPING_FILE

MASTER_SHEET_ORDER = ("Master_BS", "Master_PL")

GROUP_BS_RECON_SHEET = "BS_Reconciliation"
GROUP_PL_RECON_SHEET = "PL_Reconciliation"
ENTITY_RECON_BS_SUFFIX = "_BS_Reconciliation"
ENTITY_RECON_PL_SUFFIX = "_PL_Reconciliation"
_EXCEL_SHEET_INVALID_RE = re.compile(r"[\[\]\:\*\?/\\]")

DATABOOK_SHEET_ORDER = (
    GROUP_BS_RECON_SHEET,
    GROUP_PL_RECON_SHEET,
    "BS_Bucket",
    "Lead_BS",
    "Lead_IS",
    "Working_Capital",
    "Cashflow",
)

_FTE_SHEET_RE = re.compile(r"^FTE[_\s]", re.I)
_FY_IN_NAME_RE = re.compile(r"(?:FY|YTD)?(19|20)\d{2}")
_LEGACY_FTE_OUTPUT_RE = re.compile(r"^Personnel\s+\d{4}$", re.I)

OPOS_REPORT_SHEETS = frozenset(
    {
        "Trade debtors aging detail",
        "Trade debtors aging",
        "Trade creditors aging detail",
        "Trade creditors aging",
    }
)

FA_ROLLF_OUTPUT_SHEET = "FA roll forward"


def sanitize_entity_recon_sheet_name(entity: str, kind: str) -> str:
    """Build Excel-safe per-entity recon tab name ({Entity}_BS|PL_Reconciliation)."""
    suffix = ENTITY_RECON_BS_SUFFIX if str(kind).strip().lower() == "bs" else ENTITY_RECON_PL_SUFFIX
    stem = _EXCEL_SHEET_INVALID_RE.sub("", str(entity or "").strip()) or "Entity"
    max_stem = max(31 - len(suffix), 1)
    return f"{stem[:max_stem]}{suffix}"


def is_entity_recon_sheet(name: str) -> bool:
    n = str(name or "")
    if n in {GROUP_BS_RECON_SHEET, GROUP_PL_RECON_SHEET}:
        return False
    return n.endswith(ENTITY_RECON_BS_SUFFIX) or n.endswith(ENTITY_RECON_PL_SUFFIX)


def entity_recon_sheet_names(entities: list[str]) -> list[str]:
    """Per entity: BS then PL (entity_order sequence)."""
    out: list[str] = []
    for entity in entities:
        out.append(sanitize_entity_recon_sheet_name(entity, "bs"))
        out.append(sanitize_entity_recon_sheet_name(entity, "pl"))
    return out


def remove_stale_entity_recon_sheets(wb, entities: list[str]) -> None:
    """Drop per-entity recon tabs that are no longer in entity_order."""
    keep = set(entity_recon_sheet_names(entities))
    for sn in list(wb.sheetnames):
        if is_entity_recon_sheet(sn) and sn not in keep:
            del wb[sn]


def collect_entity_recon_sheets_ordered(existing: list[str]) -> list[str]:
    """Preserve entity order from workbook tab positions; BS before PL per entity."""
    entity_first_idx: dict[str, int] = {}
    sheets_by_entity: dict[str, dict[str, str]] = {}
    for idx, name in enumerate(existing):
        if not is_entity_recon_sheet(name):
            continue
        if name.endswith(ENTITY_RECON_BS_SUFFIX):
            entity = name[: -len(ENTITY_RECON_BS_SUFFIX)]
            kind = "bs"
        else:
            entity = name[: -len(ENTITY_RECON_PL_SUFFIX)]
            kind = "pl"
        entity_first_idx.setdefault(entity, idx)
        sheets_by_entity.setdefault(entity, {})[kind] = name
    ordered_entities = sorted(entity_first_idx.keys(), key=lambda e: entity_first_idx[e])
    out: list[str] = []
    for entity in ordered_entities:
        pair = sheets_by_entity.get(entity, {})
        if "bs" in pair:
            out.append(pair["bs"])
        if "pl" in pair:
            out.append(pair["pl"])
    return out


def _source_group_rank(name: str) -> int:
    n = str(name)
    if is_opos_ar_ap_source_sheet(n):
        return 0
    if is_fa_rollf_source_sheet(n):
        return 1
    if is_fte_source_sheet(n):
        return 2
    return 3


def _source_sort_key(sheet_name: str) -> tuple:
    name = str(sheet_name)
    return (_source_group_rank(name), name.lower())


def is_opos_report_sheet(name: str) -> bool:
    return str(name) in OPOS_REPORT_SHEETS


def is_opos_ar_ap_source_sheet(name: str) -> bool:
    n = str(name)
    return n.startswith("__SOURCE__AR_") or n.startswith("__SOURCE__AP_")


def is_fa_rollf_output_sheet(name: str) -> bool:
    lower = str(name).strip().lower()
    return lower == FA_ROLLF_OUTPUT_SHEET.lower() or "fixed assets roll" in lower


def is_fa_rollf_source_sheet(name: str) -> bool:
    n = str(name)
    return n.startswith("__SOURCE__FA_")


def is_fte_output_sheet(name: str) -> bool:
    n = str(name).strip().lower()
    return n == "fte development" or bool(_LEGACY_FTE_OUTPUT_RE.match(str(name)))


def is_fte_source_sheet(name: str) -> bool:
    n = str(name)
    if is_fte_output_sheet(n):
        return False
    if re.match(r"^FTE_", n):
        return True
    if not n.startswith("__SOURCE__"):
        return False
    if is_fa_rollf_source_sheet(n) or is_opos_ar_ap_source_sheet(n):
        return False
    rest = n[len("__SOURCE__") :]
    return bool(re.match(r"^(FY|YTD)\d{2}A$", rest))


def clear_opos_workbook_sheets(wb) -> None:
    """Remove only OPOS aging report tabs and AR/AP source sheets (re-run cleanup)."""
    for sn in list(wb.sheetnames):
        name = str(sn)
        if name in OPOS_REPORT_SHEETS:
            del wb[name]
            continue
        if is_opos_ar_ap_source_sheet(name):
            del wb[name]


def clear_fa_rollf_workbook_sheets(wb) -> None:
    """Remove only Fixed Assets rollforward output and FA period source sheets."""
    for sn in list(wb.sheetnames):
        name = str(sn)
        if is_fa_rollf_output_sheet(name) or is_fa_rollf_source_sheet(name):
            del wb[name]


def clear_fte_workbook_sheets(wb) -> None:
    """Remove only FTE Development output and FTE period source sheets."""
    for sn in list(wb.sheetnames):
        name = str(sn)
        if is_fte_output_sheet(name) or is_fte_source_sheet(name):
            del wb[name]


def _strand_sort_key(sheet_name: str) -> tuple:
    name = str(sheet_name)
    lower = name.lower()
    if lower == FA_ROLLF_OUTPUT_SHEET.lower() or "fixed assets roll" in lower or "fa roll forward" in lower:
        return (0, name)
    if _FTE_SHEET_RE.match(name) or "fte development" in lower:
        years = [int(y) for y in _FY_IN_NAME_RE.findall(name)]
        return (1, min(years) if years else 9999, name)
    if "debtor" in lower:
        return (2, 0 if "summary" in lower or not lower.endswith("detail") else 1, name)
    if "creditor" in lower:
        return (3, 0 if "summary" in lower or not lower.endswith("detail") else 1, name)
    return (4, name)


def is_workbook_source_sheet(name: str) -> bool:
    """True for hidden data sources (__SOURCE__*, legacy FTE_*), not strand outputs."""
    name = str(name)
    if name.startswith("__SOURCE__"):
        return True
    if name.lower() == "fte development":
        return False
    return bool(re.match(r"^FTE_", name))


def reorder_workbook_sheets(wb) -> None:
    """Reorder tabs: entity recons → masters → group recons → databook → strands → sources."""
    existing = list(wb.sheetnames)
    ordered: list[str] = []

    def append(names: tuple[str, ...] | list[str]) -> None:
        for name in names:
            if name in existing and name not in ordered:
                ordered.append(name)

    append(collect_entity_recon_sheets_ordered(existing))
    append(MASTER_SHEET_ORDER)
    append(DATABOOK_SHEET_ORDER)

    strand_candidates = [
        sn
        for sn in existing
        if sn not in ordered and not is_workbook_source_sheet(sn)
    ]
    strand_candidates.sort(key=_strand_sort_key)
    append(strand_candidates)

    for sn in existing:
        if sn not in ordered and not is_workbook_source_sheet(sn):
            ordered.append(sn)

    sources = sorted(
        [sn for sn in existing if is_workbook_source_sheet(sn)],
        key=_source_sort_key,
    )
    append(sources)

    for target_idx, name in enumerate(ordered):
        if name not in wb.sheetnames:
            continue
        while wb.sheetnames.index(name) > target_idx:
            wb.move_sheet(wb[name], offset=-1)


def reorder_workbook_file(path: str | Path) -> None:
    """Load workbook at path, reorder sheets, save."""
    from openpyxl import load_workbook

    p = Path(path)
    if not p.is_file():
        return
    wb = load_workbook(p)
    reorder_workbook_sheets(wb)
    wb.save(p)
    wb.close()
