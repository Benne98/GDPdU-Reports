"""Churn / ARR Bridge — bot entry with optional formula_mode (source helpers + SUMIFS for FY columns)."""
from __future__ import annotations

import json
import os
import sys

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from Churn import (  # noqa: E402
    _prepare_for_line_arr,
    build_merged_horizontal_bridge,
    contract_line_arr,
    format_churn_excel,
    fy_end_date,
    normalize_config,
    resolve_fy_years,
    write_churn_export_to_workbook,
)
from funktionssammlung import (  # noqa: E402
    build_chunked_row_sum_formula,
    build_output_file_path,
    build_sumifs_formula_body,
    revenue_report_helper_letter,
    ensure_output_writable,
    ensure_source_sheet_in_output,
    ensure_text_filter_helper_columns_from_cfg_in_ws,
    get_next_sheet_name_from_wb,
    refresh_source_sheet_from_df,
    resolve_column_names_to_df,
    source_column_range_ref,
    source_range_ref,
    _get_sheet_header_map,
    _normalize_header_name,
)
from revenue_reconciliation import (  # noqa: E402
    RECON_DIFFERENCE_LABEL,
    sales_basis_labels,
    write_reconciliation_cells,
)


def _fy_helper_col_name(fy_year: int) -> str:
    return f"_churn_arr_FY{str(int(fy_year))[-2:]}A"


def _parse_fy_col_year(fy_col: str) -> int | None:
    try:
        return 2000 + int(str(fy_col)[2:4])
    except (ValueError, IndexError):
        return None


def _append_arr_helpers_on_source(wb, source_sheet: str, df: pd.DataFrame, cfg: dict) -> dict[int, str]:
    ws = wb[source_sheet]
    d = _prepare_for_line_arr(df, cfg)
    first_fy, latest = resolve_fy_years(cfg)
    fy_years = list(range(first_fy, latest + 1))

    existing: dict[int, str] = {}
    for c in range(1, (ws.max_column or 0) + 1):
        h = str(ws.cell(row=1, column=c).value or "")
        if h.startswith("_churn_arr_FY"):
            yr = _parse_fy_col_year(h.replace("_churn_arr_", ""))
            if yr:
                existing[yr] = get_column_letter(c)

    col_letters: dict[int, str] = dict(existing)
    next_col = (ws.max_column or 0) + 1

    for fy in fy_years:
        if fy in col_letters:
            continue
        col_name = _fy_helper_col_name(fy)
        ws.cell(row=1, column=next_col).value = col_name
        as_of = fy_end_date(fy, cfg)
        arr = contract_line_arr(d, as_of, cfg).reset_index(drop=True)
        for excel_row in range(2, len(d) + 2):
            idx = excel_row - 2
            val = float(arr.iloc[idx]) if idx < len(arr) else 0.0
            ws.cell(row=excel_row, column=next_col).value = val / 1000.0
        col_letters[fy] = get_column_letter(next_col)
        next_col += 1

    return col_letters


def _source_header_present(header_map: dict[str, int], header_name: str) -> bool:
    return _normalize_header_name(header_name) in header_map


def _collect_churn_row_groups(ws, data_start: int, table_bottom: int, row_type_col: int):
    leaf_rows: list[int] = []
    bucket_rows: list[int] = []
    total_row: int | None = None
    for r in range(data_start, table_bottom + 1):
        rt = str(ws.cell(row=r, column=row_type_col).value or "").strip().lower()
        if rt == "leaf":
            leaf_rows.append(r)
        elif rt == "bucket":
            bucket_rows.append(r)
        elif rt == "total":
            total_row = r
    return leaf_rows, bucket_rows, total_row


