#!/usr/bin/env python3
"""Reconcile OPOS summary vs detail for one period — find exact gap cause."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opos import (  # noqa: E402
    POS_COL,
    AsOfPeriod,
    assign_bucket_for_due,
    build_aging_bucket_defs,
    filter_zero_partners,
    aggregate_opos,
    preprocess_opos_input,
)

MASTER = Path("/Users/mathi/finssentials/Desktop/Analyze/asdasd_Master-2.xlsx")
PERIOD_LABEL = "Dec24A"
AS_OF = pd.Timestamp("2024-12-31")
SOURCE_SHEET = "__SOURCE__AR_Dec24A"


def _read_source_df(wb) -> pd.DataFrame:
    ws = wb[SOURCE_SHEET]
    headers = [str(ws.cell(1, c).value or "").strip() for c in range(1, ws.max_column + 1)]
    rows = []
    for r in range(2, ws.max_row + 1):
        rows.append({headers[i - 1]: ws.cell(r, i).value for i in range(1, len(headers) + 1)})
    return pd.DataFrame(rows), headers


def _resolve_columns(headers: list[str]) -> dict[str, str]:
    col_map = {}
    for role, candidates in {
        "partner_id": ["Debitor", "Debitoren", "Kunde", "Partner", "Debitorennummer"],
        "partner_name": ["Debitor", "Debitoren", "Kunde", "Partner", "Name"],
        "amount": ["Betrag in Hauswährung", "Betrag", "Amount"],
        "due_date": ["Nettofälligkeit", "Faellig", "Fällig", "Due", "Belegdatum"],
    }.items():
        for c in candidates:
            if c in headers:
                col_map[role] = c
                break
    missing = [k for k in ("partner_id", "amount", "due_date") if k not in col_map]
    if missing:
        raise SystemExit(f"Missing columns {missing} in {headers}")
    if "partner_name" not in col_map:
        col_map["partner_name"] = col_map["partner_id"]
    return col_map


def _read_excel_bucket_values(wb, period_col: int) -> tuple[dict[str, float], dict[str, float]]:
    summary = wb["Trade debtors aging"]
    detail = wb["Trade debtors aging detail"]

    summary_vals: dict[str, float] = {}
    for r in range(1, 40):
        label = summary.cell(r, POS_COL).value
        if not label:
            continue
        label = str(label).strip()
        val = summary.cell(r, period_col).value
        if label in ("Not yet due",) or "days" in label.lower() or label.startswith(">"):
            try:
                summary_vals[label] = float(val or 0)
            except (TypeError, ValueError):
                summary_vals[label] = 0.0

    total_row = None
    for r in range(1, detail.max_row + 1):
        if str(detail.cell(r, POS_COL).value or "").strip() == "Total":
            total_row = r
            break
    if total_row is None:
        raise SystemExit("Detail Total row not found")

    # bucket order from header row 4
    bucket_labels: list[str] = []
    cc = period_col
    while True:
        hdr = detail.cell(4, cc).value or detail.cell(3, cc).value
        if hdr is None or str(hdr).strip().lower() == "total":
            break
        bucket_labels.append(str(hdr).strip())
        cc += 1

    detail_vals: dict[str, float] = {}
    for i, label in enumerate(bucket_labels):
        val = detail.cell(total_row, period_col + i).value
        try:
            detail_vals[label] = float(val or 0)
        except (TypeError, ValueError):
            detail_vals[label] = 0.0

    return summary_vals, detail_vals


def main() -> None:
    wb = load_workbook(MASTER, data_only=True)
    raw, headers = _read_source_df(wb)
    col_map = _resolve_columns(headers)

    cfg = {
        "side": "debitor",
        "columns": col_map,
        "filters": {"enabled": False},
        "aging_buckets": {
            "ranges": [[1, 30], [31, 60], [61, 86], [87, 180], [181, 210], [211, 360], [361, 9999]]
        },
        "sort": {"basis": "most_recent", "metric": "total", "bucket_keys": ["__total__"]},
    }
    buckets = build_aging_bucket_defs(cfg)
    periods = [AsOfPeriod(PERIOD_LABEL, AS_OF)]

    null_due = raw[col_map["due_date"]].isna() | (raw[col_map["due_date"]].astype(str).str.strip() == "")
    print(f"Raw source rows: {len(raw)}")
    print(f"Rows with missing/invalid due date: {int(null_due.sum())}")

    df = preprocess_opos_input(raw, cfg)
    print(f"Rows after preprocess (valid due): {len(df)}")

    grid, partner_order, _partner_meta = aggregate_opos({PERIOD_LABEL: df}, cfg, periods, buckets)
    partner_order_f = filter_zero_partners(partner_order, grid, periods, buckets)

    as_of = periods[0].date
    sub = df.copy()
    sub["_bucket"] = [assign_bucket_for_due(d, as_of, buckets) for d in sub["_due_date"]]
    sub["_keur"] = pd.to_numeric(sub[col_map["amount"]], errors="coerce").fillna(0) / 1000.0
    sub["_partner_id"] = sub[col_map["partner_id"]].astype(str).str.strip()

    summary_src = sub.groupby("_bucket")["_keur"].sum()

    def grid_sum(order: list[str]) -> dict[str, float]:
        out = {b.key: 0.0 for b in buckets}
        for pid in order:
            bmap = grid[PERIOD_LABEL].get(pid, {})
            for b in buckets:
                out[b.key] += float(bmap.get(b.key, 0.0))
        return out

    detail_filtered = grid_sum(partner_order_f)
    detail_all = grid_sum(partner_order)

    # Partners with balance in not_yet_due excluded from detail
    zero_dropped = set(partner_order) - set(partner_order_f)
    dropped_nyd = 0.0
    for pid in zero_dropped:
        dropped_nyd += float(grid[PERIOD_LABEL].get(pid, {}).get("not_yet_due", 0.0))

    # Source rows whose partner is NOT in detail partner list
    in_detail_pids = set(partner_order_f)
    orphan_rows = sub[~sub["_partner_id"].isin(in_detail_pids)]
    orphan_by_bucket = orphan_rows.groupby("_bucket")["_keur"].sum()

    # Rows with null due still in __SOURCE__ but excluded from both
    null_due_amt = (
        pd.to_numeric(raw.loc[null_due, col_map["amount"]], errors="coerce").fillna(0).sum() / 1000.0
    )

    excel_summary, excel_detail = _read_excel_bucket_values(wb, period_col=5)

    print(f"\nPartners: grid={len(partner_order)}, detail visible={len(partner_order_f)}, zero-filter dropped={len(zero_dropped)}")

    print(f"\n{'Bucket':<22} {'Source(all)':>12} {'Detail(sum)':>12} {'Excel Summ':>12} {'Excel Det':>12} {'Gap(S-D)':>10}")
    for b in buckets:
        src_v = float(summary_src.get(b.key, 0.0))
        det_v = float(detail_filtered.get(b.key, 0.0))
        ex_s = float(excel_summary.get(b.label, 0.0))
        ex_d = float(excel_detail.get(b.label, 0.0))
        print(f"{b.label:<22} {src_v:12.3f} {det_v:12.3f} {ex_s:12.3f} {ex_d:12.3f} {ex_s-ex_d:10.3f}")

    print(f"\nNull-due amount excluded from Python grid (still in __SOURCE__): {null_due_amt:.3f} kEUR")
    print(f"Zero-partner filter dropped not_yet_due from detail: {dropped_nyd:.3f} kEUR")

    if len(orphan_by_bucket):
        print("\nAmount by bucket for rows whose partner is NOT in detail partner list:")
        for b in buckets:
            v = float(orphan_by_bucket.get(b.key, 0.0))
            if abs(v) > 1e-6:
                print(f"  {b.label}: {v:.3f} kEUR")

    # Top partners contributing to not_yet_due gap
    gap_partners: list[tuple[str, float, float, str]] = []
    for pid in partner_order:
        nyd = float(grid[PERIOD_LABEL].get(pid, {}).get("not_yet_due", 0.0))
        in_detail = pid in partner_order_f
        if abs(nyd) > 1e-6 and not in_detail:
            gap_partners.append((pid, nyd, 0.0, "zero-filtered"))
        elif abs(nyd) > 1e-6:
            gap_partners.append((pid, nyd, nyd, "in detail"))

    # Partners in source not_yet_due not matching detail sum - per partner diff
    print("\nTop 15 partners by not_yet_due in source (Dec24A):")
    nyd_partners = (
        sub[sub["_bucket"] == "not_yet_due"]
        .groupby("_partner_id")["_keur"]
        .sum()
        .sort_values(ascending=False)
    )
    for pid, amt in nyd_partners.head(15).items():
        in_det = "YES" if pid in partner_order_f else "NO (dropped)"
        print(f"  {pid}: {amt:.3f} kEUR  detail_visible={in_det}")

    wb.close()


if __name__ == "__main__":
    main()