def _collect_churn_formula_context(ws, layout: dict, cfg: dict) -> dict:
    data_start = layout["data_start_row"]
    table_bottom = layout["table_bottom_row"]
    row_type_col = layout["row_type_col"]
    key_col = layout.get("key_col")
    level_col = layout.get("level_col")
    parent_key_col = layout.get("parent_key_col")
    max_level = layout.get("max_hierarchy_level") or len(cfg.get("group_cols") or [])

    children_by_parent: dict[str, list[int]] = {}
    total_leaf_rows: list[int] = []
    rows: list[dict] = []

    for r in range(data_start, table_bottom + 1):
        rt = str(ws.cell(row=r, column=row_type_col).value or "").strip().lower()
        key = ""
        parent_key = ""
        level = None
        if key_col:
            key = str(ws.cell(row=r, column=key_col).value or "").strip()
        if parent_key_col:
            parent_key = str(ws.cell(row=r, column=parent_key_col).value or "").strip()
        if level_col:
            try:
                level = int(ws.cell(row=r, column=level_col).value)
            except (TypeError, ValueError):
                level = None

        info = {
            "row_idx": r,
            "row_type": rt,
            "key": key,
            "parent_key": parent_key,
            "level": level,
        }
        rows.append(info)

        if rt == "total":
            continue
        if parent_key:
            children_by_parent.setdefault(parent_key, []).append(r)
        if level == max_level and rt == "leaf":
            total_leaf_rows.append(r)

    return {
        "rows": rows,
        "children_by_parent": children_by_parent,
        "total_leaf_rows": total_leaf_rows,
        "max_level": max_level,
        "hierarchy_mode": bool(key_col),
    }


def _bucket_leaf_rows(
    bucket_rows: list[int],
    leaf_rows: list[int],
    data_start: int,
    bucket_idx: int,
) -> list[int]:
    br = bucket_rows[bucket_idx]
    start_r = bucket_rows[bucket_idx - 1] + 1 if bucket_idx > 0 else data_start
    return [rr for rr in leaf_rows if start_r <= rr < br]


def _apply_churn_money_format(ws, row: int, col: int) -> None:
    ws.cell(row=row, column=col).number_format = '#,##0;(#,##0);"-"'


def _leaf_sumifs_formula(
    ws,
    r: int,
    col_idx: int,
    sum_rng: str,
    group_cols: list[str],
    layout: dict,
    source_sheet_name: str,
    src_header: dict,
) -> str | None:
    pairs: list[tuple[str, str]] = []
    n = min(len(group_cols), layout["helper_right_col"])
    for i in range(n):
        gc = group_cols[i]
        if not _source_header_present(src_header, gc):
            continue
        rng = source_range_ref(source_sheet_name, src_header, gc)
        key_ref = f"${revenue_report_helper_letter(i)}${r}"
        pairs.append((rng, key_ref))
    if not pairs:
        return None
    return "=" + build_sumifs_formula_body(sum_rng, pairs, [[]], divide_by_1000=False)


def apply_churn_formulas(
    wb,
    layout: dict,
    cfg: dict,
    source_sheet_name: str,
    col_letters: dict[int, str],
) -> None:
    report_sheet = layout.get("report_sheet")
    if not report_sheet or report_sheet not in wb.sheetnames:
        raise ValueError("layout['report_sheet'] fehlt oder Sheet existiert nicht.")
    ws = wb[report_sheet]
    src_header = _get_sheet_header_map(wb[source_sheet_name])

    data_start = layout["data_start_row"]
    table_bottom = layout["table_bottom_row"]
    row_type_col = layout["row_type_col"]
    group_cols = list(cfg.get("group_cols") or [])
    fmt_money = '#,##0;(#,##0);"-"'

    ctx = _collect_churn_formula_context(ws, layout, cfg)
    leaf_rows, bucket_rows, total_row = _collect_churn_row_groups(
        ws, data_start, table_bottom, row_type_col
    )

    def apply_col_formula(r: int, col_idx: int, formula: str | None) -> None:
        if formula:
            ws.cell(row=r, column=col_idx).value = formula
            _apply_churn_money_format(ws, r, col_idx)

    money_col_indices = (
        layout.get("fy_col_indices") or []
    ) + [(None, c) for c in (layout.get("bridge_metric_col_indices") or [])]

    for fy_label, col_idx in layout.get("fy_col_indices") or []:
        fy_year = _parse_fy_col_year(fy_label)
        if fy_year is None:
            continue
        helper = col_letters.get(fy_year)
        if not helper:
            continue
        helper_idx = column_index_from_string(helper)
        sum_rng = source_column_range_ref(source_sheet_name, helper_idx)

        for info in ctx["rows"]:
            r = info["row_idx"]
            rt = info["row_type"]
            if rt == "bucket":
                continue
            if rt == "total":
                apply_col_formula(r, col_idx, f"=SUM({sum_rng})")
                continue

            child_rows = ctx["children_by_parent"].get(info["key"], [])
            if ctx["hierarchy_mode"] and child_rows:
                apply_col_formula(r, col_idx, build_chunked_row_sum_formula(child_rows, col_idx))
            elif ctx["hierarchy_mode"] and info["level"] == ctx["max_level"] and rt == "leaf":
                apply_col_formula(
                    r,
                    col_idx,
                    _leaf_sumifs_formula(ws, r, col_idx, sum_rng, group_cols, layout, source_sheet_name, src_header),
                )
            elif not ctx["hierarchy_mode"]:
                if rt == "parent" and len(group_cols) >= 1:
                    gc = group_cols[0]
                    if _source_header_present(src_header, gc):
                        rng = source_range_ref(source_sheet_name, src_header, gc)
                        key_ref = f"${revenue_report_helper_letter(0)}${r}"
                        body = build_sumifs_formula_body(sum_rng, [(rng, key_ref)], [[]], divide_by_1000=False)
                        apply_col_formula(r, col_idx, "=" + body)
                elif rt == "leaf":
                    apply_col_formula(
                        r,
                        col_idx,
                        _leaf_sumifs_formula(ws, r, col_idx, sum_rng, group_cols, layout, source_sheet_name, src_header),
                    )

        for i, br in enumerate(bucket_rows):
            rows_in_bucket = _bucket_leaf_rows(bucket_rows, leaf_rows, data_start, i)
            apply_col_formula(br, col_idx, build_chunked_row_sum_formula(rows_in_bucket, col_idx))

    for col_idx in layout.get("bridge_metric_col_indices") or []:
        for info in ctx["rows"]:
            r = info["row_idx"]
            rt = info["row_type"]
            if rt == "bucket":
                continue
            child_rows = ctx["children_by_parent"].get(info["key"], [])
            if ctx["hierarchy_mode"] and child_rows:
                apply_col_formula(r, col_idx, build_chunked_row_sum_formula(child_rows, col_idx))
            elif rt == "total" and ctx["hierarchy_mode"] and ctx["total_leaf_rows"]:
                apply_col_formula(
                    r, col_idx, build_chunked_row_sum_formula(ctx["total_leaf_rows"], col_idx)
                )

        for i, br in enumerate(bucket_rows):
            rows_in_bucket = _bucket_leaf_rows(bucket_rows, leaf_rows, data_start, i)
            apply_col_formula(br, col_idx, build_chunked_row_sum_formula(rows_in_bucket, col_idx))

        if total_row is not None and not ctx["hierarchy_mode"] and leaf_rows:
            apply_col_formula(total_row, col_idx, build_chunked_row_sum_formula(leaf_rows, col_idx))

    for c in layout.get("money_col_indices") or []:
        for r in range(data_start, table_bottom + 1):
            ws.cell(row=r, column=c).number_format = fmt_money

    wb.calculation.fullCalcOnLoad = True
    wb.calculation.calcMode = "auto"
    wb.calculation.forceFullCalc = True


def apply_churn_reconciliation(wb, report_sheet: str, layout: dict, cfg: dict | None = None) -> None:
    labels = sales_basis_labels((cfg or {}).get("sales_basis"))
    ws = wb[report_sheet]
    label_col = layout["label_col"]
    row_type_col = layout.get("row_type_col")
    rows_by_label = {
        str(ws.cell(r, label_col).value or "").strip(): r
        for r in range(layout["data_start_row"], ws.max_row + 1)
    }
    total_row = rows_by_label.get(labels.total_label)
    recon_row = rows_by_label.get(RECON_DIFFERENCE_LABEL)
    reported_row = rows_by_label.get(labels.reported_label)
    # Prefer row_type markers — more reliable than label text after hierarchy edits.
    if row_type_col:
        for r in range(layout["data_start_row"], (ws.max_row or 0) + 1):
            rt = str(ws.cell(r, row_type_col).value or "").strip().lower()
            if rt == "total" and total_row is None:
                total_row = r
            elif rt == "recon":
                recon_row = r
            elif rt == "reported":
                reported_row = r
    if not all((total_row, recon_row, reported_row)):
        return
    period_columns = {
        label: col_idx for label, col_idx in layout.get("fy_col_indices", [])
    }
    if not period_columns:
        return
    write_reconciliation_cells(
        wb,
        ws,
        period_columns=period_columns,
        total_row=total_row,
        recon_row=recon_row,
        reported_row=reported_row,
        sales_basis=labels.sales_basis,
    )


def run_churn_in_workbook(cfg: dict, wb) -> str:
    """Fast Track / session mode: write Churn into an already-open workbook."""
    from funktionssammlung import ensure_source_sheet_in_workbook

    cfg = normalize_config(cfg)
    sheet = cfg.get("sheet_name") or "Data Template"
    df = pd.read_excel(cfg["file_path"], sheet_name=sheet, engine="openpyxl")
    df.columns = df.columns.astype(str).str.replace("\u00a0", " ", regex=False).str.strip()
    cfg = dict(cfg)
    cfg["group_cols"] = resolve_column_names_to_df(df, list(cfg["group_cols"]))
    export_df = build_merged_horizontal_bridge(df, cfg)
    formula_mode = bool(cfg.get("formula_mode", True))
    report_sheet = str(cfg.get("base_sheet_name") or "Churn")

    if formula_mode:
        source_sheet = ensure_source_sheet_in_workbook(wb, cfg, df)
        refresh_source_sheet_from_df(wb[source_sheet], df, cfg)
        ensure_text_filter_helper_columns_from_cfg_in_ws(ws_source=wb[source_sheet], source_df=df, cfg=cfg)
        col_letters = _append_arr_helpers_on_source(wb, source_sheet, df, cfg)
        report_sheet = get_next_sheet_name_from_wb(wb, report_sheet)
        write_churn_export_to_workbook(wb, export_df, report_sheet, cfg, formula_mode=True)
        layout = format_churn_excel(wb, report_sheet, export_df, cfg, formula_mode=True)
        layout["report_sheet"] = report_sheet
        apply_churn_formulas(wb, layout, cfg, source_sheet, col_letters)
        apply_churn_reconciliation(wb, report_sheet, layout, cfg)
    else:
        write_churn_export_to_workbook(wb, export_df, report_sheet, cfg, formula_mode=False)
        layout = format_churn_excel(wb, report_sheet, export_df, cfg, formula_mode=False)
        apply_churn_reconciliation(wb, report_sheet, layout, cfg)
    return report_sheet


def main() -> str:
    if len(sys.argv) < 2:
        raise ValueError("Bitte den Pfad zur churn config.json als Argument übergeben.")
    with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    cfg = normalize_config(cfg)
    sheet = cfg.get("sheet_name") or "Data Template"
    df = pd.read_excel(cfg["file_path"], sheet_name=sheet, engine="openpyxl")
    df.columns = df.columns.astype(str).str.replace("\u00a0", " ", regex=False).str.strip()
    cfg = dict(cfg)
    cfg["group_cols"] = resolve_column_names_to_df(df, list(cfg["group_cols"]))
    export_df = build_merged_horizontal_bridge(df, cfg)

    if cfg.get("case_id"):
        output_path = build_output_file_path(cfg)
    else:
        from Churn import build_output_path

        output_path = build_output_path(str(cfg.get("output_file_path") or "."), cfg)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    ensure_output_writable(output_path)

    formula_mode = bool(cfg.get("formula_mode", True))
    report_sheet = str(cfg.get("base_sheet_name") or "Churn")

    if formula_mode:
        source_sheet = ensure_source_sheet_in_output(cfg, output_path)
        wb = load_workbook(output_path)
        refresh_source_sheet_from_df(wb[source_sheet], df, cfg)
        ensure_text_filter_helper_columns_from_cfg_in_ws(ws_source=wb[source_sheet], source_df=df, cfg=cfg)
        col_letters = _append_arr_helpers_on_source(wb, source_sheet, df, cfg)
        report_sheet = get_next_sheet_name_from_wb(wb, report_sheet)
        write_churn_export_to_workbook(wb, export_df, report_sheet, cfg, formula_mode=True)
        layout = format_churn_excel(wb, report_sheet, export_df, cfg, formula_mode=True)
        layout["report_sheet"] = report_sheet
        apply_churn_formulas(wb, layout, cfg, source_sheet, col_letters)
        apply_churn_reconciliation(wb, report_sheet, layout, cfg)
        wb.save(output_path)
        wb.close()
    else:
        from openpyxl import Workbook

        wb = Workbook()
        if wb.sheetnames[0] == "Sheet":
            del wb["Sheet"]
        write_churn_export_to_workbook(wb, export_df, report_sheet, cfg, formula_mode=False)
        layout = format_churn_excel(wb, report_sheet, export_df, cfg, formula_mode=False)
        apply_churn_reconciliation(wb, report_sheet, layout, cfg)
        wb.save(output_path)
        wb.close()

    return output_path


if __name__ == "__main__":
    out = main()
    print(f"Churn output written: {out}")
